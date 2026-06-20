#!/usr/bin/env python3
"""CSV楽曲DB → SQLite インポートスクリプト

照合用CSVを読み込み、正規化済み列(ntitle/nartist)を付与してSQLiteに格納する。
matcher.py はこのDBをロードして使う（CSV直読みより起動が速く、完全一致はSQLインデックスで引ける）。

使い方:
  python3 scripts/build_db.py                                   # data/test_songs_final.csv → data/songs.db
  python3 scripts/build_db.py --csv data/test_songs_30k.csv     # 入力CSVを変更
  python3 scripts/build_db.py --db data/songs_30k.db            # 出力DBを変更
"""
import argparse
import csv
import os
import sqlite3
import sys

# matcher.py の normalize を再利用（照合と完全に同じ正規化を保証）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from matcher import normalize

DDL = """
CREATE TABLE songs (
    song_id      TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    artist       TEXT NOT NULL,
    genre        TEXT,
    release_year TEXT,
    ntitle       TEXT NOT NULL,
    nartist      TEXT NOT NULL
);
CREATE INDEX idx_norm ON songs (ntitle, nartist);
"""


def build(csv_path: str, db_path: str):
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    con.executescript(DDL)

    rows = []
    with open(csv_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append((
                r["song_id"], r["title"], r["artist"],
                r.get("genre"), r.get("release_year"),
                normalize(r["title"]), normalize(r["artist"]),
            ))
    con.executemany(
        "INSERT OR REPLACE INTO songs "
        "(song_id, title, artist, genre, release_year, ntitle, nartist) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    con.close()
    print(f"{csv_path} → {db_path}: {n:,}曲をインポート完了")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/test_songs_final.csv")
    ap.add_argument("--db", default="data/songs.db")
    args = ap.parse_args()
    build(args.csv, args.db)
