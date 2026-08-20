import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.program_merge import canonical_program_name, merge_split_programs


TOKYO = ZoneInfo("Asia/Tokyo")


def row(name, start, end, *, theme=None, source=None, station_id="TBS"):
    return {
        "broadcast_date": datetime(2026, 8, 20, tzinfo=TOKYO).date(),
        "station_id": station_id,
        "station_name": station_id,
        "program_name": name,
        "start_at": datetime.fromisoformat(f"2026-08-20T{start}:00+09:00"),
        "end_at": datetime.fromisoformat(f"2026-08-20T{end}:00+09:00"),
        "theme": theme,
        "description": name,
        "program_url": None,
        "message_url": None,
        "source_url": "https://radiko.jp/example.xml",
        "theme_source_type": source,
        "theme_source_url": "https://example.com/theme" if theme else None,
    }


class ProgramMergeTest(unittest.TestCase):
    def test_canonicalizes_numeric_suffix(self):
        self.assertEqual(canonical_program_name("SWEET!! (3)"), "SWEET!!")

    def test_canonicalizes_part_suffix(self):
        self.assertEqual(canonical_program_name("飯田浩司のOK! Cozy up! Part2"), "飯田浩司のOK! Cozy up!")

    def test_canonicalizes_hour_range_suffix(self):
        self.assertEqual(canonical_program_name("武田砂鉄 ラジオマガジン 10時～11時"), "武田砂鉄 ラジオマガジン")

    def test_merges_contiguous_fragments(self):
        rows = [
            row("SWEET!! (1)", "09:00", "10:00", theme="ひき肉", source="radiko", station_id="JORF"),
            row("SWEET!! (2)", "10:00", "11:00", theme="ひき肉", source="radiko", station_id="JORF"),
            row("SWEET!! (3)", "11:00", "11:30", theme="ひき肉", source="radiko", station_id="JORF"),
        ]
        merged = merge_split_programs(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["program_name"], "SWEET!!")
        self.assertEqual(merged[0]["start_at"].strftime("%H:%M"), "09:00")
        self.assertEqual(merged[0]["end_at"].strftime("%H:%M"), "11:30")
        self.assertEqual(merged[0]["segment_count"], 3)

    def test_does_not_merge_same_show_far_apart(self):
        rows = [
            row("番組 (1)", "09:00", "10:00"),
            row("番組 (2)", "20:00", "21:00"),
        ]
        merged = merge_split_programs(rows)
        self.assertEqual(len(merged), 2)

    def test_prefers_official_theme_over_radiko(self):
        rows = [
            row("番組 (1)", "09:00", "10:00", theme="radikoテーマ", source="radiko"),
            row("番組 (2)", "10:00", "11:00", theme="公式テーマ", source="official_program"),
        ]
        merged = merge_split_programs(rows)
        self.assertEqual(merged[0]["theme"], "公式テーマ")
        self.assertEqual(merged[0]["theme_source_type"], "official_program")


if __name__ == "__main__":
    unittest.main()
