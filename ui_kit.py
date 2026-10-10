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
import os
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox

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

    def set_border(self, border):
        """只换描边色（用于"闪烁提示位置"这类不改变卡片配色的强调）。"""
        self._border = border
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
    """深色下拉：触发条 + 弹出式选项浮层（无边框 Toplevel）。

    v1.2.12 前是"就地展开"（面板 pack 在卡片里），每展开一次就把整张卡片撑高、
    上排高度跟着跳 —— 改为浮层后布局完全不动。旧结论"不用 Toplevel 免焦点问题"
    由 grab_set + wait_visibility 推翻，v1.2.10 的 ask_text 已验证这条路可靠。
    """

    def __init__(self, master, values, command=None, *, outer=BG, fill=CARD2,
                 placeholder="请选择", font=None, height=None, highlight=BLUE):
        super().__init__(master, bg=outer)
        self._values = list(values)
        self._cmd = command
        self._outer, self._fill, self._hl = outer, fill, highlight
        self._font = font or f(10)
        self._placeholder = placeholder
        self.value = None
        self._pop = None
        self._h = height if height is not None else S(36)
        self.trig = RButton(self, text=placeholder, command=self.toggle, stretch=True,
                            fill=fill, fg=TXT2, border=BORDER, align="w",
                            chevron=True, font=self._font, outer=outer, height=self._h,
                            padx=12)
        self.trig.pack(fill="x")

    def set_values(self, values, placeholder=None):
        self._values = list(values)
        if placeholder is not None:
            self._placeholder = placeholder
        if self.value not in self._values:
            self.value = None
            self.trig.set_text(self._placeholder)
            self.trig.set_fill(self._fill, TXT2, BORDER)
        if self._pop is not None and self._pop.winfo_exists():
            self._fill_panel()

    def set(self, value, notify=False):
        self.value = value
        self.trig.set_text(value if value else self._placeholder)
        self.trig.set_fill(self._fill, TXT if value else TXT2,
                           self._hl if value else BORDER)
        if notify and self._cmd:
            self._cmd(value)

    def toggle(self):
        if self._pop is not None and self._pop.winfo_exists():
            self._collapse()
        else:
            self._expand()

    def _expand(self):
        pop = tk.Toplevel(self)
        pop.overrideredirect(True)
        pop.configure(bg=BORDER)
        self._pop = pop
        self._fill_panel()
        pop.update_idletasks()
        x = self.trig.winfo_rootx()
        y = self.trig.winfo_rooty() + self.trig.winfo_height() + S(4)
        w = self.trig.winfo_width()
        h = pop.winfo_reqheight()
        if y + h > pop.winfo_screenheight() - S(8):        # 下方放不下就朝上弹
            y = max(S(8), self.trig.winfo_rooty() - h - S(4))
        pop.geometry("%dx%d+%d+%d" % (w, h, x, y))
        pop.deiconify()
        pop.wait_visibility()                     # grab 只对"已可见"的窗口生效
        pop.grab_set()                            # 浮层开着时所有点击都归它：
        pop.bind("<Escape>", lambda e: self._collapse())     # Esc 收起
        pop.bind("<FocusOut>", lambda e: self._collapse())   # 切去别的程序收起
        pop.bind("<Button-1>", lambda e: self._collapse())   # 点浮层空白处收起

    def _fill_panel(self):
        pop = self._pop
        for c in pop.winfo_children():
            c.destroy()
        inner = tk.Frame(pop, bg=CARD2)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        wl = max(S(60), self.trig.winfo_width() - S(28))
        for v in self._values:
            row = tk.Label(inner, text=v, bg=CARD2, fg=TXT, font=self._font,
                           anchor="w", padx=S(12), pady=S(6), cursor="hand2",
                           wraplength=wl, justify="left")
            row.pack(fill="x")
            row.bind("<Enter>", lambda e, r=row: r.configure(bg=HOVER))
            row.bind("<Leave>", lambda e, r=row: r.configure(bg=CARD2))
            row.bind("<Button-1>", lambda e, v=v: self._choose(v))

    def _collapse(self):
        pop, self._pop = self._pop, None
        if pop is not None and pop.winfo_exists():   # 单线程 Tk，无需 try/except 兜销毁竞态
            pop.grab_release()
            pop.destroy()

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


# ---------------------------------------------------------------- 弹窗 / 设置列表
def _center_on_parent(win, parent):
    """把弹窗摆到父窗口中间；父窗口还没映射时退回屏幕左上偏中。"""
    try:
        win.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        if pw > 1 and ph > 1:
            win.geometry("+%d+%d" % (parent.winfo_rootx() + (pw - win.winfo_reqwidth()) // 2,
                                     parent.winfo_rooty() + (ph - win.winfo_reqheight()) // 3))
            return
    except Exception:
        pass      # 拿不到父窗口几何（启动瞬间 / 已销毁）：退回默认位置即可，
                  # 弹窗只是"位置差点"，不值得为它中断保存流程
    win.geometry("+%d+%d" % (S(320), S(220)))


HINTS = {
    "dir": "填完整的目录路径，也可以点「浏览…」选一个。保存后立即生效，并写回程序同目录的"
           "「启动器配置.json」。",
    "file": "填完整的文件路径，也可以点「浏览…」选一个。",
    "url": "填服务地址，例如 http://127.0.0.1:8188。",
}


def ask_text(parent, title, value="", *, hint="", browse=None):
    """模态输入弹窗（输入框 + 可选「浏览…」+ 取消/保存）。返回新文本，取消返回 None。

    browse: "dir" 选目录 / "file" 选文件 / None 纯文本。
    返回前会把首尾空白和误粘的引号去掉 —— Windows 上「复制为路径」带着双引号，
    直接拿去拼 PATH 会得到一条不存在的路径。
    """
    top = tk.Toplevel(parent)
    top.title(title)
    top.configure(bg=CARD)
    top.resizable(False, False)

    box = tk.Frame(top, bg=CARD)
    box.pack(fill="both", expand=True, padx=S(18), pady=S(16))
    tk.Label(box, text=title, bg=CARD, fg=TXT, font=f(10, True), anchor="w").pack(fill="x")
    if hint:
        tk.Label(box, text=hint, bg=CARD, fg=TXT2, font=f(8), anchor="w", justify="left",
                 wraplength=S(400)).pack(fill="x", pady=(S(4), S(10)))

    row = tk.Frame(box, bg=CARD)
    row.pack(fill="x")
    var = tk.StringVar(value=value or "")
    ent = tk.Entry(row, textvariable=var, bg=CARD2, fg=TXT, font=f(10), width=46,
                   insertbackground=TXT, relief="flat", highlightthickness=1,
                   highlightbackground=BORDER, highlightcolor=BLUE)
    ent.pack(side="left", fill="x", expand=True, ipady=S(6))

    def _browse():
        cur = var.get().strip().strip('"')
        kw = {"parent": top}
        if os.path.isdir(cur):
            kw["initialdir"] = cur
        elif os.path.dirname(cur) and os.path.isdir(os.path.dirname(cur)):
            kw["initialdir"] = os.path.dirname(cur)      # 文件项：从它所在目录开始
        p = (filedialog.askdirectory(**kw) if browse == "dir"
             else filedialog.askopenfilename(**kw))
        if p:
            var.set(os.path.normpath(p))                 # 统一成反斜杠，别混用 / 与 \

    if browse:
        RButton(row, text="浏览…", command=_browse, fill=CARD2, fg=TXT, outer=CARD,
                font=f(9), pady=6).pack(side="left", padx=(S(8), 0))

    got = {"v": None}

    def _save(_=None):
        got["v"] = var.get().strip().strip('"')
        top.destroy()

    btns = tk.Frame(box, bg=CARD)
    btns.pack(fill="x", pady=(S(14), 0))
    RButton(btns, text="取消", command=top.destroy, fill=CARD2, fg=TXT2, outer=CARD,
            font=f(9), pady=7, stretch=True).pack(side="left", fill="x", expand=True)
    RButton(btns, text="保存", command=_save, fill=BLUE, fg=DARKTX, border=None,
            outer=CARD, font=f(9, True), pady=7, stretch=True).pack(
                side="left", fill="x", expand=True, padx=(S(8), 0))

    ent.bind("<Return>", _save)
    top.bind("<Escape>", lambda e: top.destroy())
    top.protocol("WM_DELETE_WINDOW", top.destroy)
    _center_on_parent(top, parent.winfo_toplevel())
    ent.focus_set()
    ent.selection_range(0, "end")
    top.grab_set()                     # 模态：弹窗期间不许再点主窗口（否则可能改到一半又点开一个）
    parent.wait_window(top)            # 等它关；期间 Tk 继续跑事件循环，root.after 心跳不受影响
    return got["v"]


class SettingsList(tk.Frame):
    """左栏窄栏用的设置项列表：每项两行（标签行 + 值行），每项都能打开、都能改。

    数据驱动，界面不自己判断路径对不对 —— items 由 运行时.config_items() 给出：
      * note 非空 = 未就绪：值行整行转琥珀色，并把原因（缺 main.py 等）跟在路径后面；
      * 「打开」→ on_open(key)，怎么打开由业务层按类型决定；
      * 「修改」/ 点值行 → 弹输入框；保存后调 on_save(key, 新值)，
        返回错误串（写配置失败）时弹错误框且显示保持原样。
    """

    def __init__(self, master, items, *, outer=CARD, on_open=None, on_save=None):
        super().__init__(master, bg=outer)
        self._outer = outer
        self._items = []
        self.on_open = on_open
        self.on_save = on_save
        self.set_items(items)

    def set_items(self, items):
        """整体重画。项数只有个位，重画比逐项打补丁可靠（也不用管哪项变了）。"""
        self._items = list(items)
        for w in self.winfo_children():
            w.destroy()
        for it in self._items:
            self._add_row(it)

    def _add_row(self, it):
        note = it.get("note", "")
        row = tk.Frame(self, bg=self._outer)
        row.pack(fill="x", pady=(S(6), 0))
        tk.Label(row, text=it["label"], bg=self._outer, fg=TXT2, font=f(8),
                 anchor="w").pack(side="left")
        # 先摆「修改」再摆「打开」：side="right" 从右往左排，视觉上才是「打开 修改」
        self._link(row, "修改", lambda k=it["key"]: self._edit(k))
        self._link(row, "打开", lambda k=it["key"]: self._call(self.on_open, k))

        line = tk.Frame(self, bg=self._outer)
        line.pack(fill="x")
        lab = tk.Label(line, text=it["value"] + ("" if not note else "  ⚠️ " + note),
                       bg=self._outer, fg=(AMBER if note else TXT), font=f(8), anchor="w",
                       justify="left", wraplength=S(168), cursor="hand2")
        lab.pack(side="left", fill="x", expand=True)
        lab.bind("<Button-1>", lambda e, k=it["key"]: self._edit(k))
        lab.bind("<Enter>", lambda e, w=lab: w.configure(fg=BLUE))
        lab.bind("<Leave>", lambda e, w=lab, n=note: w.configure(fg=(AMBER if n else TXT)))

    def _link(self, parent, text, cmd):
        lab = tk.Label(parent, text=text, bg=self._outer, fg=TXT2, font=f(8), cursor="hand2")
        lab.pack(side="right", padx=(S(8), 0))
        lab.bind("<Enter>", lambda e, w=lab: w.configure(fg=BLUE))
        lab.bind("<Leave>", lambda e, w=lab: w.configure(fg=TXT2))
        lab.bind("<Button-1>", lambda e: cmd())

    def _call(self, fn, *args):
        if fn:
            fn(*args)

    def _edit(self, key):
        it = next((x for x in self._items if x["key"] == key), None)
        if it is None or not self.on_save:
            return
        new = ask_text(self.winfo_toplevel(), "修改 · " + it["label"], it["value"],
                       hint=HINTS.get(it["kind"], ""),
                       browse=it["kind"] if it["kind"] in ("dir", "file") else None)
        if new is None or new == it["value"]:      # 取消 / 没改动：什么都不做
            return
        err = self.on_save(key, new)
        if err:
            messagebox.showerror("保存失败", err, parent=self.winfo_toplevel())


_INNER = "#1c202a"      # 卡片里的"内嵌面板"底色（比 CARD 深一档），只在本模块内部用


# 详情盒固定高度：实测六套内置方案最大 320（fv_4x / sv_max / mm_i2v），+16 冗余。
# 两个分支（未选择 / 选中）都用同一个固定盒 —— 右栏需求恒定，上排高度才定得下来（v1.2.12）。
WF_DETAIL_H = S(336)


class WorkflowDetail(tk.Frame):
    """右栏工作流详情：铺开所选方案的节点步骤 + 输入输出 + 两个动作按钮。

    渲染归到控件库的原因：它是纯界面拼装（内嵌面板 + 步骤列表 + 2×2 指标网格 +
    动作按钮），跟业务无关；主文件的位置要留给"装配 + 状态机"。

    数据全由业务层给，本类不判断任何语义：
      * wf        = 内置方案 dict（engine / name / desc / steps / inN / outN），可为 None
      * local_name= 本地加载的 .json 文件名，可为 None
        （两者只会有一个非空；都为空 = 还没选，显示引导文案）
      * engines   = 引擎元数据表（color / label），由主文件传入 —— 本模块不 import 业务代码
      * nodes     = 节点数（内置方案传 len(steps)，本地传解析出的节点数）
      * on_change = 内容变了（高度跟着变）时回调，供业务层重排上排高度
      * on_push   = 点「推送 / 加入队列」时回调，参数 to_queue（True = 入队）
      * on_alias  = 点「备注」时回调（v1.2.14，只对本地工作流显示；内置方案名是
                    精修过的文案，不允许改）
      * alias     = 备注名：显示名换成它，原名照旧出现在"已从本地加载"一行里
    """

    def __init__(self, master, *, engines, outer=CARD, nodes=None,
                 on_change=None, on_push=None, on_alias=None, wf=None, local_name=None):
        super().__init__(master, bg=outer)
        self._engines = engines or {}
        self.on_change = on_change
        self.on_push = on_push
        self.on_alias = on_alias
        self.nodes = nodes
        self.show(wf, local_name)

    def show(self, wf=None, local_name=None, nodes=None, alias=None):
        if nodes is not None:
            self.nodes = nodes
        for c in self.winfo_children():
            c.destroy()
        self.pack(fill="x", pady=(S(12), 0))
        box = tk.Frame(self, bg=BORDER, height=WF_DETAIL_H)
        box.pack(fill="x", pady=(S(12), 0))
        box.pack_propagate(False)          # 固定高：内容装不下由渲染断言兜底
        inner = tk.Frame(box, bg=_INNER)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        if wf is None and local_name is None:
            tk.Label(inner, text="选择一套内置工作流，或「加载 .json」选本地 ComfyUI 工作流，"
                                 "这里会显示它的节点与输入输出。",
                     bg=_INNER, fg=TXT2, font=f(9), anchor="w", justify="left",
                     wraplength=S(250)).pack(fill="x", padx=S(12), pady=S(12))
            self._changed()
            return

        pad = tk.Frame(inner, bg=_INNER)
        pad.pack(fill="x", padx=S(12), pady=S(10))

        # 头：名称（可换行）+ 徽标靠右
        head = tk.Frame(pad, bg=_INNER)
        head.pack(fill="x")
        if wf:
            meta = self._engines.get(wf.get("engine"), {})
            name, badge, bcol = wf["name"], meta.get("label", ""), meta.get("color", BLUE)
            desc, steps = wf["desc"], wf["steps"]
            inN, outN = wf["inN"], wf["outN"]
        else:
            name, badge, bcol = (alias or local_name), "本地", AMBER
            desc = "已从本地加载：%s" % local_name
            steps, inN, outN = [], "—", "—"
        tk.Label(head, text=badge, bg=bcol, fg=DARKTX, font=f(8, True),
                 padx=S(8), pady=S(1)).pack(side="right")
        tk.Label(head, text=name, bg=_INNER, fg=TXT, font=f(10, True), anchor="w",
                 justify="left", wraplength=S(200)).pack(side="left", fill="x", expand=True)

        tk.Label(pad, text=desc, bg=_INNER, fg=TXT2, font=f(9), anchor="w",
                 justify="left", wraplength=S(250)).pack(fill="x", pady=(S(8), S(4)))

        steps_box = tk.Frame(pad, bg=_INNER)
        steps_box.pack(fill="x")
        for i, (node, io) in enumerate(steps, 1):
            r = tk.Frame(steps_box, bg=_INNER)
            r.pack(fill="x", pady=S(3))
            tk.Label(r, text=str(i), bg=bcol, fg=DARKTX, font=f(8, True),
                     width=2, padx=S(4), pady=0).pack(side="left")
            tk.Label(r, text=node, bg=_INNER, fg=TXT, font=f(9), anchor="w").pack(
                side="left", padx=(S(8), 0))
            if io:
                tk.Label(r, text=io, bg=_INNER, fg=TXT2, font=f(8)).pack(side="right")

        n = self.nodes if self.nodes is not None else len(steps)
        meta_row = tk.Frame(pad, bg=_INNER)     # 窄栏放不下 4 项横排 → 2×2 网格
        meta_row.pack(fill="x", pady=(S(10), S(4)))
        meta_row.columnconfigure(0, weight=1)
        meta_row.columnconfigure(1, weight=1)
        for i, (lab, val) in enumerate((("节点", str(n)), ("输入", inN),
                                        ("输出", outN), ("目标", "ComfyUI :8188"))):
            cell = tk.Frame(meta_row, bg=_INNER)
            cell.grid(row=i // 2, column=i % 2, sticky="w", pady=S(2))
            tk.Label(cell, text=lab + " ", bg=_INNER, fg=TXT2, font=f(8)).pack(side="left")
            tk.Label(cell, text=val, bg=_INNER, fg=TXT, font=f(8, True)).pack(side="left")

        acts = tk.Frame(pad, bg=_INNER)
        acts.pack(fill="x", pady=(S(10), 0))
        RButton(acts, text="推送到 ComfyUI", command=lambda: self._push(False),
                fill=TEAL, fg=DARKTX, border=None, outer=_INNER, font=f(9), pady=6,
                stretch=True).pack(side="left", fill="x", expand=True)
        RButton(acts, text="加入队列", command=lambda: self._push(True),
                fill="transparent", fg=TXT, border=BORDER, outer=_INNER,
                font=f(9), pady=6, stretch=True).pack(side="left", fill="x", expand=True,
                                                     padx=(S(8), 0))
        if local_name and self.on_alias:
            # 「备注」（v1.2.14）：挤同一行（三个 stretch 平分），不给卡片加一行高度
            RButton(acts, text="备注", command=self.on_alias,
                    fill="transparent", fg=TXT, border=BORDER, outer=_INNER,
                    font=f(9), pady=6, stretch=True).pack(side="left", fill="x",
                                                          expand=True, padx=(S(8), 0))
        self._changed()

    def _push(self, to_queue):
        if self.on_push:
            self.on_push(to_queue)

    def _changed(self):
        if self.on_change:
            self.on_change()


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
            pass      # 两种 API 都不可用（非 Windows / 老系统）：DPI 感知只是"更清晰"，
                      # 拿不到就用系统默认缩放，绝不能让启动失败在这里


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
        pass      # 个别 Tk 构建/主题里这条调用会失败。字号本身靠磅值交给 Tk 缩放，
                  # 拿不到 scaling 只是比例略不准，不值得为它中断启动
    UIFONT = pick_font()
    try:
        MONOFONT = "Consolas" if "Consolas" in set(tkfont.families()) else "Courier New"
    except Exception:
        MONOFONT = "Courier New"
