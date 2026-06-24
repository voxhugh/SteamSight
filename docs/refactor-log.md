# Steam Portal - 主类型画像系统重构完成

## 变更概要

### 问题定位
旧代码因两次编辑产生两套同名函数（`_build_gaming_profile`, `_build_radar` 等），导致运行时 NameError。同时旧分类体系使用多标签计数（Steam 多标签→同一游戏污染多个维度），高时长游戏（如 Sekiro/Terraria）在所有匹配门类重复计入。

### 解决方案
1. **清理重复代码**：删除 L436-L860 全部旧 `_TAG_REMAP`/`_DIFFICULT_TAGS`/`_normalize_genre`/`_remap_tag`/`_build_gaming_profile`/`_build_radar`/`_gaming_level`/`_level_code`/`_steam_rank`
2. **单游戏单门类**：`assign_primary_genre(g)` 为每款游戏分配唯一主类型（名称关键词优先 → Steam 标签兜底）
3. **门类内部代表作**：每门类 pick affinity 最高的 3 款作为代表作，拒绝全局 top 污染
4. **`_detect_series` 独立函数**：删除 `_build_gaming_profile` 内联的系列检测，抽为独立函数
5. **`_recent_genres` 修复**：将引用已删除 `_normalize_genre` 的行改为 `assign_primary_genre`
6. **重建 `run_server.py`**：解决 GBK 打印乱码

### 验证结果（用户 76561199086286116）
- 7 维雷达区分度：ACT 100 / 魂系动作 97 / ARPG/共斗 96 / 冒险 62 / 模拟经营 38 / JRPG 34 / 视觉小说 26
- 全成就游戏 16 款正确识别，置底排序
- 系列检测：生化危机/最终幻想/魂系系列等正确标注
- 口味描述 800+ 字符，4 段落

### 服务状态
- 端口 8766，运行中（PID 17220）
- 玩家端点冷启动 ~60s（335 款全库 + 60 款成就检查），缓存命中大幅加速
