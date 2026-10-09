# -*- coding: utf-8 -*-
"""视频方案启动器 —— ComfyUI 底座 + FlashVSR / SeedVR2 / MiniMax H3 一键启动。

设计要点（对应本机已踩过的坑）：
  * 所有 .ps1 一律 `powershell -NoProfile -ExecutionPolicy Bypass`（本机执行策略 Restricted）
  * 不用 -File 而用 -Command 包一层，先设 [Console]::OutputEncoding=UTF8 ——
    否则 PS 5.1 按 cp936 重编 python 的 UTF-8 输出，日志全变乱码
  * 子进程异步 + 日志实时回传（SeedVR2 一跑 20+ 分钟，绝不能卡 UI）
  * ffmpeg 目录显式前置 PATH，不依赖注册表
  * 探测 ComfyUI 必须绕开系统代理（Clash 会把 127.0.0.1 也代理掉，返回 502）
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

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
        CONFIG.update(json.load(open(_cfg_path, encoding="utf-8")))
    except Exception as e:
        print("配置文件读取失败，用默认值：", e)

FLASHVSR_PS = os.path.join(CONFIG["deploy_root"], "02-FlashVSR", "批量放大.ps1")
SEEDVR2_PS  = os.path.join(CONFIG["deploy_root"], "03-SeedVR2", "批量放大.ps1")

NO_PROXY_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PAD = {"padx": 8, "pady": 4}   # 统一内边距（模块级，页签方法里也要用）


def ps_quote(s):
    """PowerShell 单引号字面量转义。"""
    return "'" + str(s).replace("'", "''") + "'"


class App:
    def __init__(self, root):
        self.root = root
        root.title("视频方案启动器 · ComfyUI %s" % "")
        root.geometry("980x720")
        root.minsize(860, 600)

        self.logq = queue.Queue()
        self.svc_proc = None            # ComfyUI 进程句柄
        self.task_proc = None           # 当前批量任务进程
        self.task_name = ""
        self._closing = False

        self._build_ui()
        self._drain_log()
        self._probe_loop()

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        # --- 状态条 ---
        top = ttk.Frame(self.root)
        top.pack(fill="x", **PAD)
        ttk.Label(top, text="ComfyUI 服务：").pack(side="left")
        self.var_svc = tk.StringVar(value="检测中…")
        ttk.Label(top, textvariable=self.var_svc, font=("", 10, "bold")).pack(side="left")
        ttk.Label(top, text="    GPU：").pack(side="left")
        self.var_gpu = tk.StringVar(value="…")
        ttk.Label(top, textvariable=self.var_gpu).pack(side="left")

        self.btn_start = ttk.Button(top, text="启动服务", command=self.svc_start)
        self.btn_start.pack(side="left", padx=(16, 4))
        self.btn_stop = ttk.Button(top, text="停止服务", command=self.svc_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=4)
        ttk.Button(top, text="打开网页", command=self.open_web).pack(side="left", padx=4)

        # --- 方案页签 ---
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=False, **PAD)
        self.nb = nb
        self._tab_flashvsr(nb)
        self._tab_seedvr2(nb)
        self._tab_h3(nb)

        # --- 日志 ---
        lf = ttk.LabelFrame(self.root, text="日志（实时）")
        lf.pack(fill="both", expand=True, **PAD)
        self.txt = scrolledtext.ScrolledText(lf, height=14, wrap="word",
                                             font=("Consolas", 9), state="disabled")
        self.txt.pack(fill="both", expand=True, padx=4, pady=4)

        # --- 底部 ---
        bot = ttk.Frame(self.root)
        bot.pack(fill="x", **PAD)
        ttk.Button(bot, text="打开输出目录", command=lambda: self._open_dir(CONFIG["output_dir"])).pack(side="left")
        ttk.Button(bot, text="打开输入目录", command=lambda: self._open_dir(CONFIG["input_dir"])).pack(side="left", padx=6)
        self.var_task = tk.StringVar(value="空闲")
        ttk.Label(bot, textvariable=self.var_task, foreground="#555").pack(side="right")

    def _tab_flashvsr(self, nb):
        f = ttk.Frame(nb); nb.add(f, text=" ① FlashVSR 放大 ")
        r = 0
        ttk.Label(f, text="输入（视频文件或整个文件夹）").grid(row=r, column=0, sticky="w", **PAD)
        self.fv_in = tk.StringVar()
        ttk.Entry(f, textvariable=self.fv_in, width=64).grid(row=r, column=1, columnspan=3, sticky="we", **PAD)
        ttk.Button(f, text="选文件", command=lambda: self._pick(self.fv_in, file=True)).grid(row=r, column=4, **PAD)
        ttk.Button(f, text="选文件夹", command=lambda: self._pick(self.fv_in, file=False)).grid(row=r, column=5, **PAD)

        r += 1
        ttk.Label(f, text="模式").grid(row=r, column=0, sticky="w", **PAD)
        self.fv_mode = ttk.Combobox(f, values=["tiny", "tiny-long", "full"], state="readonly", width=10)
        self.fv_mode.set("tiny"); self.fv_mode.grid(row=r, column=1, sticky="w", **PAD)
        ttk.Label(f, text="倍数").grid(row=r, column=1, sticky="e", **PAD)
        self.fv_scale = ttk.Combobox(f, values=["2", "3", "4"], state="readonly", width=4)
        self.fv_scale.set("4"); self.fv_scale.grid(row=r, column=2, sticky="w", padx=(2, 8), pady=4)
        ttk.Label(f, text="编码").grid(row=r, column=2, sticky="e", **PAD)
        self.fv_fmt = ttk.Combobox(f, state="readonly", width=18, values=[
            "video/h264-mp4", "video/h265-mp4", "video/nvenc_h264-mp4", "video/nvenc_hevc-mp4"])
        self.fv_fmt.set("video/h264-mp4"); self.fv_fmt.grid(row=r, column=3, sticky="w", padx=(2, 8), pady=4)
        self.fv_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="服务没跑就自动拉起", variable=self.fv_auto).grid(row=r, column=4, columnspan=2, sticky="w", **PAD)

        r += 1
        self.btn_fv = ttk.Button(f, text="开跑（FlashVSR）", command=self.run_flashvsr)
        self.btn_fv.grid(row=r, column=1, sticky="w", **PAD)
        ttk.Label(f, text="实测参考：854×480 → 3416×1920（4x），2 分 39 秒",
                  foreground="#777").grid(row=r, column=2, columnspan=4, sticky="w", **PAD)
        f.columnconfigure(1, weight=1)

    def _tab_seedvr2(self, nb):
        f = ttk.Frame(nb); nb.add(f, text=" ② SeedVR2 放大 ")
        r = 0
        ttk.Label(f, text="输入（视频文件或整个文件夹）").grid(row=r, column=0, sticky="w", **PAD)
        self.sv_in = tk.StringVar()
        ttk.Entry(f, textvariable=self.sv_in, width=64).grid(row=r, column=1, columnspan=3, sticky="we", **PAD)
        ttk.Button(f, text="选文件", command=lambda: self._pick(self.sv_in, file=True)).grid(row=r, column=4, **PAD)
        ttk.Button(f, text="选文件夹", command=lambda: self._pick(self.sv_in, file=False)).grid(row=r, column=5, **PAD)

        r += 1
        ttk.Label(f, text="档位").grid(row=r, column=0, sticky="w", **PAD)
        self.sv_prof = ttk.Combobox(f, values=["12g", "16g", "24g"], state="readonly", width=8)
        self.sv_prof.set("16g"); self.sv_prof.grid(row=r, column=1, sticky="w", **PAD)
        ttk.Label(f, text="目标短边").grid(row=r, column=1, sticky="e", **PAD)
        self.sv_res = ttk.Combobox(f, values=["720", "1080"], state="readonly", width=6)
        self.sv_res.set("1080"); self.sv_res.grid(row=r, column=2, sticky="w", padx=(2, 8), pady=4)
        self.sv_over = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="覆盖已有输出", variable=self.sv_over).grid(row=r, column=3, columnspan=2, sticky="w", **PAD)

        r += 1
        self.btn_sv = ttk.Button(f, text="开跑（SeedVR2）", command=self.run_seedvr2)
        self.btn_sv.grid(row=r, column=1, sticky="w", **PAD)
        ttk.Label(f, text="实测参考：480p → 1080p（48 帧），约 23 分钟，音轨自动回接",
                  foreground="#777").grid(row=r, column=2, columnspan=4, sticky="w", **PAD)
        f.columnconfigure(1, weight=1)

    def _tab_h3(self, nb):
        f = ttk.Frame(nb); nb.add(f, text=" ③ MiniMax H3 生成 ")
        t = ("H3 是「文生视频 + 音画同出」，参数要在网页工作流里调（提示词 / 帧数 / 步数），\n"
             "所以启动器只负责把服务和网页拉起来，工作流你自己搭。\n\n"
             "步骤：① 点下面按钮启动服务 → ② 浏览器打开后用模板库 Video → MiniMax H3 → T2V\n"
             "     ③ 首次验证参数建议 864×480 / 124 帧 / 20 步（本机实测 3 分 13 秒/条，带立体声）\n\n"
             "注意：显存峰值 15.2/16 GiB，跑 H3 时别开别的吃显存的程序。")
        ttk.Label(f, text=t, justify="left").grid(row=0, column=0, sticky="w", **PAD)
        ttk.Button(f, text="启动服务并打开网页", command=self.open_web).grid(row=1, column=0, sticky="w", **PAD)
        f.columnconfigure(0, weight=1)

    # ------------------------------------------------------------ 小工具
    def _pick(self, var, file=True):
        if file:
            p = filedialog.askopenfilename(
                initialdir=CONFIG["input_dir"],
                filetypes=[("视频", "*.mp4 *.avi *.mov *.mkv *.webm *.flv *.wmv"), ("所有文件", "*.*")])
        else:
            p = filedialog.askdirectory(initialdir=CONFIG["input_dir"])
        if p:
            var.set(p)

    def _open_dir(self, d):
        if os.path.isdir(d):
            os.startfile(d)
        else:
            messagebox.showwarning("目录不存在", d)

    def log(self, msg):
        self.logq.put(msg.rstrip("\n"))

    def _drain_log(self):
        try:
            while True:
                line = self.logq.get_nowait()
                self.txt.configure(state="normal")
                self.txt.insert("end", line + "\n")
                self.txt.see("end")
                self.txt.configure(state="disabled")
        except queue.Empty:
            pass
        if not self._closing:
            self.root.after(150, self._drain_log)

    # ------------------------------------------------------------ 服务
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

    def svc_start(self):
        if self.comfy_alive():
            self.log("[服务] ComfyUI 已在运行，不用重复启动")
            return
        if not os.path.exists(CONFIG["comfy_py"]):
            messagebox.showerror("找不到 ComfyUI", CONFIG["comfy_py"])
            return
        self.log("[服务] 正在启动 ComfyUI（首次加载约 15~60 秒）…")
        try:
            self.svc_proc = subprocess.Popen(
                [CONFIG["comfy_py"], "-u", "main.py",
                 "--disable-pinned-memory", "--disable-async-offload", "--reserve-vram", "1"],
                cwd=CONFIG["comfy_dir"], env=self._comfy_env(),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1)
        except Exception as e:
            messagebox.showerror("启动失败", repr(e))
            return
        self.btn_stop.configure(state="normal")
        threading.Thread(target=self._svc_reader, daemon=True).start()
        threading.Thread(target=self._svc_wait_ready, daemon=True).start()

    def _svc_reader(self):
        p = self.svc_proc
        try:
            for line in p.stdout:
                self.log("[ComfyUI] " + line.rstrip())
        except Exception:
            pass
        rc = p.wait()
        if not self._closing:
            self.log("[服务] ComfyUI 进程退出，rc=%s" % rc)

    def _svc_wait_ready(self):
        for _ in range(100):                     # 最多 5 分钟
            if self._closing:
                return
            if self.comfy_alive():
                self.log("[服务] ✅ ComfyUI 已就绪：%s" % CONFIG["comfy_url"])
                webbrowser.open(CONFIG["comfy_url"])
                return
            if self.svc_proc and self.svc_proc.poll() is not None:
                return
            time.sleep(3)
        self.log("[服务] ⚠️ 5 分钟内未就绪，请看日志排查")

    def svc_stop(self):
        if self.comfy_alive() and self.svc_proc is None:
            self.log("[服务] ComfyUI 不是本程序拉起的，请在网页/原终端里关闭")
            return
        if self.svc_proc and self.svc_proc.poll() is None:
            self.log("[服务] 正在停止 ComfyUI…")
            try:
                self.svc_proc.terminate()
            except Exception as e:
                self.log("[服务] 停止失败：%r" % e)

    def open_web(self):
        if not self.comfy_alive():
            self.svc_start()
            return                                  # svc_start 就绪后会自动开网页
        webbrowser.open(CONFIG["comfy_url"])

    # ------------------------------------------------------------ 批量任务
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
        self.log("═" * 72)
        self.log("▶ %s" % title)
        self.log("  %s" % " ".join(cmd[4:]))
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
        self.var_task.set("运行中：" + title)
        for b in (self.btn_fv, self.btn_sv):
            b.configure(state="disabled")
        threading.Thread(target=self._task_reader, args=(title,), daemon=True).start()

    def _task_reader(self, title):
        p = self.task_proc
        t0 = time.time()
        try:
            for line in p.stdout:
                self.log("  " + line.rstrip())
        except Exception as e:
            self.log("[读取日志异常] %r" % e)
        rc = p.wait()
        el = time.time() - t0
        self.log("■ %s 结束：rc=%s，用时 %.0f 分 %.0f 秒"
                 % (title, rc, el // 60, el % 60))
        if rc == 0:
            self.log("  输出目录：%s" % CONFIG["output_dir"])
        self.task_proc = None
        self.task_name = ""
        self.var_task.set("空闲")
        for b in (self.btn_fv, self.btn_sv):
            b.configure(state="normal")

    def _get_input(self, var, name):
        p = var.get().strip().strip('"')
        if not p:
            messagebox.showwarning("缺输入", "请先选择 %s 的输入视频/文件夹" % name)
            return None
        if not os.path.exists(p):
            messagebox.showwarning("路径不存在", p)
            return None
        return p

    def run_flashvsr(self):
        p = self._get_input(self.fv_in, "FlashVSR")
        if not p:
            return
        args = ["-InputPath", p, "-Mode", self.fv_mode.get(), "-Scale", self.fv_scale.get(),
                "-Format", self.fv_fmt.get()]
        if self.fv_auto.get():
            args.append("-AutoStart")
        self._run_ps("FlashVSR 放大（%s / %sx）" % (self.fv_mode.get(), self.fv_scale.get()),
                     FLASHVSR_PS, args)

    def run_seedvr2(self):
        p = self._get_input(self.sv_in, "SeedVR2")
        if not p:
            return
        args = ["-InputPath", p, "-Profile", self.sv_prof.get(), "-Resolution", self.sv_res.get()]
        if self.sv_over.get():
            args.append("-Overwrite")
        self._run_ps("SeedVR2 放大（%s 档 / 短边 %s）" % (self.sv_prof.get(), self.sv_res.get()),
                     SEEDVR2_PS, args)

    # ------------------------------------------------------------ 后台探测
    def _probe_loop(self):
        if self._closing:
            return
        threading.Thread(target=self._probe_once, daemon=True).start()
        self.root.after(5000, self._probe_loop)

    def _probe_once(self):
        # 服务状态
        try:
            with NO_PROXY_OP.open(CONFIG["comfy_url"] + "/system_stats", timeout=3) as r:
                d = json.loads(r.read().decode("utf-8"))
            dev = (d.get("devices") or [{}])[0]
            used = (dev.get("vram_total", 0) - dev.get("vram_free", 0)) / 2 ** 30
            tot = dev.get("vram_total", 0) / 2 ** 30
            self.var_svc.set("● 运行中  v%s" % d.get("system", {}).get("comfyui_version", "?"))
            self.var_gpu.set("显存 %.1f / %.1f GiB" % (used, tot))
        except Exception:
            self.var_svc.set("○ 未运行")
            self._gpu_by_nvidia_smi()
        self.btn_stop.configure(state=("normal" if self.comfy_alive() or
                                       (self.svc_proc and self.svc_proc.poll() is None)
                                       else "disabled"))

    def _gpu_by_nvidia_smi(self):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=8).stdout
            u, t = out.strip().splitlines()[0].split(",")
            self.var_gpu.set("显存 %.1f / %.1f GiB" % (int(u) / 1024, int(t) / 1024))
        except Exception:
            pass

    # ------------------------------------------------------------ 退出
    def on_close(self):
        self._closing = True
        if self._task_busy():
            if not messagebox.askyesno("任务还在跑", "任务「%s」还没结束，确定退出吗？\n（退出会中断任务）" % self.task_name):
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
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    app.log("视频方案启动器已启动")
    app.log("  ComfyUI 目录 : %s" % CONFIG["comfy_dir"])
    app.log("  部署脚本目录 : %s" % CONFIG["deploy_root"])
    app.log("  ffmpeg       : %s %s" % (CONFIG["ffmpeg_dir"],
                                         "✅" if os.path.exists(os.path.join(CONFIG["ffmpeg_dir"], "ffmpeg.exe")) else "⚠️ 未找到"))
    root.mainloop()


if __name__ == "__main__":
    main()
