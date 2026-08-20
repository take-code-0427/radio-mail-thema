import unittest

from scripts.collect import extract_theme


class ExtractThemeTest(unittest.TestCase):
    def test_explicit_message_theme(self):
        text = '今日のメッセージテーマは「タクシーのはなし」♪\nメッセージフォームからお送りください。'
        self.assertEqual(extract_theme(text), 'タクシーのはなし')

    def test_tfm_recruiting_message_theme(self):
        text = '【募集中のメッセージテーマ】\n＜あの夏がくれたもの＞\nメッセージは番組のメッセージフォームから！'
        self.assertEqual(extract_theme(text), 'あの夏がくれたもの')

    def test_tane_format(self):
        text = '◇今日のメールテーマ＝「タネ」は【うちだけ語】\nメールアドレス：ij@1242.com'
        self.assertEqual(extract_theme(text), 'うちだけ語')

    def test_generic_theme_requires_submission_context(self):
        text = '今週のテーマは『イライラするのは夏のせい』\nあなたの今の怒りをメッセージで募集中！'
        self.assertEqual(extract_theme(text), 'イライラするのは夏のせい')

    def test_generic_theme_normalizes_category_prefix(self):
        text = '木曜日のテーマは、モノ、「人生をともにした宝物」です。\n番組ホームページのメッセージ・フォームからお送りください。'
        self.assertEqual(extract_theme(text), '人生をともにした宝物')

    def test_skyrocket_prefers_main_agenda(self):
        text = (
            '本日の議題は、いよいよ番組イベント直前の放送ということで\n'
            '【 ダンスダンスダンス案件〜心踊る瞬間 】\n'
            'メッセージは番組HPからお送りください。\n'
            '毎週木曜のテーマは【スコーン！とハマるアンサーソング！】'
        )
        self.assertEqual(extract_theme(text), 'ダンスダンスダンス案件〜心踊る瞬間')

    def test_selection_theme_is_not_mail_theme(self):
        text = 'TRAD Select Mission\n選曲テーマは日本の夏ソング\n番組Webサイトはこちら。\nメッセージフォーム：https://example.com/form'
        self.assertIsNone(extract_theme(text))

    def test_editorial_theme_without_submission_is_ignored(self):
        text = '今日のテーマは「小田切ヒロのお気に入りの曲」\nイベントについて詳しく紹介します。'
        self.assertIsNone(extract_theme(text))

    def test_theme_in_explanation_is_ignored(self):
        text = '「今のJAZZは面白い」をテーマにジャズのスタンダードナンバーを紹介します。'
        self.assertIsNone(extract_theme(text))

    def test_distant_mail_form_does_not_validate_editorial_theme(self):
        text = (
            '今日のテーマは「ひかりさんと検察腐敗」。ニュースを解説します。'
            + '番組内容の説明です。' * 35
            + '番組メールフォーム：https://example.com/form'
        )
        self.assertIsNone(extract_theme(text))

    def test_ogiri_prompt_is_not_a_mail_theme(self):
        text = (
            '●「爆裂！M!LK大喜利！」 M!LKに関する大喜利お題を出題！'
            '皆さんからの回答をお待ちしています！ 回答の件名は【大喜利】でお願いします！ '
            'また、大喜利のお題も募集しています！ メールは reco@joqr.net まで！'
        )
        self.assertIsNone(extract_theme(text))

    def test_explicit_odai_label_is_supported(self):
        text = '今日のお題は「夏休みにやりたいこと」\nメールで募集中です。'
        self.assertEqual(extract_theme(text), '夏休みにやりたいこと')


if __name__ == '__main__':
    unittest.main()
