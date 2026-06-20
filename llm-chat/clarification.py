"""曖昧判定 ＋ 気分解析 ＋ 楽曲候補生成（1回のLLM呼び出しでまとめて行う）

選択中のLLM自身に、システムプロンプトで次を判断させる:
  1. ユーザー入力が楽曲を提案できるほど具体的か（曖昧判定）
  2. 曖昧で、かつ確認が2回未満なら確認質問を1つだけ返す
  3. 十分具体的 or 既に2回確認済みなら、気分を解釈し実在曲の候補を出す

出力はJSON。バックエンドはこのJSONを受けて、候補を楽曲DBと照合する。
"""
import json
import re

MAX_CLARIFICATIONS = 2
N_CANDIDATES = 10

SYSTEM_PROMPT_TEMPLATE = """あなたはカラオケ楽曲レコメンドAIです。
ユーザーとの会話から「今の気分・シチュエーション・歌いたい雰囲気」を読み取り、
カラオケで歌えそうな実在の楽曲を提案します。

## 動作手順
1. ユーザーの入力が、楽曲を提案できるほど十分に具体的かを判断する。
2. 曖昧・情報不足で提案が難しく、かつ確認回数がまだ {max_clarifications} 回未満なら、
   確認質問を **1つだけ** 返す（need_clarification=true）。
   - これまでに確認した回数: {clarification_count} 回
   - 質問は簡潔に1文。複数を一度に聞かない。
3. 十分に具体的、または既に {max_clarifications} 回確認済みの場合は、確認せず
   ベストエフォートで楽曲を提案する（need_clarification=false）。

## 楽曲提案のルール
- 実在する楽曲のみ。確信が持てない曲は出さない。
- 曲名・歌手名は正式表記（略称・通称は使わない。例:「ヒゲダン」→「Official髭男dism」）。
- ユーザーの気分・シーンに合う、カラオケ定番曲を優先。最大 {n} 件。
{profile_hint}
## 出力形式（JSONのみ。説明文・コードブロック記号・前置きは一切不要）
{{
  "need_clarification": true または false,
  "question": "確認質問（need_clarification=true のときだけ。それ以外は空文字）",
  "mood": "会話から読み取った気分・シーンの短い要約（日本語1文）",
  "candidates": [{{"title": "曲名", "artist": "歌手名"}}]
}}"""


def build_system_prompt(clarification_count: int, profile_hint: str = "") -> str:
    hint_block = f"\n{profile_hint}\n" if profile_hint else ""
    return SYSTEM_PROMPT_TEMPLATE.format(
        clarification_count=clarification_count,
        max_clarifications=MAX_CLARIFICATIONS,
        n=N_CANDIDATES,
        profile_hint=hint_block,
    )


def parse_llm_json(text: str) -> dict:
    """LLM出力からJSONを取り出す。失敗時は安全側（確認質問扱い）に倒す。"""
    cleaned = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
    # 最初の { から最後の } までを抽出（前後に説明文が混ざっても拾えるように）
    m = re.search(r"\{.*\}", cleaned, flags=re.S)
    raw = m.group(0) if m else cleaned
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "need_clarification": True,
            "question": text.strip()[:300] or "もう少し詳しく教えてください。",
            "mood": "",
            "candidates": [],
        }
    return {
        "need_clarification": bool(data.get("need_clarification", False)),
        "question": str(data.get("question", "") or ""),
        "mood": str(data.get("mood", "") or ""),
        "candidates": [
            {"title": c.get("title", ""), "artist": c.get("artist", "")}
            for c in (data.get("candidates") or [])
            if isinstance(c, dict) and c.get("title")
        ],
    }


async def analyze(adapter, model: str, messages: list[dict],
                  clarification_count: int, profile_hint: str = "") -> dict:
    """LLMを1回呼び、曖昧判定＋気分＋候補をまとめた dict を返す。

    確認上限に達している場合は、LLMが確認質問を返しても提案へ強制的に倒す。
    """
    system_prompt = build_system_prompt(clarification_count, profile_hint)
    full_messages = [{"role": "system", "content": system_prompt}] + messages
    raw = await adapter.chat(full_messages, model)
    result = parse_llm_json(raw)

    if clarification_count >= MAX_CLARIFICATIONS:
        result["need_clarification"] = False
    return result
