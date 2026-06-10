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
import re
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
    def __init__(self, csv_path: str):
        self.songs = []
        with open(csv_path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                r["_ntitle"] = normalize(r["title"])
                r["_nartist"] = normalize(r["artist"])
                self.songs.append(r)
        # 完全一致用インデックス
        self.exact = {}
        for s in self.songs:
            self.exact.setdefault((s["_ntitle"], s["_nartist"]), s)

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

    m = SongMatcher("/home/claude/test_songs_final.csv")
    print(f"DB: {len(m.songs)}曲ロード完了")

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
