# -*- coding: utf-8 -*-
"""
Steam 官方 API 客户端
数据源：steamcommunity.com/dev/apikey 所列的所有公开接口
"""

import requests
import time
import random
import json
from bs4 import BeautifulSoup
import os
from typing import Optional
from dataclasses import dataclass, asdict
from datetime import datetime
from config import STEAM_API_KEY, PROXIES, REQUEST_DELAY, DATA_DIR


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


# ─────────────────────────────────────────────
# 数据模型
# ─────────────────────────────────────────────
@dataclass
class GameInfo:
    appid: int
    name: str
    current_players: int = 0
    peak_24h: int = 0
    price: str = ""
    is_free: bool = False
    metacritic: int = 0
    steam_rating: str = ""
    reviews_count: int = 0
    developers: str = ""
    publishers: str = ""
    genres: str = ""
    release_date: str = ""
    owners_range: str = ""
    average_forever: int = 0      # 平均总游戏时长（分钟）
    average_2weeks: int = 0        # 两周平均时长
    median_forever: int = 0        # 中位总时长
    median_2weeks: int = 0
    steamdb_score: float = 0.0
    tags: dict = None              # {tag: count}

    def __post_init__(self):
        if self.tags is None:
            self.tags = {}


@dataclass
class PlayerInfo:
    steamid: str
    persona_name: str
    avatar_url: str = ""
    avatar_full_url: str = ""
    real_name: str = ""
    city: str = ""
    country: str = ""
    member_since: str = ""
    level: int = 0
    privacy_state: str = ""
    visibility_state: str = ""
    profile_url: str = ""
    bio: str = ""
    game: str = ""
    game_appid: int = 0
    playing_time_2weeks: int = 0   # 分钟
    total_badges: int = 0
    friend_count: int = 0
    groups: list = None

    def __post_init__(self):
        if self.groups is None:
            self.groups = []


@dataclass
class OwnedGame:
    appid: int
    name: str
    playtime_forever: int         # 分钟
    playtime_2weeks: int = 0       # 分钟
    rt_last_played: int = 0        # Unix 时间戳（秒）
    img_icon_url: str = ""
    img_logo_url: str = ""
    has_achievements: bool = False
    achievements_checked: bool = False   # 是否实际检查过成就
    achievements_total: int = 0
    achievements_completed: int = 0
    completion_rate: float = 0.0   # 0~100
    completion_tier: str = ""       # 神级/毕业/深入/浅尝/未动
    tags: list = None              # SteamDB 标签
    genres: list = None            # Steam API 类型
    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.genres is None:
            self.genres = []


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────
def _delay():
    lo, hi = REQUEST_DELAY
    time.sleep(random.uniform(lo, hi))


def _get(key: str, params: dict = None, base_url: str = "https://api.steampowered.com") -> Optional[dict]:
    if not STEAM_API_KEY:
        return None
    params = params or {}
    params["key"] = STEAM_API_KEY
    for attempt in range(3):
        _delay()
        try:
            r = requests.get(
                f"{base_url}/{key}",
                params=params,
                headers=HEADERS,
                proxies=PROXIES(),
                timeout=20,
            )
            if r.status_code == 200:
                return r.json()
            # 429 Too Many Requests → wait longer
            if r.status_code == 429:
                time.sleep(5 + attempt * 3)
                continue
        except Exception:
            pass
    return None


def _http_get(url: str, params: dict = None, base_url: str = None, timeout: int = 20) -> Optional[dict]:
    """带重试的通用 HTTP GET"""
    for attempt in range(3):
        try:
            full_url = f"{base_url}/{url}" if base_url else url
            r = requests.get(full_url, params=params, headers=HEADERS, proxies=PROXIES(), timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(5 + attempt * 3)
                continue
        except Exception:
            pass
    return None


def _store_get(params: dict) -> Optional[dict]:
    """Steam Store API（无需 Key）"""
    for attempt in range(3):
        _delay()
        try:
            r = requests.get(
                "https://store.steampowered.com/api/appdetails",
                params=params,
                headers=HEADERS,
                proxies=PROXIES(),
                timeout=20,
            )
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(5 + attempt * 3)
                continue
        except Exception:
            pass
    return None


def get_game_tags(appid: int) -> list[str]:
    """单个游戏获取 genres（通过 Steam Store API）"""
    try:
        r = requests.get(
            f"https://store.steampowered.com/api/appdetails?appids={appid}&filters=genres",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=6,
            proxies=PROXIES(),
        )
        data = r.json()
        app_data = data.get(str(appid), {}).get("data", {})
        genres = [g["description"] for g in app_data.get("genres", [])]
        return genres
    except Exception:
        return []


def _get_single_genre(appid: int) -> tuple[int, list[str]]:
    """获取单个游戏的 genres"""
    try:
        r = requests.get(
            f"https://store.steampowered.com/api/appdetails?appids={appid}&filters=genres",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=15,
            proxies=PROXIES(),
        )
        if r.status_code != 200:
            return (appid, [])
        data = r.json()
        app_data = data.get(str(appid), {}).get("data", {})
        return (appid, [g["description"] for g in app_data.get("genres", [])])
    except Exception:
        return (appid, [])


def get_batch_genres(appids: list[int]) -> dict[int, list[str]]:
    """
    批量获取多个游戏的 genres（每个游戏单独请求，并行执行）
    Steam Store API 的 appdetails 不支持逗号分隔多 appid，必须逐个请求。
    返回 {appid: [genres...]}
    """
    result = {a: [] for a in appids}
    if not appids:
        return result
    # 并行逐个请求
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_get_single_genre, a): a for a in appids}
        for future in as_completed(futures):
            try:
                aid, genres = future.result(timeout=20)
                result[aid] = genres
            except Exception:
                pass
    return result


# ─────────────────────────────────────────────
# SteamIS ValidatedUserIDs → SteamID 互转
# ─────────────────────────────────────────────
def steamid_to_accountid(steamid: str) -> int:
    """SteamID64 → AccountID"""
    try:
        return int(steamid) - 76561197960265728
    except Exception:
        return 0


def accountid_to_steamid(acid: int) -> str:
    """AccountID → SteamID64"""
    return str(acid + 76561197960265728)


# ─────────────────────────────────────────────
# 核心 API 函数
# ─────────────────────────────────────────────

def get_current_players(appid: int) -> int:
    """当前在线人数"""
    data = _get(
        "ISteamUserStats/GetNumberOfCurrentPlayers/v1/",
        params={"appid": appid},
        base_url="https://api.steampowered.com",
    )
    if data:
        return data.get("response", {}).get("player_count", 0)
    return 0


def get_game_info(appid: int) -> GameInfo:
    """获取游戏综合信息（Store API + ISteamUserStats）"""
    game = GameInfo(appid=appid, name="")

    # Store API
    data = _store_get({"appids": appid, "cc": "cn", "l": "schinese"})
    if data:
        info = data.get(str(appid), {}).get("data", {})
        if info:
            game.name = info.get("name", "")
            game.is_free = info.get("is_free", False)
            price = info.get("price_overview", {})
            game.price = price.get("final_formatted", "Free")
            game.metacritic = info.get("metacritic", {}).get("score", 0)
            game.reviews_count = info.get("recommendations", {}).get("total", 0)
            game.developers = ", ".join(info.get("developers", []))
            game.publishers = ", ".join(info.get("publishers", []))
            game.genres = ", ".join(g["description"] for g in info.get("genres", []))
            game.release_date = info.get("release_date", {}).get("date", "")

    # ISteamUserStats（评分、时长统计）
    stats_data = _get(
        "ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/",
        params={"appid": appid},
        base_url="https://api.steampowered.com",
    )
    if stats_data:
        achievements = stats_data.get("achievementpercentages", {}).get("achievements", [])
        if achievements:
            # 统计好评率（60%+ 视为正面）
            positive = sum(1 for a in achievements if a.get("percent", 0) >= 60)
            total = len(achievements)
            if total > 0:
                pct = positive / total * 100
                if pct >= 90:
                    game.steam_rating = f"压倒性好评 ({pct:.0f}%)"
                elif pct >= 70:
                    game.steam_rating = f"多半好评 ({pct:.0f}%)"
                elif pct >= 40:
                    game.steam_rating = f"褒贬不一 ({pct:.0f}%)"
                else:
                    game.steam_rating = f"差评居多 ({pct:.0f}%)"

    return game


def get_player_profile(steamid_or_customurl: str) -> Optional[PlayerInfo]:
    """获取玩家公开资料（通过 SteamID64 或自定义 URL）"""
    # 先用 VanirResolver 解析自定义 URL → SteamID64
    resolved_id = resolve_vanity(steamid_or_customurl)
    if not resolved_id:
        # 尝试直接当作 SteamID64
        resolved_id = steamid_or_customurl

    # 基础信息
    data = _get(
        "ISteamUser/GetPlayerSummaries/v2/",
        params={"steamids": resolved_id},
        base_url="https://api.steampowered.com",
    )
    if not data:
        return None

    players = data.get("response", {}).get("players", [])
    if not players:
        return None

    p = players[0]
    player = PlayerInfo(
        steamid=p.get("steamid", ""),
        persona_name=p.get("personaname", ""),
        avatar_url=p.get("avatar", ""),
        avatar_full_url=p.get("avatarfull", ""),
        real_name=p.get("realname", ""),
        city=p.get("loccityid", ""),
        country=p.get("loccountrycode", ""),
        member_since=datetime.fromtimestamp(p.get("timecreated", 0)).strftime("%Y-%m-%d")
        if p.get("timecreated") else "",
        level=0,  # 下面单独获取
        privacy_state=p.get("communityvisibilitystate", ""),
        profile_url=p.get("profileurl", ""),
        game=p.get("gameextrainfo", "") or "",
        game_appid=int(p.get("gameid", 0) or 0),
    )

    # 等级
    level_data = _get(
        "IPlayerService/GetSteamLevel/v1/",
        params={"steamid": resolved_id},
        base_url="https://api.steampowered.com",
    )
    if level_data:
        player.level = level_data.get("response", {}).get("player_level", 0)

    # Badge / 成就数（粗估）
    badges_data = _get(
        "IPlayerService/GetBadges/v1/",
        params={"steamid": resolved_id},
        base_url="https://api.steampowered.com",
    )
    if badges_data:
        player.total_badges = len(badges_data.get("response", {}).get("badges", []))

    return player


def resolve_vanity(vanityurl: str) -> Optional[str]:
    """将自定义 URL / 用户名解析为 SteamID64"""
    # 如果已经是纯数字 SteamID64，直接返回
    if vanityurl.isdigit() and len(vanityurl) > 15:
        return vanityurl

    data = _get(
        "ISteamUser/ResolveVanityURL/v1/",
        params={"vanityurl": vanityurl},
        base_url="https://api.steampowered.com",
    )
    if data:
        resp = data.get("response", {})
        if resp.get("success") == 1:
            return resp.get("steamid")
    return None


_OWNED_GAMES_CACHE = {}  # steamid -> (games_list, timestamp)
_OWNED_CACHE_TTL = 6 * 3600  # 6 小时缓存


def get_owned_games(steamid: str) -> list[OwnedGame]:
    """获取玩家拥有的游戏列表（含 6 小时缓存）"""
    from time import time as _time
    # 缓存命中
    if steamid in _OWNED_GAMES_CACHE:
        cached, ts = _OWNED_GAMES_CACHE[steamid]
        if _time() - ts < _OWNED_CACHE_TTL:
            return cached

    data = _get(
        "IPlayerService/GetOwnedGames/v1/",
        params={
            "steamid": steamid,
            "include_appinfo": 1,
            "include_played_free_games": 1,
            "skip_unvetted_apps": 0,
        },
        base_url="https://api.steampowered.com",
    )
    games = []
    if not data:
        return games

    for g in data.get("response", {}).get("games", []):
        game = OwnedGame(
            appid=g.get("appid", 0),
            name=g.get("name", "Unknown"),
            playtime_forever=g.get("playtime_forever", 0),
            playtime_2weeks=g.get("playtime_2weeks") or 0,
            rt_last_played=g.get("rtime_last_played", 0) or 0,
            img_icon_url=g.get("img_icon_url", ""),
            img_logo_url=g.get("img_logo_url", ""),
        )
        # 时长等级
        hours = game.playtime_forever / 60
        if hours == 0:
            game.completion_tier = "未游玩"
        elif hours < 10:
            game.completion_tier = "浅尝辄止"
        elif hours < 50:
            game.completion_tier = "深入游玩"
        elif hours < 200:
            game.completion_tier = "重度玩家"
        else:
            game.completion_tier = "毕业/神级"
        games.append(game)

    result = sorted(games, key=lambda x: x.playtime_forever, reverse=True)
    # 写缓存
    from time import time as _t
    _OWNED_GAMES_CACHE[steamid] = (result, _t())
    return result


def get_game_achievements(steamid: str, appid: int) -> tuple[int, int]:
    """获取玩家某游戏的成就：已解锁 / 总数（带重试）
    
    Returns:
        (unlocked, total)  游戏有成就统计
        (-1, 0)          游戏无成就统计（no stats）
        (0, 0)           请求失败
    """
    for attempt in range(4):
        _delay()
        try:
            data = _get(
                "ISteamUserStats/GetPlayerAchievements/v1/",
                params={"steamid": steamid, "appid": appid},
                base_url="https://api.steampowered.com",
            )
            if not data:
                continue
            playerstats = data.get("playerstats", {})
            # 游戏无成就统计（API 返回 success=false）
            if not playerstats.get("success", True):
                return -1, 0
            achievements = playerstats.get("achievements", [])
            total = len(achievements)
            unlocked = sum(1 for a in achievements if a.get("achieved", 0) == 1)
            return unlocked, total
        except Exception:
            pass
    return 0, 0


def get_top_games_live(limit=30) -> list[dict]:
    """通过 SteamCharts API 获取当前最热门游戏（实时在线人数）"""
    # SteamCharts 会爬取 SteamDB 的数据并公开
    try:
        r = requests.get(
            "https://steamcharts.com/top/p.1",
            headers=HEADERS,
            proxies=PROXIES(),
            timeout=20,
        )
        if r.status_code != 200:
            return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        rows = []
        for tr in soup.select("table#top-games tr")[:limit]:
            cols = tr.select("td")
            if len(cols) < 5:
                continue
            a_tag = cols[1].select_one("a")
            if not a_tag:
                continue
            href = a_tag.get("href", "")
            # /app/12345/GameName
            m = href.strip("/").split("/")
            appid = int(m[-1]) if m else 0
            rows.append({
                "rank": cols[0].get_text(strip=True),
                "appid": appid,
                "name": a_tag.get_text(strip=True),
                "current": cols[2].get_text(strip=True),
                "peak_24h": cols[3].get_text(strip=True),
                "peak_all": cols[4].get_text(strip=True),
                "hours_30d": cols[5].get_text(strip=True),
            })
        return rows
    except Exception as e:
        return []


def get_library_stats(games: list[OwnedGame]) -> dict:
    """对游戏库做整体统计分析"""
    total = len(games)
    total_hours = sum(g.playtime_forever for g in games) / 60
    recent_hours = sum(g.playtime_2weeks for g in games) / 60
    games_never_played = sum(1 for g in games if g.playtime_forever == 0)
    games_completed_estimate = sum(1 for g in games if g.completion_tier in ("毕业/神级", "重度玩家"))
    completion_games = [g for g in games if g.playtime_forever > 0]
    avg_hours_per_played = total_hours / len(completion_games) if completion_games else 0

    # 估算总 Steam 库价值
    # 按均价 $5 估算（保守）
    estimated_value_usd = total * 5

    # 游玩强度分布
    tier_counts = {}
    for g in games:
        tier_counts[g.completion_tier] = tier_counts.get(g.completion_tier, 0) + 1

    # Top 10 游戏：两层排序（同 report["games"]）
    played_games = [g for g in games if g.playtime_forever > 0]
    played_games.sort(key=lambda g: (
        1 if g.playtime_2weeks > 0 else 0,
        g.playtime_2weeks if g.playtime_2weeks > 0 else 0,
        g.rt_last_played if g.playtime_2weeks == 0 else 0
    ), reverse=True)
    top10 = [
        {
            "name": g.name,
            "hours": round(g.playtime_forever / 60, 1),
            "rt_hours": round(g.playtime_2weeks / 60, 1),
            "tier": g.completion_tier,
            "appid": g.appid,
            "last_played": g.rt_last_played,
            # affinity_score: 近似计算（精确值需要 achievement_data）
            "affinity_score": round((g.playtime_forever / 60) * 0.5, 1),
        }
        for g in played_games[:10]
    ]

    # 游玩时间段（最近两周运行时长倒序）
    recently_played = [g for g in games if g.playtime_2weeks > 0]
    recently_played.sort(key=lambda x: x.playtime_2weeks, reverse=True)

    return {
        "total_games": total,
        "total_hours": round(total_hours, 1),
        "recent_30d_hours": round(recent_hours, 1),
        "never_played": games_never_played,
        "never_played_pct": round(games_never_played / total * 100, 1) if total else 0,
        "completed_games": games_completed_estimate,
        "avg_hours_per_played": round(avg_hours_per_played, 1),
        "estimated_value_usd": estimated_value_usd,
        "tier_distribution": tier_counts,
        "top10": top10,
        "recently_played": [
            {"name": g.name, "hours_2w": round(g.playtime_2weeks / 60, 1)}
            for g in recently_played[:5]
        ],
    }


# ─────────────────────────────────────────────
# 缓存层（支持 stale-while-revalidate 模式）
# ─────────────────────────────────────────────
_cache = {}


def cache_get(key: str) -> Optional[dict]:
    """返回缓存数据（无论是否过期），None 表示无缓存"""
    if key in _cache:
        return _cache[key]["data"]
    return None


def cache_set(key: str, data, ttl: int = 900):
    """ttl: 缓存有效期秒数（默认15分钟）"""
    from time import time
    _cache[key] = {"data": data, "ts": time(), "ttl": ttl}


def cache_is_fresh(key: str) -> bool:
    """判断缓存是否仍在有效期内"""
    if key not in _cache:
        return False
    from time import time
    return time() - _cache[key]["ts"] < _cache[key].get("ttl", 900)


def cache_age_seconds(key: str) -> float:
    """返回缓存已存在秒数（-1 表示不存在）"""
    if key not in _cache:
        return -1
    from time import time
    return time() - _cache[key]["ts"]
