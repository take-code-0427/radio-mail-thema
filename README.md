# radio-mail-thema

今日のラジオ番組のメールテーマを、画面いっぱいの一覧で探すためのサービスです。

## 収集対象

東京の主要6局を対象にしています。

- TBSラジオ
- 文化放送
- ニッポン放送
- TOKYO FM
- J-WAVE
- ラジオ日本

## 収集パイプライン

1. radiko の東京エリア番組表XMLをベースデータとして取得
2. radiko の番組説明からメールテーマ・投稿先を抽出
3. 局ごとの公式番組表で不足情報を補完
4. radiko が持つ各番組の公式URLを並列取得して補完
5. テーマがまだ見つからない番組だけ、公式サイト内の INFORMATION / ARCHIVE / 記事ページを1段だけ追跡
6. Neon Postgres に upsert
7. Vercel `/api/themes` からフロントへ配信

収集の正本は GitHub Actions です。毎日 05:10 JST に実行します。

## 局別fetcher

`scripts/station_fetchers.py` に局ごとの公式ソースと許可ドメインを定義しています。

| 局 | 主な公式ソース |
| --- | --- |
| TBSラジオ | 週間番組表 + 各番組公式ページ + 記事 |
| 文化放送 | 今日の番組表 + 各番組公式ページ |
| ニッポン放送 | 日付指定番組表 + 各番組公式ページ |
| TOKYO FM | 日付指定番組表 + 各番組公式ページ |
| J-WAVE | 各番組の INFORMATION / ARCHIVE を中心に取得 |
| ラジオ日本 | 日付指定番組表 + 各番組公式ページ |

公式サイト由来で補完したテーマは `theme_source_type` / `theme_source_url` をDBに保存するため、誤抽出を後から追跡できます。

## 抽出方針

誤抽出を減らすため、以下を優先しています。

- 「今日のメッセージテーマ」「メールテーマ」「本日の議題」など明示的な募集表現
- 単なる「テーマ」は、周辺に「メッセージ」「募集」「フォーム」等の投稿文脈がある場合のみ採用
- 「選曲テーマ」「〜をテーマに紹介」など番組内容の説明は除外
- radikoで既に抽出できたテーマは、公式サイト側で上書きしない

## 実行

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
DATABASE_URL='postgresql://...' python scripts/collect.py
```

特定日を収集する場合:

```bash
DATABASE_URL='postgresql://...' python scripts/collect.py 20260820
```

## 環境変数

- `DATABASE_URL`: Neon Postgres接続文字列
- `STATION_PAGE_FETCH_LIMIT`: 番組公式ページの最大取得数。既定100
- `STATION_DEEP_FETCH_LIMIT`: 公式記事等を1段深掘りする最大取得数。既定40
- `STATION_FETCH_WORKERS`: 並列HTTP取得数。既定8、最大10

## フロント

`index.html` が `/api/themes` を取得して、局・時間・番組名・テーマ・投稿先を一覧表示します。
