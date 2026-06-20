"""ユーザ別 会話履歴・嗜好ストア（永続化: SQLite）

session_store.py が「セッション単位の一時状態（in-memory, 再起動で消える）」を扱うのに対し、
こちらは user_id 単位で会話と推薦実績を SQLite に永続化する。
次回アクセス時に「このユーザが過去に気に入った歌手」を推薦のヒントとして使う。
"""
import os
import sqlite3
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.getenv(
    "USER_DB_PATH", os.path.join(PROJECT_ROOT, "data", "chat_users.db")
)

DDL = """
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    ts         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_user ON messages (user_id, ts);

CREATE TABLE IF NOT EXISTS liked_songs (
    user_id    TEXT NOT NULL,
    song_id    TEXT NOT NULL,
    title      TEXT NOT NULL,
    artist     TEXT NOT NULL,
    cnt        INTEGER NOT NULL DEFAULT 1,
    last_ts    REAL NOT NULL,
    PRIMARY KEY (user_id, song_id)
);
"""


class UserProfileStore:
    def __init__(self, db_path: str = DEFAULT_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        with self._con() as con:
            con.executescript(DDL)

    def _con(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def add_message(self, user_id: str, role: str, content: str) -> None:
        with self._con() as con:
            con.execute(
                "INSERT INTO messages (user_id, role, content, ts) VALUES (?,?,?,?)",
                (user_id, role, content, time.time()),
            )

    def record_recommendations(self, user_id: str, songs: list[dict]) -> None:
        """推薦して提示した曲を嗜好として加点（次回推薦のヒントに使う）。"""
        now = time.time()
        with self._con() as con:
            for s in songs:
                con.execute(
                    "INSERT INTO liked_songs (user_id, song_id, title, artist, cnt, last_ts) "
                    "VALUES (?,?,?,?,1,?) "
                    "ON CONFLICT(user_id, song_id) DO UPDATE SET "
                    "cnt = cnt + 1, last_ts = excluded.last_ts",
                    (user_id, s["song_id"], s["title"], s["artist"], now),
                )

    def top_artists(self, user_id: str, limit: int = 5) -> list[str]:
        with self._con() as con:
            rows = con.execute(
                "SELECT artist, SUM(cnt) AS c FROM liked_songs WHERE user_id=? "
                "GROUP BY artist ORDER BY c DESC, MAX(last_ts) DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [r["artist"] for r in rows]

    def recent_songs(self, user_id: str, limit: int = 10) -> list[dict]:
        with self._con() as con:
            rows = con.execute(
                "SELECT song_id, title, artist FROM liked_songs WHERE user_id=? "
                "ORDER BY last_ts DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def profile_hint(self, user_id: str) -> str:
        """システムプロンプトに差し込む嗜好ヒント文。実績がなければ空文字。"""
        artists = self.top_artists(user_id)
        if not artists:
            return ""
        return (
            "## このユーザの過去の好み（推薦の参考に。固執はしない）\n"
            "よく提案を受け入れた歌手: " + "、".join(artists)
        )


# シングルトン
profile_store = UserProfileStore()
