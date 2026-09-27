# -*- coding: utf-8 -*-
"""monitor-brightness —— 赛博朋克风显示器亮度调节器

通过 DDC/CI 协议直接控制显示器硬件亮度，效果等同于按显示器上的物理按键。
只有一个滑块，调完关掉即可，不常驻、不写注册表、不改系统设置。

界面全部用 Canvas 手绘，标题栏也是自绘的：Windows 10 不允许改系统标题栏
颜色（DWMWA_CAPTION_COLOR 是 Win11 才有的），留着系统标题栏会和霓虹配色打架。

仅依赖 Python 标准库（ctypes + tkinter）。
仅支持 Windows。
"""

import ctypes
import sys
from ctypes import wintypes

__version__ = "1.1.0"

# tkinter 在 main() 里才导入。部分 Python 发行版不带 tkinter，
# 延迟导入才能给出明确提示，而不是一上来就抛 ImportError。
tk = None

VCP_BRIGHTNESS = 0x10
SET_DEBOUNCE_MS = 120
WINDOW_TITLE = "显示器亮度"

# ---- 赛博朋克配色 ----
# 近黑底 + 霓虹青主色 + 洋红强调。深浅三层同色用来叠出发光感，
# 因为 tkinter 的 Canvas 没有模糊/阴影，只能靠偏移叠色模拟。
BG = "#05070d"           # 窗口底色，近黑
TITLEBAR_BG = "#080e15"  # 标题栏底色，比内容区略亮一档
GRID = "#0c1f28"         # 背景网格
EDGE = "#12414f"         # 硬边框描边
CYAN = "#00e5ff"         # 主霓虹青
CYAN_MID = "#0a7c8c"     # 青的中亮层（辉光内圈）
CYAN_DIM = "#0b3b46"     # 青的暗层（辉光外圈）
CYAN_HINT = "#2b6577"    # 提示文字，比 CYAN_DIM 亮一档但低于正文
SCAN_LINE = "#0e4c5a"    # 扫描线主体
SCAN_TAIL = "#082b33"    # 扫描线尾迹
MAGENTA = "#ff2d95"      # 霓虹洋红
MAGENTA_DIM = "#4d0f31"  # 洋红的暗层（滑块光晕 / 拖动残影）
YELLOW = "#fcee0a"       # 警示黄
FG = "#cdeff7"           # 主文字，近白偏青
MUTED = "#5c7d88"        # 次要文字
TRACK_BG = "#0d1f29"     # 轨道空槽
DANGER = "#ff3b5c"       # 报错

FONT = "Consolas"            # 数字与英文，等宽更有终端味
FONT_CN = "Microsoft YaHei UI"

DWORD = wintypes.DWORD
BOOL = wintypes.BOOL
HANDLE = wintypes.HANDLE
LPARAM = wintypes.LPARAM
LPDWORD = ctypes.POINTER(DWORD)
HMONITOR = HANDLE


class DdcError(RuntimeError):
    """DDC/CI 通信或显示器不支持时抛出。"""


class PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [
        ("hPhysicalMonitor", HANDLE),
        ("szPhysicalMonitorDescription", ctypes.c_wchar * 128),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", DWORD),
        ("szDevice", ctypes.c_wchar * 32),
    ]


MONITORENUMPROC = ctypes.WINFUNCTYPE(
    BOOL, HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), LPARAM)


class _Api:
    """绑定 dxva2 / user32 的函数原型。"""

    def __init__(self):
        try:
            self.dxva2 = ctypes.WinDLL("dxva2")
            self.user32 = ctypes.windll.user32
        except OSError as exc:
            raise DdcError("无法加载 dxva2.dll：%s" % exc) from exc
        self._bind()

    def _bind(self):
        u = self.user32
        d = self.dxva2

        u.EnumDisplayMonitors.argtypes = [
            wintypes.HDC, ctypes.POINTER(RECT), MONITORENUMPROC, LPARAM]
        u.EnumDisplayMonitors.restype = BOOL

        d.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [HMONITOR, LPDWORD]
        d.GetNumberOfPhysicalMonitorsFromHMONITOR.restype = BOOL

        d.GetPhysicalMonitorsFromHMONITOR.argtypes = [
            HMONITOR, DWORD, ctypes.POINTER(PHYSICAL_MONITOR)]
        d.GetPhysicalMonitorsFromHMONITOR.restype = BOOL

        d.DestroyPhysicalMonitors.argtypes = [
            DWORD, ctypes.POINTER(PHYSICAL_MONITOR)]
        d.DestroyPhysicalMonitors.restype = BOOL

        d.GetMonitorBrightness.argtypes = [HANDLE, LPDWORD, LPDWORD, LPDWORD]
        d.GetMonitorBrightness.restype = BOOL

        d.SetMonitorBrightness.argtypes = [HANDLE, DWORD]
        d.SetMonitorBrightness.restype = BOOL

        d.GetVCPFeatureAndVCPFeatureReply.argtypes = [
            HANDLE, ctypes.c_ubyte, ctypes.c_void_p, LPDWORD, LPDWORD]
        d.GetVCPFeatureAndVCPFeatureReply.restype = BOOL


class BrightnessController:
    """绑定一个物理显示器，对外提供 0-100 的百分比读写。"""

    def __init__(self, index=0):
        self._api = _Api()
        self._array = None
        self._count = 0
        self._handle = None
        self._description = "显示器"
        self._raw_min = 0
        self._raw_max = 100
        self._raw_cur = 100
        self._open(index)

    # ---- 内部 ----

    def _enum_hmonitors(self):
        found = []

        def _cb(hmon, hdc, lprect, lparam):
            found.append(hmon)
            return True

        proc = MONITORENUMPROC(_cb)
        self._api.user32.EnumDisplayMonitors(None, None, proc, 0)
        del proc
        return found

    def _open(self, index):
        hmons = self._enum_hmonitors()
        if not hmons:
            raise DdcError("未检测到任何显示器。")
        hmon = hmons[min(max(index, 0), len(hmons) - 1)]

        count = DWORD(0)
        if not self._api.dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(
                hmon, ctypes.byref(count)) or count.value == 0:
            raise DdcError("无法获取显示器的物理句柄。")

        arr = (PHYSICAL_MONITOR * count.value)()
        if not self._api.dxva2.GetPhysicalMonitorsFromHMONITOR(
                hmon, count.value,
                ctypes.cast(arr, ctypes.POINTER(PHYSICAL_MONITOR))):
            raise DdcError("读取显示器信息失败。")

        self._array = arr
        self._count = count.value
        pm = arr[0]
        self._handle = pm.hPhysicalMonitor
        if pm.szPhysicalMonitorDescription:
            self._description = pm.szPhysicalMonitorDescription

        raw_min = DWORD(0)
        raw_cur = DWORD(0)
        raw_max = DWORD(0)
        if not self._api.dxva2.GetMonitorBrightness(
                self._handle, ctypes.byref(raw_min),
                ctypes.byref(raw_cur), ctypes.byref(raw_max)):
            self.close()
            raise DdcError(
                "这台显示器没有响应 DDC/CI 亮度查询。\n\n"
                "请先在显示器自带的 OSD 菜单里找到 DDC/CI 选项并开启，"
                "然后再运行本程序。")

        self._raw_min = raw_min.value
        self._raw_max = raw_max.value
        self._raw_cur = raw_cur.value
        if self._raw_max <= self._raw_min:
            self._raw_max = self._raw_min + 100

    # ---- 对外 ----

    @property
    def description(self):
        return self._description

    def percent(self):
        """当前亮度，0-100。"""
        span = self._raw_max - self._raw_min
        if span <= 0:
            return 0
        return int(round(100.0 * (self._raw_cur - self._raw_min) / span))

    def set_percent(self, pct):
        """写入亮度，0-100。超范围自动夹紧。"""
        pct = max(0, min(100, int(pct)))
        span = self._raw_max - self._raw_min
        raw = self._raw_min + int(round(span * pct / 100.0))
        if not self._api.dxva2.SetMonitorBrightness(self._handle, raw):
            raise DdcError("写入亮度失败，显示器可能拒绝了这次请求。")
        self._raw_cur = raw

    def close(self):
        if self._array is not None and self._count:
            try:
                self._api.dxva2.DestroyPhysicalMonitors(
                    self._count,
                    ctypes.cast(self._array,
                                ctypes.POINTER(PHYSICAL_MONITOR)))
            except Exception:
                pass
        self._array = None
        self._count = 0
        self._handle = None


# 窗口与画布尺寸。界面全部用 Canvas 手绘，所以这些是唯一的布局基准。
CANVAS_W = 430
CANVAS_H = 258
TITLEBAR_H = 30
PAD_X = 34
TRACK_X0 = PAD_X
TRACK_X1 = CANVAS_W - PAD_X
TRACK_Y = 184
READOUT_Y = 96
SEGMENTS = 28
SCAN_STEP_MS = 40          # 扫描线每隔多久走一步
SCAN_STEP_PX = 2           # 每步位移
TRAIL_MS = 280             # 拖动残影存活时间
MAX_TRAILS = 7             # 最多同时存在几个残影


def _mix(c1, c2, t):
    """两个 #rrggbb 之间线性插值，t 取 0-1。"""
    a = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(
        int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


class BrightnessApp:
    """赛博朋克风格的单滑块界面。

    外观全部由 Canvas 手绘，窗口用 overrideredirect 去掉系统边框，
    标题栏自己也画一个（可拖动、带关闭按钮）。

    行为与旧版一致：拖动只更新画面，停手 120ms 后才写一次硬件。
    """

    # 先画偏移的暗色，再画中心亮色，叠出发光感
    GLOW = ((2, 0, CYAN_DIM), (-2, 0, CYAN_DIM),
            (0, 2, CYAN_DIM), (0, -2, CYAN_DIM),
            (1, 0, CYAN_MID), (-1, 0, CYAN_MID),
            (0, 1, CYAN_MID), (0, -1, CYAN_MID))

    def __init__(self, root, controller):
        self.root = root
        self.ctrl = controller
        self._job = None
        self._pending = None
        self._ready = False
        self._dragging = False
        self._move_anchor = None
        self._hover_close = False
        self._scan_job = None
        self._trails = []
        self._build()
        # 无边框窗口没有系统关闭按钮，但 Alt+F4 / 任务管理器关闭仍会走到这里，
        # 保证退出前把待写的亮度刷下去、并把 DDC 句柄释放掉。
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- 构建 ----

    def _build(self):
        import tkinter.font as tkfont

        root = self.root
        root.title(WINDOW_TITLE)
        root.resizable(False, False)
        root.configure(bg=BG)
        # 去掉系统标题栏与边框，标题栏自绘（Win10 改不了系统标题栏颜色）
        root.overrideredirect(True)

        self.canvas = tk.Canvas(root, width=CANVAS_W, height=CANVAS_H,
                                bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack()

        self.num_font = tkfont.Font(family=FONT, size=42, weight="bold")
        self.pct_font = tkfont.Font(family=FONT, size=15)
        self.tiny_font = tkfont.Font(family=FONT, size=8)
        self.cn_font = tkfont.Font(family=FONT_CN, size=8)

        self._draw_titlebar()
        self._draw_frame()
        self._draw_scanline()
        self._draw_readout()
        self._draw_scale()

        c = self.canvas
        c.bind("<Button-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Motion>", self._on_motion)
        root.bind("<Escape>", lambda _e: self._on_close())
        for seq, delta in (("<Left>", -1), ("<Right>", 1),
                           ("<Down>", -1), ("<Up>", 1),
                           ("<Prior>", 10), ("<Next>", -10)):
            root.bind(seq, lambda _e, d=delta: self._nudge(d))

        root.update_idletasks()
        self._center(CANVAS_W, CANVAS_H)

    def _glow_text(self, x, y, text, font, color, anchor="center"):
        """画一段带辉光的文字。

        返回 [(item_id, dx, dy), ...]，带上偏移量是为了之后能整体挪位
        （读数位数变化时要重新居中）。
        """
        items = []
        for dx, dy, col in self.GLOW:
            items.append((self.canvas.create_text(
                x + dx, y + dy, text=text, font=font,
                fill=col, anchor=anchor), dx, dy))
        items.append((self.canvas.create_text(
            x, y, text=text, font=font, fill=color, anchor=anchor), 0, 0))
        return items

    def _draw_titlebar(self):
        c = self.canvas
        mid = TITLEBAR_H // 2

        c.create_rectangle(0, 0, CANVAS_W, TITLEBAR_H,
                           fill=TITLEBAR_BG, outline="")

        # 顶部渐变条：青 -> 洋红
        steps = 60
        for i in range(steps):
            ax = CANVAS_W * i / float(steps)
            bx = CANVAS_W * (i + 1) / float(steps)
            c.create_rectangle(ax, 0, bx, 3, outline="",
                               fill=_mix(CYAN, MAGENTA, i / float(steps - 1)))

        # 左边：装饰块 + 名称
        c.create_text(PAD_X, mid, text="▌", anchor="w",
                      font=self.tiny_font, fill=MAGENTA)
        c.create_text(PAD_X + 13, mid, text=WINDOW_TITLE, anchor="w",
                      font=self.cn_font, fill=CYAN)

        # 右边：DDC/CI 状态灯，再往右是关闭按钮
        tag = "DDC/CI"
        tag_w = self.tiny_font.measure(tag)
        tag_x = CANVAS_W - 44 - tag_w
        c.create_text(tag_x, mid, text=tag, anchor="w",
                      font=self.tiny_font, fill=MUTED)
        c.create_rectangle(tag_x - 14, mid - 2, tag_x - 10, mid + 2,
                           fill=YELLOW, outline="")

        # 关闭按钮：默认暗青描边，鼠标移上去整块变洋红
        bx0, by0, bx1, by1 = CANVAS_W - 30, 4, CANVAS_W - 6, 26
        self._close_rect = (bx0, by0, bx1, by1)
        self._close_box = c.create_rectangle(bx0, by0, bx1, by1,
                                             outline=EDGE, fill="")
        self._close_x1 = c.create_line(bx0 + 6, by0 + 6, bx1 - 6, by1 - 6,
                                       fill=MUTED)
        self._close_x2 = c.create_line(bx1 - 6, by0 + 6, bx0 + 6, by1 - 6,
                                       fill=MUTED)

        # 标题栏下沿
        c.create_line(0, TITLEBAR_H, CANVAS_W, TITLEBAR_H, fill=MAGENTA_DIM)

    def _draw_frame(self):
        c = self.canvas
        top = TITLEBAR_H

        # 内容区网格
        for x in range(0, CANVAS_W + 1, 26):
            c.create_line(x, top, x, CANVAS_H, fill=GRID)
        for y in range(top, CANVAS_H + 1, 26):
            c.create_line(0, y, CANVAS_W, y, fill=GRID)

        # 内容区四角直角括号
        corner = 15
        for px, py, sx, sy in ((3, top + 3, 1, 1),
                               (CANVAS_W - 3, top + 3, -1, 1),
                               (3, CANVAS_H - 3, 1, -1),
                               (CANVAS_W - 3, CANVAS_H - 3, -1, -1)):
            c.create_line(px, py, px + sx * corner, py, fill=CYAN)
            c.create_line(px, py, px, py + sy * corner, fill=CYAN)

        # 最外圈描边，补上被 overrideredirect 去掉的系统边框
        c.create_rectangle(0, 0, CANVAS_W - 1, CANVAS_H - 1,
                           outline=EDGE, fill="")

    def _draw_scanline(self):
        # 扫描线：一条亮线 + 一条更暗的尾迹，在网格之上、内容之下
        self._scan_y = TITLEBAR_H + 8
        self._scan_tail = self.canvas.create_line(
            0, self._scan_y - 4, CANVAS_W, self._scan_y - 4, fill=SCAN_TAIL)
        self._scan = self.canvas.create_line(
            0, self._scan_y, CANVAS_W, self._scan_y, fill=SCAN_LINE)

    def _draw_readout(self):
        # 读数（数字 + %）作为整块水平居中，具体位置由 _set_value 按位数重算
        self._pct_w = self.pct_font.measure("%")
        self._gap = 5
        self.num_items = self._glow_text(0, READOUT_Y, "100", self.num_font,
                                         CYAN, anchor="w")
        self.pct_items = self._glow_text(0, READOUT_Y + 16, "%",
                                         self.pct_font, MAGENTA, anchor="w")

        self.canvas.create_text(CANVAS_W / 2.0, READOUT_Y + 40, fill=MUTED,
                                font=self.tiny_font, text="B R I G H T N E S S")

    def _draw_scale(self):
        c = self.canvas
        span = TRACK_X1 - TRACK_X0

        # 刻度：每 10% 一根，0/50/100 加长
        for i in range(0, 11):
            x = TRACK_X0 + span * i / 10.0
            long = i % 5 == 0
            top = TRACK_Y - 26 if long else TRACK_Y - 22
            c.create_line(x, top, x, TRACK_Y - 15,
                          fill=CYAN if long else CYAN_DIM)

        # 轨道外壳
        c.create_rectangle(TRACK_X0 - 5, TRACK_Y - 10,
                           TRACK_X1 + 5, TRACK_Y + 10,
                           outline=EDGE, fill=TRACK_BG)

        # 已填充段：颜色按整条轨道预先铺好渐变，更新时只改坐标，省开销
        self._segs = []
        for i in range(SEGMENTS):
            self._segs.append(c.create_rectangle(
                0, 0, 0, 0, outline="",
                fill=_mix(CYAN, MAGENTA, i / (SEGMENTS - 1.0))))

        # 滑块：外层光晕 + 主体
        self._thumb_glow = c.create_rectangle(0, 0, 0, 0,
                                              fill=MAGENTA_DIM, outline="")
        self._thumb = c.create_rectangle(0, 0, 0, 0,
                                         fill=MAGENTA, outline="")

        # 底部状态与快捷键提示
        self.status = c.create_text(CANVAS_W / 2.0, TRACK_Y + 36, text="",
                                    font=self.cn_font, fill=MUTED)
        c.create_text(CANVAS_W / 2.0, TRACK_Y + 58, font=self.cn_font,
                      fill=CYAN_HINT,
                      text="← → 微调  ·  PgUp / PgDn ±10  ·  Esc 退出")

    def _win_pos(self):
        """窗口左上角的屏幕坐标。

        overrideredirect 窗口在 Windows 上 winfo_x / winfo_y 常年返回 0，
        拖动窗口时必须以 Win32 报的实际矩形为准。
        """
        try:
            hwnd = ctypes.windll.user32.GetAncestor(self.root.winfo_id(), 2)
            rc = RECT()
            if hwnd and ctypes.windll.user32.GetWindowRect(
                    hwnd, ctypes.byref(rc)):
                return rc.left, rc.top
        except Exception:
            pass
        return self.root.winfo_x(), self.root.winfo_y()

    def _move_window(self, x, y):
        """移动窗口。优先走 Win32，Tk 的 geometry 在无边框窗口下不稳。"""
        try:
            hwnd = ctypes.windll.user32.GetAncestor(self.root.winfo_id(), 2)
            if hwnd:
                SWP_NOSIZE, SWP_NOZORDER = 0x0001, 0x0004
                ctypes.windll.user32.SetWindowPos(
                    hwnd, 0, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER)
                return
        except Exception:
            pass
        self.root.geometry("+%d+%d" % (x, y))

    def _raise_window(self):
        """把窗口提到最前并抢焦点。

        overrideredirect 窗口既不会自动置顶，也不会自动拿到键盘焦点，
        不显式做这一步的话，启动后可能被别的窗口盖住、快捷键也按不动。
        """
        try:
            hwnd = ctypes.windll.user32.GetAncestor(self.root.winfo_id(), 2)
            if not hwnd:
                return
            HWND_TOP, SWP_NOMOVE, SWP_NOSIZE = 0, 0x0002, 0x0001
            ctypes.windll.user32.SetWindowPos(
                hwnd, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def _center(self, w, h):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = int((sh - h) * 0.32)
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.root.update_idletasks()
        # overrideredirect 会把首次定位吞掉（窗口跑回左上角），再摆一次
        self._move_window(x, y)

    # ---- 画面更新 ----

    def _set_value(self, pct):
        text = "%d" % pct
        num_w = self.num_font.measure(text)
        x0 = (CANVAS_W - (num_w + self._gap + self._pct_w)) / 2.0
        for item, dx, dy in self.num_items:
            self.canvas.itemconfigure(item, text=text)
            self.canvas.coords(item, x0 + dx, READOUT_Y + dy)
        px = x0 + num_w + self._gap
        for item, dx, dy in self.pct_items:
            self.canvas.coords(item, px + dx, READOUT_Y + 16 + dy)
        self._set_track(pct)

    def _set_track(self, pct):
        c = self.canvas
        span = float(TRACK_X1 - TRACK_X0)
        filled = span * pct / 100.0
        seg_w = span / SEGMENTS

        for i, item in enumerate(self._segs):
            sx = TRACK_X0 + seg_w * i
            if sx >= TRACK_X0 + filled - 0.5:
                c.coords(item, 0, 0, 0, 0)
                continue
            ex = min(sx + seg_w, TRACK_X0 + filled)
            c.coords(item, sx, TRACK_Y - 7, ex, TRACK_Y + 7)

        tx = TRACK_X0 + filled
        c.coords(self._thumb_glow, tx - 8, TRACK_Y - 17, tx + 8, TRACK_Y + 17)
        c.coords(self._thumb, tx - 3, TRACK_Y - 15, tx + 3, TRACK_Y + 15)

    def _set_status(self, text, color):
        self.canvas.itemconfigure(self.status, text=text, fill=color)

    # ---- 动效 ----

    def _start_scan(self):
        if self._scan_job is None:
            self._tick_scan()

    def _tick_scan(self):
        self._scan_y += SCAN_STEP_PX
        if self._scan_y > CANVAS_H - 4:
            self._scan_y = TITLEBAR_H + 8
        self.canvas.coords(self._scan, 0, self._scan_y,
                           CANVAS_W, self._scan_y)
        self.canvas.coords(self._scan_tail, 0, self._scan_y - 4,
                           CANVAS_W, self._scan_y - 4)
        self._scan_job = self.root.after(SCAN_STEP_MS, self._tick_scan)

    def _spawn_trail(self, x):
        """拖动时在滑块走过的位置留一道残影，自己会淡掉。"""
        item = self.canvas.create_rectangle(
            x - 2, TRACK_Y - 13, x + 2, TRACK_Y + 13,
            fill=MAGENTA_DIM, outline="")
        self.canvas.tag_lower(item, self._thumb_glow)
        self._trails.append(item)
        while len(self._trails) > MAX_TRAILS:
            self.canvas.delete(self._trails.pop(0))
        self.root.after(TRAIL_MS, lambda: self._drop_trail(item))

    def _drop_trail(self, item):
        try:
            self.canvas.delete(item)
        except Exception:
            pass
        if item in self._trails:
            self._trails.remove(item)

    # ---- 交互 ----

    def _in_close_button(self, x, y):
        bx0, by0, bx1, by1 = self._close_rect
        return bx0 <= x <= bx1 and by0 <= y <= by1

    def _seek(self, x, trail=False):
        span = float(TRACK_X1 - TRACK_X0)
        pct = int(round(max(0.0, min(100.0, (x - TRACK_X0) / span * 100.0))))
        self._pending = pct
        self._set_value(pct)
        if trail:
            self._spawn_trail(TRACK_X0 + span * pct / 100.0)
        self._schedule()

    def _nudge(self, delta):
        if not self._ready:
            return
        base = self.ctrl.percent() if self._pending is None else self._pending
        self._pending = max(0, min(100, base + delta))
        self._set_value(self._pending)
        self._schedule()

    def _on_press(self, event):
        # 标题栏：拖动窗口，或者点关闭
        if event.y <= TITLEBAR_H:
            if self._in_close_button(event.x, event.y):
                self._on_close()
                return
            wx, wy = self._win_pos()
            self._move_anchor = (event.x_root - wx, event.y_root - wy)
            return
        if not self._ready or abs(event.y - TRACK_Y) > 34:
            return                       # 只在滑块带内响应，避免点别处就改亮度
        self._dragging = True
        self._seek(event.x)

    def _on_drag(self, event):
        if self._move_anchor is not None:
            dx, dy = self._move_anchor
            self._move_window(event.x_root - dx, event.y_root - dy)
            return
        if self._dragging:
            self._seek(event.x, trail=True)

    def _on_release(self, event):
        self._move_anchor = None
        if self._dragging:
            self._dragging = False
            self._seek(event.x)

    def _on_motion(self, event):
        over = self._in_close_button(event.x, event.y)
        if over != self._hover_close:
            self._hover_close = over
            color = MAGENTA if over else MUTED
            self.canvas.itemconfigure(self._close_box,
                                      outline=MAGENTA if over else EDGE)
            self.canvas.itemconfigure(self._close_x1, fill=color)
            self.canvas.itemconfigure(self._close_x2, fill=color)
            self.canvas.configure(cursor="hand2" if over else "")

    def _schedule(self):
        if self._job is not None:
            self.root.after_cancel(self._job)
        self._job = self.root.after(SET_DEBOUNCE_MS, self._apply)

    def _apply(self):
        self._job = None
        if self._pending is None:
            return
        try:
            self.ctrl.set_percent(self._pending)
            self._set_status(self.ctrl.description, MUTED)
        except DdcError as exc:
            self._set_status(str(exc).split("\n")[0], DANGER)

    def _on_close(self):
        if self._job is not None:
            self.root.after_cancel(self._job)
            self._job = None
            self._apply()
        if self._scan_job is not None:
            self.root.after_cancel(self._scan_job)
            self._scan_job = None
        self.ctrl.close()
        self.root.destroy()

    def start(self):
        pct = self.ctrl.percent()
        self._pending = pct
        self._set_value(pct)
        self._set_status(self.ctrl.description, MUTED)
        self._ready = True
        self._start_scan()
        # 无边框窗口不会自动置顶、也不会自动拿焦点，这两步得自己来
        self._raise_window()
        try:
            self.root.focus_force()
        except Exception:
            pass


def _enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _show_error(text):
    """用系统原生消息框报错，不依赖 tkinter。"""
    try:
        ctypes.windll.user32.MessageBoxW(None, str(text), WINDOW_TITLE, 0x10)
    except Exception:
        print(text)


def main():
    global tk

    if sys.platform != "win32":
        print("仅支持 Windows。")
        return 1

    try:
        import tkinter as _tk
    except ImportError:
        _show_error(
            "当前 Python 没有 tkinter，无法显示界面。\n\n"
            "请改用带 tkinter 的 Python 运行，或直接双击 运行.bat。")
        return 1
    tk = _tk

    _enable_dpi_awareness()

    try:
        controller = BrightnessController()
    except DdcError as exc:
        _show_error(str(exc))
        return 1

    root = tk.Tk()
    app = BrightnessApp(root, controller)
    app.start()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
