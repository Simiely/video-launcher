# -*- coding: utf-8 -*-
"""视频方案启动器 · 与界面无关的运行时支撑。

单独成模块的原因很具体：
  * AGENTS 的硬约定是「单文件逻辑行超过 1000 就拆」—— 主文件加上 v1.2.5 的
    崩溃落盘、上排高度自适配，以及 v1.2.6 的日志清洗后到了 1000 出头，必须拆；
  * 这里的五样东西（子进程"不弹黑窗"标记 / 崩溃落盘 / 绕代理取 JSON / 日志行解析 / 日志落盘）
    跟 tkinter 毫无关系：塞进 ui_kit 会污染"自绘控件库"的职责，
    留在主文件又挤占业务装配的位置。

依赖单向：本模块只用标准库，**不 import 业务代码，也不 import ui_kit**。
"""
import json
import os
import re
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


# ================================================================ 日志文本清洗
# ComfyUI 新版（app/logger.py 的 ColoredFormatter）会**无条件**给每条日志加 ANSI
# 颜色码 —— 不做 isatty 判断，所以即使 stdout 被重定向到管道也照样吐 ESC 序列：
#     ESC[1mESC[32m[INFO]ESC[0m Total VRAM 24564 MB
# Tk 的 Text 不解析转义序列，会把 ESC 显示成一个方块 —— 整行看着像乱码。
# 入日志区前统一剥掉（保留 [INFO]/[WARNING]/[ERROR] 这些纯文本级别标记，
# 主程序还会用它们给日志上色）。
# 实测依据：_audit/test_comfy_log.py（仿 ComfyUI 子进程 → 真实 svc_start 路径）。
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]"                    # CSI：颜色 / 光标
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"       # OSC：标题 / 超链接
    r"|\x1b[@-Z\\-_]")                          # 两字节转义


def strip_ansi(s):
    """剥掉 ANSI 转义序列；没有 ESC 的普通行原样返回（不复制、不改变内容）。"""
    return _ANSI_RE.sub("", s) if "\x1b" in s else s


# ---------------------------------------------------------------- 日志行解析
PERCENT_RE = re.compile(r"(?<!\d)(\d{1,3})\s*%")
# 分数：前面不能是数字或小数点 —— 否则会把 "0.0001/0.0002" 这种小数当进度
FRACTION_RE = re.compile(r"(?<![\d.])(\d+)\s*/\s*(\d+)")


def progress_pct(line):
    """从一行日志里解析进度百分比；解析不到返回 None。

    优先用百分比：`x/y` 在非进度语境里到处都是 —— "宽高比 16/9"、"精度 0.0001/0.0002"、
    "显存 7.6/12.0G"，拿它当进度会让进度条乱跳（实测 16/9 把标签写成 177%）。
    """
    m = PERCENT_RE.search(line)
    if m:
        return min(100, int(m.group(1)))
    m = FRACTION_RE.search(line)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if b and a <= b:                     # a > b 不是进度（"16/9"）
            return int(a * 100.0 / b)
    return None


# ComfyUI 的日志带 [INFO]/[WARNING]/[ERROR] 级别标记（剥掉 ANSI 颜色后纯文本还在）。
# 英文日志里没有"失败/错误"这类中文关键词 —— 级别标记才是可靠信号。
_LEVEL_RE = re.compile(r"\[(CRITICAL|ERROR|WARNING|WARN|DETAIL|DEBUG|INFO)\]")
_LEVEL_KIND = {"CRITICAL": "error", "ERROR": "error",
               "WARNING": "warning", "WARN": "warning",
               "DETAIL": "detail", "DEBUG": "detail", "INFO": "info"}


def log_level(line):
    """识别日志行里的级别标记，返回语义级别；没有标记返回 None。

    只返回语义（error/warning/info/detail），具体涂什么色由界面层决定。
    """
    m = _LEVEL_RE.search(line)
    return _LEVEL_KIND[m.group(1)] if m else None


# 日志行 → 着色标签名。标签名是界面层与这里约定的词汇（err/warn/ok/lw/dim），
# 具体配色由界面层的 tag_config 决定 —— 这里只负责"这行属于哪一类"。
_LEVEL_TAGS = {"error": "err", "warning": "warn", "info": "dim", "detail": "dim"}
_WARN_WORDS = ("⚠️", "失败", "错误", "Traceback", "未就绪", "解析失败")
_OK_WORDS = ("✅", "已就绪", "完成", "结束：rc=0", "rc=0")


def tag_for(line):
    """给一行日志选着色标签。

    先看级别标记 —— 英文日志（ComfyUI）里没有"失败/错误"这类中文关键词，
    [ERROR]/[WARNING] 才是可靠信号；以前这类行会被 [ComfyUI] 前缀涂成灰色。
    """
    tag = _LEVEL_TAGS.get(log_level(line))
    if tag:
        return tag
    if any(k in line for k in _WARN_WORDS):
        return "warn"
    if any(k in line for k in _OK_WORDS):
        return "ok"
    if line.startswith("[工作流]") or line.startswith("[队列]"):
        return "lw"
    return "dim" if line.startswith("[ComfyUI]") else None


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


# ================================================================ 日志落盘
# 界面上的日志区只活在内存里：关窗即失，且超过 MAX_LOG_LINES 会裁掉**最旧的**行 ——
# 而被裁掉的恰恰是 ComfyUI 启动参数、模型加载、自定义节点失败这类最该留下的信息。
# 所以加一条落盘旁路：界面显示照旧，同时把每一行写进 logs/ 下的文件。
# 开关关闭时用 NoopLog 占位，日志漏斗处因此不必写 if 判断。
LOG_FLUSH_EVERY = 1.0      # 秒：定时 flush 间隔。崩溃时最多丢这一段时间的日志，
                           # 但绝不会出现"文件里一个字都没有"（首行是立即落盘的）


class LogFile:
    """把日志行同时写进磁盘文件；构造失败自动降级（degraded 非空）。

    刻意约定：调用方传进来的是**已经 strip_ansi 的界面行**，
    所以文件内容与界面显示逐行一致 —— 出问题时两边能直接对照。
    """

    def __init__(self, log_dir, prefix="运行"):
        """建目录并按时间戳开新文件；任何失败都记进 degraded 并降级为不落盘。"""
        self.fh = None
        self.path = ""
        self.lines = 0
        self.degraded = ""              # 非空 = 已降级，值为原因
        self._last_flush = 0.0
        try:
            os.makedirs(log_dir, exist_ok=True)
            name = "%s-%s.log" % (prefix, time.strftime("%Y-%m-%d_%H%M%S"))
            self.path = os.path.join(log_dir, name)
            # 固定 newline="\n"：避免 Windows 下写成 \r\n 影响逐行核对
            self.fh = open(self.path, "w", encoding="utf-8",
                           errors="replace", newline="\n")
        except Exception as e:
            self.degraded = "%s: %s" % (type(e).__name__, e)

    def write(self, line):
        if self.fh is None:
            return
        try:
            self.fh.write(line + "\n")
            self.lines += 1
            now = time.time()
            if now - self._last_flush >= LOG_FLUSH_EVERY:
                self.fh.flush()
                self._last_flush = now
        except Exception as e:          # 磁盘满 / 网盘掉线：降级即可，界面不能受影响
            self.degraded = "%s: %s" % (type(e).__name__, e)
            self._close_quiet()

    def close(self):
        """flush 后关闭。退出路径上 flush 失败只记原因，不作补救。"""
        try:
            if self.fh:
                self.fh.flush()
        except Exception as e:
            self.degraded = self.degraded or ("close: %s" % e)
        finally:
            self._close_quiet()

    def _close_quiet(self):
        fh, self.fh = self.fh, None
        if fh is None:
            return
        try:
            fh.close()
        except Exception:
            return          # 句柄已失效或已关闭：关闭失败无需上报


class NoopLog:
    """开关关闭时的占位对象：让日志漏斗不必写 if 判断。"""

    path = ""
    degraded = ""
    lines = 0

    def write(self, line):
        """开关关闭：不落盘。"""

    def close(self):
        """开关关闭：无句柄需关。"""
