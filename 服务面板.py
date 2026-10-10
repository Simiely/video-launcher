# -*- coding: utf-8 -*-
"""服务面板 —— App 的服务控制与批量任务 mixin（v1.2.14 自主文件拆出）。

主文件逻辑行逼近千行红线，先把这块"只跟服务/子进程打交道、几乎不碰布局"的
方法整体搬过来，腾出余量。依赖方向不变：主程序 → 服务面板 → ui_kit / 运行时，
本模块不 import 业务代码、不建任何控件，只通过 self.* 使用 App 已建好的东西。

配置的接法：本模块的 CONFIG 与主程序是**同一个字典**——主程序启动时执行
`服务面板.CONFIG = CONFIG` 注入引用。之后 `CONFIG.update(覆盖项)` 是原地合并，
两边看到的内容永远一致（主文件从不重新绑定 CONFIG，见 v1.2.10 的教训）。

注意：self.log / self.btn_primary / self._probe_state / self.uiqueue 等
都由主文件的 App 提供，本类只作为 App 的基类参与拼装 —— pylint 看不到这层
关系会对 no-member 误报，故在类级关闭该检查（真实成员在 App.__init__ 定义）。
"""
import os
import subprocess
import threading
import time
import webbrowser

from tkinter import messagebox

import 运行时 as rt
from 运行时 import NO_WINDOW, ps_quote

# 主程序启动时注入同一字典（见模块 docstring）；这里给空壳只为 import 时不炸
CONFIG = {}


class 服务面板:
    # pylint: disable=no-member  # mixin 约定：宿主 App 提供界面成员，见模块 docstring
    svc_proc = None   # 类级默认：真实赋值在宿主 App.__init__（消 E0203 先读后定义误报）
    # ================================================================ 服务
    def _comfy_env(self):
        return rt.comfy_env(CONFIG)      # 拼装在 运行时.py：ffmpeg 前置 PATH + UTF-8 文本 IO

    def comfy_alive(self):
        """本机 ComfyUI 是否在跑（绕开系统代理，见 运行时.http_json）。"""
        return rt.http_json(CONFIG["comfy_url"] + "/system_stats") is not None

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
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=NO_WINDOW)
        except Exception as e:
            self._svc_starting = False
            self.btn_primary.set_state("normal")
            messagebox.showerror("启动失败", repr(e))
            return
        threading.Thread(target=self._svc_reader, daemon=True).start()
        threading.Thread(target=self._svc_wait_ready, daemon=True).start()

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
            pass          # 管道被断（进程被杀 / 句柄失效）：下面 p.wait() 会把 rc 报出来，这里不必再吵
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
        own = self.svc_proc is not None and self.svc_proc.poll() is None
        if own:
            self.log("[服务] 正在停止 ComfyUI…", "hi")
            try:
                self.svc_proc.terminate()
            except Exception as e:
                self.log("[服务] 停止失败：%r" % e, "err")
            return
        if not self.comfy_alive():
            self.log("[服务] ComfyUI 没在运行")
            return
        # 不是自己拉起的也照样能停（v1.2.13）：按命令行找到它的进程再结束。
        # PowerShell 查询要 1~3 秒，进后台线程，别把界面卡住。
        self.log("[服务] 服务不是本程序拉起的，正在定位它的进程…", "hi")
        threading.Thread(target=lambda: rt.stop_external(CONFIG, self.log),
                         daemon=True).start()

    def svc_restart(self):
        if self._svc_starting:
            self.log("[服务] 正在启动中，稍候再重启")
            return
        own = self.svc_proc is not None and self.svc_proc.poll() is None
        if own:
            self.log("[服务] 重启中：先停后起…", "hi")
            threading.Thread(target=self._restart_worker, daemon=True).start()
            return
        if not self.comfy_alive():
            self.log("[服务] ComfyUI 没在运行，直接启动…", "dim")
            self.svc_start()
            return
        self.log("[服务] 服务不是本程序拉起的，先停外部进程再启动…", "hi")
        threading.Thread(target=self._restart_ext_worker, daemon=True).start()

    def _restart_ext_worker(self):
        """外部拉起的服务也能重启（v1.2.13）：结束它的进程，等退出后走正常启动。"""
        rt.stop_external(CONFIG, self.log)
        for _ in range(40):                       # 最多等 20 秒，等外部服务真退出
            if self._closing:
                return
            if not self.comfy_alive():
                break
            time.sleep(0.5)
        if not self._closing:
            self.uiqueue.put(self.svc_start)

    def svc_stop_queue(self):
        """停掉 ComfyUI 里正在跑的 + 排队中的任务。

        两个接口各管一半（ComfyUI 官方语义）：/interrupt 打断当前执行、
        /queue{"clear":true} 清掉还没轮到的。先读一次 /queue 是为了把"停掉几个"
        说清楚 —— 队列本来就空时直接报"已停止"会让人以为程序在自说自话。

        与「停止服务」的区别：这个只走 HTTP，不碰进程；「停止服务」是结束
        ComfyUI 进程本身（自己拉起的走句柄、外部的按命令行找，v1.2.13 起都能停）。
        """
        if not self.comfy_alive():
            self.log("[队列] ComfyUI 没在运行，没有可停的任务", "warn")
            return
        q = rt.http_json(CONFIG["comfy_url"] + "/queue") or {}
        running = len(q.get("queue_running") or [])
        pending = len(q.get("queue_pending") or [])
        if not running and not pending:
            self.log("[队列] 当前没有正在跑或排队中的任务", "warn")
            return
        err = rt.http_post_json(CONFIG["comfy_url"] + "/interrupt", {})
        if err:
            self.log("[队列] 中断当前任务失败：%s" % err, "err")
            return
        err = rt.http_post_json(CONFIG["comfy_url"] + "/queue", {"clear": True})
        if err:
            self.log("[队列] 已中断当前任务，但清空排队失败：%s" % err, "err")
            return
        self.log("[队列] 已停止 %d 个执行中 + %d 个排队中的任务" % (running, pending), "ok")

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
                creationflags=NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
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

