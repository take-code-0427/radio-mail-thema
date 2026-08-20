from __future__ import annotations

import os
import re
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from scripts.program_merge import canonical_program_name
except ImportError:  # python scripts/station_fetchers.py
    from program_merge import canonical_program_name


@dataclass(frozen=True)
class StationSource:
    station_id: str
    daily_url: str | None
    domains: tuple[str, ...]

    def url_for(self, yyyymmdd: str) -> str | None:
        if not self.daily_url:
            return None
        return self.daily_url.format(date=yyyymmdd)


STATION_SOURCES = {
    "TBS": StationSource("TBS", "https://www.tbsradio.jp/timetable/", ("tbsradio.jp", "www.tbsradio.jp")),
    "QRR": StationSource("QRR", "https://www.joqr.co.jp/qr/dailyprogram/?date={date}", ("joqr.co.jp", "www.joqr.co.jp")),
    "LFR": StationSource("LFR", "https://www.1242.com/timetable/?target_date={date}", ("1242.com", "www.1242.com", "ssl.1242.com")),
    "FMT": StationSource("FMT", "https://www.tfm.co.jp/timetable/?date={date}", ("tfm.co.jp", "www.tfm.co.jp", "form.jfn.co.jp", "jfn.co.jp")),
    "FMJ": StationSource("FMJ", None, ("j-wave.co.jp", "www.j-wave.co.jp")),
    "JORF": StationSource("JORF", "https://www.jorf.co.jp/timetable.php?date={date}", ("jorf.co.jp", "www.jorf.co.jp")),
}

USER_AGENT = "radio-mail-thema/0.6 (+https://github.com/take-code-0427/radio-mail-thema)"

LISTENER_THEME_CONTEXT_RE = re.compile(
    r"(?:メッセージ|メール|お便り|投稿)(?:テーマ)?(?:.{0,45})(?:募集|募集中|送って|お送り|お寄せ|フォーム)"
    r"|(?:募集|募集中)(?:.{0,45})(?:メッセージ|メール|お便り|投稿)"
    r"|(?:メッセージ|メール|投稿)テーマ"
    r"|募集テーマ|募集中のメッセージテーマ",
    re.I | re.S,
)


def _base_program_name(value: str) -> str:
    return canonical_program_name(value)


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE)


def _official_url(station_id: str, url: str | None) -> bool:
    if not url or station_id not in STATION_SOURCES:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == d or host.endswith("." + d) for d in STATION_SOURCES[station_id].domains)


def _html_to_text(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def _looks_like_program_line(line_norm: str, program_norm: str) -> bool:
    if not line_norm or not program_norm:
        return False
    if not (program_norm in line_norm or line_norm in program_norm):
        return False
    ratio = min(len(line_norm), len(program_norm)) / max(len(line_norm), len(program_norm))
    # Do not treat a long paragraph that merely mentions the show as a title boundary.
    return ratio >= 0.72 and len(line_norm) <= max(len(program_norm) + 18, int(len(program_norm) * 1.5))


def _program_window(
    page_text: str,
    program_name: str,
    program_names: list[str] | tuple[str, ...] | None = None,
    radius: int = 2600,
) -> str | None:
    """Extract one program's timetable block without bleeding into neighbours."""
    base = _base_program_name(program_name)
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    target = _norm(base)
    if not target:
        return None

    best_idx = None
    best_score = 0.0
    for idx, line in enumerate(lines):
        nline = _norm(line)
        if not nline or not (target in nline or nline in target):
            continue
        score = min(len(target), len(nline)) / max(len(target), len(nline))
        # Prefer title-like short lines over prose that happens to mention the show.
        if len(nline) <= max(len(target) + 18, int(len(target) * 1.5)):
            score += 0.35
        if score > best_score:
            best_idx, best_score = idx, score
    if best_idx is None:
        return None

    end = min(len(lines), best_idx + 70)
    if program_names:
        other_names = {
            _norm(_base_program_name(name))
            for name in program_names
            if _norm(_base_program_name(name)) and _norm(_base_program_name(name)) != target
        }
        for idx in range(best_idx + 1, end):
            nline = _norm(lines[idx])
            if any(_looks_like_program_line(nline, other) for other in other_names):
                end = idx
                break
    else:
        # Backward-compatible fallback for isolated tests/callers.
        time_heading = re.compile(r"^\d{1,2}:\d{2}\s*[-–ー〜~]\s*\d{1,2}:\d{2}$")
        for idx in range(best_idx + 2, end):
            if time_heading.match(lines[idx]):
                end = idx
                break

    chunk = "\n".join(lines[best_idx:end])
    return chunk[:radius] if chunk else None


def _theme_has_listener_context(text: str, theme: str) -> bool:
    """Require listener-submission language near an extracted official-page theme."""
    if not theme:
        return False
    positions: list[int] = []
    start = 0
    while True:
        idx = text.find(theme, start)
        if idx < 0:
            break
        positions.append(idx)
        start = idx + max(1, len(theme))
    if not positions:
        return False
    for idx in positions:
        context = text[max(0, idx - 180) : min(len(text), idx + len(theme) + 240)]
        if LISTENER_THEME_CONTEXT_RE.search(context):
            return True
    return False


def _extract_message_link(raw_html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(raw_html, "html.parser")
    candidates: list[tuple[int, str]] = []
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").strip()
        text = a.get_text(" ", strip=True)
        if not href:
            continue
        if not href.startswith(("http://", "https://", "mailto:")):
            href = urljoin(page_url, href)
        haystack = f"{text} {href}".lower()
        if href.startswith("mailto:"):
            candidates.append((100, href))
        elif href.startswith("http"):
            score = 0
            if any(k in haystack for k in ("メッセージ", "投稿", "メール", "message", "form")):
                score += 50
            if any(k in haystack for k in ("contact", "entry", "request")):
                score += 15
            if score:
                candidates.append((score, href))
    if not candidates:
        email = re.search(r"(?<![\w.-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", soup.get_text(" ", strip=True), re.I)
        return f"mailto:{email.group(1)}" if email else None
    return max(candidates, key=lambda item: item[0])[1]


def _candidate_detail_links(station_id: str, raw_html: str, page_url: str, yyyymmdd: str) -> list[str]:
    soup = BeautifulSoup(raw_html, "html.parser")
    mmdd = yyyymmdd[4:]
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, str(a.get("href") or "").strip())
        if not _official_url(station_id, href) or href in seen or href == page_url:
            continue
        seen.add(href)
        text = a.get_text(" ", strip=True)
        haystack = f"{text} {href}".lower()
        score = 0
        if any(k in haystack for k in ("メッセージ", "メール", "お便り", "テーマ", "募集")):
            score += 50
        if any(k in haystack for k in ("information", "archive", "entry", "article", "blog", "theme")):
            score += 15
        if mmdd in re.sub(r"\D", "", haystack):
            score += 25
        # A generic blog/archive link alone is no longer enough to deep-crawl.
        if score >= 25:
            scored.append((score, href))
    scored.sort(reverse=True)
    return [url for _, url in scored[:3]]


def _fetch(session: requests.Session, url: str, timeout: float = 12.0) -> tuple[str, str] | None:
    try:
        response = session.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type and "text" not in content_type:
            return None
        return response.text, response.url
    except requests.RequestException as exc:
        print(f"[station-fetch] skip {url}: {exc}")
        return None


def _validated_official_theme(text: str, extract_theme: Callable[..., str | None]) -> str | None:
    theme = extract_theme(text)
    if not theme:
        return None
    return theme if _theme_has_listener_context(text, theme) else None


def enrich_rows(
    rows: list[dict],
    date_yyyymmdd: str,
    extract_theme: Callable[..., str | None],
    extract_message_url: Callable[[str], str | None],
) -> dict[str, int]:
    stats = {
        "daily_pages": 0,
        "program_pages": 0,
        "detail_pages": 0,
        "themes_added": 0,
        "message_urls_added": 0,
    }
    session = requests.Session()

    station_program_names: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        base = _base_program_name(row.get("program_name") or "")
        if base and base not in station_program_names[row["station_id"]]:
            station_program_names[row["station_id"]].append(base)

    daily_pages: dict[str, tuple[str, str]] = {}
    for station_id, source in STATION_SOURCES.items():
        url = source.url_for(date_yyyymmdd)
        if not url:
            continue
        fetched = _fetch(session, url)
        if fetched:
            raw, final_url = fetched
            daily_pages[station_id] = (_html_to_text(raw), final_url)
            stats["daily_pages"] += 1

    for row in rows:
        daily = daily_pages.get(row["station_id"])
        if not daily:
            continue
        page_text, page_url = daily
        window = _program_window(
            page_text,
            row["program_name"],
            station_program_names.get(row["station_id"]),
        )
        if not window:
            continue
        if not row.get("theme"):
            theme = _validated_official_theme(window, extract_theme)
            if theme:
                row["theme"] = theme
                row["theme_source_type"] = "official_daily"
                row["theme_source_url"] = page_url
                stats["themes_added"] += 1
        if not row.get("message_url"):
            message_url = extract_message_url(window)
            if message_url:
                row["message_url"] = message_url
                stats["message_urls_added"] += 1

    unique_urls: dict[str, list[dict]] = {}
    for row in rows:
        url = row.get("program_url")
        if (not row.get("theme") or not row.get("message_url")) and _official_url(row["station_id"], url):
            unique_urls.setdefault(url, []).append(row)

    limit = int(os.environ.get("STATION_PAGE_FETCH_LIMIT", "100"))
    urls = list(unique_urls)[:limit]
    fetched_pages: dict[str, tuple[str, str]] = {}
    workers = min(10, max(1, int(os.environ.get("STATION_FETCH_WORKERS", "8"))))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_fetch, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            result = future.result()
            if result:
                fetched_pages[url] = result
                stats["program_pages"] += 1

    detail_candidates: dict[str, list[dict]] = {}
    for url, associated_rows in unique_urls.items():
        fetched = fetched_pages.get(url)
        if not fetched:
            continue
        raw_html, final_url = fetched
        page_text = _html_to_text(raw_html)
        page_message_url = _extract_message_link(raw_html, final_url)
        for row in associated_rows:
            if not row.get("theme"):
                theme = _validated_official_theme(page_text, extract_theme)
                if theme:
                    row["theme"] = theme
                    row["theme_source_type"] = "official_program"
                    row["theme_source_url"] = final_url
                    stats["themes_added"] += 1
            if not row.get("message_url") and page_message_url:
                row["message_url"] = page_message_url
                stats["message_urls_added"] += 1
            if not row.get("theme"):
                for detail_url in _candidate_detail_links(row["station_id"], raw_html, final_url, date_yyyymmdd):
                    detail_candidates.setdefault(detail_url, []).append(row)

    deep_limit = int(os.environ.get("STATION_DEEP_FETCH_LIMIT", "40"))
    detail_urls = list(detail_candidates)[:deep_limit]
    detail_pages: dict[str, tuple[str, str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_fetch, session, url): url for url in detail_urls}
        for future in as_completed(futures):
            url = futures[future]
            result = future.result()
            if result:
                detail_pages[url] = result
                stats["detail_pages"] += 1

    for url, associated_rows in detail_candidates.items():
        fetched = detail_pages.get(url)
        if not fetched:
            continue
        raw_html, final_url = fetched
        text = _html_to_text(raw_html)
        theme = _validated_official_theme(text, extract_theme)
        message_url = _extract_message_link(raw_html, final_url)
        for row in associated_rows:
            if not row.get("theme") and theme:
                row["theme"] = theme
                row["theme_source_type"] = "official_detail"
                row["theme_source_url"] = final_url
                stats["themes_added"] += 1
            if not row.get("message_url") and message_url:
                row["message_url"] = message_url
                stats["message_urls_added"] += 1

    return stats
