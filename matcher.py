#!/usr/bin/env python3
"""ペア照合モジュール（PoCローカル版）

設計（確定済み仕様）:
- NFKC正規化 + 小文字化 + 記号除去 + feat表記除去
- スコア = 曲名類似度 * 0.6 + 歌手名類似度 * 0.4
- confidence: high (>=85) / medium (>=70) / low (<70)
本番ではこのロジックがバックエンドのペア照合APIに載る。
MCPサーバ(verify_songs)はこれを呼ぶ薄いラッパーになる。
"""
import csv
import os
import re
import sqlite3
import unicodedata
from rapidfuzz import fuzz

W_TITLE = 0.6
W_ARTIST = 0.4
TH_HIGH = 85
TH_MEDIUM = 70


def normalize(text: str) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text).lower()
    t = re.sub(r"(feat\.?|featuring|ft\.?|with)\s.*", "", t)
    t = re.sub(r"[\s\(\)\[\]「」『』【】・,，、。．.!！?？~〜\-_/／'’\"&＆×:：;；…‐–—]", "", t)
    return t


class SongMatcher:
    def __init__(self, path: str):
        """path が .db なら SQLite、それ以外は CSV としてロードする。

        どちらの経路でも self.songs は各曲 dict（_ntitle/_nartist 付き）のリストになり、
        ファジー照合のロジックは共通。完全一致は self.exact のインメモリ索引で引く。
        """
        if path.endswith(".db") or (os.path.exists(path) and self._is_sqlite(path)):
            self.songs = self._load_sqlite(path)
        else:
            self.songs = self._load_csv(path)
        # 完全一致用インデックス
        self.exact = {}
        for s in self.songs:
            self.exact.setdefault((s["_ntitle"], s["_nartist"]), s)

    @staticmethod
    def _is_sqlite(path: str) -> bool:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"

    @staticmethod
    def _load_csv(csv_path: str):
        songs = []
        with open(csv_path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                r["_ntitle"] = normalize(r["title"])
                r["_nartist"] = normalize(r["artist"])
                songs.append(r)
        return songs

    @staticmethod
    def _load_sqlite(db_path: str):
        # 正規化済み列(ntitle/nartist)を読み出すので起動時の再正規化が不要
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        songs = []
        for row in con.execute(
            "SELECT song_id, title, artist, genre, release_year, ntitle, nartist FROM songs"
        ):
            d = dict(row)
            d["_ntitle"] = d.pop("ntitle")
            d["_nartist"] = d.pop("nartist")
            songs.append(d)
        con.close()
        return songs

    def match_one(self, title: str, artist: str, limit: int = 3):
        nt, na = normalize(title), normalize(artist)

        # 1. 正規化後の完全一致（高速パス）
        hit = self.exact.get((nt, na))
        if hit:
            return [self._fmt(hit, 100.0)]

        # 2. ファジー照合（全件スキャン: 3万件で数十ms）
        scored = []
        for s in self.songs:
            ts = fuzz.ratio(nt, s["_ntitle"])
            if ts < 50:  # 曲名が半分も似てなければ歌手名計算を省略
                continue
            as_ = fuzz.ratio(na, s["_nartist"])
            score = ts * W_TITLE + as_ * W_ARTIST
            if score >= TH_MEDIUM - 10:
                scored.append((score, s))
        scored.sort(key=lambda x: -x[0])
        return [self._fmt(s, sc) for sc, s in scored[:limit]]

    @staticmethod
    def _fmt(s, score):
        conf = "high" if score >= TH_HIGH else ("medium" if score >= TH_MEDIUM else "low")
        return {
            "song_id": s["song_id"],
            "title": s["title"],
            "artist": s["artist"],
            "genre": s.get("genre", ""),
            "release_year": s.get("release_year", ""),
            "score": round(score, 1),
            "confidence": conf,
        }

    def verify_songs(self, candidates: list[dict], limit_per_candidate: int = 3) -> dict:
        """MCPツール verify_songs と同一インターフェース"""
        results = []
        for c in candidates:
            matches = self.match_one(c["title"], c["artist"], limit_per_candidate)
            results.append({"query": c, "matches": matches})
        return {"results": results}


if __name__ == "__main__":
    import json
    import time

    db = "data/songs.db" if os.path.exists("data/songs.db") else "data/test_songs_final.csv"
    m = SongMatcher(db)
    print(f"DB({db}): {len(m.songs)}曲ロード完了")

    # 表記ゆれを含むテスト候補（LLMが出しそうな揺れを再現）
    test_candidates = [
        {"title": "Lemon", "artist": "米津玄師"},            # 完全一致
        {"title": "lemon", "artist": "米津 玄師"},           # 空白・大文字ゆれ
        {"title": "Pretender", "artist": "ヒゲダン"},        # 略称（artist不一致）
        {"title": "残酷な天使のテーゼ", "artist": "高橋洋子"},
        {"title": "ＣＨＥ．Ｒ．ＲＹ", "artist": "YUI"},      # 全角ゆれ
        {"title": "存在しない架空の曲XYZ", "artist": "誰でもない"},  # 幻覚想定
    ]
    t0 = time.time()
    out = m.verify_songs(test_candidates)
    dt = time.time() - t0
    print(f"照合時間: {dt*1000:.0f}ms ({len(test_candidates)}件)\n")
    for r in out["results"]:
        q = r["query"]
        print(f"Q: {q['title']} / {q['artist']}")
        if not r["matches"]:
            print("   → マッチなし（候補棄却）")
        for mt in r["matches"]:
            print(f"   → [{mt['confidence']:6s}] {mt['score']:5.1f} {mt['title']} / {mt['artist']} ({mt['song_id']})")
        print()
