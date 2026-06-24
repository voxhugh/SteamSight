# -*- coding: utf-8 -*-
"""
玩家资料爬虫 - 获取玩家详细数据
支持：公开资料、游戏时长、成就完成度、游戏口味分析
"""

import time
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from bs4 import BeautifulSoup
from config import PROXIES, REQUEST_DELAY, STEAM_API_KEY
from steam_client import (
    get_player_profile, resolve_vanity,
    get_owned_games, get_game_achievements,
    get_library_stats, get_batch_genres,
)


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json, text/html, */*",
}

_MAX_ACHIEVEMENT_GAMES = 60   # 最多抓多少游戏的成就
_MAX_WORKERS = 16            # 并行抓取线程数（Steam API 延迟高，多一些并发）


def _delay():
    lo, hi = REQUEST_DELAY
    time.sleep(random.uniform(lo, hi))


def _fetch_achievement_for_game(steamid: str, g: "OwnedGame") -> dict:
    """单游戏成就抓取（供并行调用）"""
    try:
        unlocked, total = get_game_achievements(steamid, g.appid)
        # 游戏无成就统计（Steam API 返回 success=false）
        if total == -1:
            return {"appid": g.appid, "achievements_total": 0, "achievements_completed": 0,
                    "completion_rate": 0.0, "completion_tier": "❌ 无成就"}
        pct = round(unlocked / total * 100, 1) if total > 0 else 0.0
        # 标签修正：100% → 🏆 全成就，90-99% → ⭐ 几乎全成就
        if pct >= 100:
            tier = "🏆 全成就"
        elif pct >= 90:
            tier = "⭐ 几乎全成就"
        elif pct >= 60:
            tier = "🌟 大部分完成"
        elif pct >= 30:
            tier = "🌙 部分完成"
        elif pct > 0:
            tier = "🌱 刚开始"
        else:
            tier = "❌ 零成就"
        return {
            "appid": g.appid,
            "achievements_total": total,
            "achievements_completed": unlocked,
            "completion_rate": pct,
            "completion_tier": tier,
        }
    except Exception:
        return {"appid": g.appid, "achievements_total": 0, "achievements_completed": 0,
                "completion_rate": 0.0, "completion_tier": "❓ 未获取"}


# 标签改用批量接口（见 get_batch_genres）


def _completion_rate_tier(pct: float) -> str:
    if pct >= 100:  return "🏆 全成就"
    if pct >= 90:   return "⭐ 几乎全成就"
    if pct >= 60:   return "🌟 大部分完成"
    if pct >= 30:   return "🌙 部分完成"
    if pct > 0:     return "🌱 刚开始"
    return "❌ 零成就"


# ─────────────────────────────────────────────
# 玩家数据整合
# ─────────────────────────────────────────────

def get_full_player_report(identifier: str) -> dict:
    """
    输入：SteamID64 / 自定义URL / 用户名
    输出：完整玩家报告（每次抓取结果缓存 15 分钟）
    """
    from steam_client import cache_get, cache_set
    cache_key = f"player_report_{identifier}"
    cached = cache_get(cache_key)
    if cached:
        return cached
    report = {
        "identifier": identifier,
        "profile": None,
        "games": [],
        "game_stats": {},
        "library_stats": {},
        "gaming_profile": {},
        "radar": {},
        "errors": [],
    }

    # 解析 SteamID
    steamid = resolve_vanity(identifier)
    if not steamid:
        steamid = identifier

    # 1. 基本资料
    try:
        report["profile"] = get_player_profile(steamid)
    except Exception as e:
        report["errors"].append(f"profile: {e}")

    if not report["profile"]:
        report["errors"].append("无法获取玩家资料，请检查 SteamID 或用户名是否正确")
        return report

    # 2. 游戏库
    try:
        report["games"] = get_owned_games(steamid)
    except Exception as e:
        report["errors"].append(f"games: {e}")

    if not report["games"]:
        report["errors"].append("游戏库为空或无法获取")
        return report

    # 3. 并行抓取成就（按「最近活跃」排序选前 N 款有游玩的游戏）
    #    和最终游戏表格的排序逻辑保持一致：两周有运行的排前，按 playtime_2weeks 降序
    #    然后按 rt_last_played 降序
    played_games = [g for g in report["games"] if g.playtime_forever > 0]
    played_games.sort(key=lambda g: (
        1 if g.playtime_2weeks > 0 else 0,
        g.playtime_2weeks if g.playtime_2weeks > 0 else 0,
        g.rt_last_played if g.playtime_2weeks == 0 else 0
    ), reverse=True)
    # 全库成就扫描：所有有游玩的游戏逐一检查成就（不切片）
    # 主要目的是检测全成就游戏，不参与排序
    achievement_games = played_games  # 全量扫描
    print(f"[SCRAPER] Achievement games: {len(achievement_games)} (full library)", flush=True)
    ach_map = {}
    if achievement_games:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {pool.submit(_fetch_achievement_for_game, steamid, g): g for g in achievement_games}
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=60)
                    print(f"[SCRAPER] Got {result['appid']}: {result['completion_tier']}", flush=True)
                    ach_map[result["appid"]] = result
                except Exception as e:
                    g = futures[future]
                    print(f"[SCRAPER] Error for {g.appid}: {e}", flush=True)
                    pass

    print(f"[SCRAPER] ach_map size: {len(ach_map)}, keys: {list(ach_map.keys())[:5]}...", flush=True)
    updated = 0
    for g in report["games"]:
        if g.appid in ach_map:
            r = ach_map[g.appid]
            g.achievements_total = r["achievements_total"]
            g.achievements_completed = r["achievements_completed"]
            g.completion_rate = r["completion_rate"]
            g.completion_tier = r["completion_tier"]
            g.achievements_checked = True
            updated += 1
    print(f"[SCRAPER] Merged achievements for {updated} games", flush=True)

    # 4. 全库标签获取（所有有游玩的游戏，get_batch_genres 内部已并行）
    tag_games = [g for g in report["games"] if g.playtime_forever > 0]
    tag_map = {}
    if tag_games:
        all_ids = [g.appid for g in tag_games]
        tag_map = get_batch_genres(all_ids)

    # 合并标签数据
    for g in report["games"]:
        if g.appid in tag_map:
            g.tags = tag_map[g.appid]

    # 5. 游戏库统计
    try:
        report["library_stats"] = get_library_stats(report["games"])
    except Exception as e:
        report["errors"].append(f"stats: {e}")

    # 5b. 用精确成就数据更新 library_stats.top10 的 affinity_score
    for entry in report["library_stats"].get("top10", []):
        appid = entry.get("appid")
        if appid and appid in ach_map:
            r = ach_map[appid]
            total = r.get("achievements_total") or 1
            completed = r.get("achievements_completed") or 0
            rate = completed / total * 100 if total > 0 else 0
            entry["affinity_score"] = round(entry["hours"] * ((rate / 100) ** 1.5), 1)

    # 6. 游戏口味画像（增强版，含雷达维度）
    report["gaming_profile"] = _build_gaming_profile(report["games"], report["library_stats"])

    # 7. 雷达图数据（多时段多维度）
    report["radar"] = _build_radar(report["games"], report["library_stats"])

    # 8. 三阶段智能排序
    #    第一阶段：两周内有运行的 → 按 playtime_2weeks 降序（最近活跃的排前）
    #    第二阶段：两周无运行 → 按 rt_last_played 降序（最近玩过的排前）
    #    第三阶段：底部追加全成就归档游戏（玩家已通关不常打开但仍值得展示的）
    _100pct_ids = set()
    for g in report["games"]:
        if (g.achievements_total or 0) > 0 and (g.completion_rate or 0) >= 100:
            _100pct_ids.add(g.appid)
    
    def _composite_sort(g):
        is_100 = g.appid in _100pct_ids
        if g.playtime_2weeks > 0:
            return (is_100, 0, -g.playtime_2weeks, 0)
        else:
            return (is_100, 1, 0, -g.rt_last_played)
    report["games"].sort(key=_composite_sort)

    # 写入缓存（15 分钟有效期）
    cache_set(cache_key, report, ttl=900)

    return report


# ─────────────────────────────────────────────
# 游戏口味分析（重构版）：每款游戏只分配一个主类型
# ─────────────────────────────────────────────

# ===== 扩展门类体系 =====
# 名称关键词 → 主类型（按优先级降序，先匹配的优先）
# 每款游戏只属于一个门类，高时长游戏不会污染其他门类
_GENRE_RULES = [
    # ── 魂系（仅 FromSoftware 出品） ──
    ("魂系", r"dark\s*soul|elden\s*ring|sekiro|bloodborne|demon'?s?\s*soul"),
    # ── JRPG（日式角色扮演）──
    ("JRPG", r"persona\s*\d|danganronpa|trails\s*of|tales\s*of|atelier|"
     r"final\s*fantasy|dragon\s*quest|ys\s*\d|ni\s*no\s*kuni|"
     r"bravely|octopath|triangle\s*strategy|fire\s*emblem|"
     r"falcom|kiseki|blue\s*archive|honkai|genshin|star\s*rail|"
     r"fate/samurai|fate\\/"),
    # ── 视觉小说 ──
    ("视觉小说", r"nekopara|fata\s*morgana|umineko|higurashi|"
     r"steins\.gate|clannad|planetarian|symphonic\s*rain|"
     r"harmonia|katawa|school\s*days|white\s*album|muv\.luv|"
     r"fate/stay|happy\s*guy|saya\s*no|ever17|remember11|"
     r"eden\*|ef -|sharin|symphonic"),
    # ── CRPG/WRPG（欧美角色扮演）──
    ("CRPG/WRPG", r"divinity|baldur'?s?\s*gate|pathfinder|"
     r"pillars\s*of\s*eternity|wasteland|disco\s*elysium|"
     r"torment|fallout\s*[12]|planescape|icewind|"
     r"the\s*witcher|elder\s*scroll|skyrim"),
    # ── ARPG/共斗 ──
    ("ARPG/共斗", r"monster\s*hunter|god\s*eater|toukiden|"
     r"wild\s*hearts|dauntless|diablo|path\s*of\s*exile|"
     r"torchlight|grim\s*dawn|borderlands|warframe|destiny|"
     r"nioh\b|code\s*vein|mortal\s*shell|thymesia|lies\s*of\s*p|"
     r"lords\s*of\s*the\s*fallen|black\s*myth|wukong|tails\s*of\s*iron"),
    # ── ACT（纯动作）──
    ("ACT", r"devil\s*may\s*cry|dmc\s*\d|bayonetta|ninja\s*gaiden|"
     r"metal\s*gear\s*rising|mgr|hi\.fi\s*rush|vanquish|"
     r"wo\s*long|ghost\s*of|black\s*myth|wukong|"
     r"stellar\s*blade|sifu\b|katana\s*zero|metal\s*slug|"
     r"cuphead|shovel\s*knight|guacamelee"),
    # ── FPS/射击 ──
    ("FPS/射击", r"half\.life|counter\.strike|team\s*fortress|"
     r"overwatch|call\s*of\s*duty|battlefield|rainbow\.six|"
     r"valorant|doom\s*\d|quake\b|left\s*4\s*dead|l4d[a-z]?|"
     r"titanfall|apex|far\s*cry|metro\s*\d|bio\.shock|"
     r"prey\b|dishonored|control\b|max\s*payne|"
     r"crysis|wolfenstein|sniper\s*elite|ghost\s*recon"),
    # ── 动作冒险 ──
    ("动作冒险", r"tomb\s*raider|uncharted|assassin'?s?\s*creed|"
     r"god\s*of\s*war|star\s*wars\s*jedi|marvel'?s?\s*spider|"
     r"spider\.man|arkham|batman|shadow\s*of|middle\.earth|"
     r"prince\s*of\s*persia|fenyx|immortal|ori\b|metroid|"
     r"castlevania|hollow\s*knight"),
    # ── Roguelike ──
    ("Roguelike", r"dead\s*cells|hades\b|binding\s*of\s*isaac|"
     r"slay\s*the\s*spire|risk\s*of\s*rain|vampire\s*survivors|"
     r"enter\s*the\s*gungeon|nuclear\s*throne|spelunky|"
     r"ftl\b|into\s*the\s*breach|darkest\s*dungeon|"
     r"barony|noita|rogue\.legacy|curious\s*expedition|"
     r"dungreed|magic\s*survival|20\s*minutes|wall\s*of\s*death"),
    # ── 解谜 ──
    ("解谜", r"portal\b|the\s*witness|baba\s*is\s*you|fez\b|braid|"
     r"limbo|inside\b|little\s*nightmare|superliminal|"
     r"antichamber|talos\s*principle|myst\b|rime\b|"
     r"the\s*room|machinarium|samorost|botanicula|"
     r"far:?\s*changing|cocoon|catherine|what\s*remains|"
     r"edith|gone\s*home|firewatch|return\s*of\s*obra|"
     r"outer\s*wilds"),
    # ── 模拟经营/建造 ──
    ("模拟经营", r"factorio|satisfactory|cities\s*skylines|"
     r"planet\s*coaster|rollercoaster\s*tycoon|"
     r"prison\s*architect|rimworld|dwarf\s*fortress|"
     r"oxygen\s*not\s*included|two\s*point|house\s*flipper|"
     r"car\s*mechanic|powerwash|stardew\s*valley|"
     r"harvest\s*moon|story\s*of\s*seasons|rune\s*factory|"
     r"slime\s*rancher|don't\s*starve|terraria|starbound|"
     r"valheim|raft|the\s*forest|subnautica|no\s*man'?s?\s*sky|"
     r"animal\s*crossing|my\s*time\s*at|sandrock|portia|"
     r"tropico|frostpunk|banished|they\s*are\s*billions|"
     r"parkitect|timberborn|captain\s*of\s*industry|"
     r"sweet\s*transit|voice\s*of\s*dolls|vpet|vpet\.simulator"),
    # ── 策略 ──
    ("策略", r"civilization|civ\s*v|endless\s*legend|"
     r"endless\s*space|stellaris|hearts\s*of\s*iron|"
     r"europa\s*universalis|crusader\s*king|total\s*war|"
     r"age\s*of\s*empires|starcraft|warcraft|command\.conquer|"
     r"xcom\b|xenonauts|company\s*of\s*heroes|"
     r"dawn\s*of\s*war|victoria|hoi|eu[234]|ck[23]|"
     r"imperator|age\s*of\s*wonders|heroes\s*of\s*might|"
     r"disciples|king's\s*bounty|battle\s*brothers|"
     r"shadow\s*tactics|desperados|door\s*kickers|"
     r"persona\s*[345]|smt[\s:]|shin\s*megami|metaphor"),
    # ── 格斗 ──
    ("格斗", r"street\s*fighter|guilty\s*gear|blazblue|"
     r"mortal\s*kombat|tekken|king\s*of\s*fighters|kof|"
     r"soulcalibur|dead\s*or\s*alive|dragon\s*ball\s*fighter|"
     r"brawlhalla|smash\s*bros|melty\s*blood|under\.night|"
     r"p4au|skullgirls|pocket\s*rumble"),
    # ── 音游 ──
    ("音游", r"osu|beat\s*sab|project\s*diva|maimai|cytus|"
     r"deemo|musync|djmax|taiko|guitar\s*hero|rock\s*band|"
     r"patapon|thumper|audiosurf|xonic|tetris\s*effect"),
]

# Steam 英文标签映射
_TAG_I18N = {
    "Action": "动作", "Adventure": "冒险", "RPG": "角色扮演",
    "Shooter": "射击", "Strategy": "策略", "Simulation": "模拟经营",
    "Horror": "恐怖", "Racing": "竞速", "Sports": "体育",
    "Fighting": "格斗", "Puzzle": "解谜", "Massively Multiplayer": "多人在线",
    "Indie": "独立游戏", "Casual": "休闲", "Visual Novel": "视觉小说",
    "Roguelike": "Roguelike", "FPS": "射击", "JRPG": "角色扮演",
    "Survival": "恐怖", "Open World": "冒险",
}

# Steam 标签 → 主类型映射（兜底用）
_STEAM_TAG_MAP = {
    "动作": "ACT", "冒险": "冒险", "角色扮演": "RPG",
    "策略": "策略", "模拟经营": "模拟经营", "射击": "FPS/射击",
    "解谜": "解谜", "恐怖": "恐怖", "格斗": "格斗",
    "竞速": "竞速", "体育": "体育", "休闲": "休闲",
    "独立游戏": "独立游戏", "多人在线": "多人在线",
    "视觉小说": "视觉小说", "Roguelike": "Roguelike",
    "音游": "音游", "沙盒": "模拟经营",
}

# 类型元数据（Emoji + 描述，用于雷达图）
_GENRE_META = {
    "魂系":      {"emoji": "⚔️", "desc": "魂系（FromSoftware 出品）"},
    "JRPG": {"emoji": "🎴", "desc": "日式角色扮演"},
    "视觉小说": {"emoji": "📖", "desc": "视觉小说/文字冒险"},
    "CRPG/WRPG": {"emoji": "🧙", "desc": "欧美角色扮演"},
    "ARPG/共斗": {"emoji": "🗡️", "desc": "动作角色扮演/共斗"},
    "ACT": {"emoji": "💥", "desc": "纯动作/硬核动作"},
    "FPS/射击": {"emoji": "🔫", "desc": "射击竞技"},
    "动作冒险": {"emoji": "🎬", "desc": "动作冒险/探索"},
    "Roguelike": {"emoji": "🔄", "desc": "Roguelike/Roguelite"},
    "解谜": {"emoji": "🧩", "desc": "解谜益智"},
    "模拟经营": {"emoji": "🏗️", "desc": "模拟经营/建造"},
    "策略": {"emoji": "🧠", "desc": "策略战术"},
    "格斗": {"emoji": "👊", "desc": "格斗对战"},
    "音游": {"emoji": "🎵", "desc": "音乐节奏"},
    "冒险": {"emoji": "🗺️", "desc": "冒险解谜"},
    "恐怖": {"emoji": "👻", "desc": "恐怖生存"},
    "休闲": {"emoji": "☕", "desc": "休闲娱乐"},
    "独立游戏": {"emoji": "💎", "desc": "独立创意游戏"},
    "多人在线": {"emoji": "🌐", "desc": "社交联机"},
    "竞速": {"emoji": "🏎️", "desc": "竞速驾驶"},
    "体育": {"emoji": "⚽", "desc": "体育竞技"},
    "RPG": {"emoji": "📖", "desc": "角色扮演"},
}


def _remap_tag(tag: str) -> str:
    """Steam 英文标签 → 中文"""
    if tag in _TAG_I18N:
        return _TAG_I18N[tag]
    for en, zh in _TAG_I18N.items():
        if en.lower() == tag.lower():
            return zh
    return tag


def assign_primary_genre(g) -> str:
    """为游戏分配单一主类型。名称关键词优先，Steam 标签兜底。"""
    name = g.name
    # Step 1: 名称关键词检测
    for genre, pattern in _GENRE_RULES:
        if re.search(pattern, name, re.IGNORECASE):
            return genre
    # Step 2: Steam API 标签兜底
    tags = g.tags or []
    if tags:
        zh_tags = []
        for t in tags:
            zh = _remap_tag(t)
            zh_tags.append(_STEAM_TAG_MAP.get(zh, zh))
        non_indie = [t for t in zh_tags if t not in ("独立游戏", "休闲")]
        if non_indie:
            return non_indie[0]
        elif zh_tags:
            return zh_tags[0]
    return "独立游戏"


# ═══════════════════════════════════════════════
# 游戏口味分析（重构版）- 每款游戏只占一个主类型
# 🎯 原则：一款游戏 → 一个门类 → 不作为其他门类代表作
# ═══════════════════════════════════════════════

def _calc_affinity(g) -> float:
    """计算单款游戏的黏性分数（时长 × 成就完成率^1.5）
    
    原则：
    - 成就完成率越高 → 时间越真实（真的是花在游戏上）
    - 完成率低 → 时间可能有水分（挂机/闲置）
    - 无成就游戏 → 保守估计 15% 可信度
    
    示例：
    完成率 100% → 1.0x  时间全部认可
    完成率 50%  → 0.35x 时间折半多
    完成率 10%  → 0.03x 基本视为水分
    无成就      → 0.15x 默认保守值
    """
    hours = g.playtime_forever / 60
    if (g.achievements_total or 0) > 0 and (g.completion_rate or 0) > 0:
        rate = (g.completion_rate or 0) / 100  # 0~1
        # 幂次惩罚：低完成率的黏性急剧衰减
        return hours * (rate ** 1.5)
    return hours * 0.15


def _build_gaming_profile(games: list, stats: dict) -> dict:
    """
    游戏口味画像（重构版）
    
    核心变更：
    - 每款游戏通过 assign_primary_genre() 分配单一主门类
    - 一个游戏只贡献给一个门类的 affinity/hours/game_count
    - 代表作为该门类内 affinity 最高的游戏（不是全局最高）
    - 高时长游戏不会在多个门类中重复出现
    - 门类细化到 18+ 种（魂系/JRPG/视觉小说/ACT/ARPG/FPS/策略…）
    """
    # ── 按主门类聚合（每款游戏只贡献一个门类）───────────
    genre_stats = {}  # genre -> {count, hours, affinity, games[], rep_candidates[]}
    for g in games:
        if g.playtime_forever == 0:
            continue
        genre = assign_primary_genre(g)
        affinity = _calc_affinity(g)
        hours = g.playtime_forever / 60
        if genre not in genre_stats:
            genre_stats[genre] = {"count": 0, "hours": 0.0, "affinity": 0.0,
                                  "games": set(), "rep_candidates": []}
        genre_stats[genre]["count"] += 1
        genre_stats[genre]["hours"] += hours
        genre_stats[genre]["affinity"] += affinity
        genre_stats[genre]["games"].add(g.name)
        genre_stats[genre]["rep_candidates"].append((affinity, g.name))
    
    # 选代表作：每个门类内 affinity 最高的 3 款
    for genre, data in genre_stats.items():
        data["rep_candidates"].sort(key=lambda x: x[0], reverse=True)
        data["top_reps"] = [n for _, n in data["rep_candidates"][:3]]
        data["games"] = list(data["games"])
    
    # 取 top 8 门类（按总 affinity 降序）
    top_genres = sorted(genre_stats.items(), key=lambda x: x[1]["affinity"], reverse=True)[:8]
    total_hours = stats.get("total_hours", 0)
    
    # ── 系列检测 ────────────────────────────────────────────
    detected_series = _detect_series(games)
    
    # ── 深度标签（基于主门类 + 系列检测）───────────────────
    tag_labels = set()
    top_genre_names = [g for g, _ in top_genres]
    if "魂系动作" in top_genre_names:
        tag_labels.add("🔴 硬核挑战者")
    if "JRPG" in top_genre_names or "CRPG/WRPG" in top_genre_names or "RPG" in top_genre_names:
        tag_labels.add("🎮 角色扮演派")
    if "视觉小说" in top_genre_names:
        tag_labels.add("📖 故事控")
    if "模拟经营" in top_genre_names:
        tag_labels.add("🏗️ 模拟建造发烧友")
    if "策略" in top_genre_names:
        tag_labels.add("🧠 策略思考型")
    if "ACT" in top_genre_names or "FPS/射击" in top_genre_names:
        tag_labels.add("💥 动作射击狂")
    if "独立游戏" in top_genre_names:
        tag_labels.add("💎 独立游戏爱好者")
    if detected_series:
        tag_labels.add("🎯 系列全勤粉: " + ", ".join(detected_series[:3]))
    if any(t in {"Roguelike", "解谜"} for t in top_genre_names):
        tag_labels.add("🔁 Roguelike/解谜爱好者")
    
    # ── 游玩风格 ────────────────────────────────────────────
    style = []
    if total_hours > 5000:  style.append("🔥 传说级玩家")
    elif total_hours > 1000: style.append("⚡ 骨灰级玩家")
    elif total_hours > 500:  style.append("🎖️ 老鸟玩家")
    elif total_hours > 100:  style.append("🎮 入门玩家")
    else:                    style.append("🍼 萌新玩家")
    recent_games = [g for g in games if g.playtime_2weeks > 0]
    style.append("📈 最近活跃 (" + str(len(recent_games)) + "款)" if recent_games else "💤 近期不活跃")
    never_pct = stats.get("never_played_pct", 0)
    if never_pct > 60: style.append("📦 喜+1 型")
    if len(games) > 100: style.append("🏪 收藏家")
    
    # ── 生成描述 ──────────────────────────────────────────
    full_description_parts = []
    
    total_games = len(games)
    played_games_count = len([g for g in games if g.playtime_forever > 0])
    avg_h = round(stats.get("avg_hours_per_played", 0), 1)
    total_h = round(total_hours, 1)
    
    # 第一部分：综合画像（按主门类，不再有重复计数）
    genre_overview = []
    for idx, (genre, data) in enumerate(top_genres[:6]):
        hrs = round(data["hours"], 1)
        cnt = data["count"]
        reps = data.get("top_reps", [])[:2]
        rep_str = "、".join(reps) if reps else ""
        prefix = "🔥" if idx == 0 else "  ├"
        genre_overview.append(f"{prefix} {genre}（{cnt}款，{hrs}h{('，代表作「' + rep_str + '」') if rep_str else ''}）")
    
    style_line = "、".join(style[:4])
    overview_lines = [
        "▎综合画像",
        f"游戏库 {total_games} 款，游玩过 {played_games_count} 款，总时长 {total_h}h，平均每款 {avg_h}h。",
        f"风格：{style_line}。",
        "",
        *genre_overview,
    ]
    if detected_series:
        overview_lines.append("")
        overview_lines.append("🎯 系列忠诚度：")
        for s in detected_series[:4]:
            overview_lines.append(f"  ├ {s} 忠粉")
    full_description_parts.append("\n".join(overview_lines))
    
    # 第二部分：成就
    ach_games = [g for g in games if g.playtime_forever > 0 and (g.achievements_total or 0) > 0]
    completed_games_high = [g for g in ach_games if (g.completion_rate or 0) >= 80]
    completed_100pct = [g for g in ach_games if (g.completion_rate or 0) >= 100]
    ach_parts = ["▎成就与挑战"]
    if ach_games:
        avg_ach = round(sum(g.completion_rate for g in ach_games if (g.completion_rate or 0) > 0) / max(len([g for g in ach_games if (g.completion_rate or 0) > 0]), 1), 1)
        ach_parts.append(f"在 {len(ach_games)} 款有成就的游戏中，平均完成率 {avg_ach}%。")
        ach_parts.append(f"其中 {len(completed_games_high)} 款完成率 ≥80%，{len(completed_100pct)} 款达成全成就（100%）！")
        if completed_100pct:
            top_100 = [(g.name, round(g.playtime_forever / 60, 1)) for g in completed_100pct[:5]]
            top_100.sort(key=lambda x: x[1], reverse=True)
            ach_parts.append(f"全成就游戏代表：" + "、".join([f"「{n}」{h}h" for n, h in top_100]))
    full_description_parts.append("\n".join(ach_parts))
    
    # 第三部分：近期
    recent_games_list = [g for g in games if g.playtime_2weeks > 0]
    if recent_games_list:
        recent_games_list.sort(key=lambda x: x.playtime_2weeks, reverse=True)
        top3 = [(g.name, round(g.playtime_2weeks / 60, 1)) for g in recent_games_list[:3]]
        recent_str = "；".join([f"「{n}」{h}h" for n, h in top3])
        recent_total = round(sum(g.playtime_2weeks for g in games) / 60, 1)
        full_description_parts.append(
            f"▎近期动态（近2周）\n活跃于 {len(recent_games_list)} 款游戏，总计 {recent_total}h，投入重心：{recent_str}。")
    
    # 第四部分：标签
    if tag_labels:
        full_description_parts.append("▎个性标签\n" + " ".join(tag_labels))
    
    primary_desc = "\n\n".join(full_description_parts)
    
    return {
        "top_genres": [
            {
                "genre": genre,
                "hours": round(data["hours"], 1),
                "affinity": round(data["affinity"], 1),
                "game_count": data["count"],
                "top_games": data.get("top_reps", [])[:4],
            }
            for genre, data in top_genres
        ],
        "gaming_level": _gaming_level(total_hours),
        "gaming_level_code": _level_code(total_hours),
        "total_hours": round(total_hours, 1),
        "steam_rank": _steam_rank(0, total_hours),
        "stickiness_pct": stats.get("stickiness_pct", 0),
        "style": style,
        "tag_labels": list(tag_labels),
        "taste_description": primary_desc,
        "hours_distribution": _hours_distribution(games),
        "recent_genres": _recent_genres(games, top_genres),
    }


def _detect_series(games: list) -> list:
    """检测用户游玩过的系列作品，返回系列名称"""
    _SERIES_PATTERNS = [
        ("黑暗之魂", r"dark soul", re.IGNORECASE),
        ("血源诅咒", r"bloodborne", re.IGNORECASE),
        ("只狼", r"sekiro", re.IGNORECASE),
        ("艾尔登法环", r"elden ring", re.IGNORECASE),
        ("仁王", r"nioh", re.IGNORECASE),
        ("Hollow Knight", r"hollow knight", re.IGNORECASE),
        ("怪物猎人", r"monster hunter", re.IGNORECASE),
        ("BLACK SOULS", r"black soul", re.IGNORECASE),
        ("生化危机", r"resident evil", re.IGNORECASE),
        ("寂静岭", r"silent hill", re.IGNORECASE),
        ("女神异闻录", r"persona\s*\d", re.IGNORECASE),
        ("逆转裁判", r"ace attorney", re.IGNORECASE),
        ("饥荒", r"don't starve", re.IGNORECASE),
        ("死亡细胞", r"dead cells", re.IGNORECASE),
        ("吸血鬼幸存者", r"vampire survivors", re.IGNORECASE),
        ("鬼泣", r"devil may cry|dmc", re.IGNORECASE),
        ("最终幻想", r"final fantasy|ff", re.IGNORECASE),
        ("尼尔", r"nier", re.IGNORECASE),
        ("毁灭战士", r"doom", re.IGNORECASE),
        ("巫师", r"witcher", re.IGNORECASE),
        ("塞尔达", r"zelda", re.IGNORECASE),
        ("辐射", r"fallout", re.IGNORECASE),
        ("上古卷轴", r"elder scroll|skyrim", re.IGNORECASE),
        ("GTA", r"grand theft auto|gta", re.IGNORECASE),
        ("使命召唤", r"call of duty", re.IGNORECASE),
        ("守望先锋", r"overwatch", re.IGNORECASE),
        ("英雄传说/轨迹", r"kiseki|trails of", re.IGNORECASE),
        ("NEKOPARA", r"nekopara", re.IGNORECASE),
        ("Fate", r"fate/", re.IGNORECASE),
        ("刺客信条", r"assassin'?s creed", re.IGNORECASE),
        ("古墓丽影", r"tomb raider", re.IGNORECASE),
        ("战神", r"god of war", re.IGNORECASE),
        ("神秘海域", r"uncharted", re.IGNORECASE),
        ("孤岛惊魂", r"far cry", re.IGNORECASE),
        ("地铁", r"metro", re.IGNORECASE),
        ("空洞", r"hollow", re.IGNORECASE),
        ("英雄联盟", r"league of legends|lol", re.IGNORECASE),
        ("符文工房", r"rune factory", re.IGNORECASE),
        ("牧场物语", r"harvest moon|story of seasons", re.IGNORECASE),
        ("炼金工房", r"atelier", re.IGNORECASE),
        ("弹丸论破", r"danganronpa", re.IGNORECASE),
        ("Fate/samurai", r"fate/samurai|fate\\\\/", re.IGNORECASE),
    ]
    detected = []
    for series_name, pattern, flags in _SERIES_PATTERNS:
        matched = [g for g in games if g.playtime_forever > 0 and re.search(pattern, g.name)]
        if len(matched) >= 2:
            total_hrs = sum(g.playtime_forever for g in matched) / 60
            completed_any = any(
                (g.completion_rate or 0) >= 80 for g in matched
                if (g.achievements_total or 0) > 0)
            if completed_any or total_hrs >= 50:
                detected.append(series_name)
    return detected


def _build_radar(games: list, stats: dict) -> dict:
    """
    计算雷达图（重构版）
    
    基于 assign_primary_genre() 分配的主门类，每款游戏只贡献给它唯一的主门类。
    避免了高时长游戏在多个门类中重复占据高位的问题。
    取 top 6-7 门类，各自归一化到 0~100。
    """
    played = [g for g in games if g.playtime_forever > 0]
    
    # 每款游戏只贡献给它的主门类
    genre_affinity = {}
    genre_game_count = {}
    for g in played:
        genre = assign_primary_genre(g)
        affinity = _calc_affinity(g)
        genre_affinity[genre] = genre_affinity.get(genre, 0) + affinity
        genre_game_count[genre] = genre_game_count.get(genre, 0) + 1
    
    # 取 top 7
    sorted_genres = sorted(genre_affinity.items(), key=lambda x: x[1], reverse=True)[:7]
    max_affinity = max([v for _, v in sorted_genres], default=1)
    
    dims = []
    for genre, aff in sorted_genres:
        norm_value = min(100, round(aff / max_affinity * 100, 1))
        meta = _GENRE_META.get(genre, {"emoji": "\U0001f3ae", "desc": f"{genre}类"})
        count = genre_game_count.get(genre, 0)
        dims.append({
            "axis": f"{meta['emoji']} {genre}",
            "value": norm_value,
            "desc": meta["desc"],
            "raw_genre": genre,
            "game_count": count,
            "affinity": round(aff, 1),
        })
    
    return {
        "dimensions": dims,
        "method": "primary_genre_affinity",
        "summary": {d["raw_genre"]: d["value"] for d in dims},
    }


# ──────── 辅助函数 ────────

def _gaming_level(hours: float) -> str:
    if hours < 10:   return "萌新"
    if hours < 50:   return "入门"
    if hours < 200:  return "玩家"
    if hours < 500:  return "老鸟"
    return "骨灰" if hours < 2000 else "传说"

def _level_code(hours: float) -> str:
    if hours < 10:   return "\U0001f37c"
    if hours < 50:   return "\U0001f3ae"
    if hours < 200:  return "\U0001f3af"
    if hours < 500:  return "\u2694\ufe0f"
    return "\U0001f525" if hours < 2000 else "\U0001f451"

def _steam_rank(level: int, hours: float) -> str:
    if level >= 500 or hours >= 10000: return "Steam 之神"
    if level >= 200 or hours >= 3000:   return "Steam 收藏家"
    if level >= 100 or hours >= 1000:   return "Steam 老炮"
    if level >= 50  or hours >= 300:    return "Steam 玩家"
    if level >= 10  or hours >= 50:     return "Steam 新人"
    return "Steam 萌新"



def _is_preferring(games: list, keywords: list) -> bool:
    played = [g for g in games if g.playtime_forever > 0]
    count = sum(1 for g in played if any(kw.lower() in (g.name + " " + " ".join(g.tags or [])).lower() for kw in keywords))
    return count > max(len(played), 1) * 0.25


def _hours_distribution(games: list) -> dict:
    buckets = {
        "未游玩 (0h)": 0,
        "浅尝 (0-5h)": 0,
        "体验 (5-20h)": 0,
        "深入 (20-100h)": 0,
        "重度 (100-500h)": 0,
        "神级 (>500h)": 0,
    }
    for g in games:
        h = g.playtime_forever / 60
        if h == 0:      buckets["未游玩 (0h)"] += 1
        elif h < 5:     buckets["浅尝 (0-5h)"] += 1
        elif h < 20:    buckets["体验 (5-20h)"] += 1
        elif h < 100:  buckets["深入 (20-100h)"] += 1
        elif h < 500:  buckets["重度 (100-500h)"] += 1
        else:           buckets["神级 (>500h)"] += 1
    return buckets


def _recent_genres(games: list, top_genres: list) -> list:
    recent = [g for g in games if g.playtime_2weeks > 0]
    recent.sort(key=lambda x: x.playtime_2weeks, reverse=True)
    result = []
    for g in recent[:6]:
        genres = [assign_primary_genre(g)]
        genre_str = genres[0] if genres else "其他"
        result.append({"name": g.name, "hours_2w": round(g.playtime_2weeks / 60, 1), "genre": genre_str})
    return result
