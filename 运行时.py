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
