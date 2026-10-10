# DEVELOPMENT.md · 视频方案启动器

## 项目概览

单文件 Python GUI（`视频方案启动器.py`，约 450 行），管理本地三套视频 AI 方案。定位是**编排器**：自己不做推理，只负责「拉服务 / 传参调脚本 / 回传日志」。

- 入口：`main()` → `App(root)` → `root.mainloop()`
- 配置：模块级 `CONFIG` 字典 + 可选同目录 `启动器配置.json` 覆盖
- 交付：源码直接运行，或用 PyInstaller 打成单文件 exe

## 架构说明

```
main()
 └─ App(root)
     ├─ _build_ui()            状态条 / 三页签 / 日志区 / 底栏
     ├─ 服务控制
     │   ├─ svc_start()        Popen(comfy_py -u main.py --disable-pinned-memory
     │   │                          --disable-async-offload --reserve-vram 1)
     │   ├─ _svc_reader()      线程：逐行读 stdout → queue
     │   ├─ _svc_wait_ready()  线程：3s 轮询 /system_stats，最多 5 分钟，就绪自动开网页
     │   ├─ svc_stop()         只终止自己拉起的进程（外部启起的会明确提示）
     │   └─ open_web()         未运行时先 svc_start()，就绪后自动开页
     ├─ 批量任务
     │   ├─ _run_ps()          拼 PS 命令行 + Popen + 禁用按钮 + 起读日志线程
     │   ├─ _task_reader()     线程：回传日志，结束打印 rc / 耗时
     │   ├─ run_flashvsr()     -InputPath / -Mode / -Scale / -Format / -AutoStart
     │   └─ run_seedvr2()      -InputPath / -Profile / -Resolution / -Overwrite
     ├─ 后台探测
     │   ├─ _probe_loop()      每 5s 一次（root.after）
     │   ├─ _probe_once()      读 /system_stats 取服务版本 + 显存；失败退回 nvidia-smi
     │   └─ _gpu_by_nvidia_smi()
     └─ on_close()             退出前确认中断任务 / 是否一并关服务
```

**线程模型**：GUI 主线程只操作控件；子进程读日志、服务就绪轮询、状态探测都在 daemon 线程里，结果经 `queue.Queue` 由主线程 `after(150)` 消费 —— 避免跨线程操作 Tk。

**代理隔离**：`NO_PROXY_OP = build_opener(ProxyHandler({}))` 专用于本机服务探测，与系统 Clash 代理彻底隔离。

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

## 文档基线

- 2026-10-10：静态审计（radon/pylint/vulture）+ 运行期冒烟，修 v1.1.0 两个 P0（`alive` 未定义 / `Text.count` tuple 陷阱），新增本两篇问题记录
- 2026-10-10（`755c05a`）：稳定性+性能加固，新增启动中锁 / 按钮态统一 / 日志裁剪两篇问题记录
- 2026-10-09：建立四件套（README / AGENTS / DEVELOPMENT / CHANGELOG）
