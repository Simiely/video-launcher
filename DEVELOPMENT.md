# DEVELOPMENT.md · 视频方案启动器

## 项目概览

Python GUI，管理本地三套视频 AI 方案。定位是**编排器**：自己不做推理，只负责「拉服务 / 传参调脚本 / 回传日志」。

- 组成（v1.2.5 起为三文件，零第三方依赖）：
  - `视频方案启动器.py` —— 配置 + 业务逻辑 + 界面装配（约 1190 行 / 逻辑行 984）
  - `ui_kit.py` —— 深色自绘控件库 + 设计令牌（约 537 行，**不 import 业务代码**）
  - `运行时.py` —— 与界面无关的运行时支撑（约 105 行 / 逻辑行 73）：子进程"不弹黑窗"标记、
    崩溃落盘、绕代理 HTTP。**不 import 业务代码，也不 import tkinter / ui_kit**（单向：主程序 → 运行时）
- 界面：深色 Fluent **三栏控制台**，蓝本为分支 `ui-redesign` 的 `redesign/video_launcher_sidebar.html`
- **三栏分工（v1.2.5 收敛后的稳定形态）**：整体是 **2×3 网格**——上排左 / 中 / 右三栏，下排一整条运行日志（`columnspan=2`，横跨中栏 + 右栏）
  - **左栏**（跨两行、通高）＝导航（三个引擎，NavItem）+ **设置常驻**（配置只读展示 + 打开目录）。左栏是"导航 + 常驻信息"，不承载会切换的内容
  - **中栏**（上排）＝滚动舞台，只放**引擎参数卡**（单卡，无小标题、无切换按钮）。引擎切换只有左导航一个入口
  - **右栏**（上排）＝自上而下 **服务卡 → 工作流卡**，都常显不滚动
  - **下排**＝**运行日志卡**（含进度条），横跨中栏 + 右栏
- 什么地方放什么（v1.2.3 定的规矩）：**常显区不进导航项**；"点一下就换掉中栏内容"的才是导航项。所以服务卡 / 工作流卡 / 设置都不是导航项
- **上排高度＝内容实测，剩余全给日志**（v1.2.5）：上排**不给 weight**，由 `_sync_top_height()` 用 `winfo_reqheight()` 实测后写回 `rowconfigure(0, minsize=...)`；下排 `weight=1` 吃掉所有剩余高度。**只在内容变化时调用**（切引擎 / 选工作流 / 下拉展开收起），不能在 `<Configure>` 里无条件调，否则会跟 `<Configure>` 形成布局回环
- **窗口尺寸**（v1.2.5）：1260×960、`minsize` 1040×940，且用 `min(设计值, 屏幕可用区)` 夹住 —— 高分屏下 `S()` 会放大（125% 时 `S(900)=1125`），不夹的话 1080p 屏上窗口底部（运行日志）会被顶到屏幕外。**改动行/列结构后必须重算需求高度**，否则下排会把上排挤到截断
- 入口：`main()` → `install_crash_log()` → `ui_kit.init_scaling()` → `App(root)` → `watch_tk_errors(root)` → `root.mainloop()`
- 配置：模块级 `CONFIG` 字典 + 可选同目录 `启动器配置.json` 覆盖
- 交付：源码直接运行，或用 PyInstaller 打成单文件 exe（会跟随 `import ui_kit` / `import 运行时` 自动打进去）

## 架构说明

```
main()
 ├─ install_crash_log() / watch_tk_errors(root)   异常兜底落盘（GUI 程序没有 stderr）
 └─ App(root)
     ├─ _build_ui()            2×3 网格：上排三栏 + 下排日志（columnspan=2）
     │   ├─ _build_sidebar()   左栏（rowspan=2，通高）：导航（NavItem：三个引擎）+ _build_settings_section()
     │   ├─ _build_center()    中栏（上排）：滚动舞台 → _build_engine_area()（引擎单卡）
     │   ├─ _build_right()     右栏（上排）：服务卡 → 工作流卡
     │   ├─ _build_logpane()   下排：运行日志卡（row=1, column=1, columnspan=2）
     │   └─ _sync_top_height() 上排高度＝内容实测需求，剩余高度全给下排日志
     ├─ 导航 / 滚动
     │   ├─ _select_nav(key, scroll=True)  切引擎（重绘引擎卡）+ 滚到引擎卡（scroll=False 只切不滚）
     │   ├─ _scroll_to(widget)  按控件 y 换算 yview_moveto
     │   ├─ _watch_expand(dd)   就地展开的下拉会撑高卡片 → 触发上排高度重新实测
     │   └─ _flash_wf()         引擎卡里的「工作流选择在右栏上方 ↑」→ 描边闪烁提示位置
     ├─ 服务控制
     │   ├─ svc_start()        Popen(comfy_py -u main.py --disable-pinned-memory
     │   │                          --disable-async-offload --reserve-vram 1, creationflags=NO_WINDOW)
     │   ├─ svc_restart()      先终止自有进程 → _restart_worker 轮询等服务退净 → 再起
     │   ├─ _svc_reader()      线程：逐行读 stdout → queue
     │   ├─ _svc_wait_ready()  线程：3s 轮询 /system_stats，最多 5 分钟，就绪自动开网页
     │   ├─ svc_stop()         只终止自己拉起的进程（外部启起的会明确提示）
     │   └─ open_web()         未运行时先 svc_start()，就绪后自动开页
     ├─ 批量任务
     │   ├─ _run_ps()          拼 PS 命令行 + Popen（creationflags=NO_WINDOW|NEW_PROCESS_GROUP）
     │   ├─ _task_reader()     线程：回传日志，结束打印 rc / 耗时
     │   ├─ run_flashvsr()     -InputPath / -Mode / -Scale / -Format / -AutoStart
     │   └─ run_seedvr2()      -InputPath / -Profile / -Resolution / -Overwrite
     ├─ 工作流
     │   ├─ _on_wf_select()    选内置工作流 → 详情面板 + 切引擎并同步导航高亮（不滚动）
     │   ├─ _load_wf_json()    读本地 .json → 数节点 → 存 _loaded_api
     │   └─ _push_wf()         rt.http_post_json() 对已加载的 API 图 POST /prompt
     ├─ 后台探测
     │   ├─ _probe_loop()      每 5s 一次（root.after，主线程）
     │   ├─ _probe_once()      线程：rt.http_json(/system_stats) 取版本 + 显存；失败退回 nvidia-smi
     │   └─ _apply_probe()     主线程：刷圆点 / 徽标 / 按钮态
     └─ on_close()             退出前确认中断任务 / 是否一并关服务

运行时.py（与 tkinter 无关）
 ├─ NO_WINDOW = CREATE_NO_WINDOW      所有子进程都要带，否则 GUI 程序里会闪控制台窗口
 ├─ ps_quote(s)                       PowerShell 单引号转义
 ├─ NO_PROXY_OP / http_json()         绕开系统代理取 JSON（Clash 会代理 127.0.0.1 → 502）
 ├─ http_post_json()                  POST JSON，失败返回错误字符串
 ├─ install_crash_log(app_dir)        主线程 / 后台线程异常落盘，返回日志路径
 ├─ watch_tk_errors(root, path)       Tk 回调异常落盘
 └─ write_crash(path, kind, ...)      追加写「崩溃日志.log」
```

**线程模型（v1.2.0 收紧，v1.2.5 加兜底）**：GUI 主线程是**唯一**允许操作控件的地方。子进程读日志、服务就绪轮询、状态探测都在 daemon 线程，结果只做两件事：写 `logq`（日志文本）或往 `uiqueue` 投**回调**；主线程的 `_drain_log` 每 150ms 统一排空。**后台线程里绝不出现 `root.after` / 控件调用**。`_drain_log` 用 `try/finally` 保证 `after` 一定续上，单个回调抛异常只记一笔、不拖垮整个心跳。

**代理隔离**：`NO_PROXY_OP = build_opener(ProxyHandler({}))` 专用于本机服务探测，与系统 Clash 代理彻底隔离；统一封装为 `运行时.http_json()` / `http_post_json()`，业务代码不再各写一遍 `try/except`。

**依赖注入方式**：所有外部路径集中在 `CONFIG`，`_comfy_env()` 统一为子进程准备环境（PATH 前置 ffmpeg + `PYTHONIOENCODING=utf-8`），服务进程与批量任务共用同一套环境构造。

## 关键问题与方案

### 问题：PS 5.1 把 python 的 UTF-8 输出重编成 cp936 → 日志乱码

**TL;DR**：不用 `-File`，用 `-Command` 包一层并先设 `[Console]::OutputEncoding`。

- 问题：批量脚本回传的中文日志在 GUI 里全是乱码
- 根因：PowerShell 5.1 控制台输出编码默认 cp936，重编了 python 写出的 UTF-8 字节
- 解决：`_run_ps()` 统一用 `-Command "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $env:PYTHONIOENCODING='utf-8'; & <script> <args>"`
- 预防：任何新起的 PS 子进程都走 `_run_ps()`，不要另开 `-File` 路径

### 问题：探测本地 ComfyUI 返回 502

**TL;DR**：Clash 把 `127.0.0.1` 也代理了，必须绕代理。

- 问题：服务明明起着，`comfy_alive()` 却返回 False
- 根因：系统代理（Clash）接管了 `127.0.0.1:8188` 的请求，返回 502
- 解决：专用 `NO_PROXY_OP`（空 `ProxyHandler`）发请求
- 预防：本机服务探测一律不用 `urlopen`

### 问题：SeedVR2 跑 20 分钟把界面卡死

**TL;DR**：子进程 + 线程读 + queue + `after` 刷新，全异步。

- 问题：早期同步 `subprocess.run` 等任务返回，GUI 假死
- 根因：主线程被阻塞，Tk 事件循环停摆
- 解决：`Popen` + 读日志线程 + `queue`，主线程 `root.after(150)` 消费；任务期间禁用开跑按钮
- 预防：任何可能超过 1 秒的子进程调用都不许在 GUI 线程同步执行

### 问题：子进程找不到 ffmpeg

**TL;DR**：显式前置 PATH，不指望注册表。

- 问题：批量脚本内部报 ffmpeg 找不到
- 根因：某些宿主创建子进程时使用固定环境快照，父进程改的 `PATH` 传递不下去
- 解决：`_comfy_env()` 把 `CONFIG["ffmpeg_dir"]` 拼到子进程 `PATH` 最前，并设 `PYTHONIOENCODING=utf-8`
- 预防：新增外部工具依赖时同样走 `_comfy_env()` 显式注入

### 问题：H3 工作流无法由启动器代填参数

**TL;DR**：H3 是「文生视频 + 音画同出」，参数在 ComfyUI 网页工作流里，启动器只负责拉服务与开页。

- 问题：期望启动器一键出片，但 H3 的提示词 / 帧数 / 步数无法在 GUI 里完整表达
- 根因：H3 走的是模板库 Video → MiniMax H3 → T2V 的网页工作流，参数面较宽且需即时试调
- 解决：页签 ③ 只做「启动服务并打开网页」，把实测参数（864×480 / 124 帧 / 20 步，3 分 13 秒/条）写进界面提示
- 预防：后续若要 GUI 化 H3，应先固定一套 API 工作流（参考 `video-upscale-deploy/02-FlashVSR/workflow_api.json` 的做法）

### 问题：连点「启动服务」起多个 ComfyUI 进程

**TL;DR**：`svc_start()` 只用 `comfy_alive()` 判断，但服务就绪前它一直是 `False`，连点会在忙等期内 Popen 出第二个进程、首个句柄被覆盖成孤儿。

- 解决：新增 `_svc_starting` 启动中锁；`svc_start()` 命中锁直接 return，锁在「`_svc_wait_ready` 就绪 / `_svc_reader` 进程退出 / 5 分钟超时」三处统一解除。
- 预防：任何「拉起长启动进程」的入口都要有进行中锁，不能只靠 `comfy_alive()` 这类异步探测判断。

### 问题：长任务日志无限增长拖慢 UI

**TL;DR**：`ScrolledText` 只 `insert` 不裁剪，SeedVR2 跑 20+ 分钟后行数上千，每次插入 + `see("end")` 越来越慢、内存只涨不跌。

- 解决：`_drain_log` 在插入后若行数超过 `MAX_LOG_LINES = 2000` 就 `delete` 最旧的行（模块级常量，便于调）。
- 预防：任何会高频追加的文本控件都要设上限或环形缓冲，不能无脑 append。

### 问题：Tk `Text.count()` 返回 tuple，`int()` 会炸

**TL;DR**：不要用 `int(text.count("1.0","end","lines"))` 统计行数——`Text.count()` 返回的是 tuple；用自维护计数器替代。

- 问题：v1.1.0 的日志裁剪 `int(self.txt.count(...))` 一插入日志就抛 `TypeError`（`int() argument must be ... not 'tuple'`）
- 根因：Tk `Text.count()` 的返回值不是 int（本机实测为 tuple），`int()` 转换失败；异常在 `root.after` 回调里未被捕获，导致 `_drain_log` 不再重排程、日志刷新停摆
- 解决：改用 `self._log_lines` 自维护计数器，超 `MAX_LOG_LINES` 时 `delete("1.0","2.0")` 删最旧一行
- 预防：**只靠 pylint 静态检查抓不到这类运行期 API 契约错误**——必须补运行期冒烟（实跑源码/exe）。两类检查互补，缺一不可

### 问题：审计必须"静态 + 运行期"双轨

**TL;DR**：v1.1.0 一次改动引入两个 bug，分别由两类手段抓到。

- `_probe_once` 未定义变量 `alive` → 由 **pylint `E0602`**（静态）抓到
- `_drain_log` 的 `Text.count()` tuple 陷阱 → 由**运行期冒烟**（`python 视频方案启动器.py` 实跑观察 stderr）抓到
- 预防：代码改动后，静态（pylint/radon/vulture）+ 运行期（冒烟/行为探针）都要跑；见工作区审计脚本四件套

### 问题：后台线程调 `root.after` 抛 `main thread is not in main loop`

**TL;DR**：tkinter 非线程安全。后台线程**只能投递**（写 `logq` / `uiqueue`），一切控件操作留给主线程。

- 问题：v1.2.0 首版里 `_probe_once` 线程末尾调 `self.root.after(0, self._apply_probe)` 刷新状态，渲染验证时抛 `RuntimeError: main thread is not in main loop`
- 根因：非主线程调用 Tk 时，只有当主线程正处在 Tcl 事件循环里才可能被安全编组；一旦主线程不在 `mainloop`（例如以 `update()` 泵事件做自动化验证），就会直接抛错。即便在真实 `mainloop` 下"看起来能跑"，也属于未定义行为
- 解决：新增 `self.uiqueue = queue.Queue()`，后台线程只 `uiqueue.put(callback)`；主线程的 `_drain_log` 心跳每 150ms 排空并执行。`_probe_once` / `_task_reader` / `_restart_worker` 全部改走这条路
- 预防：**"后台线程里绝不出现 `root.after` 或任何控件调用"** 已固化成回归断言；纯非 Tk 的调用（如 `webbrowser.open`）可以直接在后台线程做

### 问题：深色圆角卡片在 tkinter 里怎么画

**TL;DR**：Canvas 自绘圆角多边形；`body` 四周内缩一个 `radius`，方角自然落在弧线内。

- 问题：tk 的 `Frame` 没有圆角，直接用矩形内容框会盖住圆角、露出四个直角
- 根因：`Frame` 的矩形内容区与圆角底图无法裁剪
- 解决：`RoundedFrame` = `Canvas`（`place` 铺满）+ `body`（`pack` 内缩 `radius`）。内缩 `radius` 后，`body` 的直角恰好落在圆角弧的**圆心**上，永远在弧线内 → 不露方角；再叠 `pad` 得到设计稿的 16~18px 内边距
- 阴影：tk 无 alpha 通道，用两层 `stipple="gray25"/"gray12"` 的黑色圆角块近似柔和阴影
- 预防：任何"圆角 + 内容"的容器都走 `RoundedFrame`，不要另造轮子

### 问题：Tk 把 `"transparent"` 当非法颜色名抛错

**TL;DR**：`None` / `"transparent"` 必须在画之前归一化成"无描边"。

- 问题：自绘按钮想用 `border="transparent"` 表达"不要描边"，Tk 抛 `unknown color name "transparent"`
- 根因：`"transparent"` 是 CSS 概念，Tk 颜色表里没有
- 解决：`RButton._draw` 里 `border = None if border in (None, "transparent") else border`，描边缺省时 `outline=fill`（视觉上不可见）
- 预防：自绘控件对外接受的颜色参数都要先归一化，别把 web 习惯带进 Tk

### 问题：窄窗口下顶栏按钮被挤出卡片

**TL;DR**：右侧固定元素用 `side="right"` 先占位，可伸缩的 URL 最后 `pack` 吃剩余宽度。

- 问题：顶栏按 `side="left"` 依次 `dot / 名称 / 徽标 / URL(expand) / 按钮组`，窗口一窄，URL 仍按自身请求宽度占位，把「打开网页 / 停止服务」顶出卡片右边界
- 根因：`pack` 按装箱顺序先满足先装的 slave；`expand` 只分配**剩余**空间，不会让已装的 URL 收缩
- 解决：按钮组（`停止 / 打开网页 / 启动`）先 `side="right"`，`URL` 最后 `side="left", expand=True`；并把显存/版本信息移到右栏标题行，顶栏只留设计稿里的元素
- 验证：截图脚本里加断言 `末按钮右缘 <= 卡片右缘`
- 预防：一行里"固定元素 + 弹性元素"并存时，固定元素一定先 `pack`；弹性元素放最后

### 问题：高分屏下界面发虚 / 尺寸不成比例

**TL;DR**：`SetProcessDpiAwareness(1)` + 按实际 DPI 换算像素；字号走磅值由 Tk 自动缩放。

- 解决：`ui_kit.enable_dpi_awareness()`（必须在建窗口前调）→ `ui_kit.init_scaling(root)` 设 `SCALE = winfo_fpixels("1i")/96`、`tk scaling = .../72`、挑本机存在的中文 UI 字体
- 约定：自绘控件的**像素**常量一律走 `S(v)`（乘 `SCALE`）；**字体**用磅值 `f(pt)` 交给 Tk 缩放 —— 两者才能同比例
- 预防：新增硬编码尺寸时用 `S()` 包一层，别写裸像素

### 问题：什么时候该从单文件拆分

**TL;DR**：按 `AGENTS.md`「超阈值才拆分」——超过 pylint 默认模块上限（1000 逻辑行）就拆。

- 背景：v1.2.0 把 UI 改成自绘后，单文件从 477 行涨到 1495 行、逻辑行 1268 > 1000，radon MI 从 A(26.0) 掉到 **C(0.00)**
- 依据：项目约定「单文件交付，超阈值才拆分」；阈值取 pylint 默认 `max-module-lines = 1000`
- 解决：抽出 `ui_kit.py`（控件库 + 设计令牌，532 行 / 逻辑行 ~400 / MI **B(19.0)**），主程序回到 1035 行 / 逻辑行 890
- 遗留：主程序 MI 仍为 C(0.99)——890 逻辑行的单个 `App` 类 Halstead 体积天然高；若还要改善，可把 `App` 的卡片构建方法外移成 `ui_cards.py`
- 预防：拆分判据用**可核查的数字**（radon SLOC / pylint 阈值），不靠"感觉文件有点长"

### 问题：左导航该放哪些入口

**TL;DR**：只放「会切换中栏内容」的入口；中栏已常显的东西不要再占导航项，否则是重复入口且让导航失去"切换"语义。

- 问题：v1.2.0 按设计稿把「服务 / 工作流」也做成了导航项，但这两块本来就在中栏：服务状态条置顶**不滚动**常显、工作流卡是舞台**首屏第一张**卡。导航点它们只是"滚一下"，属于重复
- 附带问题：程序启动时 `_select_nav("flashvsr")` 会自动滚到引擎参数卡，把工作流卡顶出视野 —— 于是"工作流在中栏可见"这件事在默认视图里并不成立
- 解决：① 导航去掉 `service` / `workflow` 两项，只留引擎三项 + 设置；② `_select_nav` 增加 `scroll` 开关，启动用 `scroll=False` 让中栏停在顶部（服务条 + 工作流卡首屏可见）；③ 引擎卡里的「从工作流库选择 →」改为纯 `_scroll_to(wf_card)`，不再借道导航（否则会顺带清空导航高亮）
- 预防：加导航项前先问「它对应的内容是否会随点击**变化**」。只是"跳到某块常显内容"的，做成页面内链接/滚动，别做成导航项

### 问题：导航高亮与引擎标签不一致

**TL;DR**：任何"绕过导航"直接切引擎的路径，都要顺手同步导航高亮。

- 问题：从工作流下拉选一套内置工作流时，`_on_wf_select` 直接调 `_select_engine()` 切了引擎标签，但左导航仍高亮旧项 —— 两处状态说两套话
- 解决：改为 `self._select_nav(wf["engine"], scroll=False)`：既切引擎又同步高亮，且不滚动（避免把刚展开的工作流详情面板甩出视野）
- 预防：凡是能改变"当前引擎"的入口，统一走 `_select_nav`；`scroll=False` 供"不该跳视野"的场景使用

### 问题：把常显卡片从宽栏挪进窄栏要重排

**TL;DR**：同一张卡片换个容器就等于换了一套可用宽度，横排元素必须改纵排 / 网格，换行宽度按新栏宽重设，别指望 `expand` 自动兜住。

- 背景（v1.2.2）：用户要求把「加载工作流」区域移到右栏、放在运行日志上方。右栏可用宽只有 ~340px（原中栏 ~600px）
- 问题：原卡片在中栏是横排设计 —— 标题 + 「6 套内置」+ 按钮一行、`工作流` 标签 + 下拉 + `— 节点` 一行、详情面板 4 项元信息横排。搬进窄栏后这些行全挤在一起
- 解决：① 标题行只留图标 + 标题 + 按钮（「N 套内置」下沉到底部小字行）；② 下拉独占一行（标签提到它上面）；③ 详情面板 `wraplength` 560~600 → 250，头部徽标改 `side="right"`、名称可换行；④ 元信息 4 项改 **2×2 `grid`**；⑤ 两个动作按钮 `stretch=True` 等宽
- 附带：右栏变宽（312 → 360）会挤压中栏，中栏窄了顶栏 URL 就会被截断 → 默认窗口宽度同步 1180 → 1260
- 预防：**卡片式布局别假设容器宽度**。换容器时先量新栏宽，再决定横排/纵排/网格与换行宽度

### 问题：跳转链接指向的卡片"不再可滚动"时

**TL;DR**：滚动定位只对"在滚动容器里"的控件有意义；控件移到固定区后，改为视觉提示。

- 背景（v1.2.2）：引擎卡底部的跳转链接原本是 `_scroll_to(self.wf_card)`，但工作流卡已移出滚动舞台、固定在右栏
- 问题：`_scroll_to()` 用 `widget.winfo_y()` 计算 `yview_moveto`，目标不在舞台内时算出来的是相对其新父容器的坐标 —— 会跳到毫无意义的偏移
- 解决：文案改「工作流选择在右栏上方 ↑」，行为改 `_flash_wf()` —— 给 `RoundedFrame` 加 `set_border()`，把卡片描边切成强调蓝、500ms 后恢复（不改变卡片配色，只做一次"亮边一闪"）
- 预防：**凡是 `_scroll_to(某控件)`，先确认该控件确实还在 `self.stage` 里**。移出滚动区后必须同步撤掉滚动绑定

### 问题：常显内容不该做成"点导航 → 中栏刷出一页"

**TL;DR**：判断"该不该做导航项"的标准是**这块内容是否要独占中栏**；只想顺手看一眼的常驻信息，应该放进常驻区。

- 背景（v1.2.3）：设置原本是左导航项，点它 `_scroll_to(settings_card)` —— 中栏会只剩设置卡（引擎卡被滚走），观感像"另开了一页"
- 解决：设置改成 **左栏导航下方常驻区块**（`_build_settings_section(b)`），同时删掉 `settings_card`、`nav_items["settings"]`、`_select_nav` 的 settings 分支
- 预防：导航项 = "切换中栏内容"的入口。服务卡 / 工作流卡 / 设置这类常显区一律不做导航项（v1.2.1、v1.2.3 连踩两次同一类）

### 问题：一行 hold 不住的状态条塞进窄栏

**TL;DR**：窄栏里放不下就拆行；按钮组用 `stretch=True` + `fill="x", expand=True` 等宽摊开。

- 背景（v1.2.3）：ComfyUI 服务卡从中栏（~600px）挪到右栏（~324px）
- 问题：原状态条一行放下「圆点 + 标题 + 徽标 + URL + 三个按钮」（约 620px）。窄栏里继续 `pack(side="right")` 会被裁掉
- 解决：拆三行 —— 状态行（圆点 / 标题 / 徽标，右端挂版本·显存）+ 地址行 + 等宽按钮行
- 附带：版本·显存原挂在日志标题行（当时中栏服务条太窄），服务卡搬回右栏后跟着服务走，日志标题行只剩「运行日志 / 清空」
- 预防：搬家后先算新栏可用宽（`宽 - 2×(radius + pad)`），再决定横排还是拆行

### 问题：同一功能的第二个入口就是负债

**TL;DR**：同一个状态只留一个切换入口；多余入口不仅要维护同步，还会掩盖"哪个才是主入口"。

- 背景（v1.2.3）：左导航已能切引擎，中栏另有一排 `FlashVSR / SeedVR2 / MiniMax H3` 切换按钮
- 问题：两处入口必须互相同步（v1.2.1 刚修过一次「导航高亮与引擎标签不一致」），还白占首屏高度
- 解决：删掉 tab 行与 `tab_btns` / `_select_engine()`，`_select_nav()` 直接 `_render_engine(key)`
- 预防：新加入口前先问"这个状态是不是已经有入口了"

### 问题：截图裁剪不能用"早先缓存的坐标"（验证工具）

**TL;DR**：任何提前量好的屏幕坐标，都要在使用前**现算**；截图类验证必须加"产物尺寸/内容"断言 —— 否则裁出一张 1px 空图也会显示"深色正常"。

- 背景（v1.2.3）：`_audit/shot_ui.py` 要裁一张左栏特写，用的 x 右边界是页面加载后算好的 `sidebar.right`
- 问题：窗口会被 WM 挪位，缓存的坐标到截图时已失效 → 裁出 **1px×799 的空图**，而亮度量化照样输出 30.9（"看着是深色"），**断言不报错**
- 解决：改成截图这一刻现算（`grab(root, path, crop_to=(widget, pad))`）；再用 PIL 采样像素复核 —— `x=110` 处 y=100~760 恒为 `#232730`（= CARD），证明侧栏卡确实铺满高度，之前的"没铺满"是误读
- 预防：验证脚本的每个产物都断言尺寸或关键像素，别只看"亮度在区间内"

### 问题：跨列的下排会把上排挤到截断

**TL;DR**：改行/列结构后必须重算"每行每列的需求高度"再定窗口尺寸；验证要断言"控件底 ≤ 可视底"，不能只看亮度。

- 背景（v1.2.4）：运行日志从"右栏内的一段"搬到"跨中栏 + 右栏的下排"。上排高度从此 = 窗口高 − 下排 − 间距
- 问题：上排要同时容下引擎卡（需 ~542）与右栏（服务 128 + 工作流卡含详情 483 + 间距 ≈ 639）。加了下排后上排只剩 455~576 → **引擎卡被截断**（「运行放大」落在折叠线以下：实测 引擎卡底 540 > 可视底 469）、**工作流卡被压扁**（高 420 < 需要 483，「推送/入队」被吞）。而截图亮度照样 41~45"看着正常"
- 解决：① 按需求高度倒推窗口尺寸（639 + 14 + 180 + 28 ≈ 861 → 取 1260×900、minsize 1040×880）；② 下排用 `rowconfigure(1, minsize=S(180))` 固定，并给日志 `Text(height=4)` —— 否则 Tk `Text` 默认 24 行的请求高度会把下排顶高
- 预防：**行/列结构一变就先算需求高度**；渲染验证必须加 `控件底 ≤ 可视底` 与 `实际高 ≥ 请求高` 两条断言（默认窗口与最小窗口各测一次）

### 问题：运行中的 exe 锁住 dist，让下一次打包"看起来成功"其实没换

**TL;DR**：打包的判据是"产物时间戳变新 + 体积变化 + exe 冒烟过"，不是"命令没报错"。

- 背景（v1.2.4）：上一轮验证 exe 后进程没退干净
- 问题：PyInstaller `--clean` 要删 `dist/VideoLauncher.exe` 时被沙箱 safe-delete 拦下（`OSError: trash operation ... aborted`），构建中止；命令末尾 `tail -4` 只看到 dist 列表，几乎当成"构建成功"，而 dist 里还是**上一版** exe
- 解决：打包前先 `taskkill /F /IM VideoLauncher.exe` 清残留；打包后核对产物**时间戳与体积**（13286287 → 13286917 才说明真的换了），再对 exe 单独冒烟
- 预防：把"清进程 → 打包 → 核对时间戳/体积 → exe 冒烟"当成固定四步

### 问题：GUI 程序里 spawn 控制台子进程 → 桌面一直闪黑窗

**TL;DR**：打包成 `console=False` 的 GUI 程序后，**每一个**控制台子进程都要带 `creationflags=CREATE_NO_WINDOW`，否则 Windows 会给它单开一个可见控制台窗口。

- 背景（v1.2.5，用户报「打开了会一直闪窗口然后关闭」）：打包成 exe 后是 windowed 程序（没有控制台）
- 问题：程序里 `nvidia-smi`（探测线程，**每 5 秒一次**）、`powershell`（跑 .ps1）、ComfyUI 的 `python` 都是控制台程序。windowed 程序 spawn 它们时，Windows 默认新建控制台窗口 → 每次调用在桌面上"闪一下黑窗"，5 秒一次就是"一直闪"
- 定位方法：先用 `pythonw`（等价 windowed 环境）跑一个最小复现脚本，脚本里 spawn 一个带唯一窗口标题的 powershell；另一个进程用 `EnumWindows` 数 `ConsoleWindowClass` 的可见窗口 —— **不加标记 3/3 都弹窗，加 `CREATE_NO_WINDOW` 后 0/3**
- 解决：`运行时.NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)`，全部 3 处子进程（`svc_start` / `_run_ps` / `_gpu_by_nvidia_smi`）都带上；`_run_ps` 是 `NO_WINDOW | CREATE_NEW_PROCESS_GROUP`
- 端到端复核（真实程序跑 16~18 秒，覆盖 3 个探测周期）：**修复前新增 3 个可见控制台窗口，修复后 0 个**（源码与 exe 各测一次）
- 附带优化：`nvidia-smi` 第一次 `FileNotFoundError` 后就记住不再调用（既有机器没装、又避免反复 spawn 触发杀软启发式）
- 预防：**GUI 项目里"子进程调用"要当成一条契约来守** —— 行为测试逐块核对每个 `subprocess.Popen/run` 调用点都带了 `creationflags`

### 问题：GUI 程序闪退，却看不到任何报错

**TL;DR**：`console=False` 的 exe 里 `sys.stderr` 是 `None`，traceback 没有去处；必须自己落盘。

- 背景（v1.2.5）：用户报"打开后闪一下就关了"，但没有任何可看的信息 —— 因为 GUI 程序的 stderr 不存在，tkinter 默认的 `report_callback_exception` 也是往 stderr 打
- 解决：`运行时.install_crash_log(app_dir)` 装 `sys.excepthook` + `threading.excepthook`，`watch_tk_errors(root)` 覆盖 Tk 回调，统一追加写 exe 同目录的「崩溃日志.log」；`_drain_log` 里单个后台回调抛异常也记一笔
- 验证：故意在 ① Tk 回调 ② 后台线程 里 `raise`，确认都落盘；并对 `after(0) / after(1) / after(10) / after(150)` × 「有无 App」共 8 种组合全测一遍（8/8 都记到）
- 预防：**windowed 程序的"兜底日志"不是锦上添花** —— 没有它，"闪退"这类问题是零信息的

### 问题：`winfo_reqheight()` 在几何算完前是旧值，自适配会算错且无人纠正

**TL;DR**：Tk 的几何计算是 idle 任务；刚 pack 完就读 `winfo_reqheight()` 会拿到**旧值**。任何"按需求高度自适应"的逻辑，实测前都要先跑一次 `update_idletasks()`。

- 背景（v1.2.5）：新增 `_sync_top_height()` —— 用 `winfo_reqheight()` 实测内容需求，写回上排 `minsize`，剩余高度全给日志
- 问题：选中工作流后立刻读 `wf_card.winfo_reqheight()` 得到 **263**，跑一次 idle 才是 **500**。用它算出的需求偏小 → 上排没长高 → 工作流卡被压到 268（需要 483），底部「推送到 ComfyUI / 加入队列」掉出可视区。**更麻烦的是：没有任何后续事件会纠正它**，错误会一直留在界面上（`after_idle` 里读也一样可能早于几何计算）
- 解决：`_sync_top_height()` 内部先 `self.root.update_idletasks()` 再实测；用 `_syncing` 标志挡住 `update_idletasks` 可能触发的重入
- 预防：把"先 idle 再量"写成硬约束 —— 行为测试断言 `update_idletasks()` 出现在实测语句之前

### 问题：手工累加间距算容器需求会漏算

**TL;DR**：要算"某栏内容需要多高"，别去数各控件的 `reqheight` 加 pady —— 用**真实布局位置**：`子控件.y − 容器.y + 子控件.reqheight()`。

- 背景（v1.2.5）：`_sync_top_height()` 里手写 `服务卡 + S(14) + 工作流卡` 表示右栏需求
- 问题：漏算了工作流卡自己的底部 `pady=(0, S(14))` → 需求少算 14px → 容器 642 装 656 的内容 → pack 把**最后一个**控件压矮 14px（工作流卡 486 / 需要 500）。而且 `winfo_rooty()` 在控件被压矮时**不会变**，所以"按位置算"天然免疫这种偏差
- 解决：遍历容器子控件取 `child.winfo_rooty() - col_top + child.winfo_reqheight()` 的最大值；顺手把工作流卡的底部 pady 去掉（它是该栏最后一个控件，行间距由日志行的 pady 提供）
- 预防：**能实测就别手算**。手算的常量会在下一次改 UI 时静默失准

### 问题：上排给 `weight=1`，日志就只剩 4 行

**TL;DR**：网格里"谁不能长大、谁该长大"要显式分配 —— 把 weight 给错一方，空的是上排、挤的是下排。

- 背景（v1.2.4→v1.2.5，用户反馈「运行日志太矮了，上方空的位置太多了」）
- 问题：上排 `rowconfigure(0, weight=1)`，剩余高度全被它吃掉：实测**上排中栏空 137px、右栏空 258px**，而下排日志文本区只剩 **84px（约 4 行）**。截图亮度照样 41~45"看着正常"
- 解决：上排去掉 weight、高度由内容实测决定（`_sync_top_height`）；下排 `weight=1` 吃掉剩余。日志文本区：默认状态 84 → **310px**（约 25 行）；选中工作流（右栏最高状态）**165px**（约 13 行）；最小窗口 + 选中工作流仍有 **145px**
- 附带：窗口高度 900 → 960（`minsize` 880 → 940），并用 `min(设计值, 屏幕可用区)` 夹住，避免高分屏下窗口比屏幕还高、日志被顶到屏幕外
- 预防：渲染验证要**直接把"日志文本区高度"打出来**，并断言"内容底 ≤ 可视底"；只测亮度会漏掉这类问题

### 问题：UPX 压缩让单文件 exe 更容易被杀软误杀

**TL;DR**：UPX 压缩过的单文件 exe 更像"自解压壳"，是安全软件误报的主要诱因；收益（体积）远小于风险。

- 背景（v1.2.5）：用户报 exe"打开就闪退"。程序侧查到的是"闪控制台窗口"（已修），但另一条常见成因必须一起排除
- 结论（多来源一致）：单文件模式本身就是"自解压包"，再叠 UPX 压缩会显著提高启发式扫描命中率，表现就是"进程被拦/被杀 → 窗口一闪就关"；且 UPX 还可能破坏引导流程（解压失败直接静默退出）
- 解决：spec 里 `upx=True` → **`upx=False`**。代价是体积略增（13,286,917 → 13,293,618 字节，+0.005%），换来"不被误杀 + 引导更稳"
- 预防：排"闪退"这类问题时，先确认**是否有可看的日志**（见上文崩溃落盘），再排除打包方式；别在无信息的情况下猜代码

## 文档基线

- 2026-10-10（v1.2.5）：**修"一直闪窗口"（子进程缺 `CREATE_NO_WINDOW`）+ 崩溃落盘 + 上排高度自适配（日志文本区 84→310px）**；抽出 `运行时.py`（主文件逻辑行回到 1000 以内）；新增「GUI 子进程闪黑窗 / 闪退看不到报错 / winfo_reqheight 是旧值 / 手工累加间距会漏算 / 上排 weight 抢空 / UPX 招杀软」六篇问题记录
- 2026-10-10（v1.2.4）：**运行日志改为横跨中栏 + 右栏的下排**（2×3 网格、左栏跨两行通高、窗口尺寸按内容需求倒推）；新增「跨列下排会挤截断上排 / 运行中的 exe 锁住 dist 让打包假成功」两篇问题记录
- 2026-10-10（v1.2.3）：**三栏收敛** —— 设置常驻左栏 / 服务卡移到右栏工作流上方 / 删掉中栏重复的引擎切换按钮；新增「常显内容不该做成导航页 / 状态条塞进窄栏 / 第二入口是负债 / 截图裁剪坐标要现算」四篇问题记录
- 2026-10-10（v1.2.2）：**布局调整** —— 工作流卡移入右栏（日志上方）、右栏改上下两段、窄栏重排、窗口加宽；新增「常显卡片挪进窄栏要重排 / 跳转卡片不再可滚动时」两篇问题记录
- 2026-10-10（v1.2.1）：**精简左导航**（去掉服务/工作流两项、启动不滚动、工作流链接改纯滚动、高亮一致性），新增「左导航该放哪些入口 / 导航高亮与引擎标签不一致」两篇问题记录
- 2026-10-10（v1.2.0）：**深色三栏 UI 重构** —— 抽出 `ui_kit.py` 自绘控件库；新增「跨线程 root.after / 圆角卡片画法 / transparent 颜色名 / 窄窗口 pack 顺序 / 高分屏缩放 / 拆分阈值」六篇问题记录
- 2026-10-10：静态审计（radon/pylint/vulture）+ 运行期冒烟，修 v1.1.0 两个 P0（`alive` 未定义 / `Text.count` tuple 陷阱），新增本两篇问题记录
- 2026-10-10（`755c05a`）：稳定性+性能加固，新增启动中锁 / 按钮态统一 / 日志裁剪两篇问题记录
- 2026-10-09：建立四件套（README / AGENTS / DEVELOPMENT / CHANGELOG）
