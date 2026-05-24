"""社群熱度掃描（公開 API，無需密鑰）。"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import requests

COINGECKO_TRENDING = "https://api.coingecko.com/api/v3/search/trending"
REDDIT_CRYPTO = "https://www.reddit.com/r/CryptoCurrency/hot.json?limit=50"


def fetch_coingecko_trending() -> pd.DataFrame:
    resp = requests.get(COINGECKO_TRENDING, timeout=30)
    resp.raise_for_status()
    coins = resp.json().get("coins", [])
    rows = []
    for rank, item in enumerate(coins, start=1):
        c = item.get("item", {})
        rows.append(
            {
                "rank": rank,
                "symbol": (c.get("symbol") or "").upper(),
                "name": c.get("name"),
                "market_cap_rank": c.get("market_cap_rank"),
                "score": c.get("score", 0),
                "source": "CoinGecko Trending",
            }
        )
    return pd.DataFrame(rows)


def fetch_reddit_hot_mentions() -> pd.DataFrame:
    headers = {"User-Agent": "trading-bot-dashboard/1.0"}
    resp = requests.get(REDDIT_CRYPTO, headers=headers, timeout=30)
    resp.raise_for_status()
    posts = resp.json().get("data", {}).get("children", [])
    rows = []
    for p in posts:
        d = p.get("data", {})
        rows.append(
            {
                "title": d.get("title", "")[:120],
                "score": d.get("score", 0),
                "comments": d.get("num_comments", 0),
                "upvote_ratio": d.get("upvote_ratio", 0),
                "created_utc": datetime.fromtimestamp(
                    d.get("created_utc", 0), tz=timezone.utc
                ),
                "url": f"https://reddit.com{d.get('permalink', '')}",
                "source": "r/CryptoCurrency",
            }
        )
    return pd.DataFrame(rows)


def _extract_symbols_from_text(text: str) -> list[str]:
    import re

    found = re.findall(r"\$([A-Za-z]{2,10})\b", text)
    found += re.findall(r"\b([A-Z]{2,6})USDT\b", text.upper())
    return list(dict.fromkeys(s.upper() for s in found))


def build_hype_report(
    universe_symbols: list[str] | None = None,
) -> pd.DataFrame:
    """
    合併 CoinGecko 熱門與 Reddit 討論，估算「小幣爆量潛力」觀測分數。
    universe_symbols: 例如 ['BTCUSDT','ETHUSDT'] 用於標記是否在 Top100。
    """
    trending = fetch_coingecko_trending()
    reddit = fetch_reddit_hot_mentions()

    mention_counts: dict[str, int] = {}
    for title in reddit.get("title", []):
        for sym in _extract_symbols_from_text(str(title)):
            mention_counts[sym] = mention_counts.get(sym, 0) + 1

    rows = []
    uni_set = {s.replace("/", "").upper() for s in (universe_symbols or [])}

    for _, row in trending.iterrows():
        sym = str(row["symbol"]).upper()
        mcap_rank = row.get("market_cap_rank") or 9999
        reddit_hits = mention_counts.get(sym, 0)
        in_top100 = f"{sym}USDT" in uni_set or sym in uni_set

        # 分數：熱門榜名次 + Reddit 提及 + 小市值加權
        small_cap_bonus = 30 if (mcap_rank and mcap_rank > 200) else 0
        score = (
            max(0, 11 - int(row["rank"])) * 8
            + reddit_hits * 15
            + small_cap_bonus
            + (10 if not in_top100 else 0)
        )
        outlook = "觀察" if score < 40 else ("潛力" if score < 70 else "高關注")

        rows.append(
            {
                "symbol": sym,
                "name": row["name"],
                "coingecko_rank": row["rank"],
                "market_cap_rank": mcap_rank,
                "reddit_mentions": reddit_hits,
                "in_volume_top100": in_top100,
                "hype_score": score,
                "outlook": outlook,
                "note": _outlook_note(mcap_rank, reddit_hits, in_top100),
            }
        )

    df = pd.DataFrame(rows).sort_values("hype_score", ascending=False)
    return df


def _outlook_note(mcap_rank, reddit_hits: int, in_top100: bool) -> str:
    parts = []
    if mcap_rank and mcap_rank > 200:
        parts.append("市值排名偏後段，波動空間大")
    if reddit_hits >= 2:
        parts.append("Reddit 討論升溫")
    if not in_top100:
        parts.append("尚未進入今日成交量 Top100，可能早期")
    elif in_top100:
        parts.append("已在 Top100，流動性較佳")
    return "；".join(parts) if parts else "持續觀察量能與社群熱度"
