# -*- coding: utf-8 -*-
"""视频方案启动器 —— ComfyUI 底座 + FlashVSR / SeedVR2 / MiniMax H3 一键启动。

界面：深色 Fluent 三栏控制台（左＝导航 + 设置 / 中＝引擎参数 / 右＝服务 + 工作流），
      运行日志横跨中、右两列、压在两栏下方，
      纯 tkinter 自绘（圆角卡片 + 阴影 + 圆角按钮），零第三方依赖。
      设计蓝本见仓库分支 ui-redesign 的 redesign/video_launcher_sidebar.html。

设计要点（对应本机已踩过的坑）：
  * 所有 .ps1 一律 `powershell -NoProfile -ExecutionPolicy Bypass`（本机执行策略 Restricted）
  * 不用 -File 而用 -Command 包一层，先设 [Console]::OutputEncoding=UTF8 ——
    否则 PS 5.1 按 cp936 重编 python 的 UTF-8 输出，日志全变乱码
  * 子进程异步 + 日志实时回传（SeedVR2 一跑 20+ 分钟，绝不能卡 UI）
  * ffmpeg 目录显式前置 PATH，不依赖注册表
  * 探测 ComfyUI 必须绕开系统代理（Clash 会把 127.0.0.1 也代理掉，返回 502）
  * 跨线程不得直接操作 Tk 控件：后台线程只把结果投递回主线程（root.after）再刷新
"""
import copy
import json
import os
import queue
import sys
import threading

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

import ui_kit
import 运行时 as rt
from ui_kit import (
    S, f, mono,
    BG, CARD, CARD2, BORDER, TXT, TXT2,
    BLUE, PURP, TEAL, GREEN, AMBER, RED, DARKTX, MUTED,
    RoundedFrame, RButton, swatch_text, NavItem, Dropdown, ChipRow,
    CheckBox, ProgressBar,
)

# ---------------------------------------------------------------- 配置
APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))

APP_VER = "v1.2.14"                                 # 窗口标题与文档基线共用；发版必更
CONFIG = {
    "comfy_dir":    r"C:\AI\ComfyUI",
    "comfy_py":     r"C:\AI\ComfyUI\.venv\Scripts\python.exe",
    "deploy_root":  r"C:\AI\video-upscale-deploy",
    "ffmpeg_dir":   r"C:\AI\ffmpeg\bin",
    "input_dir":    r"E:\VideoUpscale\input",
    "output_dir":   r"E:\VideoUpscale\output",
    "comfy_url":    "http://127.0.0.1:8188",
    "log_dir":      os.path.join(APP_DIR, "logs"),   # 日志落盘目录（开关见 log_to_file）
}

_cfg_path = os.path.join(APP_DIR, "启动器配置.json")
if os.path.exists(_cfg_path):                       # 允许用同名 JSON 覆盖默认路径
    try:
        with open(_cfg_path, encoding="utf-8") as _fh:
            CONFIG.update(json.load(_fh))
    except Exception as e:
        print("配置文件读取失败，用默认值：", e)

import 服务面板
服务面板.CONFIG = CONFIG    # mixin 与主程序共用同一份配置字典（原地 update，引用不变）

def engine_ps(engine):
    """引擎批量放大脚本的路径。

    做成函数而不是模块级常量：用户在设置里改了 deploy_root 之后要立刻生效，
    常量则会一直指向启动时的那份（改了等于没改，下次点运行还是老路径）。
    """
    return os.path.join(CONFIG["deploy_root"], engine, "批量放大.ps1")

strip_ansi = rt.strip_ansi        # 日志清洗：剥掉 ComfyUI 新版注入的 ANSI 颜色码

MAX_LOG_LINES = 2000           # 日志区保留的最大行数，超出自动裁剪（防长任务日志无限增长拖慢 UI）

# ---------------------------------------------------------------- 内置工作流 / 引擎数据
ENG = {
    "flashvsr": {"color": BLUE, "label": "FlashVSR", "avatar": "F"},
    "seedvr2":  {"color": PURP, "label": "SeedVR2",  "avatar": "S"},
    "minimax":  {"color": TEAL, "label": "MiniMax H3", "avatar": "M"},
}

# 内置工作流清单是纯数据（展示字段 + 节点步骤），已下沉到 运行时.py；
# 这里只留一个名字，其它地方照旧写 WORKFLOWS，改动面最小。
WORKFLOWS = rt.WORKFLOWS


# 崩溃日志：GUI 程序没有 stderr，出异常时窗口一闪就没了、什么也看不到。
# 故把未捕获异常统一写到 exe 同目录的「崩溃日志.log」（落盘实现见 运行时.py）。
_CRASH_LOG_PATH = os.path.join(APP_DIR, rt.CRASH_LOG_NAME)


def install_crash_log():
    """装上「主线程 / 后台线程」异常兜底。必须在建 Tk 之前调用。"""
    global _CRASH_LOG_PATH
    _CRASH_LOG_PATH = rt.install_crash_log(APP_DIR)
    return _CRASH_LOG_PATH


def watch_tk_errors(root):
    """把 Tk 回调里的异常也记下来（须在建好 root 之后调用）。"""
    rt.watch_tk_errors(root, _CRASH_LOG_PATH)


def _write_crash(kind, exc_type, exc, tb):
    """给主线程心跳兜底：单个后台回调坏掉时记一笔，别让它拖垮整个刷新。"""
    rt.write_crash(_CRASH_LOG_PATH, kind, exc_type, exc, tb)


class App(服务面板.服务面板):
    def __init__(self, root):
        self.root = root
        root.title("视频方案启动器 " + APP_VER)
        root.configure(bg=BG)
        # 尺寸按"内容需求"倒推：上排要同时容下中栏引擎卡（~497）与右栏（服务卡 128 + 工作流卡
        # 含详情 ~500），下排运行日志再要 ~276 —— 取 960 高，日志才有十几行可看。
        # （v1.2.4 是 900 高且给上排 weight=1，日志文本区只剩 84px / 约 4 行。）
        # 但**绝不能让窗口高过屏幕可用区**：高分屏下 S() 会放大（125% 时 S(900)=1125），
        # 在 1080p 屏上窗口底部（运行日志）会被顶到屏幕外 —— 看着就是"上面一大片空白、
        # 日志只剩一点点"，而实际是窗口根本没放下。
        scr_w, scr_h = root.winfo_screenwidth(), root.winfo_screenheight()
        avail_w, avail_h = scr_w - S(60), scr_h - S(90)     # 给标题栏 + 任务栏留余量
        w, h = min(S(1260), avail_w), min(S(960), avail_h)
        root.geometry("%dx%d+%d+%d" % (w, h,
                                       max(0, (scr_w - w) // 2), max(0, (scr_h - h) // 2 - S(20))))
        root.minsize(min(S(1040), avail_w), min(S(940), avail_h))

        self.logq = queue.Queue()
        self.uiqueue = queue.Queue()    # 后台线程 → 主线程的回调队列（tkinter 非线程安全，禁止跨线程碰控件）
        self.svc_proc = None            # ComfyUI 进程句柄
        self.task_proc = None           # 当前批量任务进程
        self.task_name = ""
        self._closing = False
        self._svc_starting = False      # 启动中锁：防止就绪前连点起多个 ComfyUI 进程
        self._log_lines = 0             # 日志区当前行数（自维护，用于裁剪，不依赖 Text.count）
        self._loaded_api = None         # 已加载的本地工作流（API 格式 JSON）
        self._local_wfs = {}            # 本次运行加载过的本地 .json：文件名 → 图（下拉里能再选回来）
        self._scanned = {}              # v1.2.14 ComfyUI 工作流目录扫描结果：文件名 → 图（选中才读，惰性）
        self._wf_api = {}               # UI 图转好的 API 图缓存：文件名 → 图（转换要 /object_info，别反复转）
        self._objinfo = None            # /object_info 缓存（1MB+，一次会话取一回；「刷新」会清）
        self._wf_alias = dict(CONFIG.get("wf_alias") or {})   # 工作流备注名：原名 → 备注名（落盘）
        self._cur_wf_src = None         # 当前选中的本地/扫描工作流**原名**（转换缓存与备注的键）
        self._active_wf_id = None
        self._probe_state = {"alive": False, "starting": False, "own": False,
                             "version": "?", "gpu": ""}
        self._run_btn = None            # 当前引擎卡上的「运行」按钮
        self._top_need = 0              # 上排内容实测高度（_sync_top_height 写回 rowconfigure）
        self._ui_ready = False          # UI 搭完前不触发上排高度自适配
        self._sync_pending = False      # after_idle 合并标记
        self._syncing = False           # 上排高度实测中（防 update_idletasks 重入）
        self._nvsmi_ok = True           # nvidia-smi 是否可用（不可用就不再反复起进程）
        # 日志落盘旁路（v1.2.8）：默认关闭；关着时是 NoopLog，日志漏斗处就不必判空
        self.logfile = rt.LogFile(CONFIG["log_dir"]) if CONFIG.get("log_to_file") else rt.NoopLog()
        if self.logfile.degraded:
            self.log("[日志] 落盘失败，已降级为不落盘：%s" % self.logfile.degraded, "warn")

        self._build_ui()
        # 启动时只选中默认引擎、不滚动：让中栏停在引擎卡首屏
        self._select_nav("flashvsr", scroll=False)
        self._scan_wf_dir()             # v1.2.14：把 ComfyUI 里存的工作流并入下拉
        self._drain_log()
        self._probe_loop()

    # ================================================================ UI 搭建
    def _build_ui(self):
        app = tk.Frame(self.root, bg=BG)
        app.pack(fill="both", expand=True, padx=S(14), pady=S(14))
        self.app = app
        app.columnconfigure(1, weight=1)               # 中栏弹性
        # 上排**不给 weight**：它只该占到"内容实际需求"那么高（minsize 由 _sync_top_height 实测写回）。
        # v1.2.4 给上排 weight=1，结果剩余高度被它全吃掉：实测上排中栏空 137px、右栏空 258px，
        # 而下排运行日志文本区只剩 84px（约 4 行）。下排改为 weight=1，吃掉上排用不完的高度。
        app.rowconfigure(0, minsize=S(520))
        app.rowconfigure(1, weight=1, minsize=S(200))

        self._build_sidebar(app)      # 左栏（跨两行，保持通高）
        self._build_center(app)       # 中栏：引擎参数
        self._build_right(app)        # 右栏：服务卡 + 工作流卡
        self._build_logpane(app)      # 下排：运行日志（columnspan=2）

        self._ui_ready = True
        self._sync_top_height()

    # ---- 上排高度自适配（内容实测）----
    def _sync_top_height(self):
        """上排高度 = max(右栏工作流卡需求 + 冗余, 中栏服务卡+引擎卡需求)（v1.2.12）。

        v1.2.12 前需求 = max(中栏舞台, 右栏"服务卡+工作流卡")：两卡叠高把整排撑大、
        中栏下方空一片，下拉就地展开还会再跳。现在右栏只剩工作流卡、详情盒固定高
        （WF_DETAIL_H，两个分支同高）→ 右栏需求不随选择变化，**右栏公式定基准**；
        中栏只做兜底（FlashVSR 引擎卡比右栏定出的还高 ~77px，不兜底会藏住「运行放大」）。
        中栏舞台 weight=1 吃剩余，内容超出就滚动，不再让下拉/选择牵着整排跳。

        **实测前必须先 update_idletasks()**：Tk 的几何计算是 idle 任务，刚 pack 完
        新内容时 winfo_reqheight() 还是旧值（实测：选中工作流后立刻读是 263，
        跑一次 idle 才是 500）—— 用旧值就会把卡片截断，而且没有任何后续事件会纠正它。
        **不能在 <Configure> 里无条件调用**，否则"改 minsize → 触发 Configure → 再改"
        会死循环；_syncing 再挡一层重入。
        """
        if not getattr(self, "_ui_ready", False) or self._syncing:
            return
        self._syncing = True
        try:
            self.root.update_idletasks()
            # 右栏需求：取"最低那个子控件的底边"。用真实布局位置 + 自身需求高来算，
            # 对 pack/grid 的 pady / 卡片圆角内边距都免疫 —— 手工累加间距实测漏算过 14px。
            col_top = self.right_col.winfo_rooty()
            right = 0
            for child in self.right_col.winfo_children():
                right = max(right, child.winfo_rooty() - col_top + child.winfo_reqheight())
            # 中栏兜底：服务卡（恒定）+ 引擎卡需求。默认 FlashVSR 卡比右栏定出的高度
            # 还高 ~77px，纯右栏定高会把「运行放大」压到折叠线以下（v1.2.5 修过的 bug），
            # 故取两者较大 —— 只有中栏真装不下时才由中栏抬高整排。
            mid = (self.svc_card.winfo_reqheight() + S(14)
                   + self.stage.winfo_reqheight())
            need = max(right + S(32), mid, S(360))
            if abs(need - self._top_need) > S(2):
                self._top_need = need
                self.app.rowconfigure(0, minsize=need)
        finally:
            self._syncing = False

    def _schedule_top_sync(self):
        """合并多次请求：等这一轮布局稳定后（after_idle）再实测一次。"""
        if not getattr(self, "_ui_ready", False) or self._sync_pending:
            return
        self._sync_pending = True

        def run():
            self._sync_pending = False
            self._sync_top_height()

        self.root.after_idle(run)


    # ---- 左：导航 ----
    def _build_sidebar(self, parent):
        side = RoundedFrame(parent, outer=BG, pad=(4, 8), radius=S(12))
        side.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, S(14)))
        side.configure(width=S(206))
        side.pack_propagate(False)
        b = side.body
        self.sidebar = b                     # 左栏 body（设置常驻在此，渲染验证要断言它的落点）

        brand = tk.Frame(b, bg=CARD)
        brand.pack(fill="x", pady=(0, S(14)), padx=S(2))
        swatch_text(brand, 38, BLUE, "V", outer=CARD, radius=S(10), font=f(13, True)).pack(side="left")
        tk.Label(brand, text="视频启动器", bg=CARD, fg=TXT, font=f(11, True)).pack(
            side="left", padx=(S(10), 0))

        # 导航只放「会切换中栏内容」的入口：三个引擎。
        # 服务卡（中栏顶部）、工作流卡（右栏）、设置（左栏下方）都是常显区，不做导航项
        self.nav_items = {}
        defs = [
            ("flashvsr", "FlashVSR", BLUE),
            ("seedvr2", "SeedVR2", PURP),
            ("minimax", "MiniMax H3", TEAL),
        ]
        for key, text, color in defs:
            it = NavItem(b, text, command=lambda k=key: self._select_nav(k),
                         color=color, outer=CARD)
            it.pack(fill="x", pady=1)
            self.nav_items[key] = it

        tk.Frame(b, height=1, bg=BORDER).pack(fill="x", pady=S(12), padx=S(6))
        self._build_settings_section(b)      # 设置常驻左栏下方，不再点导航到中栏刷出

    # ---- 中：滚动舞台（引擎区）----
    def _build_center(self, parent):
        center = tk.Frame(parent, bg=BG)
        center.grid(row=0, column=1, sticky="nsew")
        center.rowconfigure(1, weight=1)      # 行 1 = 引擎舞台吃剩余（服务卡高度固定）
        center.columnconfigure(0, weight=1)

        # 行 0：服务卡回到中栏顶部（v1.2.12），右栏只剩工作流卡 → 整排不再被两卡叠高撑大
        self._build_service_card(center)

        # 行 1：滚动舞台（引擎卡；内容超出就滚动，不再撑大整排）
        wrap = tk.Frame(center, bg=BG)
        wrap.grid(row=1, column=0, sticky="nsew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.stage_cv = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        self.stage_cv.grid(row=0, column=0, sticky="nsew")
        self.stage = tk.Frame(self.stage_cv, bg=BG)
        self._stage_win = self.stage_cv.create_window((0, 0), window=self.stage, anchor="nw")
        self.stage.bind("<Configure>",
                        lambda e: self.stage_cv.configure(scrollregion=self.stage_cv.bbox("all")))
        self.stage_cv.bind("<Configure>",
                           lambda e: self.stage_cv.itemconfigure(self._stage_win, width=e.width))
        # 滚轮（进入舞台才接管，避免影响别处）
        self.stage_cv.bind("<Enter>", lambda e: self.stage_cv.bind_all("<MouseWheel>", self._on_wheel))
        self.stage_cv.bind("<Leave>", lambda e: self.stage_cv.unbind_all("<MouseWheel>"))

        self._build_engine_area(self.stage)

    # ---- 工作流卡（右栏唯一卡片，常显不滚动）----
    def _build_workflow_card(self, parent):
        card = RoundedFrame(parent, outer=BG, pad=(6, 6))
        # 它是右栏最后一个控件：底部不留间距，行与行之间的留白由日志行的 pady 提供
        # （留了会让右栏需求多算 14px，把卡片自己挤矮）
        card.pack(fill="x")
        self.wf_card = card
        b = card.body

        # 窄栏（右栏 ~360px）排布：标题行 / 下拉独占一行 / 节点数小字一行
        head = tk.Frame(b, bg=CARD)
        head.pack(fill="x", pady=(0, S(10)))
        swatch_text(head, 30, TEAL, "⚙", outer=CARD, radius=S(8), font=f(13)).pack(side="left")
        tk.Label(head, text="选择 / 加载工作流", bg=CARD, fg=TXT, font=f(10, True)).pack(
            side="left", padx=(S(10), 0))
        self.btn_load = RButton(head, text="加载 .json", command=self._load_wf_json,
                                fill=TEAL, fg=DARKTX, border=None, outer=CARD, font=f(9), pady=6)
        self.btn_load.pack(side="right")
        self.btn_rescan = RButton(head, text="刷新", command=self._rescan_wf_dir,
                                  fill="transparent", fg=TXT, border=BORDER, outer=CARD,
                                  font=f(9), pady=6)
        self.btn_rescan.pack(side="right", padx=(0, S(6)))

        tk.Label(b, text="工作流", bg=CARD, fg=TXT2, font=f(9), anchor="w").pack(fill="x")
        self.wf_dd = Dropdown(b, self._wf_names(), command=self._on_wf_select,
                              outer=CARD, placeholder="— 请选择一套内置工作流 —", font=f(10))
        self.wf_dd.pack(fill="x", pady=(S(5), 0))
        self.wf_nodes = tk.Label(b, text="未选择 · 内置 %d 套" % len(WORKFLOWS),
                                 bg=CARD, fg=TXT2, font=f(8), anchor="w")
        self.wf_nodes.pack(fill="x", pady=(S(6), 0))

        self.wf_detail = ui_kit.WorkflowDetail(b, engines=ENG, outer=CARD,
                                               on_change=self._schedule_top_sync,
                                               on_push=self._push_wf,
                                               on_alias=self._wf_alias_edit)

    # ---- 工作流的选 / 载 / 推 ----
    def _wf_names(self):
        """下拉的选项（显示名）：内置方案 → 目录并入的 → 本次加载过的，别名覆盖原名。"""
        names = [w["name"] for w in WORKFLOWS]
        names += list(self._local_wfs)
        names += [n for n in self._scanned if n not in self._local_wfs]
        return [self._wf_alias.get(n, n) for n in names]

    def _on_wf_select(self, display):
        """下拉里选了某一项（传进来的是**显示名**）：内置方案 → 已加载图 → 目录扫描。"""
        orig = {v: k for k, v in self._wf_alias.items()}.get(display, display)
        wf = next((w for w in WORKFLOWS if w["name"] == orig), None)
        if wf:
            self._loaded_api = None
            self._active_wf_id = wf["id"]
            self._cur_wf_src = None
            self.wf_nodes.configure(text="%d 节点" % len(wf["steps"]))
            self.wf_detail.show(wf, None, nodes=len(wf["steps"]))
            tag = "lw" if wf["engine"] == "minimax" else "ok"
            self.log("[工作流] 已选择 %s" % wf["name"], tag)
            # 同步左导航高亮与引擎标签（工作流自带引擎），不滚动以免把刚展开的详情甩出视野
            self._select_nav(wf["engine"], scroll=False)
            return
        data = self._local_wfs.get(orig)          # v1.2.11：加载过的 .json 也能在下拉里选回来
        if data is None and orig in self._scanned:
            data = self._scanned[orig]            # v1.2.14：目录并入的，选中才读（惰性）
            if data is None:
                data, err = rt.read_workflow(os.path.join(rt.workflows_dir(CONFIG), orig))
                if err:
                    self.log("[工作流] %s" % err, "err")
                    return
                self._scanned[orig] = data
        if data is None:
            return
        self._use_local(orig, data, log=False)
        n = rt.api_node_count(data)
        self.log("[工作流] 已选择 %s（%d 节点）" % (display, n), "lw")
        threading.Thread(target=self._preheat_api, args=(orig, data), daemon=True).start()

    def _preheat_api(self, name, data):
        """后台预热：把 UI 图提前转成 API 格式缓存起来，推送时就不用等。

        服务没起就不吵 —— 真推送时 _ready_api 会给一句明确的报错。
        """
        if not rt.is_ui_graph(data) or name in self._wf_api:
            return
        defs = self._object_info(quiet=True)
        if defs is None:
            return
        api, err = rt.ui_to_api(data, defs)
        if err:
            self.log("[工作流] %s 暂时推不了：%s" % (name, err), "warn")
            return
        self._wf_api[name] = api
        self.log("[工作流] %s 已就绪（%d 节点，可直接推送）" % (self._wf_alias.get(name, name),
                                                          len(api)), "ok")

    def _object_info(self, quiet=False):
        """取 /object_info 节点定义（一次会话缓存一份；转换 UI 图的依据）。"""
        if self._objinfo is not None:
            return self._objinfo
        if not quiet:
            self.log("[工作流] 正在取节点定义（/object_info，几秒钟）…", "dim")
        self._objinfo = rt.http_json(CONFIG["comfy_url"] + "/object_info", timeout=30)
        if self._objinfo is None:
            self.log("[工作流] 拿不到节点定义 —— ComfyUI 没在运行，UI 图暂时转换不了", "warn")
        return self._objinfo

    def _use_local(self, name, data, log=True):
        """把一份本地图设为当前方案（下拉选中 / 刚加载 都走这里）。"""
        n = rt.api_node_count(data)
        self._loaded_api = data
        self._active_wf_id = None
        self._cur_wf_src = name
        self.wf_nodes.configure(text="%d 节点" % n)
        self.wf_detail.show(None, name, nodes=n, alias=self._wf_alias.get(name))
        if log:
            self.log("[工作流] 已加载本地 %s（%d 节点）" % (name, n), "lw")

    def _load_wf_json(self):
        # 默认落在部署脚本目录的图库（图本来就在那儿），目录不在时交给系统默认位置
        start = CONFIG["deploy_root"] if os.path.isdir(CONFIG["deploy_root"]) else None
        p = filedialog.askopenfilename(title="选择 ComfyUI 工作流 .json", initialdir=start,
                                       filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if not p:
            return
        data, err = rt.read_workflow(p)
        if err:
            self.log("[工作流] %s" % err, "err")
            return
        name = os.path.basename(p)
        self._local_wfs[name] = data
        self.wf_dd.set_values(self._wf_names())   # 先进下拉，再选中（顺序反了会被 set_values 清掉）
        self.wf_dd.set(self._wf_alias.get(name, name))
        self._use_local(name, data)
        threading.Thread(target=self._preheat_api, args=(name, data), daemon=True).start()

    def _scan_wf_dir(self, quiet=False):
        """扫 ComfyUI 的工作流目录，把里面的 .json 全部并入下拉（v1.2.14）。"""
        names, err = rt.scan_workflows(CONFIG)
        if err:
            if not quiet:
                self.log("[工作流] %s" % err, "warn")
            return
        self._scanned.update({n: None for n in names})
        self.wf_dd.set_values(self._wf_names())
        fresh = sum(1 for n in names if n not in self._local_wfs)
        if not quiet and fresh:
            self.log("[工作流] 从 ComfyUI 工作流目录并入 %d 个工作流（下拉里直接可选）" % fresh,
                     "dim")
        self.wf_nodes.configure(text="未选择 · 内置 %d 套 · 已并入 %d 个"
                                % (len(WORKFLOWS), len(self._scanned)))

    def _rescan_wf_dir(self):
        """「刷新」：重扫目录 + 清节点定义缓存（ComfyUI 重启后插件可能变了）。"""
        self._objinfo = None
        self._wf_api.clear()
        self._scan_wf_dir()
        self.log("[工作流] 已重新扫描工作流目录", "dim")

    def _wf_alias_edit(self):
        """「备注」：给当前选中的本地工作流起个好记的名字（落盘到配置文件）。"""
        name = self._cur_wf_src
        if not name:
            return
        new = simpledialog.askstring(
            "工作流备注名", "给「%s」起个好记的名字（留空清除备注）：" % name,
            initialvalue=self._wf_alias.get(name, ""), parent=self.root)
        if new is None:
            return                                  # 取消：什么都不动
        new = new.strip()
        if new:
            self._wf_alias[name] = new
        else:
            self._wf_alias.pop(name, None)
        err = rt.save_config(_cfg_path, {"wf_alias": self._wf_alias})
        if err:
            self.log("[工作流] 备注没存成：%s" % err, "err")
            return
        CONFIG["wf_alias"] = self._wf_alias
        self.wf_dd.set_values(self._wf_names())
        if self._loaded_api is not None:
            self.wf_dd.set(self._wf_alias.get(name, name))
            self.wf_detail.show(None, name, nodes=rt.api_node_count(self._loaded_api),
                                alias=self._wf_alias.get(name))
        self.log("[工作流] 备注已保存：%s → %s" % (name, new or "（已清除）"), "ok")

    def _push_wf(self, to_queue):
        """把当前方案推给 ComfyUI。

        v1.2.11 起内置方案也能推：方案没带 API 图时，从部署脚本目录里读它绑定的那份图
        （见 运行时.pick_api），再把图里的素材换成界面上选的输入。
        """
        wf = next((w for w in WORKFLOWS if w["id"] == self._active_wf_id), None)
        name = wf["name"] if wf else (self.wf_dd.value or "未选择")
        if not self.comfy_alive():
            self.log("[工作流] ComfyUI 未运行，先启动服务再%s" % ("入队" if to_queue else "推送"),
                     "warn")
            return
        video = self.in_var.get().strip() if getattr(self, "in_var", None) else ""
        api, why = self._api_for(wf, video)
        if api is None:
            self.log("[工作流] 「%s」%s" % (name, why), "warn")
            return
        if why:
            self.log("[工作流] %s" % why, "dim")
        verb = "入队" if to_queue else "推送"
        err = rt.http_post_json(CONFIG["comfy_url"] + "/prompt",
                                {"prompt": api, "client_id": "video-launcher"})
        if err is None:
            self.log("[工作流] 已%s「%s」→ ComfyUI" % (verb, name), "lw" if to_queue else "ok")
        else:
            self.log("[工作流] %s失败：%s" % (verb, err), "err")

    def _ready_api(self, data):
        """把当前选中的图备成可推送的 API 格式。

        API 图原样深拷贝；UI 格式（ComfyUI 里存的都是）先转换 —— 转换要
        /object_info 的节点定义，取回后缓存在 self._objinfo，转换结果缓存在
        self._wf_api，同一条工作流只转一回。返回 (图 or None, 原因)。
        """
        if not rt.is_ui_graph(data):
            return copy.deepcopy(data), ""
        key = self._cur_wf_src
        if key and key in self._wf_api:
            return copy.deepcopy(self._wf_api[key]), ""
        defs = self._object_info()
        if defs is None:
            return None, "拿不到节点定义（ComfyUI 没在运行？转换 UI 图需要它）"
        api, err = rt.ui_to_api(data, defs)
        if err:
            return None, err
        if key:
            self._wf_api[key] = api
        self.log("[工作流] UI 图已转成 API 格式（%d 节点）" % len(api), "dim")
        return copy.deepcopy(api), ""

    def _api_for(self, wf, video):
        """备好这次要提交的图，返回 (图 or None, 说明 / 取不到的原因)。

        两条来源，都先把图里的素材换成界面上选的输入（用户在本程序里选了输入，
        意思就是要用它 —— 尤其本地图常常带着作者机器上的硬编码示例路径）：
          * 用户「加载 .json」/ 目录并入的图优先（那是显式选择）；UI 格式的先转换；
          * 否则用内置方案绑定的图，顺带按方案覆盖参数、按素材音轨挑有声/无音轨版。
        """
        if self._loaded_api is not None:
            api, why = self._ready_api(self._loaded_api)
            if api is None:
                return None, why
        else:
            if wf is None:
                return None, "还没选方案；先在下拉里选一套，或点「加载 .json」"
            path, why = rt.pick_api(CONFIG, wf, video)
            if not path:
                return None, why
            api, err = rt.load_api(path)
            if err:
                return None, err
            k = rt.apply_patch(api, wf.get("patch"))
            if k:
                pairs = ["%s.%s=%s" % (cls, key, val)
                         for cls, over in (wf.get("patch") or {}).items()
                         for key, val in over.items()]
                self.log("[工作流] 按方案覆盖 %d 处参数（%s）" % (k, "、".join(pairs)), "dim")
        n = rt.apply_input(api, video)
        if video and not n:
            self.log("[工作流] 图里没有可替换的载入节点，沿用图内原路径", "dim")
        return api, why

    # ---- 引擎区（单卡，切换交给左导航）----
    def _build_engine_area(self, parent):
        # v1.2.3：去掉中栏那排 FlashVSR / SeedVR2 / MiniMax H3 切换按钮 ——
        # 左导航已经承担"切引擎"，两处入口重复且占掉首屏高度
        # v1.2.5：连「引擎 · 由左侧导航切换」这行小标题也去掉 —— 引擎卡自己有标题，
        # 这行只在白占 31px 高度，且"由左侧导航切换"是给开发者看的说明，不是用户需要的信息
        self.engine_card = RoundedFrame(parent, outer=BG, pad=(6, 6))
        self.engine_card.pack(fill="x", pady=(0, S(2)))

    def _render_engine(self, key):
        meta = ENG[key]
        card = self.engine_card
        card.set_fill(CARD)
        for c in card.body.winfo_children():
            c.destroy()
        b = card.body

        head = tk.Frame(b, bg=CARD)
        head.pack(fill="x", pady=(0, S(10)))
        swatch_text(head, 32, meta["color"], meta["avatar"], outer=CARD, radius=S(9),
                    font=f(13, True)).pack(side="left")
        titles = {"flashvsr": "FlashVSR 放大", "seedvr2": "SeedVR2 放大",
                  "minimax": "MiniMax H3 生成"}
        tags = {"flashvsr": "2x / 3x / 4x", "seedvr2": "短边自适应", "minimax": "网页工作流"}
        tk.Label(head, text=titles[key], bg=CARD, fg=TXT, font=f(11, True)).pack(
            side="left", padx=(S(10), 0))
        tk.Label(head, text=tags[key], bg=CARD2, fg=meta["color"], font=f(8, True),
                 padx=S(8), pady=S(2)).pack(side="right")
        tk.Frame(b, height=1, bg=BORDER).pack(fill="x", pady=(0, S(12)))

        if key == "flashvsr":
            self._fields_input(b)
            self._field_label(b, "模式")
            self.fv_mode = Dropdown(b, ["通用 · 动画", "长视频", "完整质量"], outer=CARD,
                                    font=f(10), height=S(34))
            self.fv_mode.pack(fill="x", pady=(0, S(12)))
            self.fv_mode.set("通用 · 动画")
            self._field_label(b, "放大倍数")
            self.fv_scale = ChipRow(b, ["2x", "3x", "4x"], outer=CARD, default="2x",
                                    on_fill=meta["color"])
            self.fv_scale.pack(fill="x", pady=(0, S(12)))
            self._field_label(b, "编码格式")
            self.fv_fmt = Dropdown(b, ["video/h264-mp4", "video/h265-mp4",
                                       "video/nvenc_h264-mp4", "video/nvenc_hevc-mp4"],
                                   outer=CARD, font=f(10), height=S(34))
            self.fv_fmt.pack(fill="x", pady=(0, S(12)))
            self.fv_fmt.set("video/h264-mp4")
            self.fv_auto = CheckBox(b, "服务没跑就自动拉起", on=True,
                                    outer=CARD, color=meta["color"])
            self.fv_auto.pack(fill="x", pady=(S(6), S(12)))
            self._run_btn = self._run_button(b, "运行放大", self.run_flashvsr, meta["color"])
            self._goto_wf(b)
        elif key == "seedvr2":
            self._fields_input(b)
            self._field_label(b, "档位（显存档）")
            self.sv_prof = Dropdown(b, ["标准 · 12g", "平衡 · 16g", "极致 · 24g"], outer=CARD,
                                    font=f(10), height=S(34))
            self.sv_prof.pack(fill="x", pady=(0, S(12)))
            self.sv_prof.set("平衡 · 16g")
            self._field_label(b, "目标短边")
            self.sv_res = ChipRow(b, ["720", "1080"], outer=CARD, default="1080",
                                  on_fill=meta["color"])
            self.sv_res.pack(fill="x", pady=(0, S(12)))
            self.sv_over = CheckBox(b, "覆盖已有输出", on=False,
                                   outer=CARD, color=meta["color"])
            self.sv_over.pack(fill="x", pady=(S(6), S(12)))
            self._run_btn = self._run_button(b, "运行放大", self.run_seedvr2, meta["color"])
            self._goto_wf(b)
        else:
            tk.Label(b, text="H3 是「文生视频 + 音画同出」，参数要在网页工作流里调（提示词 / 帧数 / 步数）。\n"
                             "启动器只负责把服务和网页拉起来，工作流在网页里搭。\n\n"
                             "① 点下面按钮启动服务 → ② 浏览器打开后用模板库 Video → MiniMax H3 → T2V\n"
                             "③ 首次验证参数建议 864×480 / 124 帧 / 20 步（本机实测 3 分 13 秒/条，带立体声）\n\n"
                             "注意：显存峰值 15.2/16 GiB，跑 H3 时别开别的吃显存的程序。",
                     bg=CARD, fg=TXT2, font=f(9), anchor="w", justify="left",
                     wraplength=S(600)).pack(fill="x", pady=(0, S(12)))
            self._run_btn = self._run_button(b, "打开 ComfyUI 网页", self.open_web, meta["color"])
            self._goto_wf(b)
        self._schedule_top_sync()      # 引擎卡换了 → 上排高度需求跟着变

    def _field_label(self, parent, text):
        tk.Label(parent, text=text, bg=parent["bg"], fg=TXT2, font=f(8), anchor="w").pack(
            fill="x", pady=(0, S(5)))

    def _fields_input(self, parent):
        self._field_label(parent, "输入（视频文件或整个文件夹）")
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=(0, S(12)))
        self.in_var = tk.StringVar()
        ent = tk.Entry(row, textvariable=self.in_var, bg=CARD2, fg=TXT, font=f(9),
                       insertbackground=TXT, relief="flat", highlightthickness=1,
                       highlightbackground=BORDER, highlightcolor=BLUE)
        ent.pack(side="left", fill="x", expand=True, ipady=S(5))
        RButton(row, text="选文件", command=lambda: self._pick(self.in_var, True),
                fill=CARD2, fg=TXT, outer=CARD, font=f(9), pady=6).pack(side="left", padx=(S(8), 0))
        RButton(row, text="选文件夹", command=lambda: self._pick(self.in_var, False),
                fill=CARD2, fg=TXT, outer=CARD, font=f(9), pady=6).pack(side="left", padx=(S(8), 0))

    def _run_button(self, parent, text, command, color):
        btn = RButton(parent, text=text, command=command, fill=color, fg=DARKTX,
                      border=None, outer=CARD, font=f(10, True), stretch=True, pady=9)
        btn.pack(fill="x", pady=(S(4), 0))
        return btn

    def _goto_wf(self, parent):
        link = tk.Label(parent, text="工作流选择在右栏 ↑", bg=CARD, fg=TXT2, font=f(9),
                        cursor="hand2")
        link.pack(pady=(S(9), 0))
        link.bind("<Enter>", lambda e: link.configure(fg=TXT))
        link.bind("<Leave>", lambda e: link.configure(fg=TXT2))
        link.bind("<Button-1>", lambda e: self._flash_wf())

    def _flash_wf(self):
        """工作流卡固定在右栏、不在滚动舞台里，所以用一次描边闪烁提示它在哪。"""
        self.wf_card.set_border(BLUE)
        self.root.after(500, lambda: self.wf_card.set_border(BORDER))

    # ---- 左栏下方：设置（常驻，窄栏两行式）----
    def _build_settings_section(self, parent):
        """设置常驻左栏下方（v1.2.3）：不再做成「点导航 → 中栏单独刷出设置页」。

        左栏可用宽只有 ~174px，所以每项拆两行：标签行（右侧「打开」「修改」）+ 值行（自动换行）。
        v1.2.10 起还能改：点「修改」或直接点值行 → 弹输入框，保存后写回程序同目录的
        「启动器配置.json」、当场重测并刷新显示（不必重启程序）。渲染在 ui_kit.SettingsList，
        项数据（含实测结论）来自 运行时.config_items —— 与启动日志同一份判断。
        """
        tk.Label(parent, text="设置", bg=CARD, fg=TXT2, font=f(9), anchor="w").pack(
            fill="x", padx=S(2), pady=(0, S(2)))
        self.settings = ui_kit.SettingsList(parent, rt.config_items(CONFIG), outer=CARD,
                                            on_open=self._setting_open,
                                            on_save=self._setting_save)
        self.settings.pack(fill="x")
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=(S(10), 0))
        RButton(row, text="打开输入", command=lambda: self._open_dir(CONFIG["input_dir"]),
                fill=CARD2, fg=TXT, outer=CARD, font=f(8), pady=5,
                stretch=True).pack(side="left", fill="x", expand=True)
        RButton(row, text="打开输出", command=lambda: self._open_dir(CONFIG["output_dir"]),
                fill=CARD2, fg=TXT, outer=CARD, font=f(8), pady=5,
                stretch=True).pack(side="left", fill="x", expand=True, padx=(S(6), 0))

    def _setting_open(self, key):
        """打开某一项：目录→资源管理器、文件→选中该文件、地址→浏览器（见 运行时.open_path）。"""
        if not rt.open_path(rt.field_kind(key), CONFIG.get(key, "")):
            messagebox.showwarning("打不开", CONFIG.get(key, "") or "（未配置）")

    def _setting_save(self, key, value):
        """写回配置文件并当场重测。返回错误串＝没存成（界面据此弹框并保持原显示）。"""
        err = rt.save_config(_cfg_path, {key: value})
        if err:
            return err
        CONFIG[key] = value
        if key == "comfy_url":                     # 服务卡上的地址跟着改，别显示成旧的
            self.svc_url.configure(text=value)
        self.log("[设置] %s = %s" % (rt.field_label(key), value), "hi")
        self.log(*rt.probe_line(CONFIG, key))      # 立刻复检：路径填错当场就能看见
        self.settings.set_items(rt.config_items(CONFIG))
        self._schedule_top_sync()
        return None

    # ---- 服务卡（中栏顶部，常显）----
    def _build_service_card(self, parent):
        """ComfyUI 服务卡：v1.2.3 挪去右栏，v1.2.12 按用户要求回到中栏顶部。

        右栏"服务卡 + 工作流卡"叠起来会把整排高度撑大、中栏下方空一片；
        挪回宽的中栏后，四个按钮能排回一行且等大（统一 minwidth —— pack 的
        expand 只平分多余空间，基础分配按各自请求宽，"停止队列任务"6 个字
        比 4 字按钮天生宽两个字的量，不锁宽必然错位）。
        """
        card = RoundedFrame(parent, outer=BG, pad=(6, 6))
        card.grid(row=0, column=0, sticky="ew", pady=(0, S(14)))
        self.svc_card = card
        b = card.body

        head = tk.Frame(b, bg=CARD)
        head.pack(fill="x")
        self.svc_dot = tk.Canvas(head, width=S(9), height=S(9), bg=CARD,
                                 highlightthickness=0, bd=0)
        self._dot_id = self.svc_dot.create_oval(0, 0, S(9), S(9), fill=GREEN, outline="")
        self.svc_dot.pack(side="left", padx=(0, S(10)))
        tk.Label(head, text="ComfyUI 服务", bg=CARD, fg=TXT, font=f(10, True)).pack(side="left")
        self.svc_pill = tk.Label(head, text="未运行", bg=CARD2, fg=MUTED, font=f(9, True),
                                 padx=S(9), pady=S(2))
        self.svc_pill.pack(side="left", padx=(S(10), 0))
        # 实时状态（版本 / 显存）跟着服务卡走
        self.stat_lab = tk.Label(head, text="", bg=CARD, fg=MUTED, font=f(8))
        self.stat_lab.pack(side="right")

        self.svc_url = tk.Label(b, text=CONFIG["comfy_url"], bg=CARD, fg=TXT2, font=f(8),
                                anchor="w")
        self.svc_url.pack(fill="x", pady=(S(7), S(9)))

        # 四个按钮一行等大：内容不变 → 卡片高度恒定，不参与上排实测
        row = tk.Frame(b, bg=CARD)
        row.pack(fill="x")
        self.btn_primary = RButton(row, text="启动服务", command=self._primary_service,
                                   fill=CARD2, fg=TXT, outer=CARD, font=f(9), pady=6,
                                   minwidth=112, stretch=True)
        self.btn_primary.pack(side="left", fill="x", expand=True)
        RButton(row, text="打开网页", command=self.open_web, fill=CARD2, fg=TXT,
                outer=CARD, font=f(9), pady=6, minwidth=112, stretch=True).pack(
                    side="left", fill="x", expand=True, padx=(S(8), 0))
        self.btn_stop = RButton(row, text="停止服务", command=self.svc_stop, fill=CARD2,
                                fg=TXT, outer=CARD, font=f(9), pady=6, state="disabled",
                                minwidth=112, stretch=True)
        self.btn_stop.pack(side="left", fill="x", expand=True, padx=(S(8), 0))
        self.btn_queue = RButton(row, text="停止队列任务", command=self.svc_stop_queue,
                                 fill=CARD2, fg=TXT, outer=CARD, font=f(9), pady=6,
                                 state="disabled", minwidth=112, stretch=True)
        self.btn_queue.pack(side="left", fill="x", expand=True, padx=(S(8), 0))

    # ---- 右：工作流卡（右栏唯一卡片）----
    def _build_right(self, parent):
        col = tk.Frame(parent, bg=BG)
        col.grid(row=0, column=2, sticky="nsew", padx=(S(14), 0))
        col.configure(width=S(360))
        col.pack_propagate(False)
        self.right_col = col

        # 工作流卡（常显，不随中栏滚动）。上排高度就由它定（见 _sync_top_height）
        self._build_workflow_card(col)

    # ---- 底：运行日志（v1.2.4 起横跨中栏 + 右栏，两列同宽）----
    def _build_logpane(self, parent):
        holder = tk.Frame(parent, bg=BG)
        holder.grid(row=1, column=1, columnspan=2, sticky="nsew", pady=(S(14), 0))

        pane = RoundedFrame(holder, outer=BG, pad=(4, 4), radius=S(12))
        pane.pack(fill="both", expand=True)
        b = pane.body

        head = tk.Frame(b, bg=CARD)
        head.pack(fill="x", pady=(0, S(8)))
        tk.Label(head, text="运行日志", bg=CARD, fg=TXT, font=f(10, True)).pack(side="left")
        clr = tk.Label(head, text="清空", bg=CARD, fg=TXT2, font=f(9), cursor="hand2")
        clr.pack(side="right")
        clr.bind("<Enter>", lambda e: clr.configure(fg=TXT))
        clr.bind("<Leave>", lambda e: clr.configure(fg=TXT2))
        clr.bind("<Button-1>", lambda e: self.clear_log())
        tk.Frame(b, height=1, bg=BORDER).pack(fill="x")

        # height=4 只是"请求高度"下限（让下排高度稳定在 minsize 附近），实际随容器拉伸
        self.logtxt = tk.Text(b, bg=CARD, fg=TXT2, bd=0, highlightthickness=0, height=4,
                              wrap="word", font=mono(9), state="disabled", padx=0, pady=S(8),
                              selectbackground=CARD2, insertwidth=0, spacing1=1, spacing3=2)
        self.logtxt.pack(fill="both", expand=True)
        self.logtxt.tag_configure("ok", foreground=GREEN)
        self.logtxt.tag_configure("lw", foreground=TEAL)
        self.logtxt.tag_configure("warn", foreground=AMBER)
        self.logtxt.tag_configure("err", foreground=RED)
        self.logtxt.tag_configure("hi", foreground=TXT)
        self.logtxt.tag_configure("dim", foreground=MUTED)

        tk.Frame(b, height=1, bg=BORDER).pack(fill="x")
        foot = tk.Frame(b, bg=CARD)
        foot.pack(fill="x", pady=(S(8), 0))
        self.pbar = ProgressBar(foot, outer=CARD)
        self.pbar.pack(fill="x")
        stat = tk.Frame(foot, bg=CARD)
        stat.pack(fill="x", pady=(S(6), 0))
        tk.Label(stat, text="任务进度", bg=CARD, fg=TXT2, font=f(8)).pack(side="left")
        self.pct_lab = tk.Label(stat, text="空闲", bg=CARD, fg=TXT2, font=f(8))
        self.pct_lab.pack(side="right")

    # ================================================================ 导航 / 滚动
    def _select_nav(self, key, scroll=True):
        """左导航点击：切引擎（重绘引擎卡），并把中间舞台滚到引擎卡。

        参数 scroll=False 用于程序启动时——只选中默认引擎、不滚动。

        注：引擎切换只有左导航这一个入口（v1.2.3 去掉了中栏重复的切换按钮）；
        服务卡（中栏顶部）、工作流卡（右栏）、设置（左栏下方）都是常显区，不做导航项。
        """
        for k, it in self.nav_items.items():
            it.set_active(k == key)
        if key in ("flashvsr", "seedvr2", "minimax"):
            self._render_engine(key)
            if scroll:
                self._scroll_to(self.engine_card)

    def _scroll_to(self, widget):
        self.stage.update_idletasks()
        total = max(1, self.stage.winfo_height())
        y = widget.winfo_y()
        self.stage_cv.yview_moveto(max(0.0, (y - S(12)) / total))

    def _on_wheel(self, e):
        self.stage_cv.yview_scroll(int(-e.delta / 120), "units")

    # ================================================================ 小工具
    def _pick(self, var, is_file=True):
        if is_file:
            p = filedialog.askopenfilename(
                initialdir=CONFIG["input_dir"],
                filetypes=[("视频", "*.mp4 *.avi *.mov *.mkv *.webm *.flv *.wmv"),
                           ("所有文件", "*.*")])
        else:
            p = filedialog.askdirectory(initialdir=CONFIG["input_dir"])
        if p:
            var.set(p)

    def _open_dir(self, d):
        if not rt.open_path("dir", d):
            messagebox.showwarning("目录不存在", d)

    # ================================================================ 日志
    def log(self, msg, tag=None):
        self.logq.put((msg.rstrip("\n"), tag))

    def clear_log(self):
        self.logtxt.configure(state="normal")
        self.logtxt.delete("1.0", "end")
        self.logtxt.configure(state="disabled")
        self._log_lines = 0

    def _tag_for(self, line):
        return rt.tag_for(line)

    def _drain_log(self):
        """主线程心跳：排空日志队列 + 后台回调队列，然后重排程自己。

        外层必须 try/finally 保证重排程一定发生 —— 否则任何一个回调抛异常，
        `root.after` 就不再续上，日志与状态刷新会**永久停摆**（v1.1.2 的
        「日志刷新停摆」就是这一类静默故障，当时是靠运行期冒烟才抓到的）。
        """
        try:
            dirty = self._drain_lines()
            while True:
                try:
                    self.uiqueue.get_nowait()()
                except queue.Empty:
                    break
                except Exception:                  # 单个回调坏掉不该拖垮整个心跳
                    _write_crash("后台回调", *sys.exc_info())
            if dirty:
                self.logtxt.see("end")
        finally:
            if not self._closing:
                self.root.after(150, self._drain_log)

    def _drain_lines(self):
        dirty = False
        while True:
            try:
                line, tag = self.logq.get_nowait()
            except queue.Empty:
                return dirty
            line = strip_ansi(line)       # ComfyUI 新版会注入 ANSI 颜色码，Tk 不认 → 会显示成方块
            self.logfile.write(line)      # 落盘旁路（v1.2.8）：与界面同一行，故文件与界面逐行一致
            self.logtxt.configure(state="normal")
            self.logtxt.insert("end", line + "\n", tag or self._tag_for(line) or ())
            # 超出上限则裁剪最旧的行，避免 SeedVR2 等 20+ 分钟任务日志无限堆积拖慢 UI
            # 注意：Text.count() 返回 tuple，不能 int()；故用自维护计数器
            self._log_lines += 1
            if self._log_lines > MAX_LOG_LINES:
                self.logtxt.delete("1.0", "2.0")
                self._log_lines -= 1
            self.logtxt.configure(state="disabled")
            dirty = True
            self._maybe_progress(line)

    def _maybe_progress(self, line):
        """解析日志行里的进度并更新进度条（解析规则见 运行时.progress_pct）。"""
        pct = rt.progress_pct(line)
        if pct is not None:
            self._set_pct(pct)

    def _set_pct(self, pct):
        pct = max(0, min(100, pct))       # 进度条自己会夹，但标签不会 —— 在源头夹住
        self.pbar.set(pct)
        self.pct_lab.configure(text="%d%%" % pct)

    def _set_task_ui(self, busy, name=""):
        if self._run_btn:
            self._run_btn.set_state("disabled" if busy else "normal")
        if busy:
            self.pct_lab.configure(text=name or "运行中")
        else:
            self.pbar.set(0)
            self.pct_lab.configure(text="空闲")

    def _get_input(self, name):
        p, err = rt.check_input(self.in_var.get(), name)
        if err:
            messagebox.showwarning("输入无效", err)
            return None
        return p

    def run_flashvsr(self):
        p = self._get_input("FlashVSR")
        if not p:
            return
        mode = {"通用 · 动画": "tiny", "长视频": "tiny-long", "完整质量": "full"}.get(
            self.fv_mode.value, "tiny")
        scale = (self.fv_scale.value or "2x").rstrip("x")
        args = ["-InputPath", p, "-Mode", mode, "-Scale", scale,
                "-Format", self.fv_fmt.value or "video/h264-mp4"]
        if self.fv_auto.get():
            args.append("-AutoStart")
        self._run_ps("FlashVSR 放大（%s / %sx）" % (self.fv_mode.value, scale),
                     engine_ps("02-FlashVSR"), args)

    def run_seedvr2(self):
        p = self._get_input("SeedVR2")
        if not p:
            return
        prof = {"标准 · 12g": "12g", "平衡 · 16g": "16g", "极致 · 24g": "24g"}.get(
            self.sv_prof.value, "16g")
        args = ["-InputPath", p, "-Profile", prof, "-Resolution", self.sv_res.value or "1080"]
        if self.sv_over.get():
            args.append("-Overwrite")
        self._run_ps("SeedVR2 放大（%s 档 / 短边 %s）" % (prof, self.sv_res.value),
                     engine_ps("03-SeedVR2"), args)

    # ================================================================ 后台探测
    def _probe_loop(self):
        if self._closing:
            return
        threading.Thread(target=self._probe_once, daemon=True).start()
        self.root.after(5000, self._probe_loop)

    def _probe_once(self):
        """后台线程：只采集状态，交回主线程刷新（不直接碰控件）。"""
        st = {"alive": False, "version": "?", "gpu": "",
              "own": self.svc_proc is not None and self.svc_proc.poll() is None,
              "starting": self._svc_starting}
        d = rt.http_json(CONFIG["comfy_url"] + "/system_stats")
        if d is None:
            st["gpu"] = self._gpu_by_nvidia_smi()
        else:
            st["alive"] = True
            dev = (d.get("devices") or [{}])[0]
            used = (dev.get("vram_total", 0) - dev.get("vram_free", 0)) / 2 ** 30
            tot = dev.get("vram_total", 0) / 2 ** 30
            st["version"] = d.get("system", {}).get("comfyui_version", "?")
            st["gpu"] = "显存 %.1f/%.1fG" % (used, tot)
        self._probe_state = st
        self.uiqueue.put(self._apply_probe)      # 交主线程刷新，别在此线程碰控件

    def _apply_probe(self):
        st = self._probe_state
        alive, starting, own = st["alive"], st["starting"], st["own"]
        # 圆点
        color = GREEN if alive else (AMBER if starting else MUTED)
        self.svc_dot.itemconfigure(self._dot_id, fill=color)
        # 徽标
        if alive:
            self.svc_pill.configure(text="运行中", bg="#173a25", fg=GREEN)
        elif starting:
            self.svc_pill.configure(text="启动中…", bg="#3a3320", fg=AMBER)
        else:
            self.svc_pill.configure(text="未运行", bg=CARD2, fg=MUTED)
        self.stat_lab.configure(
            text=("v%s · %s" % (st["version"], st["gpu"])) if alive and st["gpu"]
            else (st["gpu"] if st["gpu"] else ""))
        # 主按钮
        if starting:
            self.btn_primary.set_text("启动中…")
            self.btn_primary.set_state("disabled")
        elif alive:
            self.btn_primary.set_text("重启服务")
            self.btn_primary.set_state("normal")
        else:
            self.btn_primary.set_text("启动服务")
            self.btn_primary.set_state("normal")
        # 停止：自己拉起的直接停；外部的走"按命令行找进程"也能停（v1.2.13）。
        # 服务活着但进程没匹配上时，点了会得到一句实话，而不是按钮灰着没法按。
        self.btn_stop.set_state("normal" if (own or alive) else "disabled")
        # 停止队列：只要服务活着就能清（外部启动的 ComfyUI 一样能中断/清队列，
        # 不像"停止服务"那样必须是自己拉起的进程）
        self.btn_queue.set_state("normal" if self._probe_state["alive"] else "disabled")

    def _gpu_by_nvidia_smi(self):
        """读显存（解析在 运行时.gpu_by_nvidia_smi）。

        本机没有 nvidia-smi 就记住，别每 5 秒白起一个进程 —— 反复 spawn 子进程
        既浪费，也是杀软启发式扫描喜欢盯的行为。
        """
        if not self._nvsmi_ok:
            return ""
        text, ok = rt.gpu_by_nvidia_smi()
        self._nvsmi_ok = ok
        return text

    # ================================================================ 退出
    def on_close(self):
        self._closing = True
        if self._task_busy():
            if not messagebox.askyesno("任务还在跑",
                                       "任务「%s」还没结束，确定退出吗？\n（退出会中断任务）" % self.task_name):
                self._closing = False
                return
            try:
                self.task_proc.terminate()
            except Exception:
                pass      # 正在退出：进程可能已经自己结束了。此时弹窗/写日志都没意义，静默是正确选择
        if self.svc_proc and self.svc_proc.poll() is None:
            if messagebox.askyesno("关闭服务", "要一并关闭 ComfyUI 服务吗？"):
                try:
                    self.svc_proc.terminate()
                except Exception:
                    pass  # 同上：退出路径上不作补救，避免"退出时报错"比问题本身更烦人
        self.logfile.close()          # 落盘旁路：退出前 flush，别把最后几行留在缓冲里
        self.root.destroy()


def main():
    install_crash_log()
    ui_kit.enable_dpi_awareness()
    root = tk.Tk()
    watch_tk_errors(root)
    ui_kit.init_scaling(root)

    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    app.log("视频方案启动器已启动", "hi")
    for _msg, _tag in rt.startup_lines(CONFIG):
        app.log(_msg, _tag)
    root.mainloop()


if __name__ == "__main__":
    main()
