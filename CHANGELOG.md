# Changelog

本文件记录本项目的版本变更。格式：版本号 + 日期 + 变更分类。

## v1.0.0 · 2026-09-30

首个归档版本（源码取自本机 2026-09-30 交付物，此前未纳入版本管理）。

### 新增

- Tkinter 单窗口 GUI：ComfyUI 服务状态条（版本 + 显存，5 秒刷新）+ 三方案页签 + 实时日志区
- 服务控制：一键启动 / 停止 ComfyUI（`--disable-pinned-memory --disable-async-offload --reserve-vram 1`），就绪后自动打开网页
- 页签 ① FlashVSR：文件 / 文件夹输入、模式（tiny / tiny-long / full）、倍数（2 / 3 / 4）、编码（h264 / h265 / nvenc）、服务未跑自动拉起
- 页签 ② SeedVR2：档位（12g / 16g / 24g）、目标短边（720 / 1080）、覆盖已有输出开关
- 页签 ③ MiniMax H3：启动服务并打开网页（参数在网页工作流里调），附实测参数与显存峰值提示
- 子进程全异步 + 日志实时回传，任务进行中禁用开跑按钮，退出前二次确认
- 全部路径可通过程序同目录的 `启动器配置.json` 覆盖
- 打包配置 `VideoLauncher.spec`（单文件 / 无控制台窗口 / UPX）

### 已知限制

- 仅 Windows（依赖 `os.startfile`、`nvidia-smi`、PowerShell）
- H3 的工作流需自行在 ComfyUI 中搭建；启动器不代管 H3 参数
