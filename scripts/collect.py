from __future__ import annotations

import html
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg
import requests

try:
    from scripts.station_fetchers import enrich_rows
except ImportError:  # python scripts/collect.py
    from station_fetchers import enrich_rows

TOKYO = ZoneInfo("Asia/Tokyo")
AREA_ID = "JP13"
TARGET_STATIONS = {
    "TBS": "TBSラジオ",
    "QRR": "文化放送",
    "LFR": "ニッポン放送",
    "FMT": "TOKYO FM",
    "FMJ": "J-WAVE",
    "JORF": "ラジオ日本",
}

SUBMISSION_HINT_RE = re.compile(
    r"(?:メッセージ|メール|お便り|投稿|アンケート|ワンコメ|送って|お送り|お寄せ|募集中|募集|参加|フォーム)",
    re.I,
)

STRONG_THEME_PATTERNS = [
    # 伊集院光のタネ: 今日のメールテーマ＝「タネ」は【うちだけ語】
    re.compile(
        r"(?:今日|本日)?(?:の)?メールテーマ\s*[＝=]\s*「タネ」\s*は\s*[【「『]\s*(.+?)\s*[】」』]",
        re.I,
    ),
    # Skyrocket Company: 本日の議題は ...\n【 ダンスダンスダンス案件〜心踊る瞬間 】
    re.compile(r"本日の議題は[^。]{0,160}?[【「『]\s*(.+?)\s*[】」』]", re.I | re.S),
    # Explicit listener-submission themes.
    re.compile(
        r"(?:(?:今日|本日|けさ|今朝|今週|今夜)(?:の)?)?"
        r"(?:メッセージ|メール|投稿)テーマ\s*(?:は|[＝=:：])\s*[、,\s]*"
        r"[「『【]?\s*(.+?)(?:[」』】]|$|\n)",
        re.I,
    ),
    re.compile(
        r"(?:募集中のメッセージテーマ|募集テーマ|お題)\s*(?:は|[＝=:：])?\s*[、,\s]*"
        r"[＜<「『【]?\s*(.+?)(?:[＞>」』】]|$|\n)",
        re.I,
    ),
    re.compile(r"(?:本日|今日)?(?:の)?議題\s*(?:は|[＝=:：])\s*[「『【]\s*(.+?)\s*[」』】]", re.I),
]

GENERIC_THEME_LABEL_RE = re.compile(
    r"(?:(?:今日|本日|けさ|今朝|今週|今夜|月曜(?:日)?|火曜(?:日)?|水曜(?:日)?|木曜(?:日)?|金曜(?:日)?|土曜(?:日)?|日曜(?:日)?)(?:の)?)?"
    r"テーマ\s*(?:は|[＝=:：])",
    re.I,
)

GENERIC_THEME_EXCLUDED_PREFIXES = ("選曲", "楽曲", "音楽", "特集", "コーナー", "企画")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n+", "\n", value)
    return value.strip()


def normalize_theme(value: str) -> str | None:
    theme = value.strip(" \t\n、,。．・:：=＝『』「」【】[]<>＜＞\"'")
    theme = re.split(r"\n|(?:メール|メッセージ|投稿)(?:は|を|で|まで)", theme, maxsplit=1)[0].strip()
    theme = theme.rstrip("♪！!。．、,").strip()
    if not (2 <= len(theme) <= 120):
        return None
    if re.search(r"https?://|\S+@\S+", theme, re.I):
        return None
    return theme


def generic_theme_candidate(text: str, match: re.Match[str]) -> str | None:
    prefix = text[max(0, match.start() - 12) : match.start()]
    if any(word in prefix for word in GENERIC_THEME_EXCLUDED_PREFIXES):
        return None

    # A bare "テーマは" is ambiguous. Only accept it when nearby text clearly
    # asks listeners to participate or send something.
    context = text[max(0, match.start() - 220) : min(len(text), match.end() + 520)]
    if not SUBMISSION_HINT_RE.search(context):
        return None

    tail = text[match.end() : match.end() + 180].lstrip(" \t、,：:")
    quoted = re.search(r"[「『【＜<]\s*([^」』】＞>\n]{2,120}?)\s*[」』】＞>]", tail)
    if quoted and quoted.start() <= 40:
        return normalize_theme(quoted.group(1))

    raw = re.split(r"\n|[。．！!]", tail, maxsplit=1)[0]
    return normalize_theme(raw)


def extract_theme(*values: str) -> str | None:
    text = "\n".join(v for v in values if v)

    for pattern in STRONG_THEME_PATTERNS:
        match = pattern.search(text)
        if match:
            theme = normalize_theme(match.group(1))
            if theme:
                return theme

    for match in GENERIC_THEME_LABEL_RE.finditer(text):
        theme = generic_theme_candidate(text, match)
        if theme:
            return theme

    return None


def extract_message_url(raw: str) -> str | None:
    mailto = re.search(r'href=["\'](mailto:[^"\']+)', raw or "", re.I)
    if mailto:
        return mailto.group(1)
    form = re.search(r'href=["\'](https?://[^"\']+)', raw or "", re.I)
    if form and any(word in form.group(1).lower() for word in ("message", "mail", "form")):
        return html.unescape(form.group(1))
    email = re.search(r"(?<![\w.-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", raw or "", re.I)
    if email:
        return f"mailto:{email.group(1)}"
    return None


def parse_radiko_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=TOKYO)


def fetch_programs(date_yyyymmdd: str) -> list[dict]:
    source_url = f"https://radiko.jp/v3/program/date/{date_yyyymmdd}/{AREA_ID}.xml"
    response = requests.get(source_url, timeout=30, headers={"User-Agent": "radio-mail-thema/0.4"})
    response.raise_for_status()
    root = ET.fromstring(response.content)

    rows: list[dict] = []
    for station in root.findall(".//station"):
        station_id = station.attrib.get("id") or station.findtext("id")
        if station_id not in TARGET_STATIONS:
            continue
        station_name = clean_text(station.findtext("name")) or TARGET_STATIONS[station_id]
        for prog in station.findall(".//prog"):
            ft = prog.attrib.get("ft")
            to = prog.attrib.get("to")
            if not ft or not to:
                continue
            title = clean_text(prog.findtext("title"))
            desc_raw = prog.findtext("desc") or ""
            info_raw = prog.findtext("info") or ""
            desc = clean_text(desc_raw)
            info = clean_text(info_raw)
            url = clean_text(prog.findtext("url")) or None
            theme = extract_theme(title, desc, info)
            message_url = extract_message_url(info_raw) or extract_message_url(desc_raw)
            rows.append(
                {
                    "broadcast_date": datetime.strptime(date_yyyymmdd, "%Y%m%d").date(),
                    "station_id": station_id,
                    "station_name": station_name,
                    "program_name": title or "(番組名なし)",
                    "start_at": parse_radiko_datetime(ft),
                    "end_at": parse_radiko_datetime(to),
                    "theme": theme,
                    "description": (desc + ("\n" + info if info else ""))[:5000] or None,
                    "program_url": url,
                    "message_url": message_url,
                    "source_url": source_url,
                    "theme_source_type": "radiko" if theme else None,
                    "theme_source_url": source_url if theme else None,
                }
            )
    return rows


def save(rows: list[dict]) -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS radio_themes (
                  id BIGSERIAL PRIMARY KEY,
                  broadcast_date DATE NOT NULL,
                  station_id TEXT NOT NULL,
                  station_name TEXT NOT NULL,
                  program_name TEXT NOT NULL,
                  start_at TIMESTAMPTZ NOT NULL,
                  end_at TIMESTAMPTZ NOT NULL,
                  theme TEXT,
                  description TEXT,
                  program_url TEXT,
                  message_url TEXT,
                  source_url TEXT NOT NULL,
                  theme_source_type TEXT,
                  theme_source_url TEXT,
                  fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  UNIQUE (broadcast_date, station_id, start_at, program_name)
                )
                """
            )
            # Existing databases created before official-source enrichment.
            cur.execute("ALTER TABLE radio_themes ADD COLUMN IF NOT EXISTS theme_source_type TEXT")
            cur.execute("ALTER TABLE radio_themes ADD COLUMN IF NOT EXISTS theme_source_url TEXT")
            for row in rows:
                cur.execute(
                    """
                    INSERT INTO radio_themes (
                      broadcast_date, station_id, station_name, program_name,
                      start_at, end_at, theme, description, program_url,
                      message_url, source_url, theme_source_type, theme_source_url, fetched_at
                    ) VALUES (
                      %(broadcast_date)s, %(station_id)s, %(station_name)s, %(program_name)s,
                      %(start_at)s, %(end_at)s, %(theme)s, %(description)s, %(program_url)s,
                      %(message_url)s, %(source_url)s, %(theme_source_type)s, %(theme_source_url)s, NOW()
                    )
                    ON CONFLICT (broadcast_date, station_id, start_at, program_name)
                    DO UPDATE SET
                      station_name = EXCLUDED.station_name,
                      end_at = EXCLUDED.end_at,
                      theme = EXCLUDED.theme,
                      description = EXCLUDED.description,
                      program_url = EXCLUDED.program_url,
                      message_url = EXCLUDED.message_url,
                      source_url = EXCLUDED.source_url,
                      theme_source_type = EXCLUDED.theme_source_type,
                      theme_source_url = EXCLUDED.theme_source_url,
                      fetched_at = NOW()
                    """,
                    row,
                )
        conn.commit()


def main() -> None:
    now = datetime.now(TOKYO)
    date_yyyymmdd = sys.argv[1] if len(sys.argv) > 1 else now.strftime("%Y%m%d")
    rows = fetch_programs(date_yyyymmdd)
    before = sum(1 for row in rows if row["theme"])
    stats = enrich_rows(rows, date_yyyymmdd, extract_theme, extract_message_url)
    save(rows)
    themes = sum(1 for row in rows if row["theme"])
    print(
        f"saved {len(rows)} programs ({themes} themes; radiko={before}, "
        f"official_added={stats['themes_added']}) for {date_yyyymmdd}; "
        f"daily_pages={stats['daily_pages']} program_pages={stats['program_pages']} "
        f"message_urls_added={stats['message_urls_added']}"
    )


if __name__ == "__main__":
    main()
