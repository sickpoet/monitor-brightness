# -*- coding: utf-8 -*-
"""monitor-brightness —— 极简显示器亮度调节器

通过 DDC/CI 协议直接控制显示器硬件亮度，效果等同于按显示器上的物理按键。
只有一个滑块，调完关掉即可，不常驻、不写注册表、不改系统设置。

仅依赖 Python 标准库（ctypes + tkinter）。
仅支持 Windows。
"""

import ctypes
import sys
from ctypes import wintypes

__version__ = "1.0.0"

# tkinter 在 main() 里才导入。部分 Python 发行版不带 tkinter，
# 延迟导入才能给出明确提示，而不是一上来就抛 ImportError。
tk = None

VCP_BRIGHTNESS = 0x10
SET_DEBOUNCE_MS = 120
WINDOW_TITLE = "显示器亮度"

BG = "#f7f8fa"
FG = "#1f2328"
MUTED = "#8a9099"
DANGER = "#c0392b"
FONT = "Segoe UI"
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


class BrightnessApp:
    def __init__(self, root, controller):
        self.root = root
        self.ctrl = controller
        self._job = None
        self._pending = None
        self._ready = False
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self):
        root = self.root
        root.title(WINDOW_TITLE)
        root.resizable(False, False)
        root.configure(bg=BG)

        outer = tk.Frame(root, bg=BG)
        outer.pack(fill="both", expand=True, padx=22, pady=16)

        self.value_label = tk.Label(
            outer, text="--", font=(FONT, 30), bg=BG, fg=FG)
        self.value_label.pack()

        self.caption = tk.Label(
            outer, text="亮度", font=(FONT_CN, 9), bg=BG, fg=MUTED)
        self.caption.pack(pady=(0, 12))

        self.scale = tk.Scale(
            outer, from_=0, to=100, orient=tk.HORIZONTAL,
            showvalue=False, length=300, width=14, sliderlength=24,
            bd=0, highlightthickness=0, relief=tk.FLAT,
            bg=BG, fg=FG, troughcolor="#e3e6ea",
            activebackground="#2f6fed", repeatdelay=300, repeatinterval=80,
            command=self._on_slide)
        self.scale.pack()

        self.status = tk.Label(
            outer, text="", font=(FONT_CN, 8), bg=BG, fg=MUTED)
        self.status.pack(pady=(12, 0))

        root.bind("<Escape>", lambda _e: self._on_close())
        self._center(364, 186)

    def _center(self, w, h):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        # 取主显示器工作区，避免落到任务栏底下
        x = (sw - w) // 2
        y = int((sh - h) * 0.32)
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))

    # ---- 交互 ----

    def _on_slide(self, raw):
        if not self._ready:
            return
        pct = int(float(raw))
        self._pending = pct
        self.value_label.config(text="%d%%" % pct)
        if self._job is not None:
            self.root.after_cancel(self._job)
        self._job = self.root.after(SET_DEBOUNCE_MS, self._apply)

    def _apply(self):
        self._job = None
        if self._pending is None:
            return
        try:
            self.ctrl.set_percent(self._pending)
            self.status.config(text=self.ctrl.description, fg=MUTED)
        except DdcError as exc:
            self.status.config(text=str(exc).split("\n")[0], fg=DANGER)

    def _on_close(self):
        if self._job is not None:
            self.root.after_cancel(self._job)
            self._job = None
            self._apply()
        self.ctrl.close()
        self.root.destroy()

    def start(self):
        pct = self.ctrl.percent()
        self.scale.set(pct)
        self.value_label.config(text="%d%%" % pct)
        self.status.config(text=self.ctrl.description, fg=MUTED)
        self._pending = pct
        self._ready = True


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
