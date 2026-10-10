# AGENTS.md · 项目规则

> 📌 **文档基线**：2026-10-10（commit `391a7bc`）P1 收尾（子进程参数口径 / 显式判 `returncode` / 静默异常补理由）+ ComfyUI 日志保真（v1.2.6 / v1.2.7）
> **更新文档/代码后，请更新此行**（日期 + 新 commit hash），并在 CHANGELOG 追加版本

## 技术栈

- Python **3.12+**（本机 3.13.15），**纯标准库**：`tkinter`（**自绘控件，不用 ttk**）/ `subprocess` / `threading` + `queue` / `urllib.request` / `json` / `webbrowser`
- 三文件结构（v1.2.5 起）：`视频方案启动器.py`（配置 + 业务 + 界面装配） + `ui_kit.py`（深色自绘控件库 + 设计令牌） + `运行时.py`（与界面无关的运行时支撑：`NO_WINDOW` / 绕代理 HTTP / 崩溃落盘 / **日志行解析与清洗**）
- 打包：PyInstaller（`VideoLauncher.spec`，单文件、`console=False`、**`upx=False`**；会自动跟随 `import ui_kit` / `import 运行时`）
- 宿主链路：PowerShell 5.1 → `video-upscale-deploy` 下的 `.ps1` → ComfyUI（`127.0.0.1:8188`）

## 关键坑（改代码前先看）

- **PS 执行策略**：本机为 `Restricted`，`.ps1` 一律用 `powershell -NoProfile -ExecutionPolicy Bypass` 拉起。
- **不要用 `-File`**：必须用 `-Command` 包一层，先设 `[Console]::OutputEncoding=[System.Text.Encoding]::UTF8` 和 `$env:PYTHONIOENCODING='utf-8'`；否则 PS 5.1 按 cp936 重编 python 的 UTF-8 输出，**日志全变乱码**。
- **探测本机服务必须绕代理**：Clash 会把 `127.0.0.1` 也代理掉并返回 502。一律走 `运行时.NO_PROXY_OP`（`ProxyHandler({})`），**不要用 `urlopen`**；业务代码统一调 `rt.http_json()` / `rt.http_post_json()`。
- **每个子进程都要带 `creationflags=NO_WINDOW`**（v1.2.5）：exe 是 `console=False` 的 GUI 程序，spawn 控制台程序（`powershell` / `nvidia-smi` / ComfyUI 的 `python`）时 Windows 会**新开一个可见控制台窗口** —— 探测线程每 5 秒调一次 `nvidia-smi`，桌面就不停闪黑窗（用户报"打开了会一直闪窗口"）。`_run_ps` 用 `NO_WINDOW | CREATE_NEW_PROCESS_GROUP`。
- **windowed 程序没有 stderr，异常必须落盘**（v1.2.5）：`main()` 里先 `install_crash_log()`、建 root 后 `watch_tk_errors(root)`，未捕获异常统一写同目录 `崩溃日志.log`。没有它，"闪退"是零信息问题。
- **长任务不许同步等**：SeedVR2 单条 20+ 分钟。用子进程 + 线程读 stdout + `queue` + `root.after(150)` 刷新，UI 不能阻塞。
- **后台线程绝不碰控件**：tkinter 非线程安全。线程里**只能**写 `logq` / `uiqueue`，`root.after` 与控件调用一律留给主线程（v1.2.0 曾因此抛 `main thread is not in main loop`）。
- **静默吞异常必须写明理由**（v1.2.7）：`except Exception: pass` 本身可以是正确选择（退出路径、非关键信息降级），但**不许不写注释**。且优先"显式判掉正常失败"（`returncode != 0`、`read() is None`）而不是靠后续 `IndexError` 兜住 —— 后者让"预期失败"变成"意外异常"，行为测试里有一条逐行扫裸 `pass` 的守卫。
- **每个子进程的参数口径统一**（v1.2.7）：`text=True` 一律配 `encoding="utf-8", errors="replace"`，`creationflags=NO_WINDOW` 必带。缺 `errors` 会让非 UTF-8 输出变成 `UnicodeDecodeError`，看着"降级正常"其实过程不可控。
- **主线程心跳要 `try/finally`**（v1.2.5）：`_drain_log` 的 `root.after` 重排程必须在 `finally` 里；否则任一回调抛异常就会让刷新**永久停摆**（v1.1.2 同类静默故障）。
- **进日志区的行必须先 `strip_ansi()`**（v1.2.6）：ComfyUI 新版给每条日志**无条件**加 ANSI 颜色码（连 `isatty` 都不判断），重定向到管道照样吐 `ESC[1mESC[32m[INFO]ESC[0m`；Tk 的 `Text` 不解析转义序列，会把它显示成方块 —— 每行都是乱码。**任何新增的日志来源（新的子进程、新的文件读取）都要过这一道**。
- **只有本程序 spawn 的进程才有日志可读**（v1.2.6）：接日志靠的是"我持有的管道"，不是"服务在跑"。外部已启动的 ComfyUI 输出绑在别的终端上，本程序没有句柄 —— 日志区不会出现 `[ComfyUI]` 行，这不是 bug。别为了"显示外部服务的日志"去猜实现。
- **解析进度别用 `x/y` 当唯一信号**（v1.2.6）：日志里 `16/9`（宽高比）、`0.0001/0.0002`（小数）、`7.6/12.0G`（显存）都会被误判，实测把百分比标签写成过 **177%**。规则在 `运行时.progress_pct()`：优先 `n%` → 分数要求 `a ≤ b` → 分数前不能是数字/小数点 → 写入前夹 0~100。**测试解析器要拿真实反例，只测正例等于没测**。
- **给日志上色别只认中文关键词**（v1.2.6）：ComfyUI 是英文日志，`[ComfyUI] [ERROR] ...` 里没有"失败/错误"，会被涂成普通灰。优先用机器生成的级别标记（`运行时.log_level()` → `tag_for()`）。
- **量尺寸前先跑 idle**（v1.2.5）：Tk 几何计算是 idle 任务，刚 `pack` 完读 `winfo_reqheight()` 拿到的是**旧值**（实测 263 vs 500）。任何"按需求高度自适应"的实测前必须 `update_idletasks()`，并加标志防重入。
- **算容器需求别手数 pady**（v1.2.5）：用 `子控件.winfo_rooty() − 容器.winfo_rooty() + 子控件.winfo_reqheight()` 取最大值；手写 `A + S(14) + B` 实测漏算过 14px。
- **ffmpeg 不靠注册表**：`_comfy_env()` 把 `ffmpeg_dir` 显式前置到子进程 `PATH`；本机实测出现过「父进程改了 PATH 但传不到子进程」。
- **H3 参数在网页里调**：启动器只负责把服务与网页拉起来，工作流（提示词 / 帧数 / 步数）在 ComfyUI 里自己搭。
- **自绘控件的颜色参数要归一化**：Tk 不认 CSS 的 `"transparent"`，写进 `outline` 会抛 `unknown color name`。
- **一行里固定元素先 `pack`**：顶栏「按钮组 + 弹性 URL」必须先给按钮组分配空间，否则窄窗口会把按钮挤出卡片。
- **像素走 `S()`、字号走 `f()`**：`ui_kit.S()` 按 DPI 缩放像素，字号用磅值交给 Tk 缩放，两者才同比例。

## 约定

- UI 文案、注释、日志**全中文**；**零第三方依赖**，不引入 `requests` / `psutil` 等（打包体积与离线可用性优先）。
- **左导航只放「会切换中栏内容」的入口**：导航项只有三个引擎。服务卡 / 工作流卡 / 设置都是**常显区**，不做导航项（v1.2.1、v1.2.3 两次收敛同一类问题）。引擎切换的唯一入口是 `_select_nav`，不该跳视野时用 `scroll=False`。
- **三栏分工（v1.2.5 起为 2×3 网格）**：上排 左＝导航 + **设置常驻**（`_build_settings_section`）/ 中＝滚动舞台（只放引擎卡，无小标题、无切换按钮）/ 右＝**服务卡 → 工作流卡**；下排＝**运行日志横跨中栏 + 右栏**（`row=1, column=1, columnspan=2`），左栏 `rowspan=2` 通高。**卡片换容器＝换可用宽度**，搬动时按新栏宽重排（横排→纵排 / 网格，重设 `wraplength`）。`_scroll_to()` 只对还在 `self.stage` 里的控件有效。
- **上排高度＝内容实测，剩余全给日志**（v1.2.5）：上排**不给 weight**，`_sync_top_height()` 实测后写回 `rowconfigure(0, minsize=...)`；下排 `weight=1` 吃剩余。**只在内容变化时调用**（切引擎 / 选工作流 / 下拉展开收起）—— 在 `<Configure>` 里无条件调会和 `<Configure>` 形成布局回环。就地展开的 `Dropdown` 会撑高卡片，故新增下拉要挂 `_watch_expand()`。
- **窗口尺寸**（v1.2.5）：1260×960 / `minsize` 1040×940，且 `min(设计值, 屏幕可用区)` 夹住 —— 高分屏下 `S()` 会放大（125% 时 `S(900)=1125`），不夹的话 1080p 屏上窗口底部（日志）会被顶到屏幕外。**改布局后必须重算每行需求高度**，否则下排会把上排挤到截断（按钮落折叠线下）。渲染验证要断言"控件底 ≤ 可视底"与"实际高 ≥ 请求高"。
- **模块拆分按阈值**：单文件逻辑行超过 pylint 默认上限（**1000**）才拆。`ui_kit.py` 只放控件与设计令牌；`运行时.py` 只放与界面无关的运行时支撑（包括**日志行解析与清洗**：`strip_ansi` / `progress_pct` / `log_level` / `tag_for`），**两者都不 import 业务代码**（保持单向依赖：主程序 → 这两个模块）。分层要求：解析层只返回语义（级别名、标签名），**配色只由界面层的 `tag_config` 决定**。
- **打包前先清进程 + 包后核对时间戳/体积**（v1.2.4 教训）：运行中的 exe 会锁住 `dist`，`--clean` 删不掉旧产物 → 构建中止却"看起来成功"。
- 视觉改动必须**对齐设计稿**：蓝本见分支 `ui-redesign` 的 `redesign/video_launcher_sidebar.html`；改完跑截图验证（`radon`/像素亮度），别只靠"看着像"。
- 路径不写死：新增路径一律先进 `CONFIG` 字典，并允许 `启动器配置.json` 覆盖。
- 打包产物（`build/`、`dist/`）不入库，发行走 Releases。

## 常用命令

```bash
python 视频方案启动器.py                                          # 直接运行
taskkill /F /IM VideoLauncher.exe                                # 打包前清残留（会锁 dist）
pyinstaller VideoLauncher.spec --noconfirm --clean                # 打包单文件 exe
```

## 验证纪律（静态 + 运行期 + 渲染三轨）

改动后五件事都要跑，别只跑一件：

1. `py_compile` 语法
2. 静态：`pylint --enable=E` / `radon mi,cc,raw` / `vulture`（并确认主文件逻辑行 <1000）
3. 运行期冒烟：实跑源码，盯 stderr 有无 Traceback
4. 渲染验证：截图 + 量化亮度（深色应落在 40~55/255）+ **落点/防截断断言**，改 UI 时必跑
5. **改动涉及子进程或打包时**：exe 也要单独验一次，并用「数可见控制台窗口」的方式确认没有黑窗闪现

验证脚本在工作区 `../_audit/`（不入仓库）：`test_behavior.py`（行为断言）、`shot_ui.py`（渲染+落点）、`measure_layout.py`（布局量化）、`test_comfy_log.py`（**日志采集端到端**：仿 ComfyUI 子进程 → 真实 `svc_start()`，含修复前后对照与截图）、`watch_flash.py`（闪窗端到端）、`verify_exe.py`（exe 冒烟）、`probe_console.py`/`watch_console.py`（闪窗最小复现）。

## 详细规则（按需 @引用）

- @DEVELOPMENT.md —— 架构说明 + 关键问题与方案（一坑一篇）
