# AGENTS.md · 项目规则

> 📌 **文档基线**：2026-10-10（commit `4f1e86f`）深色三栏 UI 重构（v1.2.0），抽出 `ui_kit.py`
> **更新文档/代码后，请更新此行**（日期 + 新 commit hash），并在 CHANGELOG 追加版本

## 技术栈

- Python **3.12+**（本机 3.13.15），**纯标准库**：`tkinter`（**自绘控件，不用 ttk**）/ `subprocess` / `threading` + `queue` / `urllib.request` / `json` / `webbrowser`
- 两文件结构（v1.2.0 起）：`视频方案启动器.py`（配置 + 业务 + 界面装配） + `ui_kit.py`（深色自绘控件库 + 设计令牌）
- 打包：PyInstaller（`VideoLauncher.spec`，单文件、`console=False`、`upx=True`；会自动跟随 `import ui_kit`）
- 宿主链路：PowerShell 5.1 → `video-upscale-deploy` 下的 `.ps1` → ComfyUI（`127.0.0.1:8188`）

## 关键坑（改代码前先看）

- **PS 执行策略**：本机为 `Restricted`，`.ps1` 一律用 `powershell -NoProfile -ExecutionPolicy Bypass` 拉起。
- **不要用 `-File`**：必须用 `-Command` 包一层，先设 `[Console]::OutputEncoding=[System.Text.Encoding]::UTF8` 和 `$env:PYTHONIOENCODING='utf-8'`；否则 PS 5.1 按 cp936 重编 python 的 UTF-8 输出，**日志全变乱码**。
- **探测本机服务必须绕代理**：Clash 会把 `127.0.0.1` 也代理掉并返回 502。一律走 `NO_PROXY_OP`（`ProxyHandler({})`），**不要用 `urlopen`**。
- **长任务不许同步等**：SeedVR2 单条 20+ 分钟。用子进程 + 线程读 stdout + `queue` + `root.after(150)` 刷新，UI 不能阻塞。
- **后台线程绝不碰控件**：tkinter 非线程安全。线程里**只能**写 `logq` / `uiqueue`，`root.after` 与控件调用一律留给主线程（v1.2.0 曾因此抛 `main thread is not in main loop`）。
- **ffmpeg 不靠注册表**：`_comfy_env()` 把 `ffmpeg_dir` 显式前置到子进程 `PATH`；本机实测出现过「父进程改了 PATH 但传不到子进程」。
- **H3 参数在网页里调**：启动器只负责把服务与网页拉起来，工作流（提示词 / 帧数 / 步数）在 ComfyUI 里自己搭。
- **自绘控件的颜色参数要归一化**：Tk 不认 CSS 的 `"transparent"`，写进 `outline` 会抛 `unknown color name`。
- **一行里固定元素先 `pack`**：顶栏「按钮组 + 弹性 URL」必须先给按钮组分配空间，否则窄窗口会把按钮挤出卡片。
- **像素走 `S()`、字号走 `f()`**：`ui_kit.S()` 按 DPI 缩放像素，字号用磅值交给 Tk 缩放，两者才同比例。

## 约定

- UI 文案、注释、日志**全中文**；**零第三方依赖**，不引入 `requests` / `psutil` 等（打包体积与离线可用性优先）。
- **左导航只放「会切换中栏内容」的入口**：中栏已常显的东西（置顶的服务状态条、舞台首屏的工作流卡）不做导航项 —— 那只是"滚一下"，做成页面内滚动链接即可（v1.2.1）。任何改变"当前引擎"的入口统一走 `_select_nav`，不该跳视野时用 `scroll=False`。
- **模块拆分按阈值**：单文件逻辑行超过 pylint 默认上限（**1000**）才拆；`ui_kit.py` 只放控件与设计令牌，**不 import 业务代码**（保持单向依赖）。
- 视觉改动必须**对齐设计稿**：蓝本见分支 `ui-redesign` 的 `redesign/video_launcher_sidebar.html`；改完跑截图验证（`radon`/像素亮度），别只靠"看着像"。
- 路径不写死：新增路径一律先进 `CONFIG` 字典，并允许 `启动器配置.json` 覆盖。
- 打包产物（`build/`、`dist/`）不入库，发行走 Releases。

## 常用命令

```bash
python 视频方案启动器.py            # 直接运行
pyinstaller VideoLauncher.spec     # 打包单文件 exe
```

## 验证纪律（静态 + 运行期双轨）

改动后四件事都要跑，别只跑一件：

1. `py_compile` 语法
2. 静态：`pylint --enable=E` / `radon mi,cc` / `vulture`
3. 运行期冒烟：实跑源码，盯 stderr 有无 Traceback
4. 渲染验证：截图 + 量化亮度（深色应落在 40~55/255），改 UI 时必跑

## 详细规则（按需 @引用）

- @DEVELOPMENT.md —— 架构说明 + 关键问题与方案（一坑一篇）
