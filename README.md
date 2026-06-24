# SteamSight

<p align="center">
  <i>透视你的 Steam 游戏世界</i><br>
  <b>Steam 游戏数据仪表盘 & 玩家画像分析工具</b>
</p>

---

## ✨ 功能特性

### 🎮 游戏数据中心
- **热门游戏追踪** — 实时获取 Steam 当前热门游戏（在线人数、价格、折扣）
- **游戏详情** — 名称、封面、描述、评分、标签一站式查看
- **全库扫描** — 扫描玩家全部游戏库（支持 500+ 款游戏）

### 👤 玩家画像系统
- **多维雷达图** — 基于实际游玩类型的 7 维能力分析
- **游戏口味画像** — 自动生成多段式文字描述（综合画像 / 成就与挑战 / 近期动态 / 个性标签）
- **门类专业分类** — 魂系动作、JRPG、ARPG、ACT、FPS、解谜、模拟经营等 14 个细分类型
- **每游戏单主类型** — 告别"一款游戏污染所有维度"的问题

### 📊 成就与深度分析
- **全库成就扫描** — 自动检测全成就游戏，置底展示"荣誉墙"
- **综合黏性分数** — `时长 × (成就完成率^1.5)`，精准衡量游戏投入度
- **系列粉检测** — 自动识别系列作品完整体验（如 Dark Souls 全系列）

### 🛠️ 技术特性
- **智能代理检测** — 自动扫描本地代理端口，支持 HTTP/SOCKS5 协议识别
- **缓存预热** — stale-while-revalidate 策略，二次访问秒开
- **并行抓取** — ThreadPoolExecutor 加速成就/标签批量获取
- **PowerShell 控制脚本** — 一键启动/停止/重启/状态检查

---

## 🚀 快速开始

### 环境要求
- Python 3.10+
- Steam Web API Key（[免费申请](https://steamcommunity.com/dev/apikey)）
- 本地代理工具（Clash / V2RayN 等，可选但推荐）

### 安装依赖
```bash
pip install -r requirements.txt
```

### 配置
编辑 `backend/config.py`，填入你的 Steam API Key：
```python
STEAM_API_KEY = "你的API密钥"
STEAM_USERNAME = "你的SteamID64或自定义URL"
```

### 启动服务
```powershell
# 使用 PowerShell 控制脚本（推荐）
.\scripts\steam_sight.ps1 start

# 或直接使用 Python
py backend\main.py
```

服务启动后访问：
- 数据中心：`http://localhost:8766`
- 玩家画像：`http://localhost:8766/profile?user=你的SteamID64`

### 控制脚本命令
```powershell
.\scripts\steam_sight.ps1 start    # 启动
.\scripts\steam_sight.ps1 stop     # 停止
.\scripts\steam_sight.ps1 restart  # 重启
.\scripts\steam_sight.ps1 status   # 查看状态
.\scripts\steam_sight.ps1 log      # 查看日志
.\scripts\steam_sight.ps1 purge    # 清除缓存并重启
```

---

## 📁 项目结构

```
SteamSight/
├── backend/                 # 后端服务
│   ├── main.py              # FastAPI 主服务，API 路由
│   ├── steam_client.py      # Steam 官方 API 客户端
│   ├── player_scraper.py    # 玩家画像构建引擎
│   ├── steamdb_scraper.py   # SteamDB 数据爬虫（备用）
│   └── config.py            # 配置与代理自动检测
├── web/                     # 前端页面
│   ├── index.html           # 数据中心首页
│   └── profile.html         # 玩家画像页（ECharts 雷达图）
├── data/                    # 数据缓存目录
├── scripts/                 # 控制脚本
│   ├── steam_sight.ps1      # PowerShell 控制器
│   ├── steam_sight.bat      # Batch 包装器
│   └── run_server.py        # GBK 编码修复启动器
├── docs/                    # 设计文档
├── requirements.txt         # Python 依赖
├── .gitignore
└── README.md
```

---

## 🎯 核心算法

### 门类分类系统
每款游戏通过 `assign_primary_genre()` 分配唯一主类型，规则优先级：
1. **游戏名称关键词匹配**（如 "Dark Souls" → 魂系动作）
2. **Steam 标签兜底**（如 genres 含 "Visual Novel" → 视觉小说）
3. **扩展门类表**（14 类）：魂系 / JRPG / 视觉小说 / CRPG / ARPG / ACT / FPS / 动作冒险 / Roguelike / 解谜 / 模拟经营 / 策略 / 格斗 / 音游

### 综合黏性公式
```
affinity_score = hours × (completion_rate / 100)^1.5
```
- 成就完成率高的游戏获得更高权重
- 无成就游戏 affinity 降至 0.15x（避免时长泡沫）

### 玩家画像构建
1. 全库游戏按 `assign_primary_genre()` 分类
2. 每个门类内部按 affinity_score 选取代表作
3. 7 维雷达图归一化到 0~100
4. 多段式文字描述自动生成

---

## 🔧 API 端点

| 端点 | 说明 |
|------|------|
| `GET /api/trending` | 热门游戏列表 |
| `GET /api/game/{appid}` | 单游戏详情 |
| `GET /api/games/batch?appids=730,570` | 批量游戏查询 |
| `GET /api/player/{identifier}` | 玩家完整报告（JSON）|
| `GET /api/player/{id}/game/{appid}` | 玩家某游戏详情 |
| `GET /api/steamdb/top` | SteamDB Top100（需代理）|
| `GET /api/status` | 服务状态（代理/缓存信息）|

---

## 📝 开发日志

详见 `docs/` 目录：
- `control-design.md` — 控制脚本设计文档
- `full-library-design.md` — 全库扫描设计方案
- `refactor-log.md` — 门类重构记录
- `profile-upgrade.md` — 画像系统升级记录

---

## 📄 License

MIT License — 仅供学习交流使用，请遵守 Steam API 使用条款。

---

<blockquote align="center">
  <p><i>"Data is the new steam."</i> — SteamSight</p>
</blockquote>
