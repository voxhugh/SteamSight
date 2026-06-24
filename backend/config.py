# -*- coding: utf-8 -*-
"""
Steam Portal 配置文件
"""

import os
import socket
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Steam Web API Key（从 https://steamcommunity.com/dev/apikey 免费申请）
# 不填也能跑部分功能（热门游戏、在线人数），但个人数据需要填
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "BD8B9FFDF079295036230DFD6A6D42D0")

# Steam 账号（用于查看个人数据）
STEAM_USERNAME = os.getenv("STEAM_USERNAME", "76561199086286116")
STEAM_PASSWORD = os.getenv("STEAM_PASSWORD", "")

# SteamDB 访问（可选，填了能获取更完整数据）
STEAMDB_TOKEN = os.getenv("STEAMDB_TOKEN", "")

# ── 代理自动检测 ───────────────────────────────
# 常见代理工具端口（按优先级排列）：
#   Clash Verge / Clash Meta:  7890-7899
#   V2RayN / V2Box:  10800-10809
#   Sing-box / Nekoray:  10808, 10809, 1080
#   SSR / SSR Rust:  1080-1089
#   Shadowsocks:  1080, 1086, 8118, 9090
#   HTTP 代理（其他）: 8080, 8888, 8899, 3128
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request

# 分优先级组扫描，同组并行，组间串行
# 组0：最常用端口（秒开）
_GROUP_0 = [7890, 10808, 10809, 1080, 7891, 7892, 7893]
# 组1：次常用（慢扫）
_GROUP_1 = [10800, 10801, 10802, 10803, 10804, 10805, 10806, 10807,
            1086, 7894, 7895, 7896, 7897, 7898, 7899]
# 组2：兜底（其余常见）
_GROUP_2 = [8080, 8888, 8899, 9090, 8118, 3128]

# HTTP 代理探测目标（轻量快速，用于验证代理是否真正可用）
_PROBE_URL = "http://httpbin.org/ip"
_PROBE_TIMEOUT = 3


def _scan_ports(ports: list, timeout: float = 0.6) -> list:
    """并行扫描一组端口，返回打开的端口号列表（按原顺序）"""
    open_ports = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        fut_map = {pool.submit(_check_port, p, timeout): p for p in ports}
        for fut in as_completed(fut_map):
            p = fut_map[fut]
            try:
                if fut.result():
                    open_ports.append(p)
            except Exception:
                pass
    # 按原顺序返回
    return [p for p in ports if p in open_ports]


def _check_port(port: int, timeout: float = 0.6) -> bool:
    """检查单个端口是否开放"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex(("127.0.0.1", port))
        sock.close()
        return result == 0
    except Exception:
        return False


def _probe_http_port(port: int) -> bool:
    """验证端口是否真的支持 HTTP 代理（用 httpbin 做轻量探测）"""
    proxy_url = f"http://127.0.0.1:{port}"
    try:
        handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        opener = urllib.request.build_opener(handler)
        opener.open(_PROBE_URL, timeout=_PROBE_TIMEOUT)
        return True
    except Exception:
        return False


def _probe_socks_port(port: int) -> dict | None:
    """验证端口是否支持 SOCKS5 代理"""
    try:
        import socks  # pip install PySocks
        import socket as sk
        orig = sk.socket
        sk.socket = socks.socksocket
        socks.set_default_proxy(socks.SOCKS5, "127.0.0.1", port)
        try:
            import urllib.request as ul
            r = ul.urlopen("http://httpbin.org/ip", timeout=_PROBE_TIMEOUT)
            return {"http": f"socks5://127.0.0.1:{port}", "https": f"socks5://127.0.0.1:{port}"}
        finally:
            sk.socket = orig
    except ImportError:
        return None
    except Exception:
        return None


def _detect_local_proxy() -> dict | None:
    """智能检测本地代理
    
    检测优先级：
    1. 系统环境变量 ALL_PROXY / HTTP_PROXY / HTTPS_PROXY（最快，且验证可用性）
    2. 本地端口扫描 → HTTP 代理验证 → SOCKS5 兜底（组0→组1→组2）
    3. 返回 None（不用代理）
    """
    # Step 1: 检查系统环境变量（最快路径）
    for env_var in ["ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
        val = os.getenv(env_var, "").strip()
        if val:
            proxy_url = val if val.startswith("http") else f"http://{val}"
            if _probe_http_port(int(proxy_url.split(":")[-1])):
                return {"http": proxy_url, "https": proxy_url}
    # Step 2: 分组并行扫描本地端口（先扫端口，再验证）
    for group in [_GROUP_0, _GROUP_1, _GROUP_2]:
        open_ports = _scan_ports(group)
        if not open_ports:
            continue
        # 先尝试 HTTP 代理
        for port in open_ports:
            try:
                if _probe_http_port(port):
                    return {"http": f"http://127.0.0.1:{port}", "https": f"http://127.0.0.1:{port}"}
            except:
                continue
        # HTTP 都不行，尝试 SOCKS5 兜底
        for port in open_ports:
            try:
                result = _probe_socks_port(port)
                if result:
                    return result
            except:
                continue
    return None


# ─── 代理懒加载：每次请求时重新检测 ───
_proxy_cache = {}
_proxy_cache_time = 0
_PROXY_CACHE_TTL = 30  # 秒，避免每次请求都扫描端口

def get_proxies() -> dict | None:
    """获取代理配置（带 30 秒缓存，自动重检测）"""
    global _proxy_cache, _proxy_cache_time
    now = time.monotonic()
    if _proxy_cache and (now - _proxy_cache_time) < _PROXY_CACHE_TTL:
        return _proxy_cache.get("proxies")
    # 优先级检查
    proxy_http = os.getenv("PROXY_HTTP", "")
    proxy_https = os.getenv("PROXY_HTTPS", "")
    if proxy_http or proxy_https:
        proxies = {"http": proxy_http or proxy_https, "https": proxy_https or proxy_http}
    elif os.getenv("STEAM_NO_PROXY", ""):
        proxies = None
    else:
        proxies = _detect_local_proxy()
    _proxy_cache = {"proxies": proxies}
    _proxy_cache_time = now
    return proxies

# HTTP 代理优先级：
# 1. 环境变量显式设置
# 2. 自动检测到的本地代理
# 3. 固定 fallback（注释掉则无代理）
# ⚡ 动态代理：每次请求自动检测（带 30 秒缓存）
# 如需手动指定，设置环境变量 PROXY_HTTP / PROXY_HTTPS
def PROXIES():
    """向后兼容：保持旧代码 `proxies=PROXIES` 调用方式
    现在 PROXIES 是一个函数，调用后返回当前代理配置"""
    return get_proxies()

# 请求延时（秒），防止被限流
REQUEST_DELAY = (0.5, 1.5)

# 并发线程数
MAX_WORKERS = 6

# 数据缓存时间（分钟）
CACHE_TTL = 10

# 服务器配置
HOST = "0.0.0.0"
PORT = 8766
