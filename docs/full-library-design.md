# Steam Portal - 全量游戏库修复

## 问题
玩家资料页只展示前50款游戏，缺失400+款游戏数据。

## 根因分析

### 1. 后端 limit 截断
`main.py` 中 `PlayerReportResponse.top_games` 只取了 `report["games"][:50]`，
导致 API 响应最多 50 款游戏。

**修复**: 改为 `report["games"]`，去掉 `[:50]` 切片。

### 2. get_batch_genres API 格式错误（双重根因）
Steam Store API 的 `appdetails` 端点**不支持**逗号分隔多 appid:
- ❌ `appids=730,570,440` → HTTP 400
- ✅ 必须逐个请求 `appids=730`（并行执行）

原有 `get_batch_genres` 用逗号分隔拼接，返回空数据，导致全部 335 款游戏 `tags=[]`。

**修复**: 
- 新增 `_get_single_genre(appid)` 单个请求函数
- `get_batch_genres` 内部用 `ThreadPoolExecutor(max_workers=8)` 并行逐个请求
- 简化 `player_scraper.py` 调用端，去掉嵌套批量分片

## 当前状态
- ✅ 全库 335 款游戏返回
- ✅ 60 款成就检查（前 30 最近活跃 + 前 30 按总时长）
- ✅ 16 款全成就（100%）游戏**置底**（按复合排序逻辑）
- ✅ 213 款游戏有 Steam Store Genre 标签
- ✅ 雷达图 6 维度（动作/冒险/角色扮演/独立/模拟经营/策略）
- ✅ 多段落口味描述（853 字符）
- ✅ 5 个品味标签 + 3 个风格标签
- ✅ 首次冷启动 ~120s（含全库并行 genres 抓取）
- ✅ 缓存命中 ~10s

## 剩余可优化
- 冷启动 120s 偏慢，可考虑 genres 长缓存（1h）
- 前端 335 行游戏表未分页（浏览器渲染没问题但 UX 一般）
- Steam Store API 仍有 122 款游戏返回空 genres（多为无商店页的免费游戏）
