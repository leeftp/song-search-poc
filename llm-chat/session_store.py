"""
in-memory セッションストア
セッションIDごとに会話履歴と曖昧確認カウントを管理
"""
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Session:
    session_id: str
    user_id: str = "anonymous"
    messages: list[dict] = field(default_factory=list)
    clarification_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class SessionStore:
    _TTL_SECONDS = 3600  # 1時間でセッション期限切れ

    def __init__(self):
        self._store: dict[str, Session] = {}

    def get(self, session_id: str) -> Session:
        """セッションを取得（なければ新規作成）"""
        self._evict_expired()
        if session_id not in self._store:
            self._store[session_id] = Session(session_id=session_id)
        return self._store[session_id]

    def update(self, session: Session) -> None:
        session.updated_at = time.time()
        self._store[session.session_id] = session

    def delete(self, session_id: str) -> None:
        self._store.pop(session_id, None)

    def _evict_expired(self) -> None:
        now = time.time()
        expired = [
            sid for sid, s in self._store.items()
            if now - s.updated_at > self._TTL_SECONDS
        ]
        for sid in expired:
            del self._store[sid]


# シングルトン
session_store = SessionStore()
