# Steam Portal - 玩家资料页核心业务升级记录

## 时间
2026-06-24 22:00 - 23:00

## 改动总览

### 1. 雷达图维度重构（`player_scraper.py` → `_build_radar`）
**旧版**：抽象 6 维度（活跃度/深度/广度/成就/社交/挑战）
**新版**：基于玩家实际游戏门类的 affinity 黏性分数，取 Top 6 类型作为雷达维度
- 每个维度的值 = 该类型黏性分数归一化到 0~100
- 带 emoji 图标和游戏数信息
- 方法标记 `"method": "genre_affinity"`

### 2. 口味画像大幅扩充（`player_scraper.py` → `_build_gaming_profile`）
**旧版**：一行描述（如"经营达人，模拟类爱好者"）
**新版**：多段式详细画像
- ▎游戏类型画像 — 各类型游戏数、时长、代表作
- ▎综合评级 — 游戏库规模、喜+1比例、风格标签
- ▎成就与完成度 — 有成就游戏数、平均完成率、80%+ 游戏数
- ▎近期动态 — 近两周活跃游戏、总时长
- ▎个性标签 — 系列标签等

### 3. 成就采集选择策略优化（`player_scraper.py` → `get_full_player_report` step 3）
**旧逻辑**：按 `playtime_forever` 总时长排序选前 N 款
**新逻辑**：按**最终表格排序逻辑**（两周运行时长降序 → 上次运行时间降序）选前 N 款
- 确保前端表格排名前 60 的游戏都有成就数据
- `_MAX_ACHIEVEMENT_GAMES` 从 30 提升至 60

### 4. 饼/柱状图色彩丰富化（`profile.html`）
- 新增 10 色调色板 `pieChartColors`
- 柱状图使用多彩单色替代渐变色
- 口味描述支持多段式渲染（`\n` → `<br>`，`▎` 高亮）

### 5. 配置文件（`steam_client.py`）
- 完整游戏库获取：`include_played_free_games=1, skip_unvetted_apps=0`
- `get_owned_games` 6 小时缓存

### 6. Bug 修复
- `_build_gaming_profile` 中引用 `data["top_games"]` → 修正为 `data["games"]`（KeyError 500 修复）

## 当前性能
- 冷启动首次：~116s（60 成就游戏 + 批量标签）
- 缓存二次：预估 ~18s
- Radar 维度已全部基于游戏门类展示
- 前 50 条游戏全部含成就数据
