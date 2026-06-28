"""楽曲DBアクセス層（Part 2）

song-search-poc の SQLite 楽曲DB(songs.db) と照合ロジック(matcher.py) を再利用する。
LLMが挙げた候補をこのDBと照合し、「実在しDBに存在する曲」だけを通す関門になる。

バックエンドはこの SongDB を経由してのみ楽曲情報にアクセスする（=楽曲DBアクセスをAPI化）。
本番でMCPサーバや外部楽曲APIに差し替える場合は、verify() / search() を
同じ入出力のまま実装し直せばよい（matcher.verify_songs は MCPツール verify_songs と同一IF）。
"""
from __future__ import annotations  # `X | None` 等を Python 3.9 でも書けるように

import os
import sys

# llm-chat の親 = song-search-poc プロジェクトルート。matcher.py / data/songs.db を借りる
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from matcher import SongMatcher  # noqa: E402

DEFAULT_DB_PATH = os.getenv(
    "SONG_DB_PATH", os.path.join(PROJECT_ROOT, "data", "songs.db")
)
# DB外の曲を返さないため、この信頼度以上の照合のみ採用する
ACCEPT_CONFIDENCE = {"high", "medium"}

# 楽曲DBに人気度/ランキングの列が無いため、検索結果が0件のときのフォールバック用に
# 暫定の人気曲リストを順位付きで保持する（本番では実チャートAPIに差し替え想定）。
FALLBACK_HIT_CHART = [
    {"title": "Lemon", "artist": "米津玄師"},
    {"title": "Pretender", "artist": "Official髭男dism"},
    {"title": "夜に駆ける", "artist": "YOASOBI"},
    {"title": "マリーゴールド", "artist": "あいみょん"},
    {"title": "白日", "artist": "King Gnu"},
    {"title": "インフェルノ", "artist": "Mrs. GREEN APPLE"},
    {"title": "クリスマスソング", "artist": "back number"},
    {"title": "糸", "artist": "中島みゆき"},
    {"title": "チェリー", "artist": "スピッツ"},
    {"title": "丸ノ内サディスティック", "artist": "椎名林檎"},
]


class SongDB:
    """楽曲DBへの唯一の入口。内部で matcher を持つ。"""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        if not os.path.exists(db_path):
            raise FileNotFoundError(
                f"楽曲DBが見つかりません: {db_path}\n"
                f"先に `python3 scripts/build_db.py` で data/songs.db を生成してください。"
            )
        self.db_path = db_path
        self.matcher = SongMatcher(db_path)

    @property
    def size(self) -> int:
        return len(self.matcher.songs)

    def search(self, title: str, artist: str = "", limit: int = 3) -> list[dict]:
        """単一の曲名/歌手名でDBを引く。"""
        return self.matcher.match_one(title, artist, limit)

    def verify(self, candidates: list[dict]) -> dict:
        """候補配列をDB照合（MCPツール verify_songs と同一IF）。"""
        return self.matcher.verify_songs(candidates)

    def resolve_candidates(self, candidates: list[dict]) -> list[dict]:
        """LLM候補をDB照合し、DBに実在する曲だけを重複排除して返す。

        返り値: [{song_id, title, artist, score, confidence}] （信頼度降順）
        """
        verified = self.verify(candidates)
        resolved: dict[str, dict] = {}
        for r in verified["results"]:
            best = r["matches"][0] if r["matches"] else None
            if best and best["confidence"] in ACCEPT_CONFIDENCE:
                # 同一曲を複数候補が指したらスコアが高い方を残す
                cur = resolved.get(best["song_id"])
                if cur is None or best["score"] > cur["score"]:
                    resolved[best["song_id"]] = best
        return sorted(resolved.values(), key=lambda s: -s["score"])

    def weekly_hit_chart(self, limit: int = 5) -> list[dict]:
        """検索結果が0件のときのフォールバック。固定リストをランキング順で返す。

        返り値: [{song_id, title, artist, score, confidence, rank}] （rank昇順）
        """
        verified = self.verify(FALLBACK_HIT_CHART)
        chart = []
        for r in verified["results"]:
            best = r["matches"][0] if r["matches"] else None
            if best and best["confidence"] in ACCEPT_CONFIDENCE:
                chart.append(best)
        for rank, song in enumerate(chart[:limit], start=1):
            song["rank"] = rank
        return chart[:limit]


# シングルトン（起動時に1回だけDBをロード）
_songdb: SongDB | None = None


def get_songdb() -> SongDB:
    global _songdb
    if _songdb is None:
        _songdb = SongDB()
    return _songdb
