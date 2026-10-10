# -*- coding: utf-8 -*-
"""视频方案启动器 · 与界面无关的运行时支撑。

单独成模块的原因很具体：
  * AGENTS 的硬约定是「单文件逻辑行超过 1000 就拆」—— 主文件加上 v1.2.5 的
    崩溃落盘与上排高度自适配后到了 1000 出头，必须拆；
  * 这里的三样东西（子进程"不弹黑窗"标记 / 崩溃落盘 / 绕代理取 JSON）跟 tkinter
    毫无关系：塞进 ui_kit 会污染"自绘控件库"的职责，留在主文件又挤占业务装配的位置。

依赖单向：本模块只用标准库，**不 import 业务代码，也不 import ui_kit**。
"""
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.request

# ================================================================ 子进程：别弹黑窗
# 关键：本程序打包成 exe 时是「无控制台窗口」（console=False）的 GUI 程序。
# 这种程序里 spawn 控制台子进程（powershell / nvidia-smi / ComfyUI 的 python）时，
# Windows 默认会**给子进程新开一个控制台窗口**，表现为每次调用都"闪一下黑窗"。
# 探测线程每 5 秒跑一次 nvidia-smi —— 桌面于是就不停闪窗。
# （v1.2.5 实测复现：不加标记时 3/3 都弹出可见控制台窗口，加上后 0/3。）
# 所有子进程一律带上它。
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def ps_quote(s):
    """PowerShell 单引号字面量转义。"""
    return "'" + str(s).replace("'", "''") + "'"


# ================================================================ 本机 HTTP（绕代理）
# Clash 之类会把 127.0.0.1 也代理掉，探测本机 ComfyUI 会拿到 502；
# 所以必须用空 ProxyHandler 的 opener，**不能用 urllib.request.urlopen**。
NO_PROXY_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(url, timeout=3):
    """取 JSON 并解析；任何失败都返回 None，调用方据此判定"没起来"。"""
    try:
        with NO_PROXY_OP.open(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def http_post_json(url, obj, timeout=10):
    """POST 一个 JSON；成功返回 None，失败返回错误字符串（便于日志给出原因）。

    同样走 NO_PROXY_OP —— 否则 127.0.0.1 会被 Clash 代理掉、拿到 502。
    """
    try:
        req = urllib.request.Request(
            url, data=json.dumps(obj).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with NO_PROXY_OP.open(req, timeout=timeout) as resp:
            resp.read()
        return None
    except Exception as e:
        return repr(e)


# ================================================================ 崩溃落盘
CRASH_LOG_NAME = "崩溃日志.log"


def write_crash(path, kind, exc_type, exc, tb):
    """把未捕获异常追加到崩溃日志（GUI 程序没有 stderr，只能落盘）。"""
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n" + "=" * 70 + "\n")
            fh.write("[%s] %s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                        kind, exc_type.__name__))
            fh.write("".join(traceback.format_exception(exc_type, exc, tb)))
    except Exception:
        pass          # 连日志都写不了就只能放弃，绝不能让异常处理器自己再抛


def install_crash_log(app_dir):
    """装上「主线程 / 后台线程」异常兜底，返回崩溃日志路径。

    打包成 exe 后是 GUI 程序，traceback 没有去处 —— 窗口一闪就关、什么也看不到。
    落盘之后，"闪退"才可能被事后排查。
    """
    path = os.path.join(app_dir, CRASH_LOG_NAME)

    def hook(exc_type, exc, tb):
        write_crash(path, "主线程", exc_type, exc, tb)

    sys.excepthook = hook
    if hasattr(threading, "excepthook"):                  # 后台线程里抛的异常
        threading.excepthook = lambda a: write_crash(
            path, "线程 %s" % (a.thread.name if a.thread else "?"),
            a.exc_type, a.exc_value, a.exc_traceback)
    return path


def watch_tk_errors(root, path):
    """把 Tk 回调里的异常也记下来。

    Tkinter 默认只把回调异常打到 stderr —— GUI 程序里 stderr 是 None，等于丢掉。
    必须在建好 root 之后调用。
    """
    type(root).report_callback_exception = staticmethod(
        lambda exc, val, tb: write_crash(path, "Tk 回调", type(val), val, tb))
