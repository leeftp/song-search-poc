"""ムード評価の分類定義と採点関数

- 年代(era): クエリの年代指定 → 年範囲。曲の release_year が範囲内かで客観採点。
- ジャンル(genre): 期待ジャンル → DBの genre 値の許容集合。別名を吸収して採点。
- ムード(mood): 外部真値が無いため LLM-judge で代理採点（採点ロジックは mood_eval 側）。

年代・ジャンルは自前DB(songs.db)の列をそのまま真値に使う（Last.fmはJP曲で機能しないため）。
"""

# --- 年代: ラベル → (開始年, 終了年) 包含 ---
ERA_RANGES = {
    "1950s": (1950, 1959), "1960s": (1960, 1969), "1970s": (1970, 1979),
    "1980s": (1980, 1989), "1990s": (1990, 1999), "2000s": (2000, 2009),
    "2010s": (2010, 2019), "2020s": (2020, 2029),
    # 和暦・口語
    "昭和": (1926, 1989), "平成": (1989, 2019), "令和": (2019, 2030),
}
# 「90年代」「90s」等の表記ゆれを ERA_RANGES のキーへ
_ERA_ALIASES = {
    "50年代": "1950s", "60年代": "1960s", "70年代": "1970s", "80年代": "1980s",
    "90年代": "1990s", "00年代": "2000s", "10年代": "2010s", "20年代": "2020s",
    "50s": "1950s", "60s": "1960s", "70s": "1970s", "80s": "1980s",
    "90s": "1990s", "00s": "2000s", "10s": "2010s", "20s": "2020s",
}

# --- ジャンル: 期待ラベル → DB genre の許容集合（別名・近縁を吸収） ---
GENRE_ALIASES = {
    "J-Pop": {"J-Pop", "ポップ", "歌謡曲"},
    "ポップ": {"ポップ", "J-Pop"},
    "ロック": {"ロック", "オルタナティブ", "メタル"},
    "アニメ": {"アニメ"},
    "演歌": {"演歌", "歌謡曲"},
    "K-Pop": {"K-Pop"},
    "ヒップホップ": {"ヒップホップ／ラップ"},
    "R&B": {"R&B／ソウル"},
    "ダンス": {"ダンス", "エレクトロニック"},
    "サウンドトラック": {"サウンドトラック"},
}


def era_to_range(era: str):
    """年代ラベル(表記ゆれ含む)を (開始年, 終了年) に解決。未知なら None。"""
    if not era:
        return None
    key = era if era in ERA_RANGES else _ERA_ALIASES.get(era)
    return ERA_RANGES.get(key) if key else None


def era_match(year, era: str) -> bool:
    """曲の release_year が期待年代の範囲内か。"""
    rng = era_to_range(era)
    if rng is None or year in (None, ""):
        return False
    try:
        y = int(str(year)[:4])
    except (ValueError, TypeError):
        return False
    return rng[0] <= y <= rng[1]


def genre_match(db_genre: str, expected: str) -> bool:
    """曲のDBジャンルが期待ジャンルに合致するか（別名集合で判定）。"""
    if not db_genre or not expected:
        return False
    allowed = GENRE_ALIASES.get(expected, {expected})
    return db_genre in allowed
