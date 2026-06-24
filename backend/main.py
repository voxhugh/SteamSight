# -*- coding: utf-8 -*-
"""
Steam Portal - FastAPI 主程序
提供 REST API + Web Dashboard
"""

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
import uvicorn
import json
import os
from pathlib import Path
from datetime import datetime
from time import time

def _fmt_time(ts: int) -> str:
    """Unix 时间戳 → 人类可读日期（超过一年显示年份）"""
    if not ts:
        return ""
    diff = time() - ts
    if diff < 60:
        return f"{int(diff)}秒前"
    if diff < 3600:
        return f"{int(diff/60)}分钟前"
    if diff < 86400:
        return f"{int(diff/3600)}小时前"
    if diff < 86400 * 30:
        return f"{int(diff/86400)}天前"
    if diff < 86400 * 365:
        return f"{int(diff/86400/30)}个月前"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


# ── 本地模块 ──
from config import HOST, PORT, DATA_DIR, STEAM_API_KEY, STEAM_USERNAME, PROXIES
import steam_client as sc
import steamdb_scraper as sdb
import player_scraper as ps

# ─────────────────────────────────────────────
# FastAPI 初始化
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
STATIC_DIR = WEB_DIR / "static"

app = FastAPI(
    title="Steam Portal",
    description="Steam 游戏数据 + 个人游戏口味分析仪表盘",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ─────────────────────────────────────────────
# 响应模型
# ─────────────────────────────────────────────
class GameResponse(BaseModel):
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
    steamdb: Optional[dict] = None


class PlayerReportResponse(BaseModel):
    identifier: str
    profile: dict = None
    game_count: int = 0
    total_hours: float = 0
    library_stats: dict = None
    gaming_profile: dict = None
    radar: dict = None
    top_games: list = None
    errors: list = None


# ─────────────────────────────────────────────
# API 路由：全局热门游戏
# ─────────────────────────────────────────────
@app.get("/api/trending")
async def get_trending_games(limit: int = Query(30, ge=1, le=100)) -> dict:
    """
    获取当前最热门游戏排行榜
    数据源：steamcharts.com
    策略：stale-while-revalidate（始终返回缓存，后台更新）
    """
    import asyncio, concurrent.futures

    cache_key = f"trending_{limit}"
    cached = sc.cache_get(cache_key)
    is_fresh = sc.cache_is_fresh(cache_key)

    # 始终返回缓存（哪怕过期），快速响应
    if cached:
        result = dict(cached)  # 复制一份避免污染原始缓存
        # 后台刷新：缓存过期 OR 没有缓存时，异步更新
        if not is_fresh:
            async def _refresh():
                try:
                    loop = asyncio.get_event_loop()
                    rows = await loop.run_in_executor(
                        None, lambda: sc.get_top_games_live(limit=limit)
                    )
                    sc.cache_set(cache_key, {
                        "updated_at": datetime.now().isoformat(),
                        "games": rows,
                        "source": "steamcharts.com",
                    })
                except Exception:
                    pass  # 静默失败，不影响前端
            asyncio.create_task(_refresh())
        return result

    # 完全无缓存：同步等待（首屏必须给数据）
    rows = sc.get_top_games_live(limit=limit)
    result = {
        "updated_at": datetime.now().isoformat(),
        "games": rows,
        "source": "steamcharts.com",
    }
    sc.cache_set(cache_key, result)
    return result


@app.get("/api/game/{appid}")
def get_game_detail(appid: int) -> GameResponse:
    """
    获取单个游戏的完整数据
    优先 Steam Store API，降级到 SteamDB
    """
    # Steam Store + API 数据
    game = sc.get_game_info(appid)
    if not game.name:
        raise HTTPException(status_code=404, detail="游戏未找到")

    current = sc.get_current_players(appid)
    game.current_players = current

    # SteamDB 补充数据（可选，降级友好）
    steamdb_data = {}
    try:
        sdb_data = sdb.get_app_page(appid)
        if sdb_data.get("steamdb_status") == "ok":
            steamdb_data = {k: v for k, v in sdb_data.items() if k not in ("appid", "steamdb_status")}
            # 补充峰值
            if not game.peak_24h and "peak_24h" in sdb_data:
                game.peak_24h = sdb_data.get("peak_24h", 0)
    except Exception:
        pass

    return GameResponse(
        appid=game.appid,
        name=game.name,
        current_players=game.current_players,
        peak_24h=game.peak_24h,
        price=game.price,
        is_free=game.is_free,
        metacritic=game.metacritic,
        steam_rating=game.steam_rating,
        reviews_count=game.reviews_count,
        developers=game.developers,
        publishers=game.publishers,
        genres=game.genres,
        release_date=game.release_date,
        steamdb=steamdb_data if steamdb_data else None,
    )


@app.get("/api/games/batch")
def get_games_batch(appids: str = Query(..., description="逗号分隔的 AppID 列表", example="730,1172470,1245620")) -> dict:
    """
    批量获取多个游戏数据
    示例：/api/games/batch?appids=730,1172470,1245620
    """
    id_list = [int(x.strip()) for x in appids.split(",") if x.strip().isdigit()]
    results = []
    for aid in id_list[:50]:  # 最多50个
        try:
            g = sc.get_game_info(aid)
            g.current_players = sc.get_current_players(aid)
            results.append({
                "appid": g.appid,
                "name": g.name,
                "current_players": g.current_players,
                "peak_24h": g.peak_24h,
                "price": g.price,
                "is_free": g.is_free,
                "metacritic": g.metacritic,
                "steam_rating": g.steam_rating,
                "genres": g.genres,
                "developers": g.developers,
            })
        except Exception:
            pass
    return {"games": results, "count": len(results)}


@app.get("/api/search")
def search_games(q: str = Query(..., min_length=1)) -> dict:
    """
    搜索 Steam 游戏（通过 SteamDB 搜索）
    """
    results = sdb.get_trending_page(page=1)
    if not results:
        return {"games": []}
    # 简单关键词过滤
    q_lower = q.lower()
    filtered = [r for r in results if q_lower in r["name"].lower()]
    return {"query": q, "games": filtered[:20]}


# ─────────────────────────────────────────────
# API 路由：玩家数据
# ─────────────────────────────────────────────
@app.get("/api/player/{identifier}", response_model=PlayerReportResponse)
def get_player_report(identifier: str) -> PlayerReportResponse:
    """
    获取玩家完整报告
    identifier: SteamID64 / 自定义URL / 用户名
    """
    if not STEAM_API_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "⚠️ 需要配置 STEAM_API_KEY 才能获取玩家数据\n"
                "请到 https://steamcommunity.com/dev/apikey 免费申请，"
                "然后设置环境变量 STEAM_API_KEY\n"
                "也可以修改 backend/config.py 里的 STEAM_API_KEY"
            )
        )

    report = ps.get_full_player_report(identifier)

    if not report.get("profile"):
        raise HTTPException(
            status_code=404,
            detail=f"找不到玩家: {identifier}\n可能原因：隐私设置 / 用户名不存在 / SteamID 错误\n错误: {report.get('errors', [])}"
        )

    # 序列化 OwnedGame → dict（games 已按最近运行时长降序排列）
    def game_to_dict(g: sc.OwnedGame):
        tier = g.completion_tier if g.achievements_checked else "❓ 未获取"
        return {
            "appid": g.appid,
            "name": g.name,
            "playtime_hours": round(g.playtime_forever / 60, 1),
            "playtime_2weeks_hours": round(g.playtime_2weeks / 60, 1),
            "rt_last_played": g.rt_last_played,
            "rt_last_played_str": _fmt_time(g.rt_last_played) if g.rt_last_played else "从未",
            "completion_tier": tier,
            "achievements_checked": g.achievements_checked,
            "achievements_total": g.achievements_total if g.achievements_checked else None,
            "achievements_completed": g.achievements_completed if g.achievements_checked else None,
            "completion_rate": g.completion_rate if g.achievements_checked else None,
            "tags": g.tags or [],
            "affinity_score": round(
                (g.playtime_forever / 60) * (
                    0.5 + 0.5 * (g.completion_rate or 0) / 100
                ) if g.playtime_forever > 0 and (g.achievements_total or 0) > 0 and (g.completion_rate or 0) > 0
                else (g.playtime_forever / 60) * 0.5
            , 1),
        }

    profile_dict = None
    if report.get("profile"):
        p = report["profile"]
        profile_dict = {
            "steamid": p.steamid,
            "name": p.persona_name,
            "avatar": p.avatar_full_url,
            "level": p.level,
            "member_since": p.member_since,
            "real_name": p.real_name,
            "country": p.country,
            "profile_url": p.profile_url,
            "total_badges": p.total_badges,
            "currently_playing": p.game,
        }

    return PlayerReportResponse(
        identifier=identifier,
        profile=profile_dict,
        game_count=len(report["games"]),
        total_hours=report["library_stats"].get("total_hours", 0),
        library_stats=report["library_stats"],
        gaming_profile=report["gaming_profile"],
        radar=report.get("radar", {}),
        top_games=[game_to_dict(g) for g in report["games"]],
        errors=report["errors"],
    )


@app.get("/api/player/{identifier}/game/{appid}")
def get_player_game_detail(identifier: str, appid: int) -> dict:
    """获取玩家在特定游戏里的详情（成就、时长）"""
    if not STEAM_API_KEY:
        raise HTTPException(status_code=503, detail="需要 STEAM_API_KEY")

    steamid = sc.resolve_vanity(identifier) or identifier

    # 基础游戏信息
    game_info = sc.get_game_info(appid)
    current = sc.get_current_players(appid)
    game_info.current_players = current

    # 玩家成就
    unlocked, total = sc.get_game_achievements(steamid, appid)

    # SteamDB 补充
    steamdb = {}
    try:
        sdb_data = sdb.get_app_page(appid)
        if sdb_data.get("steamdb_status") == "ok":
            steamdb = {k: v for k, v in sdb_data.items()
                       if k not in ("appid", "steamdb_status")}
    except Exception:
        pass

    return {
        "game": {
            "appid": game_info.appid,
            "name": game_info.name,
            "current_players": current,
            "genres": game_info.genres,
            "developers": game_info.developers,
            "steamdb": steamdb,
        },
        "player": {
            "steamid": steamid,
            "playtime": None,  # 需要从 owned_games 里找
            "achievements_unlocked": unlocked,
            "achievements_total": total,
            "achievement_rate": round(unlocked / total * 100, 1) if total else 0,
        }
    }


# ─────────────────────────────────────────────
# API 路由：SteamDB Top
# ─────────────────────────────────────────────
@app.get("/api/steamdb/top")
def get_steamdb_top(limit: int = Query(50, ge=1, le=200)) -> dict:
    """SteamDB Top 游戏（需要 SteamDB Token 才能完整访问）"""
    cached = sc.cache_get(f"steamdb_top_{limit}")
    if cached:
        return cached

    top = sdb.get_top_100()
    result = {
        "updated_at": datetime.now().isoformat(),
        "games": top[:limit],
    }
    sc.cache_set(f"steamdb_top_{limit}", result)
    return result


@app.get("/api/steamdb/app/{appid}")
def get_steamdb_detail(appid: int) -> dict:
    """SteamDB 单游戏完整数据"""
    data = sdb.get_app_page(appid)
    if data.get("steamdb_status") == "failed":
        raise HTTPException(status_code=404, detail="SteamDB 数据获取失败")
    return data


# ─────────────────────────────────────────────
# API 路由：健康检查 / 配置
# ─────────────────────────────────────────────
@app.get("/api/status")
def get_status() -> dict:
    """服务状态"""
    _p = PROXIES()
    return {
        "status": "running",
        "has_api_key": bool(STEAM_API_KEY),
        "has_username": bool(STEAM_USERNAME),
        "username": STEAM_USERNAME or None,
        "proxy_active": _p is not None,
        "proxy_port": int(_p["http"].split(":")[-1]) if _p else None,
        "data_dir": str(DATA_DIR),
        "uptime": datetime.now().isoformat(),
    }


@app.get("/api/config")
def get_config() -> dict:
    """公开配置（不含密钥）"""
    return {
        "api_key_configured": bool(STEAM_API_KEY),
        "username_configured": bool(STEAM_USERNAME),
        "requires_auth_for_player_data": True,
        "get_api_key_url": "https://steamcommunity.com/dev/apikey",
    }


# ─────────────────────────────────────────────
# Web 页面路由
# ─────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index_page():
    """主仪表盘"""
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return HTMLResponse("<h1>Steam Portal Running</h1><p>请访问 /static/index.html 或配置 Web 目录</p>")


@app.get("/profile", response_class=HTMLResponse)
def profile_page():
    """玩家资料页"""
    profile_file = WEB_DIR / "profile.html"
    if profile_file.exists():
        return FileResponse(str(profile_file))
    return HTMLResponse("<h1>Steam Portal - Profile</h1>")


# ─────────────────────────────────────────────
# 启动
# ─────────────────────────────────────────────
def start_server():
    print(f"""
╔══════════════════════════════════════════════╗
║           🎮  Steam Portal  已启动           ║
╠══════════════════════════════════════════════╣
║  Dashboard:  http://localhost:{PORT}         ║
║  API Docs:    http://localhost:{PORT}/docs    ║
║  Profile:     http://localhost:{PORT}/profile ║
╠══════════════════════════════════════════════╣
║  API Key:     {'✅ 已配置' if STEAM_API_KEY else '❌ 未配置（需要 Steam API Key）'}
║  Username:    {'✅ ' + STEAM_USERNAME if STEAM_USERNAME else '❌ 未配置'}
╠══════════════════════════════════════════════╣
║  停止服务：按 Ctrl+C                         ║
╚══════════════════════════════════════════════╝
    """)
    uvicorn.run(app, host=HOST, port=PORT, reload=False, timeout_keep_alive=120)


if __name__ == "__main__":
    start_server()
