# AGENTS.md · 项目规则

> 📌 **文档基线**：2026-10-10（commit `fdd24a0`）静态审计 + 运行期冒烟，修 v1.1.0 两个 P0
> **更新文档/代码后，请更新此行**（日期 + 新 commit hash），并在 CHANGELOG 追加版本

## 技术栈

- Python **3.12+**（本机 3.13.15），**纯标准库**：`tkinter` / `ttk`（`vista` 主题）/ `subprocess` / `threading` + `queue` / `urllib.request` / `json` / `webbrowser`
- 打包：PyInstaller（`VideoLauncher.spec`，单文件、`console=False`、`upx=True`）
- 宿主链路：PowerShell 5.1 → `video-upscale-deploy` 下的 `.ps1` → ComfyUI（`127.0.0.1:8188`）

## 关键坑（改代码前先看）

- **PS 执行策略**：本机为 `Restricted`，`.ps1` 一律用 `powershell -NoProfile -ExecutionPolicy Bypass` 拉起。
- **不要用 `-File`**：必须用 `-Command` 包一层，先设 `[Console]::OutputEncoding=[System.Text.Encoding]::UTF8` 和 `$env:PYTHONIOENCODING='utf-8'`；否则 PS 5.1 按 cp936 重编 python 的 UTF-8 输出，**日志全变乱码**。
- **探测本机服务必须绕代理**：Clash 会把 `127.0.0.1` 也代理掉并返回 502。一律走 `NO_PROXY_OP`（`ProxyHandler({})`），**不要用 `urlopen`**。
- **长任务不许同步等**：SeedVR2 单条 20+ 分钟。用子进程 + 线程读 stdout + `queue` + `root.after(150)` 刷新，UI 不能阻塞。
- **ffmpeg 不靠注册表**：`_comfy_env()` 把 `ffmpeg_dir` 显式前置到子进程 `PATH`；本机实测出现过「父进程改了 PATH 但传不到子进程」。
- **H3 参数在网页里调**：启动器只负责把服务与网页拉起来，工作流（提示词 / 帧数 / 步数）在 ComfyUI 里自己搭。

## 约定

- UI 文案、注释、日志**全中文**；**零第三方依赖**，不引入 `requests` / `psutil` 等（打包体积与离线可用性优先）。
- 单文件交付：逻辑集中在 `视频方案启动器.py`，超阈值才拆分（见 knowledge-base 单项目规范）。
- 路径不写死：新增路径一律先进 `CONFIG` 字典，并允许 `启动器配置.json` 覆盖。
- 打包产物（`build/`、`dist/`）不入库，发行走 Releases。

## 常用命令

```bash
python 视频方案启动器.py            # 直接运行
pyinstaller VideoLauncher.spec     # 打包单文件 exe
```

## 详细规则（按需 @引用）

- @DEVELOPMENT.md —— 架构说明 + 关键问题与方案（一坑一篇）
