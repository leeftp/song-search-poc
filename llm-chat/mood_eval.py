#!/usr/bin/env python3
"""ムード推薦 評価ハーネス

フロー（1クエリあたり）:
  気分/年代/ジャンルクエリ
   → 選択LLMで楽曲候補を生成
   → 楽曲DB(songs.db)と照合し、DBに実在する曲だけに絞る（song_search）
   → 年代一致率 / ジャンル一致率を自前DB列(release_year/genre)で客観採点
   → ムード適合を別LLM(judge)で代理採点（外部真値が無いため）
   → LLM別に集計して比較

実行:
  export ANTHROPIC_API_KEY=...   # 生成/judgeに使うプロバイダの鍵
  python3 mood_eval.py --provider claude                 # 生成=claude, judge=既定(claude)
  python3 mood_eval.py --provider gemini --judge claude  # 生成=gemini, judge=claude
  python3 mood_eval.py --limit 4
  python3 mood_eval.py --selftest                        # 鍵不要。採点ロジックだけ検証

判定の真値の出どころ:
  年代・ジャンル = 自前 songs.db の列（100%充足）。
  ムード        = LLM-judge（Last.fmはJP曲でムード/年代を持たないため使えない）。
"""
import argparse
import asyncio
import json
import os
import re
import sys

from dotenv import load_dotenv

from song_search import get_songdb
from mood_taxonomy import era_match, genre_match, era_to_range

load_dotenv()

# アプリと同じモデル構成（プロバイダ → 実モデルID）
MODELS = {
    "claude": ("anthropic", "claude-haiku-4-5"),
    "gemini": ("gemini", "gemini-3.1-flash-lite"),
    "openai": ("openai", "gpt-5.4-mini"),
}
ENV_KEY = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}
N_CANDIDATES = 10

GEN_SYSTEM = """あなたはカラオケ楽曲レコメンドエンジンです。
ユーザーの希望に合う実在の楽曲を最大{n}件、JSONのみで出力してください。
- 実在曲のみ。曲名・歌手名は正式表記（略称禁止。例:「ヒゲダン」→「Official髭男dism」）。
- 説明文・コードブロック記号は不要。
出力: {{"candidates": [{{"title": "曲名", "artist": "歌手名"}}]}}"""

JUDGE_SYSTEM = """あなたは楽曲のムード適合を判定する審査員です。
与えられた曲リストが、ユーザーの希望ムードに合うかを各曲 true/false で判定してください。
JSONのみ・入力と同じ順序で出力: {"verdicts": [true, false, ...]}"""


# ---------- アダプタ生成（llm-chat の adapters を流用） ----------
def build_adapter(provider: str):
    key = os.getenv(ENV_KEY[provider], "")
    if not key:
        raise RuntimeError(f"{provider} の APIキー({ENV_KEY[provider]})が未設定です。")
    if provider == "anthropic":
        from adapters.anthropic_adapter import AnthropicAdapter
        return AnthropicAdapter(api_key=key)
    if provider == "gemini":
        from adapters.gemini_adapter import GeminiAdapter
        return GeminiAdapter(api_key=key)
    from adapters.openai_adapter import OpenAIAdapter
    return OpenAIAdapter(api_key=key)


def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"^```(json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", cleaned, flags=re.S)
    try:
        return json.loads(m.group(0) if m else cleaned)
    except (json.JSONDecodeError, AttributeError):
        return {}


async def generate_candidates(adapter, model: str, query_text: str) -> list:
    msgs = [
        {"role": "system", "content": GEN_SYSTEM.format(n=N_CANDIDATES)},
        {"role": "user", "content": query_text},
    ]
    data = _extract_json(await adapter.chat(msgs, model))
    return [c for c in data.get("candidates", []) if isinstance(c, dict) and c.get("title")]


async def judge_mood(adapter, model: str, query_text: str, mood: str, songs: list) -> list:
    """各曲が希望ムードに合うか true/false のリストを返す（songs と同順）。"""
    if not songs:
        return []
    listing = "\n".join(f"{i+1}. {s['title']} / {s['artist']}" for i, s in enumerate(songs))
    user = f"ユーザーの希望: 「{query_text}」\n想定ムード: {mood}\n\n曲リスト:\n{listing}"
    data = _extract_json(await adapter.chat(
        [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}], model))
    verdicts = data.get("verdicts", [])
    # 長さを songs に合わせる（不足は False、超過は切り捨て）
    verdicts = [bool(v) for v in verdicts][: len(songs)]
    verdicts += [False] * (len(songs) - len(verdicts))
    return verdicts


# ---------- 採点（純粋関数。selftestで鍵なし検証） ----------
def score_query(resolved: list, expect: dict, mood_verdicts: list) -> dict:
    """resolved: [{song_id,title,artist,genre,release_year}], expect: {era?,genre?,mood?}"""
    n = len(resolved)
    out = {"n_songs": n, "era_rate": None, "genre_rate": None, "mood_rate": None}
    if n == 0:
        return out
    if expect.get("era"):
        out["era_rate"] = sum(era_match(s.get("release_year"), expect["era"]) for s in resolved) / n
    if expect.get("genre"):
        out["genre_rate"] = sum(genre_match(s.get("genre"), expect["genre"]) for s in resolved) / n
    if expect.get("mood") and mood_verdicts:
        out["mood_rate"] = sum(mood_verdicts) / len(mood_verdicts)
    return out


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _pct(v):
    return f"{v*100:5.0f}%" if v is not None else "    -"


# ---------- メイン ----------
async def run(args):
    songdb = get_songdb()
    # song_id → DB行（genre/release_year 取得用）
    meta = {s["song_id"]: s for s in songdb.matcher.songs}

    gen_provider, gen_model = MODELS[args.provider]
    judge_provider, judge_model = MODELS[args.judge]
    gen_adapter = build_adapter(gen_provider)
    judge_adapter = build_adapter(judge_provider) if args.judge != args.provider else gen_adapter

    queries = json.load(open(args.queries, encoding="utf-8"))["queries"]
    if args.limit:
        queries = queries[: args.limit]

    print(f"生成={args.provider}({gen_model}) judge={args.judge}({judge_model}) "
          f"DB={songdb.size:,}曲 / {len(queries)}クエリ\n")

    records = []
    for q in queries:
        expect = q.get("expect", {})
        cands = await generate_candidates(gen_adapter, gen_model, q["text"])
        resolved = songdb.resolve_candidates(cands)
        # genre/release_year を付与
        for s in resolved:
            row = meta.get(s["song_id"], {})
            s["genre"] = row.get("genre")
            s["release_year"] = row.get("release_year")

        verdicts = []
        if expect.get("mood") and resolved:
            verdicts = await judge_mood(judge_adapter, judge_model, q["text"], expect["mood"], resolved)

        sc = score_query(resolved, expect, verdicts)
        sc["coverage"] = round(len(resolved) / len(cands), 2) if cands else 0
        records.append({"id": q["id"], "text": q["text"], "expect": expect, **sc,
                        "songs": [{k: s[k] for k in ("song_id", "title", "artist", "genre", "release_year")} for s in resolved]})
        print(f"[{q['id']}] 候補{len(cands):2d}→DB{sc['n_songs']:2d} "
              f"年代{_pct(sc['era_rate'])} ジャンル{_pct(sc['genre_rate'])} ムード{_pct(sc['mood_rate'])} | {q['text']}")

    print("\n===== 集計 =====")
    print(f"年代一致率   平均: {_pct(_avg([r['era_rate'] for r in records]))}")
    print(f"ジャンル一致率 平均: {_pct(_avg([r['genre_rate'] for r in records]))}")
    print(f"ムード一致率  平均: {_pct(_avg([r['mood_rate'] for r in records]))}  (judge={args.judge})")
    print(f"DB名寄せ率   平均: {_pct(_avg([r['coverage'] for r in records]))}")

    out = f"mood_eval_{args.provider}.json"
    json.dump(records, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n詳細ログ: {out}")


def selftest():
    """採点ロジックを鍵なしで検証（モックの推薦結果を流す）。"""
    print("=== mood_eval selftest（採点ロジックのみ・API不要） ===")
    # era_to_range
    assert era_to_range("1990s") == (1990, 1999)
    assert era_to_range("90年代") == (1990, 1999)
    assert era_to_range("昭和") == (1926, 1989)
    assert era_to_range("不明") is None
    # era_match / genre_match
    assert era_match("1995", "1990s") and not era_match("2001", "1990s")
    assert era_match("1985", "昭和") and not era_match("1995", "昭和")
    assert genre_match("ポップ", "J-Pop") and not genre_match("演歌", "J-Pop")
    # score_query: モック推薦（1990sのJ-Pop 3曲中2曲が年代内、2曲がジャンル一致）
    resolved = [
        {"song_id": "A", "genre": "J-Pop", "release_year": "1994"},
        {"song_id": "B", "genre": "ポップ", "release_year": "1998"},
        {"song_id": "C", "genre": "ロック", "release_year": "2005"},
    ]
    sc = score_query(resolved, {"era": "1990s", "genre": "J-Pop", "mood": "x"}, [True, False, True])
    assert abs(sc["era_rate"] - 2/3) < 1e-9, sc
    assert abs(sc["genre_rate"] - 2/3) < 1e-9, sc
    assert abs(sc["mood_rate"] - 2/3) < 1e-9, sc
    assert score_query([], {"era": "1990s"}, [])["n_songs"] == 0
    print("  era_to_range / era_match / genre_match / score_query: 全PASS ✅")
    print(f"  例: 1990sJ-Pop想定の推薦3曲 → 年代{sc['era_rate']*100:.0f}% ジャンル{sc['genre_rate']*100:.0f}% ムード{sc['mood_rate']*100:.0f}%")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=list(MODELS), default="claude", help="候補生成に使うLLM")
    ap.add_argument("--judge", choices=list(MODELS), default="claude", help="ムード採点に使うLLM")
    ap.add_argument("--queries", default="mood_queries.json")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--selftest", action="store_true", help="API不要で採点ロジックを検証")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        sys.exit(0)
    try:
        asyncio.run(run(args))
    except RuntimeError as e:
        print(f"[エラー] {e}", file=sys.stderr)
        print("ヒント: 設定パネル(/config/keys)か .env で対象プロバイダのAPIキーを設定してください。", file=sys.stderr)
        sys.exit(1)
