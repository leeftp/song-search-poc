"""LLM 楽曲レコメンドチャット - FastAPI バックエンド

フロー（POST /chat）:
  ユーザーテキスト
    → 選択LLMで「曖昧判定 ＋ 気分解析 ＋ 楽曲候補生成」（clarification.analyze）
    → 曖昧なら確認質問を返す（最大2回）
    → 候補を楽曲DB(songs.db)と照合し、DBに実在する曲だけを返す（song_search）
    → ユーザ別に会話・推薦実績を永続化（user_profile）し、次回推薦のヒントにする

楽曲DBへのアクセスは SongDB(song_search.py) 経由に一本化（API化）。
"""
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv, set_key

from session_store import session_store
from user_profile import profile_store
from song_search import get_songdb
from clarification import analyze, MAX_CLARIFICATIONS

# ========== 初期化 ==========
load_dotenv()

BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"

app = FastAPI(title="LLM 楽曲レコメンドチャット", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 開発用に全許可
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ========== APIキー管理（サーバサイドのみ保持。フロントには返さない） ==========
_api_keys: dict[str, str] = {
    "openai": os.getenv("OPENAI_API_KEY", ""),
    "gemini": os.getenv("GEMINI_API_KEY", ""),
    "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
}

# フロントのセレクター値 → (プロバイダ, 実モデルID)
MODEL_REGISTRY = {
    "gpt-5.4-mini": ("openai", "gpt-5.4-mini"),
    "claude-haiku-4-5": ("anthropic", "claude-haiku-4-5"),
    "gemini-3.1-flash-lite": ("gemini", "gemini-3.1-flash-lite"),
}
DEFAULT_MODEL = "claude-haiku-4-5"


def get_adapter(model_key: str):
    entry = MODEL_REGISTRY.get(model_key)
    if not entry:
        raise HTTPException(status_code=400, detail=f"未対応のモデル: {model_key}")
    provider, real_model = entry

    key = _api_keys.get(provider, "")
    if not key:
        raise HTTPException(
            status_code=400,
            detail=f"{provider} のAPIキーが未設定です。設定パネルから登録してください。",
        )

    if provider == "openai":
        from adapters.openai_adapter import OpenAIAdapter
        return OpenAIAdapter(api_key=key), real_model
    if provider == "gemini":
        from adapters.gemini_adapter import GeminiAdapter
        return GeminiAdapter(api_key=key), real_model
    from adapters.anthropic_adapter import AnthropicAdapter
    return AnthropicAdapter(api_key=key), real_model


# ========== Pydantic モデル ==========
class ChatRequest(BaseModel):
    message: str
    model: str = DEFAULT_MODEL
    session_id: Optional[str] = None
    user_id: str = "anonymous"


class ConfigKeysRequest(BaseModel):
    openai: Optional[str] = None
    gemini: Optional[str] = None
    anthropic: Optional[str] = None


class VerifyRequest(BaseModel):
    candidates: list[dict]


# ========== エンドポイント ==========
@app.get("/")
async def root():
    return FileResponse(str(static_dir / "index.html"))


@app.get("/health")
async def health():
    try:
        n = get_songdb().size
        return {"status": "ok", "songs_loaded": n}
    except Exception as e:  # noqa: BLE001
        return {"status": "degraded", "error": str(e)}


@app.post("/chat")
async def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="メッセージが空です。")

    session_id = req.session_id or str(uuid.uuid4())
    session = session_store.get(session_id)
    session.user_id = req.user_id

    # 会話履歴に追加（in-memory ＋ ユーザ別永続）
    session.messages.append({"role": "user", "content": req.message})
    profile_store.add_message(req.user_id, "user", req.message)

    adapter, real_model = get_adapter(req.model)

    # 過去の好みヒント（次回推薦に活かす）
    profile_hint = profile_store.profile_hint(req.user_id)

    # 1回のLLM呼び出しで「曖昧判定＋気分＋候補」を取得
    try:
        result = await analyze(
            adapter, real_model, session.messages,
            session.clarification_count, profile_hint,
        )
    except Exception as e:  # noqa: BLE001  認証エラー・レート制限・モデル名誤り等を整形して返す
        raise HTTPException(status_code=502, detail=f"LLM呼び出しに失敗しました: {e}")

    # --- 確認質問を返す分岐 ---
    if result["need_clarification"] and result["question"]:
        session.clarification_count += 1
        session.messages.append({"role": "assistant", "content": result["question"]})
        session_store.update(session)
        profile_store.add_message(req.user_id, "assistant", result["question"])
        return {
            "type": "clarification",
            "session_id": session_id,
            "message": result["question"],
            "clarification_count": session.clarification_count,
            "songs": [],
            "mood": result["mood"],
            "model": req.model,
        }

    # --- 楽曲提案分岐: 候補を楽曲DBと照合（DB外は落とす） ---
    songs = get_songdb().resolve_candidates(result["candidates"])

    if songs:
        profile_store.record_recommendations(req.user_id, songs)
        lines = "、".join(f"{s['title']}（{s['artist']}）" for s in songs[:3])
        reply = f"{result['mood']}\nこの気分なら、こんな曲はどうでしょう: {lines} など。"
    else:
        reply = (
            f"{result['mood']}\n"
            "ぴったりの曲を楽曲DB内で見つけられませんでした。"
            "もう少しイメージ（年代・ジャンル・アーティストなど）を教えてください。"
        )

    # 確認カウントはリセット（一連の意図が解決したとみなす）
    session.clarification_count = 0
    session.messages.append({"role": "assistant", "content": reply})
    session_store.update(session)
    profile_store.add_message(req.user_id, "assistant", reply)

    return {
        "type": "recommendation",
        "session_id": session_id,
        "message": reply,
        "mood": result["mood"],
        "songs": songs,                # [{song_id, title, artist, score, confidence}]
        "clarification_count": 0,
        "model": req.model,
    }


# ---- 楽曲DB API（バックエンドからのDBアクセスをAPI化。MCP差し替え点でもある） ----
@app.get("/songs/search")
async def songs_search(title: str, artist: str = "", limit: int = 3):
    return {"matches": get_songdb().search(title, artist, limit)}


@app.post("/songs/verify")
async def songs_verify(req: VerifyRequest):
    """MCPツール verify_songs と同一インターフェース。"""
    return get_songdb().verify(req.candidates)


# ---- APIキー設定 ----
@app.post("/config/keys")
async def set_keys(req: ConfigKeysRequest):
    updated = []
    for provider, key in [("openai", req.openai), ("gemini", req.gemini), ("anthropic", req.anthropic)]:
        if key:
            _api_keys[provider] = key
            if not ENV_FILE.exists():
                ENV_FILE.touch()
            set_key(str(ENV_FILE), f"{provider.upper()}_API_KEY", key)
            updated.append(provider)
    return {"updated": updated, "message": f"{', '.join(updated) or 'なし'} のAPIキーを更新しました"}


@app.get("/config/keys")
async def get_keys():
    def mask(key: str) -> str:
        if not key:
            return ""
        if len(key) <= 8:
            return "*" * len(key)
        return key[:4] + "*" * (len(key) - 8) + key[-4:]

    # キー本体は返さない（set 状態とマスク表示のみ）
    return {
        p: {"set": bool(_api_keys[p]), "masked": mask(_api_keys[p])}
        for p in ("openai", "gemini", "anthropic")
    }


# ---- ユーザ履歴・嗜好 ----
@app.get("/users/{user_id}/profile")
async def user_profile(user_id: str):
    return {
        "user_id": user_id,
        "top_artists": profile_store.top_artists(user_id),
        "recent_songs": profile_store.recent_songs(user_id),
    }


@app.delete("/session/{session_id}")
async def clear_session(session_id: str):
    session_store.delete(session_id)
    return {"message": f"セッション {session_id} を削除しました"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
