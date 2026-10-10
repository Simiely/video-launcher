# -*- coding: utf-8 -*-
"""视频方案启动器 —— ComfyUI 底座 + FlashVSR / SeedVR2 / MiniMax H3 一键启动。

界面：深色 Fluent 三栏控制台（左＝导航 + 设置 / 中＝引擎参数 / 右＝服务 + 工作流 + 日志），
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
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

import tkinter as tk
from tkinter import filedialog, messagebox

import ui_kit
from ui_kit import (
    S, f, mono,
    BG, CARD, CARD2, BORDER, HOVER, TXT, TXT2,
    BLUE, PURP, TEAL, GREEN, AMBER, RED, DARKTX, MUTED,
    RoundedFrame, RButton, swatch_text, NavItem, Dropdown, ChipRow,
    CheckBox, ProgressBar,
)

# ---------------------------------------------------------------- 配置
APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))

CONFIG = {
    "comfy_dir":    r"C:\AI\ComfyUI",
    "comfy_py":     r"C:\AI\ComfyUI\.venv\Scripts\python.exe",
    "deploy_root":  r"C:\AI\video-upscale-deploy",
    "ffmpeg_dir":   r"C:\AI\ffmpeg\bin",
    "input_dir":    r"E:\VideoUpscale\input",
    "output_dir":   r"E:\VideoUpscale\output",
    "comfy_url":    "http://127.0.0.1:8188",
}

_cfg_path = os.path.join(APP_DIR, "启动器配置.json")
if os.path.exists(_cfg_path):                       # 允许用同名 JSON 覆盖默认路径
    try:
        with open(_cfg_path, encoding="utf-8") as _fh:
            CONFIG.update(json.load(_fh))
    except Exception as e:
        print("配置文件读取失败，用默认值：", e)

FLASHVSR_PS = os.path.join(CONFIG["deploy_root"], "02-FlashVSR", "批量放大.ps1")
SEEDVR2_PS  = os.path.join(CONFIG["deploy_root"], "03-SeedVR2", "批量放大.ps1")

NO_PROXY_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

MAX_LOG_LINES = 2000           # 日志区保留的最大行数，超出自动裁剪（防长任务日志无限增长拖慢 UI）

# ---------------------------------------------------------------- 内置工作流 / 引擎数据
ENG = {
    "flashvsr": {"color": BLUE, "label": "FlashVSR", "avatar": "F"},
    "seedvr2":  {"color": PURP, "label": "SeedVR2",  "avatar": "S"},
    "minimax":  {"color": TEAL, "label": "MiniMax H3", "avatar": "M"},
}

WORKFLOWS = [
    {"id": "fv_2x", "engine": "flashvsr", "name": "FlashVSR · 通用 2x 放大",
     "desc": "通用超分，适合写实/日常视频，输出 2 倍分辨率。",
     "steps": [("LoadVideo", "input_dir"), ("FlashVSR 超分", "scale=2"),
               ("(可选) 帧插值", "fps×2"), ("SaveVideo", "output_dir")],
     "inN": "1 × LoadVideo", "outN": "1 × SaveVideo"},
    {"id": "fv_4x", "engine": "flashvsr", "name": "FlashVSR · 动画 4x 放大",
     "desc": "针对动画/二次元优化，输出 4 倍分辨率，细节更锐。",
     "steps": [("LoadVideo", "input_dir"), ("FlashVSR 超分", "scale=4"),
               ("CAS 锐化", "strength=0.4"), ("SaveVideo", "output_dir")],
     "inN": "1 × LoadVideo", "outN": "1 × SaveVideo"},
    {"id": "sv_std", "engine": "seedvr2", "name": "SeedVR2 · 标准 1080p",
     "desc": "标准档，短边缩放到 1080，速度与质量平衡。",
     "steps": [("LoadVideo", "input_dir"), ("SeedVR2 标准档", "tile=512"),
               ("Resize 短边", "1080"), ("SaveVideo", "output_dir")],
     "inN": "1 × LoadVideo", "outN": "1 × SaveVideo"},
    {"id": "sv_max", "engine": "seedvr2", "name": "SeedVR2 · 极致 原画",
     "desc": "极致档，保留原画分辨率，显存占用高、最慢。",
     "steps": [("LoadVideo", "input_dir"), ("SeedVR2 极致档", "tile=384"),
               ("FaceRestore", "on"), ("SaveVideo", "output_dir")],
     "inN": "1 × LoadVideo", "outN": "1 × SaveVideo"},
    {"id": "mm_t2v", "engine": "minimax", "name": "MiniMax H3 · 文生视频 T2V",
     "desc": "用提示词直接生成视频（Text-to-Video）。",
     "steps": [("TextEncode", "prompt"), ("MiniMax H3", "T2V"),
               ("VAEDecode", ""), ("SaveVideo", "output_dir")],
     "inN": "0（纯文本）", "outN": "1 × SaveVideo"},
    {"id": "mm_i2v", "engine": "minimax", "name": "MiniMax H3 · 图生视频 I2V",
     "desc": "以首帧图片驱动生成视频（Image-to-Video）。",
     "steps": [("LoadImage", "first_frame"), ("MiniMax H3", "I2V"),
               ("VAEDecode", ""), ("SaveVideo", "output_dir")],
     "inN": "1 × LoadImage", "outN": "1 × SaveVideo"},
]


def ps_quote(s):
    """PowerShell 单引号字面量转义。"""
    return "'" + str(s).replace("'", "''") + "'"


class App:
    def __init__(self, root):
        self.root = root
        root.title("视频方案启动器")
        root.configure(bg=BG)
        root.geometry("%dx%d" % (S(1260), S(760)))   # 右栏放工作流卡后需要更宽（中栏 ~624 / 右栏 360）
        root.minsize(S(1040), S(640))

        self.logq = queue.Queue()
        self.uiqueue = queue.Queue()    # 后台线程 → 主线程的回调队列（tkinter 非线程安全，禁止跨线程碰控件）
        self.svc_proc = None            # ComfyUI 进程句柄
        self.task_proc = None           # 当前批量任务进程
        self.task_name = ""
        self._closing = False
        self._svc_starting = False      # 启动中锁：防止就绪前连点起多个 ComfyUI 进程
        self._log_lines = 0             # 日志区当前行数（自维护，用于裁剪，不依赖 Text.count）
        self._loaded_api = None         # 已加载的本地工作流（API 格式 JSON）
        self._active_wf_id = None
        self._probe_state = {"alive": False, "starting": False, "own": False,
                             "version": "?", "gpu": ""}
        self._run_btn = None            # 当前引擎卡上的「运行」按钮

        self._build_ui()
        # 启动时只选中默认引擎、不滚动：让中栏停在引擎卡首屏
        self._select_nav("flashvsr", scroll=False)
        self._drain_log()
        self._probe_loop()

    # ================================================================ UI 搭建
    def _build_ui(self):
        app = tk.Frame(self.root, bg=BG)
        app.pack(fill="both", expand=True, padx=S(14), pady=S(14))
        app.columnconfigure(1, weight=1)
        app.rowconfigure(0, weight=1)

        self._build_sidebar(app)
        self._build_center(app)
        self._build_logpane(app)

    # ---- 左：导航 ----
    def _build_sidebar(self, parent):
        side = RoundedFrame(parent, outer=BG, pad=(4, 8), radius=S(12))
        side.grid(row=0, column=0, sticky="nsw", padx=(0, S(14)))
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
        # 服务卡（右栏顶部）、工作流卡（右栏中部）、设置（左栏下方）都是常显区，不做导航项
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
        center.rowconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)

        # 滚动舞台：服务卡已挪到右栏顶部、设置已挪到左栏下方，此处只剩引擎区
        wrap = tk.Frame(center, bg=BG)
        wrap.grid(row=0, column=0, sticky="nsew")
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

    def _section(self, parent, text):
        tk.Label(parent, text=text, bg=BG, fg=TXT2, font=f(9), anchor="w").pack(
            fill="x", pady=(0, S(8)), padx=S(2))

    # ---- 工作流卡（右栏顶部，常显不滚动）----
    def _build_workflow_card(self, parent):
        card = RoundedFrame(parent, outer=BG, pad=(6, 6))
        card.pack(fill="x", pady=(0, S(14)))
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

        tk.Label(b, text="工作流", bg=CARD, fg=TXT2, font=f(9), anchor="w").pack(fill="x")
        self.wf_dd = Dropdown(b, [w["name"] for w in WORKFLOWS], command=self._on_wf_select,
                              outer=CARD, placeholder="— 请选择一套内置工作流 —", font=f(10))
        self.wf_dd.pack(fill="x", pady=(S(5), 0))
        self.wf_nodes = tk.Label(b, text="未选择 · 共 %d 套内置" % len(WORKFLOWS),
                                 bg=CARD, fg=TXT2, font=f(8), anchor="w")
        self.wf_nodes.pack(fill="x", pady=(S(6), 0))

        self.wf_detail = tk.Frame(b, bg=CARD)
        self._render_wf_detail(None, None)

    def _render_wf_detail(self, wf, local_name=None):
        # 右栏窄（~340 可用宽），正文换行宽度统一收窄到 ~250
        d = self.wf_detail
        for c in d.winfo_children():
            c.destroy()
        d.pack(fill="x", pady=(S(12), 0))
        box = tk.Frame(d, bg=BORDER)
        box.pack(fill="x")
        inner = tk.Frame(box, bg="#1c202a")
        inner.pack(fill="x", padx=1, pady=1)

        if wf is None and local_name is None:
            tk.Label(inner, text="选择一套内置工作流，或「加载 .json」选本地 ComfyUI 工作流，"
                                 "这里会显示它的节点与输入输出。",
                     bg="#1c202a", fg=TXT2, font=f(9), anchor="w", justify="left",
                     wraplength=S(250)).pack(fill="x", padx=S(12), pady=S(12))
            return

        pad = tk.Frame(inner, bg="#1c202a")
        pad.pack(fill="x", padx=S(12), pady=S(10))

        # 头：名称（可换行）+ 徽标靠右
        head = tk.Frame(pad, bg="#1c202a")
        head.pack(fill="x")
        if wf:
            meta = ENG[wf["engine"]]
            name, badge, bcol = wf["name"], meta["label"], meta["color"]
            desc, steps = wf["desc"], wf["steps"]
            inN, outN = wf["inN"], wf["outN"]
        else:
            name, badge, bcol = local_name, "本地", AMBER
            desc = "已从本地加载：%s" % local_name
            steps, inN, outN = [], "—", "—"
        tk.Label(head, text=badge, bg=bcol, fg=DARKTX, font=f(8, True),
                 padx=S(8), pady=S(1)).pack(side="right")
        tk.Label(head, text=name, bg="#1c202a", fg=TXT, font=f(10, True), anchor="w",
                 justify="left", wraplength=S(200)).pack(side="left", fill="x", expand=True)

        tk.Label(pad, text=desc, bg="#1c202a", fg=TXT2, font=f(9), anchor="w",
                 justify="left", wraplength=S(250)).pack(fill="x", pady=(S(8), S(4)))

        steps_box = tk.Frame(pad, bg="#1c202a")
        steps_box.pack(fill="x")
        for i, (node, io) in enumerate(steps, 1):
            r = tk.Frame(steps_box, bg="#1c202a")
            r.pack(fill="x", pady=S(3))
            n = tk.Label(r, text=str(i), bg=bcol, fg=DARKTX, font=f(8, True),
                         width=2, padx=S(4), pady=0)
            n.pack(side="left")
            tk.Label(r, text=node, bg="#1c202a", fg=TXT, font=f(9), anchor="w").pack(
                side="left", padx=(S(8), 0))
            if io:
                tk.Label(r, text=io, bg="#1c202a", fg=TXT2, font=f(8)).pack(side="right")

        nodes = len(steps)
        if local_name:
            nodes = self.wf_nodes_text if getattr(self, "wf_nodes_text", None) else nodes
        # 窄栏放不下 4 项横排 → 2×2 网格
        meta_row = tk.Frame(pad, bg="#1c202a")
        meta_row.pack(fill="x", pady=(S(10), S(4)))
        meta_row.columnconfigure(0, weight=1)
        meta_row.columnconfigure(1, weight=1)
        for i, (lab, val) in enumerate((("节点", str(nodes)), ("输入", inN),
                                        ("输出", outN), ("目标", "ComfyUI :8188"))):
            cell = tk.Frame(meta_row, bg="#1c202a")
            cell.grid(row=i // 2, column=i % 2, sticky="w", pady=S(2))
            tk.Label(cell, text=lab + " ", bg="#1c202a", fg=TXT2, font=f(8)).pack(side="left")
            tk.Label(cell, text=val, bg="#1c202a", fg=TXT, font=f(8, True)).pack(side="left")

        acts = tk.Frame(pad, bg="#1c202a")
        acts.pack(fill="x", pady=(S(10), 0))
        RButton(acts, text="推送到 ComfyUI", command=lambda: self._push_wf(False),
                fill=TEAL, fg=DARKTX, border=None, outer="#1c202a", font=f(9), pady=6,
                stretch=True).pack(side="left", fill="x", expand=True)
        RButton(acts, text="加入队列", command=lambda: self._push_wf(True),
                fill="transparent", fg=TXT, border=BORDER, outer="#1c202a",
                font=f(9), pady=6, stretch=True).pack(side="left", fill="x", expand=True,
                                                     padx=(S(8), 0))

    # ---- 引擎区（单卡，切换交给左导航）----
    def _build_engine_area(self, parent):
        # v1.2.3：去掉中栏那排 FlashVSR / SeedVR2 / MiniMax H3 切换按钮 ——
        # 左导航已经承担"切引擎"，两处入口重复且占掉首屏高度
        self._section(parent, "引擎 · 由左侧导航切换")

        self.engine_card = RoundedFrame(parent, outer=BG, pad=(6, 6))
        self.engine_card.pack(fill="x", pady=(0, S(16)))

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
        """工作流卡固定在右栏顶部、不在滚动舞台里，所以用一次描边闪烁提示它在哪。"""
        self.wf_card.set_border(BLUE)
        self.root.after(500, lambda: self.wf_card.set_border(BORDER))

    # ---- 左栏下方：设置（常驻，窄栏两行式）----
    def _build_settings_section(self, parent):
        """设置常驻左栏下方（v1.2.3）：不再做成「点导航 → 中栏单独刷出设置页」。

        左栏可用宽只有 ~174px，所以每项拆两行：标签行（右侧挂「打开」链接）+ 值行（自动换行）。
        """
        tk.Label(parent, text="设置", bg=CARD, fg=TXT2, font=f(9), anchor="w").pack(
            fill="x", padx=S(2), pady=(0, S(2)))
        keys = [("comfy_dir", "ComfyUI 目录", True), ("comfy_py", "ComfyUI Python", False),
                ("deploy_root", "部署脚本目录", True), ("ffmpeg_dir", "ffmpeg 目录", True),
                ("input_dir", "输入目录", True), ("output_dir", "输出目录", True),
                ("comfy_url", "服务地址", False)]
        for k, label, is_dir in keys:
            row = tk.Frame(parent, bg=CARD)
            row.pack(fill="x", pady=(S(6), 0))
            tk.Label(row, text=label, bg=CARD, fg=TXT2, font=f(8), anchor="w").pack(side="left")
            if is_dir:
                link = tk.Label(row, text="打开", bg=CARD, fg=TXT2, font=f(8), cursor="hand2")
                link.pack(side="right")
                link.bind("<Enter>", lambda e, w=link: w.configure(fg=BLUE))
                link.bind("<Leave>", lambda e, w=link: w.configure(fg=TXT2))
                link.bind("<Button-1>", lambda e, p=CONFIG.get(k, ""): self._open_dir(p))
            tk.Label(parent, text=CONFIG.get(k, ""), bg=CARD, fg=TXT, font=f(8), anchor="w",
                     justify="left", wraplength=S(168)).pack(fill="x")
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=(S(10), 0))
        RButton(row, text="打开输入", command=lambda: self._open_dir(CONFIG["input_dir"]),
                fill=CARD2, fg=TXT, outer=CARD, font=f(8), pady=5,
                stretch=True).pack(side="left", fill="x", expand=True)
        RButton(row, text="打开输出", command=lambda: self._open_dir(CONFIG["output_dir"]),
                fill=CARD2, fg=TXT, outer=CARD, font=f(8), pady=5,
                stretch=True).pack(side="left", fill="x", expand=True, padx=(S(6), 0))

    # ---- 服务卡（右栏顶部，常显）----
    def _build_service_card(self, parent):
        """ComfyUI 服务卡：原先在中栏置顶，v1.2.3 按要求挪到右栏、工作流卡上方。

        右栏 ~324 可用宽放不下一整行（圆点+标题+徽标+地址+三个按钮），
        故拆成两行：状态行（左：圆点/标题/徽标，右：版本+显存）+ 地址行 + 按钮行。
        """
        card = RoundedFrame(parent, outer=BG, pad=(6, 6))
        card.pack(fill="x", pady=(0, S(14)))
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

        row = tk.Frame(b, bg=CARD)
        row.pack(fill="x")
        self.btn_primary = RButton(row, text="启动服务", command=self._primary_service,
                                   fill=CARD2, fg=TXT, outer=CARD, font=f(9), pady=6,
                                   stretch=True)
        self.btn_primary.pack(side="left", fill="x", expand=True)
        RButton(row, text="打开网页", command=self.open_web, fill=CARD2, fg=TXT,
                outer=CARD, font=f(9), pady=6, stretch=True).pack(
                    side="left", fill="x", expand=True, padx=(S(6), S(6)))
        self.btn_stop = RButton(row, text="停止服务", command=self.svc_stop, fill=CARD2,
                                fg=TXT, outer=CARD, font=f(9), pady=6, state="disabled",
                                stretch=True)
        self.btn_stop.pack(side="left", fill="x", expand=True)

    # ---- 右：服务（上）+ 工作流（中）+ 日志（下，占满剩余）----
    def _build_logpane(self, parent):
        col = tk.Frame(parent, bg=BG)
        col.grid(row=0, column=2, sticky="nsew", padx=(S(14), 0))
        col.configure(width=S(360))
        col.pack_propagate(False)

        # 上：ComfyUI 服务卡（常显）
        self._build_service_card(col)

        # 中：工作流卡（常显，不随中栏滚动）
        self._build_workflow_card(col)

        # 下：日志卡（吃掉剩余高度）
        pane = RoundedFrame(col, outer=BG, pad=(4, 4), radius=S(12))
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

        self.logtxt = tk.Text(b, bg=CARD, fg=TXT2, bd=0, highlightthickness=0,
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
        服务卡（右栏顶部）、工作流卡（右栏中部）、设置（左栏下方）都是常显区，不做导航项。
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

    # ================================================================ 工作流
    def _on_wf_select(self, name):
        wf = next((w for w in WORKFLOWS if w["name"] == name), None)
        if not wf:
            return
        self._loaded_api = None
        self._active_wf_id = wf["id"]
        self.wf_nodes.configure(text="%d 节点" % len(wf["steps"]))
        self.wf_nodes_text = len(wf["steps"])
        self._render_wf_detail(wf, None)
        tag = "lw" if wf["engine"] == "minimax" else "ok"
        self.log("[工作流] 已选择 %s" % wf["name"], tag)
        # 同步左导航高亮与引擎标签（工作流自带引擎），不滚动以免把刚展开的详情面板甩出视野
        self._select_nav(wf["engine"], scroll=False)

    def _load_wf_json(self):
        p = filedialog.askopenfilename(title="选择 ComfyUI 工作流 .json",
                                       filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if not p:
            return
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            self.log("[工作流] 解析失败：%r" % e, "err")
            return
        n = 0
        if isinstance(data, dict):
            if isinstance(data.get("nodes"), list):
                n = len(data["nodes"])
            elif isinstance(data.get("api"), dict) and isinstance(data["api"].get("nodes"), list):
                n = len(data["api"]["nodes"])
            else:
                n = len(data)
        self._loaded_api = data
        self._active_wf_id = None
        name = os.path.basename(p)
        self.wf_dd.set(name)
        self.wf_nodes.configure(text="%d 节点" % n)
        self.wf_nodes_text = n
        self._render_wf_detail(None, name)
        self.log("[工作流] 已加载本地 %s（%d 节点）" % (name, n), "lw")

    def _push_wf(self, to_queue):
        wf = next((w for w in WORKFLOWS if w["id"] == self._active_wf_id), None)
        name = wf["name"] if wf else (self.wf_dd.value or "未选择")
        if self._loaded_api is None:
            self.log("[工作流] 「%s」是内置方案说明，未含 API 图；请「加载 .json」后再推送" % name,
                     "warn")
            return
        if not self.comfy_alive():
            self.log("[工作流] ComfyUI 未运行，先启动服务再%s" % ("入队" if to_queue else "推送"),
                     "warn")
            return
        try:
            payload = json.dumps({"prompt": self._loaded_api,
                                  "client_id": "video-launcher"}).encode("utf-8")
            req = urllib.request.Request(CONFIG["comfy_url"] + "/prompt", data=payload,
                                         headers={"Content-Type": "application/json"})
            with NO_PROXY_OP.open(req, timeout=10) as r:
                r.read()
            self.log("[工作流] 已%s「%s」→ ComfyUI" % ("入队" if to_queue else "推送", name),
                     "lw" if to_queue else "ok")
        except Exception as e:
            self.log("[工作流] 推送失败：%r" % e, "err")

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
        if os.path.isdir(d):
            os.startfile(d)
        else:
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
        if any(k in line for k in ("⚠️", "失败", "错误", "Traceback", "未就绪", "解析失败")):
            return "warn"
        if any(k in line for k in ("✅", "已就绪", "完成", "结束：rc=0", "rc=0")):
            return "ok"
        if line.startswith("[工作流]") or line.startswith("[队列]"):
            return "lw"
        if line.startswith("[ComfyUI]"):
            return "dim"
        return None

    def _drain_log(self):
        dirty = False
        try:
            while True:
                line, tag = self.logq.get_nowait()
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
        except queue.Empty:
            pass
        # 排空「后台线程 → 主线程」回调（tkinter 只能在主线程操作控件）
        try:
            while True:
                self.uiqueue.get_nowait()()
        except queue.Empty:
            pass
        if dirty:
            self.logtxt.see("end")
        if not self._closing:
            self.root.after(150, self._drain_log)

    def _maybe_progress(self, line):
        m = re.search(r"(\d+)\s*/\s*(\d+)", line)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b:
                self.pbar.set(a * 100.0 / b)
                self.pct_lab.configure(text="%d%%" % int(a * 100.0 / b))
            return
        m = re.search(r"(\d{1,3})\s*%", line)
        if m:
            self.pbar.set(int(m.group(1)))
            self.pct_lab.configure(text=m.group(1) + "%")

    def _set_task_ui(self, busy, name=""):
        if self._run_btn:
            self._run_btn.set_state("disabled" if busy else "normal")
        if busy:
            self.pct_lab.configure(text=name or "运行中")
        else:
            self.pbar.set(0)
            self.pct_lab.configure(text="空闲")

    # ================================================================ 服务
    def _comfy_env(self):
        env = dict(os.environ)
        env["PATH"] = CONFIG["ffmpeg_dir"] + os.pathsep + env.get("PATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def comfy_alive(self):
        try:
            with NO_PROXY_OP.open(CONFIG["comfy_url"] + "/system_stats", timeout=3) as r:
                json.loads(r.read().decode("utf-8"))
            return True
        except Exception:
            return False

    def _primary_service(self):
        if self._probe_state["alive"]:
            self.svc_restart()
        else:
            self.svc_start()

    def svc_start(self):
        if self._svc_starting:
            self.log("[服务] ComfyUI 正在启动中，请稍候…")
            return
        if self.svc_proc is not None and self.svc_proc.poll() is None:
            self.log("[服务] ComfyUI 已在本程序运行")
            return
        if self.comfy_alive():
            self.log("[服务] ComfyUI 已在运行，不用重复启动")
            return
        if not os.path.exists(CONFIG["comfy_py"]):
            messagebox.showerror("找不到 ComfyUI", CONFIG["comfy_py"])
            return
        self._svc_starting = True
        self.btn_primary.set_state("disabled")
        self.log("[服务] 正在启动 ComfyUI（首次加载约 15~60 秒）…", "hi")
        try:
            self.svc_proc = subprocess.Popen(
                [CONFIG["comfy_py"], "-u", "main.py",
                 "--disable-pinned-memory", "--disable-async-offload", "--reserve-vram", "1"],
                cwd=CONFIG["comfy_dir"], env=self._comfy_env(),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1)
        except Exception as e:
            self._svc_starting = False
            self.btn_primary.set_state("normal")
            messagebox.showerror("启动失败", repr(e))
            return
        threading.Thread(target=self._svc_reader, daemon=True).start()
        threading.Thread(target=self._svc_wait_ready, daemon=True).start()

    def svc_restart(self):
        if self._svc_starting:
            self.log("[服务] 正在启动中，稍候再重启")
            return
        own = self.svc_proc is not None and self.svc_proc.poll() is None
        if not own:
            self.log("[服务] 当前服务不是本程序拉起的，无法重启；请手动关闭后再启动", "warn")
            return
        self.log("[服务] 重启中：先停后起…", "hi")
        threading.Thread(target=self._restart_worker, daemon=True).start()

    def _restart_worker(self):
        p = self.svc_proc
        try:
            p.terminate()
        except Exception as e:
            self.log("[服务] 停止失败：%r" % e, "err")
        for _ in range(40):                       # 最多等 20 秒退化
            if self._closing:
                return
            if p.poll() is not None and not self.comfy_alive():
                break
            time.sleep(0.5)
        if not self._closing:
            self.uiqueue.put(self.svc_start)

    def _svc_reader(self):
        p = self.svc_proc
        try:
            for line in p.stdout:
                self.log("[ComfyUI] " + line.rstrip())
        except Exception:
            pass
        rc = p.wait()
        self._svc_starting = False          # 进程退出即解除启动中锁（按钮态由探测统一刷新）
        if not self._closing:
            self.log("[服务] ComfyUI 进程退出，rc=%s" % rc)

    def _svc_wait_ready(self):
        for _ in range(100):                     # 最多 5 分钟
            if self._closing:
                return
            if self.comfy_alive():
                self._svc_starting = False
                self.log("[服务] ✅ ComfyUI 已就绪：%s" % CONFIG["comfy_url"], "ok")
                webbrowser.open(CONFIG["comfy_url"])   # 非 Tk 调用，后台线程可直接开浏览器
                return
            if self.svc_proc and self.svc_proc.poll() is not None:
                self._svc_starting = False
                return
            time.sleep(3)
        self._svc_starting = False
        self.log("[服务] ⚠️ 5 分钟内未就绪，请看日志排查", "warn")

    def svc_stop(self):
        if self.svc_proc is None:
            self.log("[服务] ComfyUI 不是本程序拉起的，请在网页/原终端里关闭", "warn")
            return
        if self.svc_proc.poll() is None:
            self.log("[服务] 正在停止 ComfyUI…", "hi")
            try:
                self.svc_proc.terminate()
            except Exception as e:
                self.log("[服务] 停止失败：%r" % e, "err")

    def open_web(self):
        if not self.comfy_alive():
            self.svc_start()
            return                                  # svc_start 就绪后会自动开网页
        webbrowser.open(CONFIG["comfy_url"])

    # ================================================================ 批量任务
    def _task_busy(self):
        return self.task_proc is not None and self.task_proc.poll() is None

    def _run_ps(self, title, script, arglist):
        if self._task_busy():
            messagebox.showwarning("有任务在跑", "当前任务：%s\n等它结束再开新的" % self.task_name)
            return
        args = " ".join(a if a.startswith("-") else ps_quote(a) for a in arglist)
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
               "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
               "$env:PYTHONIOENCODING='utf-8';"
               "& %s %s; $rc = if ($?) { 0 } else { 1 }; exit $rc" % (ps_quote(script), args)]
        self.log("")
        self.log("═" * 60, "dim")
        self.log("▶ %s" % title, "hi")
        self.log("  %s" % " ".join(cmd[4:]), "dim")
        try:
            self.task_proc = subprocess.Popen(
                cmd, cwd=CONFIG["deploy_root"], env=self._comfy_env(),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        except Exception as e:
            messagebox.showerror("启动失败", repr(e))
            return
        self.task_name = title
        self._set_task_ui(True, title)
        threading.Thread(target=self._task_reader, args=(title,), daemon=True).start()

    def _task_reader(self, title):
        p = self.task_proc
        t0 = time.time()
        try:
            for line in p.stdout:
                self.log("  " + line.rstrip())
        except Exception as e:
            self.log("[读取日志异常] %r" % e, "err")
        rc = p.wait()
        el = time.time() - t0
        self.log("■ %s 结束：rc=%s，用时 %.0f 分 %.0f 秒" % (title, rc, el // 60, el % 60),
                 "ok" if rc == 0 else "err")
        if rc == 0:
            self.log("  输出目录：%s" % CONFIG["output_dir"], "ok")
        self.task_proc = None
        self.task_name = ""
        self.uiqueue.put(lambda: self._set_task_ui(False))

    def _get_input(self, name):
        p = self.in_var.get().strip().strip('"')
        if not p:
            messagebox.showwarning("缺输入", "请先选择 %s 的输入视频/文件夹" % name)
            return None
        if not os.path.exists(p):
            messagebox.showwarning("路径不存在", p)
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
                     FLASHVSR_PS, args)

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
                     SEEDVR2_PS, args)

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
        try:
            with NO_PROXY_OP.open(CONFIG["comfy_url"] + "/system_stats", timeout=3) as r:
                d = json.loads(r.read().decode("utf-8"))
            st["alive"] = True
            dev = (d.get("devices") or [{}])[0]
            used = (dev.get("vram_total", 0) - dev.get("vram_free", 0)) / 2 ** 30
            tot = dev.get("vram_total", 0) / 2 ** 30
            st["version"] = d.get("system", {}).get("comfyui_version", "?")
            st["gpu"] = "显存 %.1f/%.1fG" % (used, tot)
        except Exception:
            st["gpu"] = self._gpu_by_nvidia_smi()
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
        # 停止：仅当自己拉起的进程在跑
        self.btn_stop.set_state("normal" if own else "disabled")

    def _gpu_by_nvidia_smi(self):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=8).stdout
            u, t = out.strip().splitlines()[0].split(",")
            return "显存 %.1f/%.1fG" % (int(u) / 1024, int(t) / 1024)
        except Exception:
            return ""

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
                pass
        if self.svc_proc and self.svc_proc.poll() is None:
            if messagebox.askyesno("关闭服务", "要一并关闭 ComfyUI 服务吗？"):
                try:
                    self.svc_proc.terminate()
                except Exception:
                    pass
        self.root.destroy()


def main():
    ui_kit.enable_dpi_awareness()
    root = tk.Tk()
    ui_kit.init_scaling(root)

    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    app.log("视频方案启动器已启动", "hi")
    app.log("  ComfyUI 目录 : %s" % CONFIG["comfy_dir"])
    app.log("  部署脚本目录 : %s" % CONFIG["deploy_root"])
    app.log("  ffmpeg       : %s %s" % (CONFIG["ffmpeg_dir"],
                                        "✅" if os.path.exists(os.path.join(CONFIG["ffmpeg_dir"], "ffmpeg.exe")) else "⚠️ 未找到"))
    root.mainloop()


if __name__ == "__main__":
    main()
