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
ユーザーとの会話から意図を読み取り、カラオケで歌えそうな実在の楽曲を提案します。

## 重要: web検索の使用について
楽曲のトレンドは常に変化しており、学習データだけでは最新のヒット曲・流行曲を正確に把握できません。
**候補を生成する前に、必ずweb_searchツールを使って最新の楽曲情報・チャート・流行を検索してから回答すること。**
検索なしで学習データだけから候補を生成することは禁止します。

## 動作手順
1. まず入力の種類を判断する。
   - "direct": 曲名・歌手名など、求めている曲そのものを特定できる具体的な言及がある
     （例:「○○の△△が歌いたい」「△△という曲を知りたい」「○○のヒット曲を教えて」）。
   - "mood": 曲名・歌手名の直接の言及がなく、今の気分・シチュエーション・雰囲気から
     曲を探したい場合（例:「元気になりたい」「飲み会で歌える曲」「最近ちょっと寂しい」）。
2. 楽曲を提案できるほど十分に具体的かを判断する。「ユーザーが選択した状況」
   （気分・感情・地域・人数・年代）が渡されている場合は、それも具体性の材料として使い、
   会話本文だけでは曖昧でも状況情報と合わせて十分なら確認せず提案する。
   曖昧・情報不足で提案が難しく、かつ確認回数がまだ {max_clarifications} 回未満なら、
   確認質問を **1つだけ** 返す（need_clarification=true）。
   - これまでに確認した回数: {clarification_count} 回
   - 質問は簡潔に1文。複数を一度に聞かない。
3. 十分に具体的、または既に {max_clarifications} 回確認済みの場合は、確認せず
   ベストエフォートで楽曲を提案する（need_clarification=false）。
   - request_type="direct" のとき: 気分推測は行わず、言及された曲（歌手名が分かれば
     合わせて）を最優先候補として返す。mood は空文字でよい。
   - request_type="mood" のとき: 会話から気分・シーンを読み取り、合う曲を提案する。

## 楽曲提案のルール
- 実在する楽曲のみ。確信が持てない曲は出さない。
- 曲名・歌手名は正式表記（略称・通称は使わない。例:「ヒゲダン」→「Official髭男dism」）。
- request_type="mood" のときは、ユーザーの気分・シーンに合う、カラオケ定番曲を優先。
- 会話履歴で自分（assistant）が直前に曲を提案済みで、ユーザーがそのどれも選ばずに
  会話を続けている（「他にない?」「イマイチ」等、別の曲を求めている）場合は、
  直前に提案した曲を繰り返さず、別の候補を提案する。
- 候補は最大 {n} 件。
{profile_hint}
## 出力形式（JSONのみ。説明文・コードブロック記号・前置きは一切不要）
{{
  "need_clarification": true または false,
  "question": "確認質問（need_clarification=true のときだけ。それ以外は空文字）",
  "request_type": "direct" または "mood",
  "mood": "会話から読み取った気分・シーンの短い要約（request_type=mood のときのみ。直接指定なら空文字）",
  "candidates": [{{"title": "曲名", "artist": "歌手名"}}]
}}"""


def build_system_prompt(clarification_count: int, profile_hint: str = "", context_hint: str = "") -> str:
    blocks = "\n".join(b for b in (context_hint, profile_hint) if b)
    hint_block = f"\n{blocks}\n" if blocks else ""
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
            "request_type": "mood",
            "mood": "",
            "candidates": [],
        }
    request_type = data.get("request_type")
    if request_type not in ("direct", "mood"):
        request_type = "mood"
    return {
        "need_clarification": bool(data.get("need_clarification", False)),
        "question": str(data.get("question", "") or ""),
        "request_type": request_type,
        "mood": str(data.get("mood", "") or ""),
        "candidates": [
            {"title": c.get("title", ""), "artist": c.get("artist", "")}
            for c in (data.get("candidates") or [])
            if isinstance(c, dict) and c.get("title")
        ],
    }


async def analyze(adapter, model: str, messages: list[dict],
                  clarification_count: int, profile_hint: str = "", context_hint: str = "") -> dict:
    """LLMを1回呼び、曖昧判定＋気分＋候補をまとめた dict を返す。

    確認上限に達している場合は、LLMが確認質問を返しても提案へ強制的に倒す。
    """
    system_prompt = build_system_prompt(clarification_count, profile_hint, context_hint)
    full_messages = [{"role": "system", "content": system_prompt}] + messages
    raw = await adapter.chat(full_messages, model)
    result = parse_llm_json(raw)

    if clarification_count >= MAX_CLARIFICATIONS:
        result["need_clarification"] = False
    return result
