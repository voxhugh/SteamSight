# Steam Portal 一键启停脚本

## 文件
- `steam_portal.ps1` — 主脚本
- `steam_portal.bat` — Batch 包装（支持双击 / cmd 直接调用，转发到 .ps1）

## 用法 (PowerShell 或 CMD 均可)

```
.\steam_portal start     启动服务（后台，绿色进程）
.\steam_portal stop      停止（先优雅 → 再强制）
.\steam_portal restart   重启
.\steam_portal status    查看状态（PID/端口/API/代理/缓存）
.\steam_portal log       实时 tail 日志（Ctrl+C 退出）
.\steam_portal purge     清缓存 + 重启
```

## 特性
- PID 文件 + 端口双重追踪，多节点切换无残留
- 代理自动检测（当前 10808），30s 缓存自动刷新
- 启动 45 秒超时保护
- 颜色输出（绿 OK / 黄 warn / 红 err）
- 状态页同时显示 API 是否可达、代理、缓存计数
