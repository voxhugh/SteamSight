# -*- coding: utf-8 -*-
"""
SteamDB 爬虫 - 获取更丰富的游戏数据
注意：SteamDB 有强反爬，失败时自动降级到 Steam API 数据
"""

import requests
import time
import random
import re
import json
from typing import Optional
from bs4 import BeautifulSoup
from config import PROXIES, REQUEST_DELAY, STEAMDB_TOKEN


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://steamdb.info/",
}

COOKIES = {}
if STEAMDB_TOKEN:
    COOKIES["sgdb_token"] = STEAMDB_TOKEN


# ─────────────────────────────────────────────
# 轻量标签获取
# ─────────────────────────────────────────────

def get_game_tags(appid: int) -> list[str]:
    """
    快速获取游戏类型标签（通过 Steam Store API）
    返回：['Action', 'RPG', 'Open World', ...]
    """
    try:
        import requests
        r = requests.get(
            f"https://store.steampowered.com/api/appdetails?appids={appid}&filters=genres",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=10,
            proxies=PROXIES or None,
        )
        data = r.json()
        app_data = data.get(str(appid), {}).get("data", {})
        genres = [g["description"] for g in app_data.get("genres", [])]
        return genres
    except Exception:
        return []


def _delay():
    lo, hi = REQUEST_DELAY
    time.sleep(random.uniform(lo, hi))


def _get(url: str, retry=2, timeout=20) -> Optional[BeautifulSoup]:
    for attempt in range(retry):
        try:
            _delay()
            r = requests.get(
                url,
                headers=HEADERS,
                cookies=COOKIES,
                proxies=PROXIES,
                timeout=timeout,
            )
            if r.status_code in (200, 404):
                return BeautifulSoup(r.text, "html.parser")
        except Exception:
            pass
        time.sleep(2 ** attempt)
    return None


def _parse_number(text: str) -> int:
    """解析带 K/M 的数字"""
    if not text:
        return 0
    text = text.strip().upper().replace(",", "").replace(" ", "")
    for pattern, multiplier in [("M", 1_000_000), ("K", 1_000)]:
        m = re.search(rf"([\d.]+)\s*{pattern}", text)
        if m:
            return int(float(m.group(1)) * multiplier)
    m = re.search(r"([\d,]+)", text)
    if m:
        return int(m.group(1).replace(",", ""))
    return 0


def _get_json(url: str, retry=2) -> Optional[dict]:
    for attempt in range(retry):
        try:
            _delay()
            r = requests.get(
                url,
                headers={**HEADERS, "Accept": "application/json"},
                cookies=COOKIES,
                proxies=PROXIES,
                timeout=15,
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(2 ** attempt)
    return None


def get_app_page(appid: int) -> dict:
    """
    获取单个游戏在 SteamDB 的完整数据
    返回字段：
      - followers, players_count
      - 30d_peak, alltime_peak
      - 30d_avg, 30d_gain
      - price (current / original / discount)
      - tags / demographics
      - developer, publisher, release
      - supported languages, achievements count
      - score, score rank
      - linked apps (DLCs)
    """
    result = {"appid": appid, "steamdb_status": "ok"}
    soup = _get(f"https://steamdb.info/app/{appid}/")
    if not soup:
        result["steamdb_status"] = "failed"
        return result

    # ── 游戏名 ──
    title = soup.select_one("h4[data-appid], h2.page-title")
    if title:
        result["name"] = title.get_text(strip=True)

    # ── 关键数据行（表格解析） ──
    for tr in soup.select("table.app-details-table tr, tr[data-cy-app-row]"):
        label_td = tr.select_one("td:first-child, th")
        val_td = label_td.find_next_sibling() if label_td else None
        if not label_td or not val_td:
            continue
        label = label_td.get_text(strip=True).lower()
        val = val_td.get_text(strip=True)

        if any(k in label for k in ("followers", "tracking")):
            result["followers"] = _parse_number(val)
        elif "players" in label and "peak" not in label and "chart" not in label:
            result["players_tracked"] = _parse_number(val)
        elif "30-day peak" in label:
            result["peak_30d"] = _parse_number(val)
        elif "all-time peak" in label:
            result["peak_alltime"] = _parse_number(val)
        elif "average players" in label:
            result["avg_30d"] = _parse_number(val)
        elif "gain" in label or "change" in label:
            result["change_30d"] = val
        elif "price" in label and "discount" not in label:
            result["price_current"] = val
        elif "original" in label and "price" in label:
            result["price_original"] = val
        elif "discount" in label:
            result["discount_pct"] = val
        elif "release date" in label:
            result["release_date"] = val
        elif "developer" in label:
            devs = [a.get_text(strip=True) for a in val_td.select("a")]
            result["developers"] = ", ".join(devs)
        elif "publisher" in label:
            pubs = [a.get_text(strip=True) for a in val_td.select("a")]
            result["publishers"] = ", ".join(pubs)
        elif "achievements" in label:
            result["achievements_total"] = _parse_number(val)
        elif "languages" in label:
            result["languages"] = val
        elif "score" in label:
            score_rank = val_td.select_one(".score, .score-rank")
            result["steamdb_score"] = val
            result["score_rank"] = score_rank.get_text(strip=True) if score_rank else ""

    # ── Rating Box ──
    rating_tag = soup.select_one(
        ".ccusageneutral, .ccusagegood, .ccusagebad, "
        ".steamdb-rating-good, .steamdb-rating-bad"
    )
    if rating_tag:
        rating_text = rating_tag.get_text(strip=True)
        if rating_text:
            result["steamdb_rating"] = rating_text

    # ── Tags（标签云） ──
    tags = {}
    for tag_el in soup.select(".tag, .app-tag"):
        tag_text = tag_el.get_text(strip=True)
        tag_count = tag_el.get("data-count", "")
        if tag_text:
            tags[tag_text] = int(tag_count) if tag_count.isdigit() else 1
    if tags:
        result["tags"] = tags

    # ── 30天玩家数据图 ──
    graph_data = _get_json(f"https://steamdb.info/api/GetGraphData/?appid={appid}&type=player")
    if graph_data:
        try:
            players = graph_data.get("data", {}).get("players", [])
            if players:
                result["graph_24h"] = players
                result["peak_24h"] = max(players)
                result["avg_24h"] = int(sum(players) / len(players))
        except Exception:
            pass

    # ── 历史峰值 ──
    graph_avg = _get_json(f"https://steamdb.info/api/GetGraphData/?appid={appid}&type=avg")
    if graph_avg:
        try:
            avgs = graph_avg.get("data", {}).get("average", [])
            if avgs:
                result["avg_alltime"] = int(sum(avgs) / len(avgs))
        except Exception:
            pass

    # ── Price History ──
    price_hist = _get_json(f"https://steamdb.info/api/GetPriceHistory/?appid={appid}")
    if price_hist:
        result["price_history"] = price_hist.get("data", [])

    # ── DLC ──
    dlc_list = []
    for dlc_row in soup.select(".dlc-row, tr[data-dlc]"):
        dlc_a = dlc_row.select_one("a[href*='/app/']")
        if dlc_a:
            href = dlc_a.get("href", "")
            m = re.search(r"/app/(\d+)", href)
            if m:
                dlc_list.append({
                    "appid": int(m.group(1)),
                    "name": dlc_a.get_text(strip=True),
                })
    result["dlc_count"] = len(dlc_list)
    result["dlcs"] = dlc_list[:10]  # 截取前10个

    result["steamdb_url"] = f"https://steamdb.info/app/{appid}/"
    return result


def get_trending_page(page=1) -> list[dict]:
    """SteamDB 趋势页，获取当前热门游戏"""
    url = f"https://steamdb.info/Graphs/?page={page}"
    soup = _get(url)
    if not soup:
        return []

    results = []
    for row in soup.select("tr.app-row, .tablesorter tr"):
        appid_tag = row.select_one("a[href*='/app/']")
        if not appid_tag:
            continue
        href = appid_tag.get("href", "")
        m = re.search(r"/app/(\d+)", href)
        if not m:
            continue
        appid = int(m.group(1))
        name = appid_tag.get_text(strip=True)

        # 尝试找当前人数
        current_col = row.select_one(".app-numbers-current, .current")
        current = current_col.get_text(strip=True) if current_col else "0"

        results.append({
            "appid": appid,
            "name": name,
            "current_players": _parse_number(current),
        })
    return results


def get_top_100() -> list[dict]:
    """SteamDB Top 100 游戏（通过 Charts 页面）"""
    url = "https://steamdb.info/charts/"
    soup = _get(url)
    if not soup:
        return []

    results = []
    # 解析表格行
    for tr in soup.select("table tbody tr"):
        cols = tr.select("td")
        if len(cols) < 4:
            continue
        a_tag = cols[1].select_one("a[href*='/app/']")
        if not a_tag:
            continue
        href = a_tag.get("href", "")
        m = re.search(r"/app/(\d+)", href)
        if not m:
            continue
        appid = int(m.group(1))
        results.append({
            "appid": appid,
            "name": a_tag.get_text(strip=True),
            "current": _parse_number(cols[2].get_text(strip=True)),
            "peak_24h": _parse_number(cols[3].get_text(strip=True)),
            "peak_all": _parse_number(cols[4].get_text(strip=True) if len(cols) > 4 else "0"),
        })
        if len(results) >= 100:
            break
    return results
