# シーケンス図（クライアント ⇄ バックエンド）

うたレコの通信フロー。すべて REST（HTTP/JSON, `fetch()`）。Mermaid記法なので
GitHub・対応エディタでは図として描画される。

## 1. 楽曲推薦（POST /chat）

```mermaid
sequenceDiagram
    autonumber
    actor U as ユーザー
    participant B as ブラウザ<br/>(index.html)
    participant API as FastAPI<br/>(main.py)
    participant CL as clarification<br/>(analyze)
    participant LLM as LLMプロバイダ<br/>(Claude/Gemini/OpenAI)
    participant DB as SongDB<br/>(songs.db)
    participant UP as UserProfile<br/>(chat_users.db)

    U->>B: 気分を入力（例: 雨の日にしっとり泣ける曲）
    B->>API: POST /chat {message, model, session_id, user_id}
    API->>UP: 履歴追加 + profile_hint(user_id)
    UP-->>API: 過去の好み（よく受けた歌手）
    API->>CL: analyze(messages, count, hint)
    CL->>LLM: chat(system+messages)  ※曖昧判定+気分+候補を1回で
    LLM-->>CL: JSON {need_clarification, question, mood, candidates}
    CL-->>API: 解析結果

    alt 曖昧 かつ 確認 < 2回
        API->>UP: 確認質問を履歴に保存
        API-->>B: {type:"clarification", message:質問, clarification_count}
        B-->>U: 確認質問を表示（確認 n/2）
    else 十分 or 確認2回到達（強制提案）
        API->>DB: resolve_candidates(candidates)
        Note over DB: 正規化→完全一致/ファジー照合<br/>high・mediumのみ採用＝DB外は除外
        DB-->>API: DB実在曲のみ [{song_id,title,artist,score,confidence}]
        API->>UP: record_recommendations(user_id, songs)
        API-->>B: {type:"recommendation", mood, message, songs[]}
        B-->>U: 気分の要約＋曲カード（song_id/曲名/歌手/一致度）
    end
```

## 2. APIキー設定（POST/GET /config/keys）

```mermaid
sequenceDiagram
    autonumber
    actor U as ユーザー
    participant B as ブラウザ<br/>(設定パネル)
    participant API as FastAPI

    U->>B: ⚙ を開く
    B->>API: GET /config/keys
    API-->>B: {openai:{set,masked}, gemini:{...}, anthropic:{...}}
    Note right of B: キー本体は返さない（マスク表示のみ）
    U->>B: キー入力して保存
    B->>API: POST /config/keys {anthropic?, openai?, gemini?}
    API->>API: メモリ更新 + .env へ保存
    API-->>B: {updated, message}
    B->>API: GET /config/keys（状態再取得）
    API-->>B: set=true / masked
```

## 3. 音声入力（クライアント完結、サーバは音声を受け取らない）

```mermaid
sequenceDiagram
    autonumber
    actor U as ユーザー
    participant B as ブラウザ<br/>(Web Speech API)
    participant V as 音声認識クラウド<br/>(Google/MS/Apple)
    participant API as FastAPI

    U->>B: 🎤 を押して発話
    B->>V: 音声ストリーム（ブラウザ実装が送信）
    V-->>B: 変換テキスト
    B->>B: 入力欄に反映
    Note over B,API: 音声はFastAPIに届かない。<br/>以降は通常の POST /chat（テキスト）
    U->>B: 送信
    B->>API: POST /chat {message: 変換テキスト, ...}
```

## 補足

- LLM呼び出しは **バックエンドからサーバ間**で行う。APIキーはサーバ保管で、ブラウザもLLMベンダーも保持しない。
- `POST /songs/verify`（MCP `verify_songs` と同一IF）等のDB系APIは内部/将来連携用で、現フロントは未使用。
- 詳細な処理仕様は [recommendation-logic.md](recommendation-logic.md) を参照。
