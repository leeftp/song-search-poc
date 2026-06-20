# ムード推薦の妥当性検証（設計メモ・未実装）

LLMが「気分クエリ」から選んだ楽曲が、世の中で分析済みのムードデータと整合するかを
後で測れるようにするための設計メモ。実装は保留。データソースは下記から選んで差し替える。

## 評価の流れ（共通）

1. 気分クエリ（例:「雨の日にしっとり泣ける曲」）→ アプリの推薦曲リストを取得
   （`POST /chat` の `songs[]`、または song-search-poc の候補生成）
2. 推薦された各曲を **外部ムードソース**へ `title + artist` で名寄せ検索
3. 取得したムード指標が、リクエストした気分カテゴリと一致するか採点
4. クエリ集合（test_queries.json のムード/シーン系）で「ムード一致率」を集計

```
気分クエリ ──LLM──▶ 推薦曲 ──名寄せ──▶ 外部ムード指標 ──▶ 一致判定 ──▶ ムード一致率
```

最大の難所は **名寄せ精度**。本DBは邦楽カラオケ（iTunes由来、外部ID無し）のため、
title+artist のテキスト検索でしか外部ソースに当てられない。表記ゆれは matcher.normalize と
同じ正規化をかけてから照合するのが無難。

## データソースの選択肢

### 選択肢A: Last.fm track top tags（推奨・低コスト）
- 取得: `track.getTopTags`（無料APIキー）で曲ごとの群衆タグを取得
- ムード解釈: タグに含まれる `sad / melancholic / chill / happy / energetic / romantic` 等を
  気分カテゴリへマッピング
- 長所: 無料、邦楽の有名曲もカバー良好、セットアップが軽い
- 短所: タグは定性的（数値でない）、マイナー曲はタグが薄い
- 鍵: `LASTFM_API_KEY`

### 選択肢B: Spotify Audio Features（定量・要確認）
- 取得: トラック検索 → `audio-features` で `valence`（陽性度0–1）・`energy`・`tempo` 等
- ムード解釈: valence×energy 平面で4象限（しっとり/明るい/激しい/穏やか）に分類して照合
- 長所: 数値で定量比較でき、しきい値評価がしやすい
- 短所: **2024年11月に audio-features 等が新規/一般アプリ向けに制限**されたため、
  利用可否はアカウント権限に依存（要確認）
- 鍵: `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`

### 参考（未採用）
- AcousticBrainz: オープンなmood分類器ダンプ。収集は2022停止、邦カラオケ曲は穴あり。
- Deezer / Gracenote / Musixmatch: 商用ムードメタデータ。

## 実装時のイメージ

`song-search-poc/run_eval.py` のムード版として、`mood_eval.py` を追加する想定:
- `MoodSource` インターフェース（`mood_of(title, artist) -> dict|tags`）を切り口に
  LastfmSource / SpotifySource を差し替え可能にする（adapters/ と同じ発想）
- 出力: カテゴリ別ムード一致率 ＋ 名寄せ成功率（名寄せできた曲の割合も併記する）

## 決める必要があること（着手時）
- どのソースを使うか（A or B、両方並べて比較も可）
- 気分カテゴリ → 外部タグ/数値しきい値 のマッピング定義
- 「一致」とみなす基準（タグ部分一致 / valence帯の重なり 等）
