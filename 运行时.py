# -*- coding: utf-8 -*-
"""视频方案启动器 · 与界面无关的运行时支撑。

单独成模块的原因很具体：
  * AGENTS 的硬约定是「单文件逻辑行超过 1000 就拆」—— 主文件加上 v1.2.5 的
    崩溃落盘、上排高度自适配，以及 v1.2.6 的日志清洗后到了 1000 出头，必须拆；
  * 这里的六样东西（子进程"不弹黑窗"标记 / 崩溃落盘 / 绕代理取 JSON / 日志行解析 /
    日志落盘 / 启动自检）跟 tkinter 毫无关系：塞进 ui_kit 会污染"自绘控件库"的职责，
    留在主文件又挤占业务装配的位置。

依赖单向：本模块只用标准库，**不 import 业务代码，也不 import ui_kit**。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser

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


# ================================================================ 启动自检：真实检测
# 把路径"打印"出来不等于"检查过"：路径拼错了、盘符变了、目录建了但里面是空的 ——
# 打印出来都一样好看。所以这里逐项**真的去查**，而且查到"能力级"而不是"目录级"：
# 目录在、但 ComfyUI 没克隆完（缺 main.py）、部署脚本没铺进去（缺引擎子目录），
# 是最常见的半成品状态；只看目录在不在，就会给出假绿灯。
# 自检表：配置键 → 日志里的名字 → 必须存在的子项 → PATH 上可替代的可执行名（空＝无）
STARTUP_PROBES = (
    ("comfy_dir", "ComfyUI 目录", ("main.py",), ""),
    ("comfy_py", "ComfyUI Python", (), ""),
    ("deploy_root", "部署脚本目录", ("02-FlashVSR", "03-SeedVR2"), ""),
    ("ffmpeg_dir", "ffmpeg", ("ffmpeg.exe",), "ffmpeg"),
    ("input_dir", "输入目录", (), ""),
    ("output_dir", "输出目录", (), ""),
)
_PROBE_BY_KEY = {p[0]: p for p in STARTUP_PROBES}


def _alt_hint(spec):
    """本目录里缺东西时，提一句系统 PATH 上是否另有一份（好照着拷过去）。"""
    found = shutil.which(spec[3]) if spec[3] else None
    return "（系统 PATH 上有：%s）" % found if found else ""


def probe_path(cfg, key):
    """真实检测配置里的某一项，返回 (路径, 是否就绪, 缺什么)。

    "缺什么"为空串表示就绪，否则是一句能照着做的话（"路径不存在" / "缺 main.py"）。
    不在自检表里的键（如服务地址）无从检测，一律按就绪处理。
    """
    path = str(cfg.get(key, "") or "")
    spec = _PROBE_BY_KEY.get(key)
    if spec is None:
        return path, True, ""
    if not path:
        return path, False, "未配置"
    if not os.path.exists(path):
        return path, False, "路径不存在" + _alt_hint(spec)
    for need in spec[2]:
        if not os.path.exists(os.path.join(path, need)):
            return path, False, "缺 " + need + _alt_hint(spec)
    return path, True, ""


def probe_line(cfg, key):
    """单项自检，返回 (文本, 着色标签) —— 与启动日志同一格式，改完设置当场复用它。"""
    spec = _PROBE_BY_KEY.get(key)
    label = spec[1] if spec else key
    path, ok, miss = probe_path(cfg, key)
    return ("  %s : %s %s" % (label, path, "✅" if ok else "⚠️ " + miss),
            "ok" if ok else "warn")


def startup_lines(cfg):
    """逐项真实检测，返回可直接写进日志的 [(文本, 着色标签)]。"""
    return [probe_line(cfg, key) for key, _label, _needs, _exe in STARTUP_PROBES]


# ================================================================ 设置项：展示 / 修改
# 设置区既要"看得见"（打开）也要"改得动"（修改），这两件事的**数据与语义**都在这里，
# 界面层只负责画。分开的原因：这些都是路径 / JSON / 进程环境逻辑，跟 tkinter 无关。
# 每项 = (配置键, 界面上的名字, 类型)。类型决定「浏览…」弹什么、以及怎么"打开"：
#   dir  → 选目录 / 资源管理器打开目录
#   file → 选文件 / 资源管理器打开并选中
#   url  → 纯文本     / 浏览器打开
CONFIG_FIELDS = (
    ("comfy_dir",   "ComfyUI 目录",    "dir"),
    ("comfy_py",    "ComfyUI Python",  "file"),
    ("deploy_root", "部署脚本目录",     "dir"),
    ("ffmpeg_dir",  "ffmpeg 目录",      "dir"),
    ("input_dir",   "输入目录",         "dir"),
    ("output_dir",  "输出目录",         "dir"),
    ("comfy_url",   "服务地址",         "url"),
)


def field_label(key):
    """配置键 → 界面上显示的名字（不认识就原样返回键名）。"""
    return next((lab for k, lab, _t in CONFIG_FIELDS if k == key), key)


def field_kind(key):
    """配置键 → 类型（dir / file / url）；不在表里的按目录处理。"""
    return next((t for k, _lab, t in CONFIG_FIELDS if k == key), "dir")


def config_items(cfg):
    """设置面板要的数据：路径、显示名、类型、实测结论（note 空串＝就绪）。

    实测结论直接来自 probe_path —— 设置面板与启动日志因此共用同一份判断，
    不存在"日志说缺失、面板说就绪"这种两边不一致。
    """
    out = []
    for key, label, kind in CONFIG_FIELDS:
        path, ok, miss = probe_path(cfg, key)
        out.append({"key": key, "label": label, "value": path, "kind": kind,
                    "note": "" if ok else miss})
    return out


def save_config(path, updates):
    """把若干配置键合并写回 JSON；成功返回 None，失败返回原因串。

    **合并而非覆盖**：文件里原有的其它键（`_说明`、用户自己加的备注）都保留 ——
    启动器只该改用户点过的那几项，不该把「只保留需要改的键」的配置文件越写越满。
    """
    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                data = {}
        except Exception as e:
            return "读取现有配置失败：%s" % e
    data.update(updates)
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except Exception as e:
        return "写入配置失败：%s" % e
    return None


def open_path(kind, path):
    """按类型打开：url→浏览器、file→选中该文件、dir→打开目录。返回 False＝路径不存在。"""
    path = str(path or "")
    if not path:
        return False
    if kind == "url":
        webbrowser.open(path)
        return True
    if kind == "file" and os.path.isfile(path):
        # /select, 必须与路径分成两个参数；且 explorer 只认反斜杠路径
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)],
                         creationflags=NO_WINDOW)
        return True
    if os.path.isdir(path):
        os.startfile(path)
        return True
    return False


def comfy_env(cfg, base=None):
    """子进程环境：ffmpeg 目录前置到 PATH（不依赖注册表），并统一 UTF-8 文本 IO。"""
    env = dict(os.environ if base is None else base)
    ff = str(cfg.get("ffmpeg_dir", "") or "")
    if ff:
        env["PATH"] = ff + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def gpu_by_nvidia_smi():
    """ComfyUI 没起来时退而求其次，用 nvidia-smi 读显存。

    返回 (文本, 本机是否有 nvidia-smi)：可用=False 时调用方应停止每 5 秒再试 ——
    反复 spawn 子进程既浪费，也是杀软启发式扫描爱盯的行为。显存只是锦上添花，
    一切失败都静默降级（返回空文本）。
    """
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8, creationflags=NO_WINDOW)
    except FileNotFoundError:
        return "", False
    except Exception:          # 超时 / 被杀：降级即可，不该影响探测循环
        return "", True
    if r.returncode != 0:      # 驱动异常时 stdout 是空的；显式判掉，不靠 IndexError 兜底
        return "", True
    try:
        used, total = r.stdout.strip().splitlines()[0].split(",")
        return "显存 %.1f/%.1fG" % (int(used) / 1024, int(total) / 1024), True
    except (IndexError, ValueError):     # 输出格式变了（换驱动/多卡）：当次拿不到而已
        return "", True


def find_comfy_pids(comfy_dir, comfy_py=""):
    """按命令行找正在跑的 ComfyUI 进程 —— 外部启动的也算（v1.2.13）。

    v1.2.13 前「停止服务 / 重启」只认本程序自己拉起的进程（手里有 Popen 句柄才敢
    动手），外部启动的一律拒绝。但 exe 更新重开是常态：旧服务还在跑、新 exe 天生
    不认它，用户就卡在"请手动关闭"上。这里用 PowerShell 问一遍 Win32_Process：
    python 进程的命令行或可执行文件路径里带着配置的 comfy 目录 / comfy Python
    的，就是要找的。wmic 在新 Windows 上已弃用，不押它。

    返回 [(pid, 命令行)]；**查询失败返回 None**（区别于"没找到"——调用方话术不同）。
    一次查询约 1~3 秒：只放在用户点击动作里、后台线程调用，别挂到周期探测上。
    """
    keys = []
    for p in (comfy_dir, comfy_py):
        p = os.path.normpath(str(p or "")).lower().replace("'", "''")
        if p and p not in keys:
            keys.append(p)
    if not keys:
        return []
    cond = " -or ".join("$c.Contains('%s') -or ($e -and $e.Contains('%s'))" % (k, k)
                        for k in keys)
    # 注意别用 % 格式化拼整段脚本：'Name like '%python%'' 里的 %p / %' 会被当成格式符
    ps = ("Get-CimInstance Win32_Process -Filter \"Name like '@PY@'\" | "
          "ForEach-Object { if ($_.CommandLine) { "
          "$c = $_.CommandLine.ToLower(); $e = $_.ExecutablePath; "
          "if ($e) { $e = $e.ToLower() } "
          "if (__COND__) { \"$($_.ProcessId)`t$($_.CommandLine)\" } } }"
          ).replace("@PY@", "%python%").replace("__COND__", cond)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=30, creationflags=NO_WINDOW)
    except Exception:          # 超时 / powershell 不可用：让调用方走"查询失败"话术
        return None
    out = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        pid, _, cmd = line.partition("\t")
        if pid.isdigit() and cmd:
            out.append((int(pid), cmd.strip()))
    return out


def find_main_py_pids():
    """候选进程：命令行里有 main.py 的 python 进程（v1.2.13）。

    ComfyUI 用"在某目录里 python -u main.py"方式启动时，命令行里看不到那个目录
    （工作目录不进 Win32_Process.CommandLine），find_comfy_pids 会漏掉它 —— 实测
    本机就有一个 uv python 起的 ComfyUI 落在这。这里把它列为候选交给上层决定，
    **只列不杀**：别的程序也可能叫 main.py，命令行又看不出工作目录，自动杀会误伤。
    查询失败返回 None。
    """
    ps = ("Get-CimInstance Win32_Process -Filter \"Name like '@PY@'\" | "
          "ForEach-Object { if ($_.CommandLine -and "
          "$_.CommandLine.ToLower().Contains('main.py')) { "
          "\"$($_.ProcessId)`t$($_.CommandLine)\" } }").replace("@PY@", "%python%")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=30, creationflags=NO_WINDOW)
    except Exception:
        return None
    out = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        pid, _, cmd = line.partition("\t")
        if pid.isdigit() and cmd:
            out.append((int(pid), cmd.strip()))
    return out


def kill_pid(pid):
    """强杀一个进程树。外部服务手里没有句柄、也没法优雅关，直接 taskkill /T /F。"""
    r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=NO_WINDOW)
    return r.returncode == 0


def short_cmd(cmd, limit=70):
    """日志里展示命令行用的截断（全路径太长会把日志行顶乱）。"""
    cmd = str(cmd or "").strip()
    return cmd if len(cmd) <= limit else cmd[:limit - 1] + "…"


def stop_external(cfg, log):
    """结束外部拉起的 ComfyUI 进程。log(文本, 级别) 由调用方给（运行时不碰界面）。

    返回 True＝已结束进程；False＝没找到（服务可能在响应但进程没带配置目录，
    不算失败）；None＝查询失败。三种情况话术都写在 log 里，调用方不用再猜。
    """
    pids = find_comfy_pids(cfg.get("comfy_dir"), cfg.get("comfy_py"))
    if pids is None:
        log("[服务] 进程查询失败，请手动关闭 ComfyUI", "err")
        return None
    if not pids:
        cand = find_main_py_pids() or []
        if cand:
            log("[服务] 服务在响应，但它的命令行里没有配置的目录；"
                "找到 %d 个带 main.py 的疑似进程：" % len(cand), "warn")
            for pid, cmd in cand:
                log("[服务] 疑似进程 %d：%s" % (pid, short_cmd(cmd)), "dim")
            log("[服务] 确认是 ComfyUI 的话请手动结束，"
                "或把它的所在目录填进设置里的「ComfyUI 目录」再试", "warn")
            return False
        log("[服务] 服务在响应，但没找到对应的进程；请在原终端或网页里关闭", "warn")
        return False
    done = 0
    for pid, cmd in pids:
        log("[服务] 结束进程 %d：%s" % (pid, short_cmd(cmd)), "dim")
        done += 1 if kill_pid(pid) else 0
    if done == len(pids):
        log("[服务] 已结束 %d 个 ComfyUI 进程" % done, "ok")
    else:
        log("[服务] 结束了 %d/%d 个进程，残余的请手动处理" % (done, len(pids)), "warn")
    return True


def check_input(path, name="输入"):
    """批量任务的输入校验：返回 (路径, 错误文案)。错误文案为空串表示可用。"""
    p = str(path or "").strip().strip('"')
    if not p:
        return "", "请先选择 %s 的输入视频 / 文件夹" % name
    if not os.path.exists(p):
        return p, "路径不存在：%s" % p
    return p, ""


# ================================================================ 工作流 API 图
# 内置方案在界面上只是一份"节点说明"（steps / inN / outN），没有能提交给 ComfyUI 的图 ——
# 所以选中它点推送，老版本只能回一句「未含 API 图，请先加载 .json」。
# 这里把**确实有图的那几个方案**跟部署脚本目录里的图绑起来（相对 deploy_root 的路径），
# 并顺手把图里的素材换成界面上选的那份、按方案覆盖几个关键参数。
#
# 为什么必须"有声 / 无音轨"二选一：VHS_LoadVideoPath 的 audio 输出是**惰性取流**，
# 只有被下游引用时才去抽音轨（videohelpersuite/utils.py 的 get_audio）。源文件没有
# 音轨流时 ffmpeg 直接抛 "Output file does not contain any stream"，VHS 不做降级 ——
# 任务在**第一个节点**就 error，报错信息还很晦涩。依据：部署仓库 workflows/README.md。
#
# 载入节点的类名 / 字段名是 ComfyUI 生态的既有约定，不是本项目自造：
#   VHS_LoadVideoPath.video → 素材的**完整路径**
#   LoadVideo.file         → ComfyUI input 目录下的**文件名**（语义不同，别混用，
#                            所以这里只收"吃完整路径"的那两类；详见 DEVELOPMENT）
API_INPUTS = (("VHS_LoadVideoPath", "video"), ("LoadVideoPath", "video"))
_API_INPUT_CLS = dict(API_INPUTS)


def api_dir(cfg):
    """部署脚本目录下的图库（没绑图的方案用它指路）。"""
    return os.path.normpath(
        os.path.join(str((cfg or {}).get("deploy_root", "") or ""), "workflows"))


def ffprobe_exe(cfg):
    """找 ffprobe：先看配置里的 ffmpeg 目录，再退回系统 PATH。"""
    p = os.path.join(str((cfg or {}).get("ffmpeg_dir", "") or ""), "ffprobe.exe")
    return p if os.path.exists(p) else shutil.which("ffprobe")


def probe_audio(cfg, video):
    """素材有没有音轨，返回 (是否有音轨, 依据文本)。

    判不出来一律按"有音轨"处理 —— 走有声版只是多抽一次音轨；反过来若源真有音轨却按
    无音轨版推，产物会**静默丢掉原声**，那是用户更难发现的错。
    """
    exe = ffprobe_exe(cfg)
    if not exe:
        return True, "没找到 ffprobe"
    if not os.path.isfile(video):
        return True, "素材不是单个文件（批量场景无从判断）"
    try:
        r = subprocess.run(
            [exe, "-v", "error", "-select_streams", "a", "-show_entries",
             "stream=codec_type", "-of", "csv=p=0", video],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace", timeout=30, check=False, creationflags=NO_WINDOW)
    except Exception as e:
        return True, "ffprobe 调用失败：%r" % e
    if r.returncode != 0:                   # 读不了这份素材（损坏/不是媒体）：不敢下结论
        return True, "ffprobe 返回 %d" % r.returncode
    has = "audio" in (r.stdout or "")
    return has, "ffprobe 判定%s音轨" % ("" if has else "无")


def pick_api(cfg, wf, video):
    """给内置方案挑一份 API 图，返回 (图的完整路径 or None, 说明文本)。

    wf["api"] 可以是单个相对路径；也可以是 {"audio": …, "silent": …} —— 按素材有无
    音轨二选一。挑不到时说明文本是一句能照着做的话（指出图库在哪）。
    """
    root = str((cfg or {}).get("deploy_root", "") or "")
    src = (wf or {}).get("api")
    if not src:
        return None, "该方案没有内置 API 图；可在 %s 里挑一份点「加载 .json」" % api_dir(cfg)
    if isinstance(src, str):
        return os.path.join(root, src), ""
    has, why = probe_audio(cfg, video)
    rel = src.get("audio" if has else "silent")
    return (os.path.join(root, rel) if rel else None,
            "%s → 用「%s」版" % (why, "有声" if has else "无音轨"))


def load_api(path):
    """读一份 API 图（每节点都带 class_type 的扁平表）。返回 (数据, 错误串)。"""
    if not path:
        return None, "没有指定 API 图"
    if not os.path.exists(path):
        return None, "API 图不存在：%s" % path
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:
        return None, "API 图解析失败：%r" % e
    if not isinstance(data, dict) or not data:
        return None, "API 图内容不是节点表（应为 {节点id: {class_type, inputs}}）"
    return data, ""


def read_workflow(path):
    """读一份工作流 json —— UI 格式 / API 格式都收（是哪种交给 is_ui_graph 判）。

    load_api 收紧了校验（只认 API 扁平表），这里保持宽松：ComfyUI 网页里存的
    工作流都是 UI 格式，读进来先给下拉和详情用，推送前才转换。返回 (数据, 错误串)。
    """
    if not path or not os.path.exists(path):
        return None, "工作流文件不存在：%s" % path
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:
        return None, "工作流解析失败：%r" % e
    if not isinstance(data, dict) or not data:
        return None, "工作流内容不是 JSON 对象"
    return data, ""


def apply_input(api, video):
    """把图里"载入视频"节点的素材换成 video。返回改动的节点数（0 = 图里没有这类节点）。"""
    v = str(video or "").strip().strip('"')
    if not v:
        return 0
    n = 0
    for node in api.values():
        if isinstance(node, dict) and node.get("class_type") in _API_INPUT_CLS:
            node.setdefault("inputs", {})[_API_INPUT_CLS[node["class_type"]]] = v
            n += 1
    return n


def extract_first_frame(cfg, video):
    """用 ffmpeg 抽视频首帧，存进 ComfyUI 的 input 目录（LoadImage 的取图语义）。

    返回 (input 目录下的文件名, 错误串)。文件名固定，重复推送直接覆盖。
    """
    exe = os.path.join(str((cfg or {}).get("ffmpeg_dir", "") or ""), "ffmpeg.exe")
    indir = os.path.join(str((cfg or {}).get("comfy_dir", "") or ""), "input")
    if not os.path.isfile(str(video or "")):
        return None, "素材不存在：%s" % video
    if not os.path.isfile(exe):
        return None, "找不到 ffmpeg.exe（%s）" % exe
    try:
        os.makedirs(indir, exist_ok=True)
    except Exception as e:
        return None, "建不了 ComfyUI input 目录：%r" % e
    name = "launcher_first_frame.png"
    out = os.path.join(indir, name)
    try:
        r = subprocess.run(
            [exe, "-y", "-i", str(video), "-frames:v", "1", out],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", timeout=60, check=False,
            creationflags=NO_WINDOW)
    except Exception as e:
        return None, "ffmpeg 调用失败：%r" % e
    if r.returncode != 0 or not os.path.isfile(out):
        return None, "抽首帧失败（rc=%d）：%s" % (r.returncode, (r.stderr or "")[-160:])
    return name, ""


def apply_input_image(api, video, cfg):
    """把图里 LoadImage 节点的图换成 video 的首帧。返回 (改动节点数, 提醒串)。

    MiniMax I2V 这类方案吃的是图片（input 目录语义），启动器的输入却是视频 ——
    约定：取所选视频的第一帧当首帧图。图里没有 LoadImage 时原样返回 (0, "")。
    """
    if not any(isinstance(nd, dict) and nd.get("class_type") == "LoadImage"
               for nd in api.values()):
        return 0, ""
    v = str(video or "").strip().strip('"')
    if not v:
        return 0, "图生视频需要先选素材（要取它的首帧当首帧图）"
    name, err = extract_first_frame(cfg, v)
    if err:
        return 0, err
    for nd in api.values():
        if isinstance(nd, dict) and nd.get("class_type") == "LoadImage":
            nd.setdefault("inputs", {})["image"] = name
    return 1, ""


def apply_patch(api, patch):
    """按节点类名覆盖参数（如 FlashVSRNode 的 scale）。返回改动的节点数。"""
    patch = patch or {}
    n = 0
    for node in api.values():
        over = patch.get(node.get("class_type")) if isinstance(node, dict) else None
        if over:
            node.setdefault("inputs", {}).update(over)
            n += 1
    return n


def api_node_count(api):
    """数一份图里的节点数。

    两种写法都要认：API 格式（{节点id: {class_type…}}，节点数＝顶层键数）以及
    前端导出的"UI 格式"（带 nodes 数组，也可能裹在 api.nodes 里）。
    """
    if not isinstance(api, dict):
        return 0
    if isinstance(api.get("nodes"), list):
        return len(api["nodes"])
    inner = api.get("api")
    if isinstance(inner, dict) and isinstance(inner.get("nodes"), list):
        return len(inner["nodes"])
    return len(api)


# ================================================================ UI 图 → API 图
# 前端保存的工作流（user/default/workflows 下的那些）是"UI 格式"：nodes 数组 +
# links 连线表 + widgets_values 值数组；而 /prompt 只收"API 格式"（{节点id:
# {class_type, inputs}} 的扁平表，本机 ComfyUI 的 execution.validate_prompt 第一
# 个循环就要求每项带 class_type，UI 格式直接报 missing_node_type）。
# 所以「把 ComfyUI 里存的工作流并进启动器」的关键就是这步转换 —— 规则对齐前端
# 的 graphToPrompt：
#   * 连接输入的值 = [来源节点id, 输出槽号]（按 UI inputs 里的 link 找连线）；
#   * 小部件的值按 object_info 定义顺序（required 在前 optional 在后）从
#     widgets_values 里依序取用 —— 所以下发前必须拿到 /object_info 的节点定义；
#   * 名字带 seed 的 INT/FLOAT、以及定义里标 control_after_generate 的输入，
#     后面各多占一位"随机化选项"的值，取用时跳过；
#   * 旁路（mode=4）节点不进 API 图，它的输出由"同类型输入"直通顶替；
#     直通不了且下游接的是可选输入 → 该输入整个省略（真实案例：seedvr2 示例图
#     旁路的 TorchCompileSettings 没有任何输入连线，它的输出只喂可选槽）。
#   * Note / MarkdownNote 是画布便签，不是可执行节点，直接跳过。
#
# 值数组还有第三种形态：VHS 系列节点存成 {名字: 值} 的字典（老序列化），按名
# 映射即可，不用对位。
#
# 前端导出的节点 id 是数字，API 图的键是字符串化的 id —— 字符串化这一步别漏，
# 否则 ComfyUI 4.2+ 的 job 调度找不到节点。

_UI_NOTE_TYPES = ("Note", "MarkdownNote")
_WIDGET_TYPES = ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO")


def _spec_type(spec):
    """object_info 输入定义的类型名；COMBO 的选项列表形态返回 None（按部件算）。"""
    if isinstance(spec, list) and spec:
        return spec[0]
    return None


def _spec_opts(spec):
    """object_info 输入定义的选项字典（没有就空字典）。"""
    if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1]
    return {}


def is_ui_graph(data):
    """这份图是 UI 格式吗？（nodes 数组 = UI 格式；否则当 API 扁平表用）"""
    if not isinstance(data, dict):
        return False
    if isinstance(data.get("nodes"), list):
        return True
    inner = data.get("api")
    return isinstance(inner, dict) and isinstance(inner.get("nodes"), list)


def _ui_resolver(nodes, links, defs):
    """造一个 resolve(节点id, 输出槽号) 函数：连线起点落地成 API 值。

    规则见 ui_to_api 的注释：旁路（mode=4）顺着同类型输入往前找、Reroute
    纯拐弯、静音（mode=2）报错。返回 (值, 错误串)；(None, "") 表示"旁路死头 +
    下游是可选输入，可整体省略"。
    """
    byid = {n.get("id"): n for n in nodes}

    def resolve(nid, slot, depth=0):
        if depth > 16:
            return None, "旁路直通链超过 16 层，图可能有问题"
        n = byid.get(nid)
        if n is None:
            return None, "连线指向不存在的节点 #%s" % nid
        if n.get("type") == "Reroute":
            # Reroute 是前端虚拟节点（object_info 里没有），纯粹把线拐个弯：
            # 唯一的输入连到哪，输出就是哪。
            for inp in n.get("inputs") or []:
                lid = inp.get("link")
                if lid is None:
                    continue
                lk = links.get(lid)
                if lk is not None:
                    return resolve(lk[1], lk[2], depth + 1)
            return None, ""
        m = n.get("mode", 0)
        if m == 0:
            return [str(nid), slot], ""
        if m == 4:
            outs = (defs.get(n.get("type")) or {}).get("output") or []
            want = outs[slot] if slot < len(outs) else None
            for inp in n.get("inputs") or []:
                lid = inp.get("link")
                if lid is None:
                    continue
                lk = links.get(lid)
                if lk is None:
                    continue
                if want is None or inp.get("type") in (want, "*") or want == "*":
                    return resolve(lk[1], lk[2], depth + 1)
            return None, ""          # 旁路死头：没有可直通的输入，交由调用方按可选省略
        return None, "节点「%s」被静音（mode=%s），它的下游没法推" % (
            n.get("title") or n.get("type"), m)

    return resolve


def _ui_input_value(name, spec, ui_in, wv, k, link_val):
    """定一个输入的最终值。返回 (是否处理, 值, 错误串, 新的值位游标 k)。

    False = 这是没连线的连接槽（可选的省略、必填的交给服务端报），游标原样传回。
    """
    t = _spec_type(spec)
    opts = _spec_opts(spec)
    conv = name in ui_in and "widget" in ui_in[name]
    widgety = ((not isinstance(t, str)) or t in _WIDGET_TYPES or conv)
    linked = name in ui_in and ui_in[name].get("link") is not None
    if widgety and not opts.get("forceInput"):
        # 部件类输入**一律占一位值**（哪怕被转成输入槽还连了线，
        # widgets_values 里也留着它的旧值，见 CreateVideo 的 fps）。
        slot = k
        k += 1
        if t in ("INT", "FLOAT") and (opts.get("control_after_generate")
                                      or "seed" in name.lower()):
            k += 1                # 前端给 seed 类输入追加"随机化选项"，多占一位
        if linked:
            val, err = link_val(ui_in[name]["link"])
        elif slot < len(wv):
            val, err = wv[slot], ""
        else:
            val, err = None, ""   # 值数组不够长：可选就省略
        return True, val, err, k
    if linked:                    # 连接槽（IMAGE/MODEL 这类，不占值位）
        val, err = link_val(ui_in[name]["link"])
        return True, val, err, k
    return False, None, "", k


def _ui_node_inputs(n, defs, links, resolve):
    """单个 UI 节点的 inputs 映射。返回 (inputs, 错误串)。

    值位规则见 ui_to_api 的注释：部件类输入按 object_info 定义序对位取值
    （连线了的也占位、只是值改从连线来）；连接槽不占值位；seed 类部件在
    前端多带一位"随机化选项"，取用时要跳过。
    """
    idef = defs[n["type"]].get("input") or {}
    req = idef.get("required") or {}
    opt = idef.get("optional") or {}
    ui_in = {i.get("name"): i for i in n.get("inputs") or []}
    wv = n.get("widgets_values")
    if isinstance(wv, dict):          # VHS 老序列化：{名字: 值}，按名映射
        return {k: v for k, v in wv.items() if k in req or k in opt}, ""

    def link_val(lid):
        lk = links.get(lid)
        if lk is None:
            return None, "引用了找不到的连线 #%s" % lid
        return resolve(lk[1], lk[2])

    wv = list(wv or [])
    inputs = {}
    k = 0
    for sect in (req, opt):
        for name, spec in sect.items():
            handled, val, err, k = _ui_input_value(name, spec, ui_in, wv, k, link_val)
            if not handled:
                continue
            if err:
                return None, "输入 %s %s" % (name, err)
            if val is not None:
                inputs[name] = val
    return inputs, ""


def ui_to_api(ui, defs):
    """UI 格式 → API 扁平表。返回 (图, 错误串)；转换不了时图为 None、错误串说明原因。

    defs = /object_info 的解析结果（class → 定义）。规则对齐前端 graphToPrompt：
      * 连接输入的值 = [来源节点id, 输出槽号]（按 UI inputs 里的 link 找连线）；
      * 小部件的值按定义顺序（required 在前 optional 在后）从 widgets_values
        里对位取用 —— 所以下发前必须拿到 /object_info 的节点定义；
      * 名字带 seed 的 INT/FLOAT、定义里标 control_after_generate 的输入，
        后面各多占一位"随机化选项"的值，取用时跳过；
      * 旁路（mode=4）节点不进 API 图，输出由"同类型输入"直通顶替；直通不了
        且下游接的是可选输入 → 该输入整个省略（真实案例：seedvr2 示例图里旁路的
        TorchCompileSettings 自己没有任何输入连线，输出只喂可选槽）；
      * Note / MarkdownNote 是画布便签、Reroute 是纯拐弯，都不进 API 图。
    子图节点（新前端的 definitions.subgraphs，类型是一串 UUID）暂不支持 ——
    要推请先在 ComfyUI 里把子图展开再保存。
    """
    if not isinstance(ui, dict) or not isinstance(ui.get("nodes"), list):
        return None, "不是 UI 格式的工作流（缺 nodes 数组）"
    if not isinstance(defs, dict) or not defs:
        return None, "没有节点定义（/object_info），转换无从谈起"
    nodes = ui["nodes"]
    links = {}
    for lk in ui.get("links") or []:
        if isinstance(lk, list) and len(lk) >= 5:
            links[lk[0]] = lk
    resolve = _ui_resolver(nodes, links, defs)
    api = {}
    for n in nodes:
        cls = n.get("type")
        if cls in _UI_NOTE_TYPES or cls == "Reroute":
            continue                  # 便签 / 拐弯节点：不可执行，也不占 API 图
        if not isinstance(cls, str) or cls not in defs:
            return None, ("图里有本机 ComfyUI 认不了的节点（%s）——要么插件没装，"
                          "要么是暂不支持的子图，请在 ComfyUI 里展开后再存" % cls)
        if n.get("mode", 0) != 0:
            continue                  # 旁路 / 静音节点都不进 API 图（静音的进了会被执行！）
        inputs, err = _ui_node_inputs(n, defs, links, resolve)
        if err:
            return None, "节点「%s」%s" % (n.get("title") or cls, err)
        api[str(n.get("id"))] = {"class_type": cls, "inputs": inputs,
                                 "_meta": {"title": n.get("title") or cls}}
    if not api:
        return None, "图里没有可执行节点（全是便签、旁路或静音？）"
    return api, ""


# ================================================================ 工作流目录扫描
# ComfyUI 自己把网页里保存的工作流放在 <comfy_dir>/user/default/workflows/。
# 启动器每次启动扫一遍这个目录，目录里的 .json 全部进下拉 —— 用户在 ComfyUI
# 里存了新图，重启启动器（或点刷新）就能看到，不用再手动「加载 .json」。
# 路径可在 启动器配置.json 里加 "comfy_workflows_dir" 覆盖（不进设置界面，
# 免得左栏高度又得动）。


def workflows_dir(cfg):
    """ComfyUI 存工作流的目录：配置可覆盖，默认从 comfy_dir 派生。"""
    over = str((cfg or {}).get("comfy_workflows_dir", "") or "").strip()
    if over:
        return over
    return os.path.join(str((cfg or {}).get("comfy_dir", "") or ""),
                        "user", "default", "workflows")


def scan_workflows(cfg):
    """扫工作流目录。返回 (文件名列表有序, 错误串)；目录不在不算错，返回空表。"""
    d = workflows_dir(cfg)
    if not os.path.isdir(d):
        return [], "工作流目录不存在：%s" % d
    try:
        names = sorted(f for f in os.listdir(d)
                       if f.lower().endswith(".json")
                       and os.path.isfile(os.path.join(d, f)))
    except Exception as e:
        return [], "工作流目录读不了：%r" % e
    return names, ""


# ================================================================ 内置工作流清单
# 纯数据（界面上的展示字段 + 节点步骤），不含颜色等界面令牌 —— 所以留在这里，
# 而不是跟配色一起放在主文件里占地方。
#
# api   = 该方案对应的真实 API 图（相对 deploy_root 的路径）；没有就不写，界面会指路。
# patch = 推之前要覆盖的节点参数（按 class_type 定位）—— 同一份图按方案微调用的。
FLASHVSR_API = {                       # FlashVSR 两版图只差"有没有连音频线"
    "audio":  "workflows/FlashVSR/FlashVSR-12G-有声素材.json",
    "silent": "workflows/FlashVSR/FlashVSR-12G-无音轨素材.json",
}
WORKFLOWS = [
    {"id": "fv_2x", "engine": "flashvsr", "name": "FlashVSR · 通用 2x 放大",
     "desc": "通用超分，适合写实/日常视频，输出 2 倍分辨率。",
     "steps": [("LoadVideo", "input_dir"), ("FlashVSR 超分", "scale=2"),
               ("(可选) 帧插值", "fps×2"), ("SaveVideo", "output_dir")],
     "inN": "1 × LoadVideo", "outN": "1 × SaveVideo", "api": FLASHVSR_API},
    {"id": "fv_4x", "engine": "flashvsr", "name": "FlashVSR · 动画 4x 放大",
     "desc": "针对动画/二次元优化，输出 4 倍分辨率，细节更锐。",
     "steps": [("LoadVideo", "input_dir"), ("FlashVSR 超分", "scale=4"),
               ("CAS 锐化", "strength=0.4"), ("SaveVideo", "output_dir")],
     "inN": "1 × LoadVideo", "outN": "1 × SaveVideo",
     "api": FLASHVSR_API, "patch": {"FlashVSRNode": {"scale": 4}}},
    {"id": "sv_std", "engine": "seedvr2", "name": "SeedVR2 · 标准 1080p",
     "desc": "标准档，短边缩放到 1080，速度与质量平衡。",
     "api": "workflows/SeedVR2/SeedVR2-12G-1080p.json",
     "steps": [("LoadVideo", "input_dir"), ("SeedVR2 标准档", "tile=512"),
               ("Resize 短边", "1080"), ("SaveVideo", "output_dir")],
     "inN": "1 × LoadVideo", "outN": "1 × SaveVideo"},
    {"id": "sv_max", "engine": "seedvr2", "name": "SeedVR2 · 极致 2160",
     "desc": "极致档，短边冲 2160（4K），显存占用高、最慢。"
             "（SeedVR2 没有「保持原画」参数，短边必须给目标值，故用 2160。）",
     "api": "workflows/SeedVR2/SeedVR2-极致-2160.json",
     "steps": [("LoadVideo", "input_dir"), ("SeedVR2 极致档", "tile=384"),
               ("FaceRestore", "on"), ("SaveVideo", "output_dir")],
     "inN": "1 × LoadVideo", "outN": "1 × SaveVideo"},
    {"id": "mm_t2v", "engine": "minimax", "name": "MiniMax H3 · 文生视频 T2V",
     "desc": "用提示词直接生成视频（Text-to-Video）。",
     "api": "workflows/MiniMax/MiniMax-H3-T2V.json",
     "steps": [("TextEncode", "prompt"), ("MiniMax H3", "T2V"),
               ("VAEDecode", ""), ("SaveVideo", "output_dir")],
     "inN": "0（纯文本）", "outN": "1 × SaveVideo"},
    {"id": "mm_i2v", "engine": "minimax", "name": "MiniMax H3 · 图生视频 I2V",
     "desc": "以首帧图片驱动生成视频（Image-to-Video）。",
     "api": "workflows/MiniMax/MiniMax-H3-I2V.json",
     "steps": [("LoadImage", "first_frame"), ("MiniMax H3", "I2V"),
               ("VAEDecode", ""), ("SaveVideo", "output_dir")],
     "inN": "1 × LoadImage", "outN": "1 × SaveVideo"},
]
