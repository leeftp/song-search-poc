#!/usr/bin/env python3
"""楽曲検索PoC 評価ハーネス

フロー: テストクエリ → LLM候補生成(Claude/Gemini) → ローカル照合(matcher.py) → 指標集計

実行方法:
  pip install anthropic google-genai rapidfuzz
  python3 scripts/build_db.py                  # 先にCSV→SQLite(data/songs.db)を生成

  export ANTHROPIC_API_KEY=sk-ant-...
  python3 run_eval.py                           # Claude(デフォルト) 全40問
  python3 run_eval.py --limit 5                 # 動作確認用に5問だけ

  export GEMINI_API_KEY=...                      # GOOGLE_API_KEY でも可
  python3 run_eval.py --provider gemini          # Geminiで実行
  python3 run_eval.py --provider gemini --no-web # 内蔵web検索なしで比較測定

指標:
  - 照合ヒット率   : LLM候補のうちDBにhigh confidenceで照合できた割合
  - 正解ヒット率   : expected_pairsありのクエリで、正解が最終候補(high)に含まれた割合
  - 取りこぼし検知 : in_db=falseのクエリで、正解が正しく「DBになし」扱いになったか
  - レイテンシ     : LLM呼び出し時間（照合時間は別掲）
  - コスト         : input/outputトークンから概算（プロバイダ別の単価で算出）
"""
import argparse
import json
import os
import time
import unicodedata
import re

from matcher import SongMatcher, normalize

# プロバイダ別設定。priceは$/Mトークン（最新価格は実行時に各社の料金ページで要確認）
PROVIDERS = {
    "claude": {"model": "claude-sonnet-4-6", "price_in": 3.0, "price_out": 15.0},
    "gemini": {"model": "gemini-2.5-flash", "price_in": 0.30, "price_out": 2.50},
}
DEFAULT_PROVIDER = "claude"

DB_PATH = "data/songs.db"                    # build_db.pyで生成。無ければCSVにフォールバック
DB_CSV_FALLBACK = "data/test_songs_final.csv"
QUERIES = "test_queries.json"
N_CANDIDATES = 10

SYSTEM_PROMPT = """あなたはカラオケ楽曲検索の候補生成エンジンです。
ユーザーの検索リクエストに合う実在の楽曲候補を最大{n}件、JSONのみで出力してください。

ルール:
- 実在する楽曲のみ。確信が持てない曲は出さない
- 曲名・歌手名は正式表記で（略称・通称は使わない。例:「ヒゲダン」→「Official髭男dism」）
- 曖昧なリクエスト（ムード・シーン）はカラオケで歌われる定番曲を優先
- 必要に応じてweb検索で最新情報・タイアップ情報を確認してよい

出力形式（JSONのみ、説明文・コードブロック記号は一切不要）:
{{"candidates": [{{"title": "曲名", "artist": "歌手名"}}, ...]}}"""


def parse_candidates(text: str) -> list:
    """LLM出力テキストからcandidates配列を取り出す（コードブロック記号を除去）。"""
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text).get("candidates", [])
    except (json.JSONDecodeError, AttributeError):
        return []


class ClaudeProvider:
    """Claude API（Anthropic）経由の候補生成。web検索は内蔵web_searchツール。"""

    def __init__(self, model: str):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model

    def generate(self, query_text: str, use_web: bool):
        kwargs = dict(
            model=self.model,
            max_tokens=1500,
            system=SYSTEM_PROMPT.format(n=N_CANDIDATES),
            messages=[{"role": "user", "content": query_text}],
        )
        if use_web:
            kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}]
        t0 = time.time()
        resp = self.client.messages.create(**kwargs)
        latency = time.time() - t0
        text = "".join(b.text for b in resp.content if b.type == "text")
        u = resp.usage
        return parse_candidates(text), latency, u.input_tokens, u.output_tokens


class GeminiProvider:
    """Gemini API（google-genai）経由の候補生成。web検索はGoogle検索グラウンディング。"""

    def __init__(self, model: str):
        from google import genai
        from google.genai import types
        self.types = types
        # GEMINI_API_KEY / GOOGLE_API_KEY を環境変数から自動取得
        self.client = genai.Client()
        self.model = model

    def generate(self, query_text: str, use_web: bool):
        types = self.types
        cfg = dict(
            system_instruction=SYSTEM_PROMPT.format(n=N_CANDIDATES),
            max_output_tokens=1500,
        )
        if use_web:
            cfg["tools"] = [types.Tool(google_search=types.GoogleSearch())]
        config = types.GenerateContentConfig(**cfg)
        t0 = time.time()
        resp = self.client.models.generate_content(
            model=self.model, contents=query_text, config=config
        )
        latency = time.time() - t0
        u = resp.usage_metadata
        tin = u.prompt_token_count or 0
        tout = u.candidates_token_count or 0
        return parse_candidates(resp.text or ""), latency, tin, tout


def build_provider(name: str, model: str):
    if name == "claude":
        return ClaudeProvider(model)
    if name == "gemini":
        return GeminiProvider(model)
    raise ValueError(f"unknown provider: {name}")


def pair_eq(a: dict, b: dict) -> bool:
    return normalize(a["title"]) == normalize(b["title"]) and \
           normalize(a["artist"]) == normalize(b["artist"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=list(PROVIDERS), default=DEFAULT_PROVIDER,
                    help="候補生成に使うLLMプロバイダ")
    ap.add_argument("--model", default=None, help="モデル名を上書き（省略時はプロバイダ既定）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-web", action="store_true")
    args = ap.parse_args()

    cfg = PROVIDERS[args.provider]
    model = args.model or cfg["model"]
    provider = build_provider(args.provider, model)

    db_path = DB_PATH if os.path.exists(DB_PATH) else DB_CSV_FALLBACK
    matcher = SongMatcher(db_path)
    queries = json.load(open(QUERIES, encoding="utf-8"))["queries"]
    if args.limit:
        queries = queries[: args.limit]
    use_web = not args.no_web
    print(f"provider={args.provider} model={model} web={'on' if use_web else 'off'} "
          f"DB={db_path}({len(matcher.songs):,}曲)\n")

    records = []
    for q in queries:
        candidates, lat, tin, tout = provider.generate(q["text"], use_web)
        t0 = time.time()
        verified = matcher.verify_songs(candidates) if candidates else {"results": []}
        match_ms = (time.time() - t0) * 1000

        high_hits = []
        for r in verified["results"]:
            best = r["matches"][0] if r["matches"] else None
            if best and best["confidence"] == "high":
                high_hits.append(best)

        # 正解ヒット判定
        expected_hit = None
        if q["expected_pairs"]:
            if q["in_db"]:
                expected_hit = any(
                    any(pair_eq(e, h) for h in high_hits) for e in q["expected_pairs"]
                )
            else:
                # DB未収録クエリ: 正解がhighに「含まれない」のが正しい挙動
                expected_hit = not any(
                    any(pair_eq(e, h) for h in high_hits) for e in q["expected_pairs"]
                )

        rec = {
            "id": q["id"], "category": q["category"], "text": q["text"],
            "n_candidates": len(candidates),
            "n_high": len(high_hits),
            "hit_rate": round(len(high_hits) / len(candidates), 2) if candidates else 0,
            "expected_ok": expected_hit,
            "in_db": q["in_db"],
            "llm_latency_s": round(lat, 2),
            "match_ms": round(match_ms),
            "tokens_in": tin, "tokens_out": tout,
            "candidates": candidates,
            "final": [{"title": h["title"], "artist": h["artist"], "score": h["score"]} for h in high_hits],
        }
        records.append(rec)
        mark = {True: "○", False: "×", None: "-"}[expected_hit]
        print(f"[{q['id']}] {q['category']:5s} 候補{len(candidates):2d}→high{len(high_hits):2d} "
              f"正解{mark} {lat:.1f}s | {q['text'][:30]}")

    # ---- 集計 ----
    print("\n===== 集計 =====")
    cats = sorted({r["category"] for r in records})
    print(f"{'カテゴリ':8s} {'件数':>4s} {'照合ヒット率':>10s} {'正解率':>8s} {'平均LLM秒':>9s}")
    for c in cats + ["全体"]:
        rs = [r for r in records if c == "全体" or r["category"] == c]
        n = len(rs)
        hr = sum(r["hit_rate"] for r in rs) / n
        ev = [r for r in rs if r["expected_ok"] is not None]
        er = (sum(1 for r in ev if r["expected_ok"]) / len(ev)) if ev else None
        lt = sum(r["llm_latency_s"] for r in rs) / n
        er_s = f"{er*100:6.0f}%" if er is not None else "     -"
        print(f"{c:8s} {n:4d} {hr*100:9.0f}% {er_s} {lt:8.1f}s")

    tin = sum(r["tokens_in"] for r in records)
    tout = sum(r["tokens_out"] for r in records)
    cost = tin / 1e6 * cfg["price_in"] + tout / 1e6 * cfg["price_out"]
    print(f"\nトークン: in={tin:,} out={tout:,} "
          f"概算コスト=${cost:.3f} (1クエリ平均 ${cost/len(records):.4f}) "
          f"[{args.provider} {model} @ ${cfg['price_in']}/{cfg['price_out']} per M]")

    out = f"eval_result_{args.provider}_{'web' if use_web else 'noweb'}.json"
    json.dump(records, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"詳細ログ: {out}")


if __name__ == "__main__":
    main()
