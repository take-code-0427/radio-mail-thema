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

    def test_official_domain_allowlist(self):
        self.assertTrue(_official_url("FMT", "https://www.tfm.co.jp/bo/"))
        self.assertTrue(_official_url("LFR", "https://ssl.1242.com/aplform/form/aplform.php?fcode=ij"))
        self.assertFalse(_official_url("FMT", "https://example.com/fake-tfm"))
        self.assertFalse(_official_url("TBS", "https://radiko.jp/"))


if __name__ == "__main__":
    unittest.main()
