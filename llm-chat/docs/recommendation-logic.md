# 楽曲推薦ロジック

うたレコ（llm-chat）が「ユーザーのテキスト → 歌える楽曲の提案」を行う処理の全体仕様。
実装は `main.py` / `clarification.py` / `song_search.py` / `user_profile.py`。

## 全体フロー（POST /chat 1往復）

```
ユーザーテキスト（例: 雨の日にしっとり泣ける曲）
  │
  ├─① セッション/履歴ロード        session_store(in-memory) + user_profile(SQLite永続)
  │
  ├─② LLM 1回呼び出し              clarification.analyze()
  │      = 曖昧判定 + 気分解析 + 候補生成 をまとめてJSON出力
  │
  ├─③ 分岐
  │    ├─ 曖昧 & 確認<2回 → 確認質問を返す（type=clarification）
  │    └─ 十分 or 確認2回到達 → 候補を楽曲DB照合へ
  │
  ├─④ 楽曲DB照合                   song_search.resolve_candidates()
  │      = DBに実在する曲だけを通す（DB外＝幻覚は除外）
  │
  ├─⑤ ユーザー嗜好を記録           user_profile.record_recommendations()
  │
  └─⑥ 応答返却（type=recommendation）
         { mood, message, songs:[{song_id,title,artist,score,confidence}] }
```

## ① セッションと履歴

- `session_id`（ブラウザlocalStorage）単位で会話履歴・確認回数を **in-memory** 保持（`session_store.py`、TTL 1時間、再起動で消える）。
- `user_id`（同じくlocalStorage）単位で会話と推薦実績を **SQLite に永続**（`user_profile.py` → `data/chat_users.db`）。
- 次回アクセス時、過去によく提案を受け入れた歌手を `profile_hint()` でシステムプロンプトに注入し、推薦のバイアスにする（固執はさせない）。

## ② LLM呼び出し（曖昧判定＋気分解析＋候補生成）

`clarification.analyze()` が **1回のLLM呼び出し**で以下をまとめて行い、JSONで受け取る。

```json
{
  "need_clarification": true/false,
  "question": "確認質問（曖昧なときだけ）",
  "mood": "会話から読み取った気分の要約",
  "candidates": [{"title": "曲名", "artist": "歌手名"}]
}
```

- 曖昧かどうかは **選択中のLLM自身**にシステムプロンプトで判断させる（ルールは `SYSTEM_PROMPT_TEMPLATE`）。
- 候補は実在曲・正式表記（「ヒゲダン」→「Official髭男dism」）・最大10件。
- JSON抽出は `parse_llm_json()`。前後に説明文が混ざっても最初の `{...}` を拾い、失敗時は安全側（確認質問扱い）に倒す。

## ③ 曖昧判定（確認は最大2回）

- `need_clarification=true` かつ確認回数 < 2 → 確認質問を返し、`clarification_count++`。
- 確認回数が上限(2)に達したら、LLMが確認質問を返してもサーバ側で **強制的に提案へ倒す**（`analyze()` 末尾）。
- 提案を返した時点で `clarification_count` は 0 にリセット（一連の意図が解決したとみなす）。

```
1回目: 曖昧 → 確認①
2回目: まだ曖昧 → 確認②
3回目: 強制的にベストエフォートで提案
```

## ④ 楽曲DB照合（最重要：DB外の曲は返さない）

LLMの候補は幻覚や表記ゆれを含みうるため、**必ず楽曲DB(songs.db)と照合してから返す**。
照合は `song_search.SongDB`（内部で song-search-poc の `matcher.py` を再利用）。

- 各候補を正規化（NFKC・小文字・記号/feat除去）し、
  1. 正規化後の完全一致（高速パス、score=100）
  2. 外れたら全曲ファジー照合：`曲名類似度×0.6 + 歌手名類似度×0.4`
- confidence: high(≥85) / medium(≥70) / low(<70)。
- `resolve_candidates()` は **high / medium のみ採用**し、同一曲(song_id)は最高スコアで重複排除、スコア降順で返す。
- → 返るのは必ず **DBに実在する行**。LLMが作った架空曲はここで落ちる。

> 保証: 応答に出る曲は 100% songs.db のレコード（song_id付き）。これがプロダクト要件
> 「楽曲DBにない楽曲は応答しない」の担保点。

## ⑤ ユーザー嗜好の記録

- 提示した曲を `liked_songs(user_id, song_id, title, artist, cnt, last_ts)` に加点（`record_recommendations()`）。
- `top_artists()` がよく受け入れた歌手を集計 → 次回 `profile_hint()` でプロンプトに反映。
- 会話自体も `messages` テーブルに永続（`add_message()`）。

## ⑥ 応答

- 推薦あり: `type=recommendation`、`mood` の要約 + 上位曲を文章化 + `songs[]`（song_id/title/artist/score/confidence）。
- 推薦が0件（DB内に該当なし）: 年代・ジャンル・アーティスト等の追加ヒントを促すメッセージ。
- 確認質問: `type=clarification`、`message` に質問・`clarification_count`。

## 楽曲DBアクセスの方針（API/MCP）

- バックエンドからDBへは `SongDB` 経由に一本化（in-process）。
- 同等機能を REST でも公開：`GET /songs/search`、`POST /songs/verify`。
- `POST /songs/verify` は MCPツール `verify_songs` と同一インターフェース。
  本番でMCPサーバ/外部楽曲APIに差し替える場合は、`SongDB.verify()` / `search()` を
  同じ入出力で実装し直すだけ（matcher側を呼ばず実バックエンドへ）。

## 推薦の妥当性検証（評価ハーネス）

`mood_eval.py` で「LLMの推薦が指示に忠実か」を測る。詳細は [mood-validation.md](mood-validation.md)。

- 年代一致率 / ジャンル一致率 … 自前DB列（release_year / genre、100%充足）を真値に客観採点。
- ムード一致率 … 外部真値が無いため別LLM(judge)による代理採点。
- LLM別（claude / gemini / gpt）に集計して比較。
- 注: Last.fmはJP曲のムード/年代タグをほぼ持たないため、年代・ジャンルの真値には使わない。

## 関連ファイル

| ファイル | 役割 |
|---|---|
| `main.py` | /chat パイプライン、各エンドポイント |
| `clarification.py` | 曖昧判定＋気分解析＋候補生成（②③） |
| `song_search.py` | 楽曲DB照合・DB外除外（④） |
| `user_profile.py` | ユーザー別 履歴・嗜好の永続化（①⑤） |
| `session_store.py` | セッション状態（in-memory） |
| `adapters/` | Claude / Gemini / OpenAI 共通インターフェース |
| `mood_eval.py` | 推薦妥当性の評価ハーネス |
