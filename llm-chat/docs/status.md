# 対応内容と課題

うたレコ（llm-chat）＋ song-search-poc 拡張の実装サマリと、残課題の記録。

---

## 2026-06-26 実施内容

### プロンプト・推薦ロジック改善

#### request_type 判定（direct / mood）
- `clarification.py` システムプロンプトを改修。入力を "direct"（曲名・歌手名の直接言及）と "mood"（気分・シーン探索）に分類。
- direct の場合: 気分推測を省き、言及された曲を最優先候補として返す。
- LLM の JSON 出力スキーマに `request_type` フィールドを追加（欠落時は "mood" にフォールバック）。
- direct の返信文を温和に改善: 1曲ヒット → `「○○」（△△）ですね！いい曲です、ぜひ楽しんでください♪` / 複数ヒット → `ご希望の曲が見つかりました！…どれも素敵ですよ♪`

#### 楽曲返却数を5件に統一
- `main.py` で `resolve_candidates()[:5]`（`RECOMMEND_COUNT = 5`）に統一。

#### 次の5曲（繰り返し回避）
- プロンプトに「直前の提案曲をユーザーが選ばずに会話を続けている場合は同じ曲を繰り返さない」ルールを追加。会話履歴をもとに LLM が自動判断。

#### 0件時フォールバック（暫定ヒットチャート）
- `song_search.py` に `FALLBACK_HIT_CHART`（DB実在確認済み10曲、順位付き）と `weekly_hit_chart()` を追加。
- DB 照合で0件の場合、「今週のヒット曲ランキング」として1〜5位を表示。
- 本番では実チャート API への差し替えを想定（コメント明記）。

#### デフォルトモデルを Gemini に変更
- `main.py` の `DEFAULT_MODEL` と `index.html` の `<select>` の `selected` 属性を `gemini-3.1-flash-lite` に変更。

---

### デモ用画面の大幅改修（index.html）

#### 状況フィルター（チャット枠外）
- ヘッダー下に気分 / 感情 / 地域 / 人数 / 年代 の5つのセレクトボックスを追加。
- 選択値は `/chat` リクエストに同送（`mood_tag` / `emotion` / `region` / `group_size` / `age_group`）。
- `main.py` でビット文字列 `context_hint` を組み立て、`clarification.py` のシステムプロンプトに注入。LLM が曖昧さの判断材料として活用（確認質問のスキップ基準にも反映）。
- フィルター選択時にボーダーがインディゴ色に変わり「アクティブ」を視覚表示。送信時にユーザーバブルへフィルタータグを表示（適用を明示）。

#### 2カラムレイアウト（PC幅）
- 左列: チャット（ヘッダー＋フィルター＋会話＋入力欄）。
- 右列: **楽曲リンクパネル**（YouTube / Spotify リンクのみ表示）。モバイルはチャット下に折り返し。

#### 楽曲カード（チャット内）でタップ選択
- 楽曲カードはチャットバブル内に表示（YouTube/Spotify リンクは右パネルへ分離）。
- カード全体をタップ = 歌唱履歴に記録。緑色ハイライト＋「✓」で選択済みを表示。
- チャットに `「○○」を再生します。` メッセージを追加表示。

#### 音声入力の自動送信
- `continuous: true`（手動停止まで聞き続ける）→ `continuous: false`（発話後の無音で自動終了）に変更。
- `rec.onend` で `finalText` が存在する場合、自動的に `send()` を呼び出してサーバに送信。手動停止（再度マイクボタン押下）の場合は送信しない。

#### ブラウザキャッシュ対策
- `/` エンドポイントの `FileResponse` に `Cache-Control: no-store` を追加。Windows Chrome など LAN 接続端末での旧 JS キャッシュ問題を解消。

---

### 歌唱履歴機能（DB）

#### sing_history テーブル
- `user_profile.py` の DDL に `sing_history`（id / user_id / song_id / title / artist / genre / ts）と索引を追加。
- `liked_songs`（推薦提示の全曲を加点）とは分離し、**ユーザーが明示的に選択した曲だけ**を記録する強いシグナルとして設計。

#### POST /songs/select エンドポイント
- `main.py` に追加。フロントがカードタップ時に呼び出し、`profile_store.record_sing()` を経由して `sing_history` に INSERT。

#### 歌唱履歴の要約と推薦への反映
- `singing_summary()` を追加: `sing_history` から好みジャンル・歌手を集計し短い日本語テキストを生成。
- `profile_hint()` を拡張: 既存の `top_artists`（提示曲ベース）に `singing_summary`（選択曲ベース）を追記し、次回推薦のシステムプロンプトに反映。

---

### その他改修

- `matcher.py` `_fmt()`: 返却 dict に `genre` / `release_year` を追加（`sing_history` 保存・将来のジャンル絞り込みに利用）。
- 楽曲カード間の余白を `space-y-0` に詰めて一覧の視認性を向上。

---

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
