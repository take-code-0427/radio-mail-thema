import unittest

from scripts.station_fetchers import _official_url, _program_window


class StationFetcherTest(unittest.TestCase):
    def test_program_window_strips_radiko_segment_suffix(self):
        page = """
        09:00 - 11:00
        SWEET!!
        グルメな木曜日！今日のメッセージテーマは「ひき肉」
        番組へのメッセージはこちら
        11:00 - 12:00
        次の番組
        """
        window = _program_window(page, "SWEET!! (2)")
        self.assertIsNotNone(window)
        self.assertIn("ひき肉", window)

    def test_program_window_strips_part_suffix(self):
        page = "飯田浩司のOK! Cozy up!\nニュース番組\nメールはこちら"
        window = _program_window(page, "飯田浩司のOK! Cozy up! Part2")
        self.assertIsNotNone(window)

    def test_program_window_stops_at_next_known_program(self):
        page = """
        04:00-06:00
        Memories＆Discoveries
        今日の選曲をお届けします。
        メッセージフォームはこちら
        06:00-09:00
        ONE MORNING
        今日のメッセージテーマは「私のドーパミンライフ」
        メッセージ募集中です。
        """
        window = _program_window(
            page,
            "Memories＆Discoveries",
            ["Memories＆Discoveries", "ONE MORNING"],
        )
        self.assertIsNotNone(window)
        self.assertNotIn("私のドーパミンライフ", window)
        self.assertNotIn("ONE MORNING", window)

    def test_program_window_handles_minute_range_title(self):
        page = """
        大竹まこと ゴールデンラジオ！
        番組へのメールはこちら
        次の番組
        """
        window = _program_window(page, "大竹まこと ゴールデンラジオ！ 11時30分～13時")
        self.assertIsNotNone(window)

    def test_official_domain_allowlist(self):
        self.assertTrue(_official_url("FMT", "https://www.tfm.co.jp/bo/"))
        self.assertTrue(_official_url("LFR", "https://ssl.1242.com/aplform/form/aplform.php?fcode=ij"))
        self.assertFalse(_official_url("FMT", "https://example.com/fake-tfm"))
        self.assertFalse(_official_url("TBS", "https://radiko.jp/"))


if __name__ == "__main__":
    unittest.main()
