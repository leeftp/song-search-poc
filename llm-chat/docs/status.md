# 対応内容と課題

うたレコ（llm-chat）＋ song-search-poc 拡張の実装サマリと、残課題の記録。

## 対応内容（実装済み）

### 評価キット（song-search-poc 本体）
- `run_eval.py`: LLMプロバイダ抽象化（`--provider claude|gemini`、`--model` 上書き、プロバイダ別コスト算出）。
- `scripts/build_db.py`: 照合用CSV → SQLite(`data/songs.db`、正規化済み列＋索引)。
- `matcher.py`: SQLite/CSV どちらからもロード可能に（`.db` 自動判定、後方互換）。

### llm-chat アプリ（新規・ブラウザ楽曲レコメンドチャット）
- バックエンド(FastAPI): `/chat`・`/config/keys`(GET/POST, マスク表示)・`/songs/search`・`/songs/verify`(MCP同一IF)・`/users/{id}/profile`・`/health`。
- LLMアダプタ3種（Claude / Gemini / OpenAI）を共通IF（`chat`/`chat_stream`）で切替。
- 曖昧判定＋気分解析＋候補生成を1回のLLM呼び出しでJSON取得（`clarification.py`）。確認は最大2回、3回目は強制提案。
- 楽曲DB照合（`song_search.py`）: LLM候補を `songs.db` と照合し、**DBに実在する曲(high/medium)だけ返す**＝幻覚除外。
- ユーザー別履歴・嗜好を SQLite に永続化（`user_profile.py`）。次回推薦のヒントに利用。
- フロント(`static/index.html`, Vanilla JS + Tailwind CDN): モデルセレクタ、チャットUI(スマホ対応)、APIキー設定パネル、音声入力、各曲に YouTube/Spotify 検索リンク、曲カードの余白調整。

### 音声入力
- ブラウザの Web Speech API（`webkitSpeechRecognition`, ja-JP）。
- `continuous=true`＋手動停止まで自動再開で「途中で切れる」を解消。確定テキストは保持。

### HTTPS / LAN公開
- 自己署名証明書(`certs/`, SANにLAN IP)でHTTPS起動。`run.sh` が証明書を検知して自動でHTTPS化。
- 別PCからLANアクセス可能（音声入力のセキュアコンテキスト要件を満たすため）。

### ムード評価ハーネス
- `mood_eval.py`＋`mood_queries.json`＋`mood_taxonomy.py`。
- 年代・ジャンルは**自前DB列(release_year/genre, 100%充足)を真値に客観採点**。ムードは外部真値が無いため**LLM-judgeで代理採点**。LLM別(claude/gemini/gpt)比較。
- `--selftest` で採点ロジックを鍵なし検証（PASS済み）。

### 調査で確定した方針転換
- Last.fmは**JP曲の「曲単位」タグがほぼ空**（Lemonですら0件）。アーティスト単位もジャンル＋国籍中心でムード/年代は薄い。
- → 年代・ジャンルの真値は**自前DB列**を採用。Last.fmはジャンル横断チェックの補助に留める。

### ドキュメント
- `docs/recommendation-logic.md` / `sequence-diagram.md`(Mermaid) / `mood-validation.md` / 本書。

## 課題（未対応・既知の制約）

### セッション/ユーザー管理
- **マルチワーカー非対応**: session はプロセス内メモリのシングルトン。`--workers 2+` で共有されない → 本番は Redis等の外部ストアへ。
- **認証なし**: `user_id` は localStorage の乱数のみ。別ブラウザ/PC・localStorage削除で別ユーザー扱い。個人特定にはログイン認証が必要。
- **会話履歴が無制限**: in-memory の messages が伸び続ける → トークン上限・コスト対策に直近N件トリム等が必要。

### 音声
- Web Speech API は**契約/SLAのないブラウザ機能**。Chromeは音声をGoogleに送信（オンデバイスではない）。過去にレート制限例あり → 本番は Google Cloud STT 等の正規サービスへ。
- **ブラウザ差**（Firefoxは実質未対応）。LANではHTTPS(セキュアコンテキスト)必須。
- **ハミング(Query by Humming)非対応**。音声認識(言葉→テキスト)とは別技術で、かつ `songs.db` にメロディ/音程データが無い。対応するなら ACRCloud / Houndify 等の外部QbHサービス＋メロディDBが前提。

### 推薦・評価
- **ムードの外部真値が無い**（JP）。LLM-judgeは代理指標で、judge自身のバイアスを含む。Spotify valence は Audio Features API の制限で取得可否が不確実。
- **場所・状況**は客観検証の真値が無く、ムード(judge)に内包。独立軸化するには各曲への自前LLMタグ付けが必要。
- `mood_eval.py` のライブ実行は**APIキー必須**（現状オフラインの selftest のみ検証済み）。

### その他
- モデル名 `gpt-5.4-mini` / `gemini-3.1-flash-lite` は指定どおり。実行には有効なAPIキーが必要。
- `/chat` は**非ストリーミング**（候補→DB照合の後処理が必要なため構造化JSON応答）。アダプタに `chat_stream` は残置。
- 曲リンクは**検索URL方式**（実トラックIDではない）。曲No(song_id)はモバイルで非表示（リンク優先）。
- ローカル実行環境が **Python 3.9.6**（仕様は3.10+）。`song_search.py` に `from __future__ import annotations` で回避。
- 証明書は自己署名のため、ブラウザ初回に警告（手動許可が必要）。

## 次にやるなら（優先候補）
1. セッションの外部ストア化（Redis）＋会話履歴トリム
2. 本番STT（Cloud STT）への移行とブラウザ差の吸収
3. mood_eval のライブ実行で3モデル比較（要キー）
4. （希望次第）ハミング検索の別モードPoC（ACRCloud）
