"""Steam Portal 服务器启动脚本（强制 UTF-8 编码 + 缓存预热）"""
import sys, io, os, threading

# 强制 UTF-8 输出
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['PYTHONLEGACYWINDOWSSTDIO'] = 'utf-8'

# 添加 backend 到路径并导入
backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backend')
sys.path.insert(0, backend_dir)

import time, logging

# ── 启动时预热缓存（后台进行，不阻塞服务启动）─────────────────────────────
def _prewarm_cache():
    """服务器启动后预热热门数据缓存"""
    time.sleep(1)  # 等待服务完全就绪
    try:
        import steam_client as sc
        from datetime import datetime
        logger = logging.getLogger("uvicorn")
        logger.info("[Steam Portal] 正在预热缓存...")

        # 预热 trending（主页面核心数据）
        for limit in [50, 100]:
            try:
                rows = sc.get_top_games_live(limit=limit)
                sc.cache_set(f"trending_{limit}", {
                    "updated_at": datetime.now().isoformat(),
                    "games": rows,
                    "source": "steamcharts.com (prewarmed)",
                })
                logger.info(f"[Steam Portal] ✅ trending_{limit} 预热完成 ({len(rows)} 款)")
            except Exception as e:
                logger.warning(f"[Steam Portal] trending_{limit} 预热失败: {e}")

        logger.info("[Steam Portal] 缓存预热完成，服务就绪")
    except Exception as e:
        logging.warning(f"[Steam Portal] 缓存预热出错: {e}")

import uvicorn, main

# 启动后台预热线程
threading.Thread(target=_prewarm_cache, daemon=True).start()

uvicorn.run('main:app', host='0.0.0.0', port=8766,
            reload=False, timeout_keep_alive=120, log_level='info')
