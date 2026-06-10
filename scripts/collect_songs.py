#!/usr/bin/env python3
"""iTunes Search APIから実在楽曲メタデータを収集してCSV化する"""
import csv
import json
import time
import unicodedata
import urllib.parse
import urllib.request

ARTISTS_FILE = "/home/claude/artists.txt"
OUT_CSV = "/home/claude/test_songs.csv"
LIMIT = 200
SLEEP = 0.6

def fetch(artist: str):
    q = urllib.parse.quote(artist)
    url = (f"https://itunes.apple.com/search?term={q}"
           f"&entity=song&country=JP&limit={LIMIT}")
    req = urllib.request.Request(url, headers={"User-Agent": "poc-test-data/1.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r).get("results", [])
        except Exception as e:
            wait = 5 * (attempt + 1)
            print(f"  retry {artist}: {e} (wait {wait}s)")
            time.sleep(wait)
    return []

def norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").lower().strip()

def main():
    with open(ARTISTS_FILE, encoding="utf-8") as f:
        artists = [a.strip() for a in f if a.strip()]
    artists = list(dict.fromkeys(artists))
    print(f"seed artists: {len(artists)}")

    seen = set()
    rows = []
    for i, artist in enumerate(artists, 1):
        results = fetch(artist)
        added = 0
        for r in results:
            title = r.get("trackName")
            name = r.get("artistName")
            if not title or not name:
                continue
            key = (norm(title), norm(name))
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "title": title,
                "artist": name,
                "genre": r.get("primaryGenreName", ""),
                "release_year": str(r.get("releaseDate", ""))[:4],
            })
            added += 1
        if i % 20 == 0 or added == 0:
            print(f"[{i}/{len(artists)}] {artist}: +{added} (total {len(rows)})")
        time.sleep(SLEEP)

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["title", "artist", "genre", "release_year"])
        w.writeheader()
        w.writerows(rows)
    print(f"done: {len(rows)} songs -> {OUT_CSV}")

if __name__ == "__main__":
    main()
