from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta


SOURCE_PRIORITY = {
    None: 0,
    "radiko": 10,
    "official_daily": 20,
    "official_program": 30,
    "official_detail": 40,
}


def canonical_program_name(value: str) -> str:
    """Normalize radiko schedule fragments to their parent program name."""
    name = (value or "").strip()
    previous = None
    while name != previous:
        previous = name
        name = re.sub(r"\s*[（(]\s*\d+\s*[）)]\s*$", "", name)
        name = re.sub(r"\s+Part\s*\d+\s*$", "", name, flags=re.I)
        # Clock-style ranges: 8:00～9:00, 08:00-09:00.
        name = re.sub(
            r"\s+\d{1,2}[：:]\d{2}\s*[～〜~\-]\s*\d{1,2}[：:]\d{2}\s*$",
            "",
            name,
        )
        # Japanese ranges: 8時～9時, 11時30分～13時, 17時13分～17時35分.
        name = re.sub(
            r"\s+\d{1,2}時(?:\d{1,2}分)?\s*[～〜~\-]\s*\d{1,2}時(?:\d{1,2}分)?\s*$",
            "",
            name,
        )
    return name.strip() or (value or "").strip()


def _source_priority(row: dict) -> int:
    return SOURCE_PRIORITY.get(row.get("theme_source_type"), 0)


def _best_theme(rows: list[dict]) -> dict | None:
    candidates = [row for row in rows if row.get("theme")]
    if not candidates:
        return None
    candidates.sort(key=lambda row: (-_source_priority(row), row["start_at"]))
    return candidates[0]


def _message_url_score(value: str | None) -> int:
    if not value:
        return 0
    lower = value.lower()
    if lower.startswith("https://") and any(word in lower for word in ("form", "message", "entry", "contact")):
        return 30
    if lower.startswith("https://"):
        return 20
    if lower.startswith("mailto:"):
        return 10
    return 1


def _best_message_url(rows: list[dict]) -> str | None:
    candidates = [row.get("message_url") for row in rows if row.get("message_url")]
    if not candidates:
        return None
    return max(candidates, key=_message_url_score)


def _first_nonempty(rows: list[dict], key: str):
    for row in rows:
        if row.get(key):
            return row[key]
    return None


def _merge_descriptions(rows: list[dict], limit: int = 5000) -> str | None:
    seen: set[str] = set()
    parts: list[str] = []
    for row in rows:
        value = (row.get("description") or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        parts.append(value)
    merged = "\n".join(parts).strip()
    return merged[:limit] if merged else None


def _merge_cluster(rows: list[dict], canonical_name: str) -> dict:
    rows = sorted(rows, key=lambda row: row["start_at"])
    base = dict(rows[0])
    # Only rename when multiple consecutive fragments prove this is a split show.
    base["program_name"] = canonical_name if len(rows) > 1 else rows[0]["program_name"]
    base["start_at"] = min(row["start_at"] for row in rows)
    base["end_at"] = max(row["end_at"] for row in rows)
    base["description"] = _merge_descriptions(rows)
    base["program_url"] = _first_nonempty(rows, "program_url")
    base["message_url"] = _best_message_url(rows)
    base["source_url"] = _first_nonempty(rows, "source_url") or base.get("source_url")

    theme_row = _best_theme(rows)
    if theme_row:
        base["theme"] = theme_row.get("theme")
        base["theme_source_type"] = theme_row.get("theme_source_type")
        base["theme_source_url"] = theme_row.get("theme_source_url")
    else:
        base["theme"] = None
        base["theme_source_type"] = None
        base["theme_source_url"] = None

    base["segment_count"] = len(rows)
    return base


def merge_split_programs(rows: list[dict], max_gap_minutes: int = 15) -> list[dict]:
    """Merge consecutive radiko fragments of the same logical program.

    Repeated programs later in the day are kept separate. Only fragments with the
    same station and canonical name whose time ranges overlap or are separated by
    at most ``max_gap_minutes`` are merged.
    """
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        canonical = canonical_program_name(row.get("program_name") or "")
        grouped[(row.get("station_id") or "", canonical)].append(row)

    merged: list[dict] = []
    max_gap = timedelta(minutes=max_gap_minutes)
    for (_station_id, canonical), group in grouped.items():
        group.sort(key=lambda row: row["start_at"])
        cluster: list[dict] = []
        cluster_end = None
        for row in group:
            if not cluster:
                cluster = [row]
                cluster_end = row["end_at"]
                continue

            if row["start_at"] <= cluster_end + max_gap:
                cluster.append(row)
                cluster_end = max(cluster_end, row["end_at"])
            else:
                merged.append(_merge_cluster(cluster, canonical))
                cluster = [row]
                cluster_end = row["end_at"]

        if cluster:
            merged.append(_merge_cluster(cluster, canonical))

    merged.sort(key=lambda row: (row["start_at"], row.get("station_name") or ""))
    return merged
