# -*- coding: utf-8 -*-
"""ui_kit —— 视频方案启动器的深色自绘控件库（纯标准库 tkinter）。

设计蓝本：仓库分支 `ui-redesign` 的 `redesign/video_launcher_sidebar.html`。

实现要点：
  * 圆角卡片用 Canvas 自绘（tk 无原生圆角）；body 四周内缩 = radius，
    使它的直角始终落在圆角弧线之内，不会露出方角。
  * 阴影无 alpha 通道，用 stipple 点阵近似。
  * 按钮 / 下拉 / 胶囊 / 进度条 / 复选框全部自绘，避免 ttk 在深色下的
    白边与主题不一致。
  * 配色只用于表状态 / 区分引擎，不滥用。

约定：本模块不 import 任何业务代码，只提供「设计令牌 + 几何/颜色工具 + 控件」。
"""
import tkinter as tk
from tkinter import font as tkfont

# ---------------------------------------------------------------- 设计令牌（深色 Fluent）
BG       = "#15171c"   # 页面底色
CARD     = "#232730"   # 卡片
CARD2    = "#2b303b"   # 卡片内控件
BORDER   = "#353b47"   # 描边
HOVER    = "#333a47"   # 悬停
TXT      = "#eef1f5"   # 主文字
TXT2     = "#9aa3b2"   # 次文字
BLUE     = "#4a9eff"   # 强调蓝
PURP     = "#b389f0"   # 强调紫
TEAL     = "#34d3b0"   # 强调青
GREEN    = "#3fb950"
AMBER    = "#e3b341"
RED      = "#f85149"
DARKTX   = "#0b1220"   # 亮底上的深色字
MUTED    = "#5b6270"

SCALE    = 1.0                     # DPI 缩放系数（init_scaling 里按实际 DPI 计算）
UIFONT   = "Microsoft YaHei UI"
MONOFONT = "Consolas"


def S(v):
    """逻辑像素 → 实际像素（按 DPI 缩放）。"""
    return max(1, int(round(v * SCALE)))


def f(pt, bold=False):
    """UI 字体（字号为磅值，随 tk scaling 自动放大）。"""
    return (UIFONT, pt, "bold") if bold else (UIFONT, pt)


def mono(pt):
    """等宽字体（日志区）。"""
    return (MONOFONT, pt)


# ---------------------------------------------------------------- 颜色 / 几何工具
def _hex2rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb2hex(r, g, b):
    return "#%02x%02x%02x" % (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))


def mix(c1, c2, t):
    """按 t（0..1）在两色间线性插值。"""
    a, b = _hex2rgb(c1), _hex2rgb(c2)
    return _rgb2hex(*[int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3)])


def lighten(c, t=0.12):
    """提亮颜色（悬停态用）。"""
    return mix(c, "#ffffff", t)


def round_pts(x1, y1, x2, y2, r):
    """圆角矩形顶点串（配合 create_polygon(smooth=True) 使用）。"""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def _border_of(border, fill):
    """把 None / "transparent" 归一成 Tk 能接受的描边色（否则 unknown color name）。"""
    if border in (None, "transparent"):
        return None
    return border


# ---------------------------------------------------------------- 圆角卡片（圆角 + 阴影）
class RoundedFrame(tk.Frame):
    """圆角卡片：Canvas 画底（阴影 + 描边 + 填充），body 承载内容。"""

    def __init__(self, master, *, fill=CARD, border=BORDER, radius=None,
                 outer=BG, pad=(5, 5), shadow=True, **kw):
        super().__init__(master, bg=outer, **kw)
        self._fill, self._border, self._outer = fill, border, outer
        self._r = radius if radius is not None else S(12)
        self._shadow = shadow
        self.cv = tk.Canvas(self, bg=outer, highlightthickness=0, bd=0)
        self.cv.place(x=0, y=0, relwidth=1, relheight=1)
        inset = self._r
        self.body = tk.Frame(self, bg=fill)
        self.body.pack(fill="both", expand=True,
                       padx=(inset + S(pad[0]), inset + S(pad[0])),
                       pady=(inset + S(pad[1]), inset + S(pad[1])))
        self.bind("<Configure>", self._redraw)

    def set_fill(self, fill):
        self._fill = fill
        self.body.configure(bg=fill)
        self.cv.delete("all")
        self._redraw()

    def _redraw(self, e=None):
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            return
        cv = self.cv
        cv.delete("all")
        r = self._r
        if self._shadow:
            # 用 stipple 近似半透明阴影（tk 无 alpha 通道）
            cv.create_polygon(round_pts(S(2), S(3), w - S(1), h - S(1), r),
                              smooth=True, fill="#000000", outline="", stipple="gray25")
            cv.create_polygon(round_pts(S(1), S(5), w - S(1), h - S(1), r),
                              smooth=True, fill="#000000", outline="", stipple="gray12")
        cv.create_polygon(round_pts(S(1), S(1), w - S(2), h - S(2), r),
                          smooth=True, fill=self._fill, outline=self._border, width=1)


# ---------------------------------------------------------------- 圆角按钮 / 胶囊
class RButton(tk.Canvas):
    """自绘圆角按钮。stretch=True 时撑满父容器宽度；fill="transparent" 为幽灵按钮。"""

    def __init__(self, master, text="", command=None, *, fill=CARD2, fg=TXT,
                 hover=None, border=BORDER, radius=None, padx=None, pady=None,
                 font=None, outer=BG, stretch=False, align="center",
                 chevron=False, minwidth=0, state="normal", height=None):
        self._font = font or f(10)
        self._padx = S(padx if padx is not None else 14)
        self._pady = S(pady if pady is not None else 7)
        meas = tkfont.Font(font=self._font)
        tw = meas.measure(text)
        th = meas.metrics("linespace")
        w0 = max(tw + self._padx * 2 + (S(18) if chevron else 0), S(minwidth))
        h0 = height if height is not None else th + self._pady * 2
        super().__init__(master, width=w0, height=h0, bg=outer, highlightthickness=0, bd=0)
        self._w0, self._h0 = w0, h0
        self._fill, self._fg, self._border = fill, fg, border
        self._hover = hover if hover is not None else (
            lighten(fill, 0.10) if fill and fill != "transparent" else HOVER)
        self._r = radius if radius is not None else S(8)
        self._text, self._align, self._chevron = text, align, chevron
        self._cmd, self._state, self._stretch = command, state, stretch
        self._over = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self._draw()

    # --- 对外 ---
    def set_text(self, text):
        self._text = text
        self._draw()

    def set_state(self, state):
        self._state = state
        self.configure(cursor="hand2" if state == "normal" and self._cmd else "arrow")
        self._draw()

    def set_fill(self, fill, fg=None, border=None):
        self._fill = fill
        if fg:
            self._fg = fg
        if border is not None:
            self._border = border
        self._hover = lighten(fill, 0.10) if fill and fill != "transparent" else HOVER
        self._draw()

    # --- 内部 ---
    def _size(self):
        w = self.winfo_width() if self._stretch else self._w0
        h = self.winfo_height() if self._stretch else self._h0
        if w <= 1:
            w = self._w0
        if h <= 1:
            h = self._h0
        return w, h

    def _on_enter(self, _=None):
        if self._state == "normal" and self._cmd:
            self._over = True
            self.configure(cursor="hand2")
            self._draw()

    def _on_leave(self, _=None):
        self._over = False
        self._draw()

    def _on_click(self, _=None):
        if self._state == "normal" and self._cmd:
            self._cmd()

    def _draw(self):
        w, h = self._size()
        self.delete("all")
        fill, fg = self._fill, self._fg
        border = _border_of(self._border, fill)
        if self._state == "disabled":
            if fill != "transparent":
                fill = CARD2
            fg = MUTED
        elif self._over:
            fill = self._hover
        if fill == "transparent":
            if border:
                self.create_polygon(round_pts(S(1), S(1), w - S(2), h - S(2), self._r),
                                    smooth=True, fill="", outline=border, width=1)
        else:
            self.create_polygon(round_pts(S(1), S(1), w - S(2), h - S(2), self._r),
                                smooth=True, fill=fill, outline=border or fill, width=1)
        tx = self._padx if self._align == "w" else w / 2
        self.create_text(tx, h / 2, text=self._text, fill=fg, font=self._font,
                         anchor=("w" if self._align == "w" else "center"))
        if self._chevron:
            cx, cy = w - S(14), h / 2
            self.create_line(cx - S(4), cy - S(2), cx, cy + S(2), cx + S(4), cy - S(2),
                             fill=fg if self._state == "normal" else MUTED,
                             width=max(1, S(2)), capstyle="round", joinstyle="round")


def swatch(master, size, color, *, outer=BG, radius=None, border=None):
    """小圆角色块（导航图标底座）。"""
    size = S(size)
    cv = tk.Canvas(master, width=size, height=size, bg=outer, highlightthickness=0, bd=0)
    r = radius if radius is not None else S(6)
    cv.create_polygon(round_pts(1, 1, size - 1, size - 1, r), smooth=True,
                      fill=color, outline=border or color, width=1)
    return cv


def swatch_text(master, size, color, text, *, outer=BG, radius=None, fg=DARKTX, font=None):
    """带文字的圆角色块（logo / 引擎头像）。"""
    cv = swatch(master, size, color, outer=outer, radius=radius)
    cv.create_text(size / 2, size / 2 + S(1), text=text, fill=fg,
                   font=font or f(12, True))
    return cv


# ---------------------------------------------------------------- 左导航项
class NavItem(tk.Frame):
    """左导航项：active 时左侧 3px 强调条 + CARD2 底。"""

    def __init__(self, master, text, command=None, *, color=BLUE, badge=None, outer=BG):
        super().__init__(master, bg=outer, height=S(36))
        self.pack_propagate(False)
        self._outer, self._active, self._cmd = outer, False, command
        self.bar = tk.Frame(self, width=S(3), bg=outer)
        self.bar.pack(side="left", fill="y", padx=(S(2), S(8)))
        self.sw = swatch(self, 20, color, outer=outer, radius=S(6))
        self.sw.pack(side="left", pady=S(8))
        self.lab = tk.Label(self, text=text, bg=outer, fg=TXT2, font=f(10), anchor="w")
        self.lab.pack(side="left", fill="x", expand=True, padx=(S(10), 0))
        self.badge = None
        if badge is not None:
            self.badge = tk.Label(self, text=str(badge), bg=BLUE, fg=DARKTX,
                                  font=f(8, True), padx=S(6), pady=0)
            self.badge.pack(side="right", padx=(0, S(8)))
        for w in (self, self.sw, self.lab, self.bar):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)
        self.configure(cursor="hand2")

    def _paint(self, bg, fg, bar):
        self.configure(bg=bg)
        self.bar.configure(bg=bar)
        self.sw.configure(bg=bg)
        self.lab.configure(bg=bg, fg=fg)

    def _enter(self, _=None):
        if not self._active:
            self._paint(CARD2, TXT, self._outer)

    def _leave(self, _=None):
        if not self._active:
            self._paint(self._outer, TXT2, self._outer)

    def _click(self, _=None):
        if self._cmd:
            self._cmd()

    def set_active(self, on):
        self._active = on
        if on:
            self._paint(CARD2, TXT, BLUE)
        else:
            self._paint(self._outer, TXT2, self._outer)


# ---------------------------------------------------------------- 内联下拉
class Dropdown(tk.Frame):
    """深色内联下拉：触发条 + 就地展开的选项面板（不用 Toplevel，免焦点问题）。"""

    def __init__(self, master, values, command=None, *, outer=BG, fill=CARD2,
                 placeholder="请选择", font=None, height=None, highlight=BLUE):
        super().__init__(master, bg=outer)
        self._values = list(values)
        self._cmd = command
        self._outer, self._fill, self._hl = outer, fill, highlight
        self._font = font or f(10)
        self._placeholder = placeholder
        self.value = None
        self._open = False
        self._h = height if height is not None else S(36)
        self.trig = RButton(self, text=placeholder, command=self.toggle, stretch=True,
                            fill=fill, fg=TXT2, border=BORDER, align="w",
                            chevron=True, font=self._font, outer=outer, height=self._h,
                            padx=12)
        self.trig.pack(fill="x")
        self.panel = tk.Frame(self, bg=outer)

    def set_values(self, values, placeholder=None):
        self._values = list(values)
        if placeholder is not None:
            self._placeholder = placeholder
        if self.value not in self._values:
            self.value = None
            self.trig.set_text(self._placeholder)
            self.trig.set_fill(self._fill, TXT2, BORDER)
        if self._open:
            self._build_panel()

    def set(self, value, notify=False):
        self.value = value
        self.trig.set_text(value if value else self._placeholder)
        self.trig.set_fill(self._fill, TXT if value else TXT2,
                           self._hl if value else BORDER)
        if notify and self._cmd:
            self._cmd(value)

    def toggle(self):
        if self._open:
            self._collapse()
        else:
            self._expand()

    def _expand(self):
        self._build_panel()
        self.panel.pack(fill="x", pady=(S(4), 0))
        self._open = True

    def _collapse(self):
        self.panel.pack_forget()
        self._open = False

    def _build_panel(self):
        for c in self.panel.winfo_children():
            c.destroy()
        box = tk.Frame(self.panel, bg=BORDER)
        box.pack(fill="x")
        inner = tk.Frame(box, bg=CARD2)
        inner.pack(fill="x", padx=1, pady=1)
        for v in self._values:
            row = tk.Label(inner, text=v, bg=CARD2, fg=TXT, font=self._font,
                           anchor="w", padx=S(12), pady=S(6), cursor="hand2")
            row.pack(fill="x")
            row.bind("<Enter>", lambda e, r=row: r.configure(bg=HOVER))
            row.bind("<Leave>", lambda e, r=row: r.configure(bg=CARD2))
            row.bind("<Button-1>", lambda e, v=v: self._choose(v))

    def _choose(self, v):
        self._collapse()
        self.set(v, notify=True)


# ---------------------------------------------------------------- 互斥胶囊 / 复选框 / 进度条
class ChipRow(tk.Frame):
    """一排互斥小胶囊（倍数 / 分辨率）。"""

    def __init__(self, master, values, command=None, *, outer=CARD, default=None,
                 fill=CARD2, on_fill=BLUE, font=None):
        super().__init__(master, bg=outer)
        self._cmd = command
        self.value = default if default in values else (values[0] if values else None)
        self._fill, self._on = fill, on_fill
        self._btns = {}
        for v in values:
            b = RButton(self, text=v, command=lambda v=v: self.set(v),
                        fill=(on_fill if v == self.value else fill),
                        fg=(DARKTX if v == self.value else TXT),
                        border="transparent" if v == self.value else BORDER,
                        font=font or f(10), outer=outer, stretch=True, pady=7)
            b.pack(side="left", fill="x", expand=True,
                   padx=(0, S(8)) if v != values[-1] else 0)
            self._btns[v] = b

    def set(self, v, notify=True):
        self.value = v
        for k, b in self._btns.items():
            if k == v:
                b.set_fill(self._on, DARKTX, "transparent")
            else:
                b.set_fill(self._fill, TXT, BORDER)
        if notify and self._cmd:
            self._cmd(v)

    def get(self):
        return self.value


class CheckBox(tk.Frame):
    """自绘复选框（圆角方框 + 勾）。"""

    def __init__(self, master, text, command=None, *, on=False, outer=CARD,
                 color=BLUE, font=None):
        super().__init__(master, bg=outer)
        self._on = bool(on)
        self._outer, self._color, self._cmd = outer, color, command
        self.box = tk.Canvas(self, width=S(16), height=S(16), bg=outer,
                             highlightthickness=0, bd=0)
        self.box.pack(side="left")
        self.lab = tk.Label(self, text=text, bg=outer, fg=TXT2, font=font or f(9))
        self.lab.pack(side="left", padx=(S(8), 0))
        for w in (self.box, self.lab):
            w.bind("<Button-1>", self._toggle)
            w.configure(cursor="hand2")
        self._paint()

    def _paint(self):
        self.box.delete("all")
        if self._on:
            self.box.create_polygon(round_pts(1, 1, S(15), S(15), S(4)), smooth=True,
                                    fill=self._color, outline="")
            self.box.create_line(S(4), S(8), S(7), S(11), S(12), S(4), fill=DARKTX,
                                 width=max(1, S(2)), capstyle="round", joinstyle="round")
        else:
            self.box.create_polygon(round_pts(1, 1, S(15), S(15), S(4)), smooth=True,
                                    fill=CARD2, outline=BORDER)

    def _toggle(self, _=None):
        self._on = not self._on
        self._paint()
        if self._cmd:
            self._cmd(self._on)

    def get(self):
        return self._on

    def set(self, v):
        self._on = bool(v)
        self._paint()


class ProgressBar(tk.Canvas):
    """细长圆角进度条。"""

    def __init__(self, master, *, outer=CARD, track=CARD2, bar=BLUE, height=None):
        h = height if height is not None else S(8)
        super().__init__(master, height=h, bg=outer, highlightthickness=0, bd=0)
        self._track, self._bar = track, bar
        self.pct = 0.0
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, pct):
        self.pct = max(0.0, min(100.0, float(pct)))
        self._draw()

    def _draw(self):
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            return
        r = h / 2
        self.delete("all")
        self.create_polygon(round_pts(0, 0, w, h, r), smooth=True,
                            fill=self._track, outline=self._track)
        fw = int(w * self.pct / 100.0)
        if fw > 1:
            self.create_polygon(round_pts(0, 0, fw, h, r), smooth=True,
                                fill=self._bar, outline=self._bar)


# ---------------------------------------------------------------- 启动期基础设施
def enable_dpi_awareness():
    """开启进程级 DPI 感知（必须在创建 Tk 窗口之前调用，否则高分屏发虚）。"""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def pick_font():
    """挑一个本机存在的中文 UI 字体。"""
    try:
        fams = set(tkfont.families())
    except Exception:
        return "TkDefaultFont"
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Segoe UI"):
        if name in fams:
            return name
    return "TkDefaultFont"


def init_scaling(root):
    """按实际 DPI 设定 SCALE / tk scaling / UI 字体（必须在建控件前调用）。"""
    global SCALE, UIFONT, MONOFONT
    try:
        SCALE = root.winfo_fpixels("1i") / 96.0
    except Exception:
        SCALE = 1.0
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    except Exception:
        pass
    UIFONT = pick_font()
    try:
        MONOFONT = "Consolas" if "Consolas" in set(tkfont.families()) else "Courier New"
    except Exception:
        MONOFONT = "Courier New"
