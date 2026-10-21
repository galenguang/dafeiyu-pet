# -*- coding: utf-8 -*-
"""ctypes 封装：音量 / 亮度 / 剪贴板 / 窗口 / 截图 / 热键 / 启动程序。

**只做"系统级设置"，不做键鼠模拟。** 唯一例外是音量兜底（COM 拿不到时才
按几下 VK_VOLUME_UP/DOWN），因为那是唯一的退路。不做通用鼠标键盘控制，既是
产品选择也是安全选择——键鼠模拟 + 截屏 + 启动进程是杀软眼里的标准木马三件套。

**每个函数内部都必须有超时或重试上限**。这不是性能优化，是安全要求：合作式
检查点挡不住 C 层阻塞，一个卡死的 ``OpenClipboard`` 能让整个 runner 线程停在
那里，用户只能重启桌宠。

**这个模块不许 import Qt。** 截图走 GDI + 手写 BMP，不用 QImage —— 一来
runner 线程里不该碰 Qt，二来 BMP 头 54 字节，手写比引依赖便宜。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import struct
import time
import uuid

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)


class WinError(RuntimeError):
    """文案已经翻译成人话。"""


# ==========================================================================
# 音量（COM: IAudioEndpointVolume）
# ==========================================================================
_CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
_IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
_IID_IAudioEndpointVolume = "{5CDF2C82-841E-4546-9722-0CF74078229A}"

_CLSCTX_ALL = 23
_eRender, _eMultimedia = 0, 1
# vtable 下标
_VT_GET_DEFAULT_ENDPOINT = 4          # IMMDeviceEnumerator
_VT_ACTIVATE = 3                      # IMMDevice
_VT_GET_VOLUME_SCALAR = 9             # IAudioEndpointVolume
_VT_SET_VOLUME_SCALAR = 7
_VT_SET_MUTE = 14
_VT_GET_MUTE = 15


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    u = uuid.UUID(text.strip("{}"))
    return _GUID(u.time_low, u.time_mid, u.time_hi_version,
                 (ctypes.c_ubyte * 8)(*u.bytes[8:]))


def _vtbl(p, index: int, restype, argtypes, *args):
    vt = ctypes.cast(p, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vt[index])(p, *args)


def _release(p) -> None:
    if p:
        try:
            _vtbl(p, 2, ctypes.c_ulong, [])
        except Exception:
            pass


class _Volume:
    """一次 COM 调用用完就放，不做长连接——COM 对象跨线程太容易出事。"""

    def __init__(self):
        self.enum = None
        self.dev = None
        self.iface = None

    def __enter__(self):
        ole32.CoInitialize(None)
        clsid, iid = _guid(_CLSID_MMDeviceEnumerator), _guid(_IID_IMMDeviceEnumerator)
        p = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, _CLSCTX_ALL,
                                    ctypes.byref(iid), ctypes.byref(p))
        if hr != 0 or not p:
            raise WinError(f"拿不到音频设备枚举器（HRESULT {hr:#x}）")
        self.enum = p
        dev = ctypes.c_void_p()
        hr = _vtbl(self.enum, _VT_GET_DEFAULT_ENDPOINT, ctypes.c_long,
                   [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
                   _eRender, _eMultimedia, ctypes.byref(dev))
        if hr != 0 or not dev:
            raise WinError("没有可用的播放设备")
        self.dev = dev
        iid = _guid(_IID_IAudioEndpointVolume)
        iface = ctypes.c_void_p()
        hr = _vtbl(self.dev, _VT_ACTIVATE, ctypes.c_long,
                   [ctypes.POINTER(_GUID), ctypes.c_uint32, ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_void_p)],
                   ctypes.byref(iid), _CLSCTX_ALL, None, ctypes.byref(iface))
        if hr != 0 or not iface:
            raise WinError("拿不到音量接口")
        self.iface = iface
        return self

    def __exit__(self, *exc):
        _release(self.iface)
        _release(self.dev)
        _release(self.enum)
        try:
            ole32.CoUninitialize()
        except Exception:
            pass
        return False

    def get(self) -> int:
        f = ctypes.c_float()
        hr = _vtbl(self.iface, _VT_GET_VOLUME_SCALAR, ctypes.c_long,
                   [ctypes.POINTER(ctypes.c_float)], ctypes.byref(f))
        if hr != 0:
            raise WinError("读不到当前音量")
        return int(round(f.value * 100))

    def set(self, pct: int) -> None:
        f = ctypes.c_float(max(0.0, min(1.0, pct / 100.0)))
        hr = _vtbl(self.iface, _VT_SET_VOLUME_SCALAR, ctypes.c_long,
                   [ctypes.c_float, ctypes.c_void_p], f, None)
        if hr != 0:
            raise WinError("设置音量失败")

    def get_mute(self) -> bool:
        b = wt.BOOL()
        hr = _vtbl(self.iface, _VT_GET_MUTE, ctypes.c_long,
                   [ctypes.POINTER(wt.BOOL)], ctypes.byref(b))
        return bool(b.value) if hr == 0 else False

    def set_mute(self, on: bool) -> None:
        _vtbl(self.iface, _VT_SET_MUTE, ctypes.c_long,
              [wt.BOOL, ctypes.c_void_p], wt.BOOL(1 if on else 0), None)


def volume_get() -> dict:
    with _Volume() as v:
        return {"volume": v.get(), "muted": v.get_mute()}


def volume_set(pct: int) -> dict:
    pct = max(0, min(100, int(pct)))
    with _Volume() as v:
        v.set(pct)
        return {"volume": v.get(), "muted": v.get_mute()}


# ---- 兜底：COM 不可用时按音量键（唯一的"按键"用法，仅此一处） ----
_VK_VOLUME_MUTE, _VK_VOLUME_DOWN, _VK_VOLUME_UP = 0xAD, 0xAE, 0xAF
_KEYEVENTF_KEYUP = 0x0002


def _press_vk(vk: int, times: int = 1) -> None:
    for _ in range(max(1, min(60, times))):
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)


# ==========================================================================
# 亮度（dxva2 DDC/CI，只对笔记本内屏可靠）
# ==========================================================================
class _PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [("hPhysicalMonitor", wt.HANDLE),
                ("szDescription", wt.WCHAR * 128)]


def _brightness_handle():
    try:
        dxva2 = ctypes.WinDLL("dxva2")
    except OSError:
        raise WinError("这台机器没有 dxva2.dll，调不了亮度") from None
    hmon = user32.MonitorFromPoint(wt.POINT(0, 0), 1)   # MONITOR_DEFAULTTOPRIMARY
    if not hmon:
        raise WinError("找不到主显示器")
    n = wt.DWORD(0)
    if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(n)) or n.value < 1:
        raise WinError("拿不到显示器句柄")
    arr = (_PHYSICAL_MONITOR * n.value)()
    if not dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, n.value, arr):
        raise WinError("拿不到显示器句柄")
    return dxva2, arr[0].hPhysicalMonitor


def brightness_get() -> dict:
    dxva2, h = _brightness_handle()
    lo, cur, hi = wt.DWORD(), wt.DWORD(), wt.DWORD()
    if not dxva2.GetMonitorBrightness(h, ctypes.byref(lo), ctypes.byref(cur),
                                      ctypes.byref(hi)):
        raise WinError("这个显示器不支持软件调亮度（外接屏常见）")
    return {"brightness": int(cur.value), "min": int(lo.value), "max": int(hi.value)}


def brightness_set(pct: int) -> dict:
    pct = max(0, min(100, int(pct)))
    dxva2, h = _brightness_handle()
    lo, cur, hi = wt.DWORD(), wt.DWORD(), wt.DWORD()
    if not dxva2.GetMonitorBrightness(h, ctypes.byref(lo), ctypes.byref(cur),
                                      ctypes.byref(hi)):
        raise WinError("这个显示器不支持软件调亮度（外接屏常见）")
    lo_v, hi_v = int(lo.value), int(hi.value)
    target = lo_v + round((hi_v - lo_v) * pct / 100.0)
    if not dxva2.SetMonitorBrightness(h, wt.DWORD(target)):
        raise WinError("设置亮度失败")
    return {"brightness": pct, "raw": target}


# ==========================================================================
# 锁屏
# ==========================================================================
def lock_screen() -> dict:
    if not user32.LockWorkStation():
        raise WinError("锁屏失败")
    return {"locked": True}


# ==========================================================================
# 剪贴板（重试 10×30ms —— 别的程序可能正占着）
# ==========================================================================
CF_UNICODETEXT = 13


def _open_clipboard(retries: int = 10, gap: float = 0.03) -> None:
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return
        time.sleep(gap)
    raise WinError("剪贴板被别的程序占着，等一会儿再试")


def clipboard_get() -> str:
    _open_clipboard()
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def clipboard_set(text: str) -> dict:
    data = (text or "")
    buf = ctypes.create_unicode_buffer(data, len(data) + 1)
    size = ctypes.sizeof(buf)
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(0x0042, size)      # GMEM_MOVEABLE | GMEM_ZEROINIT
        if not h:
            raise WinError("申请剪贴板内存失败")
        ptr = kernel32.GlobalLock(h)
        ctypes.memmove(ptr, buf, size)
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            raise WinError("写剪贴板失败")
    finally:
        user32.CloseClipboard()
    return {"ok": True, "chars": len(data)}


# ==========================================================================
# 窗口
# ==========================================================================
_WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _own_pid() -> int:
    return os.getpid()


def windows_list(limit: int = 40) -> list[dict]:
    """列出可见的顶层窗口。**不含进程名**——那属于隐私边界。

    返回的 ``id`` 只是本次调用里的序号，配合 ``windows_list`` 的调用顺序使用；
    跨步复用会拿到错误的窗口。
    """
    out: list[dict] = []
    me = _own_pid()

    def cb(hwnd, _lp):
        if len(out) >= limit:
            return False
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        if not title:
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == me:
            return True                              # 跳过我们自己
        rect = wt.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w < 80 or h < 60:
            return True                              # 工具条、隐藏窗
        out.append({"hwnd": int(hwnd), "title": title[:60],
                    "w": int(w), "h": int(h)})
        return True

    user32.EnumWindows(_WNDENUMPROC(cb), 0)
    return out


def window_activate(hwnd: int, wait: float = 0.25) -> dict:
    hwnd = int(hwnd)
    if not user32.IsWindow(hwnd):
        raise WinError("这个窗口已经不存在了")
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                   # SW_RESTORE
    if not user32.SetForegroundWindow(hwnd):
        # 前台锁：先 AttachThreadInput 再试一次
        fg = user32.GetForegroundWindow()
        tid_fg = user32.GetWindowThreadProcessId(fg, None)
        tid_me = kernel32.GetCurrentThreadId()
        if tid_fg and tid_fg != tid_me:
            user32.AttachThreadInput(tid_fg, tid_me, True)
            ok = user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(tid_fg, tid_me, False)
            if not ok:
                raise WinError("系统不让我切到这个窗口")
        else:
            raise WinError("系统不让我切到这个窗口")
    time.sleep(min(1.0, max(0.0, wait)))
    return {"foreground": int(user32.GetForegroundWindow() or 0),
            "ok": int(user32.GetForegroundWindow() or 0) == hwnd}


# ==========================================================================
# 截图（GDI BitBlt → 手写 BMP，不引 Qt/PIL）
# ==========================================================================
_SRCCOPY = 0x00CC0020
_DIB_RGB_COLORS = 0


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


def screenshot_save(path: str, max_side: int = 0) -> dict:
    """把主屏抓下来存成 BMP。**只写盘，永不进上下文。**"""
    user32.SetProcessDPIAware()
    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)
    if w <= 0 or h <= 0:
        raise WinError("拿不到屏幕尺寸")

    hdc = user32.GetDC(None)
    if not hdc:
        raise WinError("拿不到屏幕 DC")
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(mem, 0, 0, w, h, hdc, 0, 0, _SRCCOPY):
            raise WinError("截屏失败")

        bi = _BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bi.biWidth = w
        bi.biHeight = -h                     # 负数 = 从上到下，省得自己翻转
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = _DIB_RGB_COLORS
        size = w * h * 4
        buf = ctypes.create_string_buffer(size)
        if not gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), _DIB_RGB_COLORS):
            raise WinError("读取像素失败")

        # BMP 文件头 + 信息头；高度写正数，配合下面的行翻转
        rows = [buf.raw[i * w * 4:(i + 1) * w * 4] for i in range(h)]
        body = b"".join(reversed(rows))       # BGRA 自下而上
        hdr = struct.pack("<2sIHHI", b"BM", 54 + len(body), 0, 0, 54)
        info = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0, len(body), 2835, 2835, 0, 0)
        with open(path, "wb") as f:
            f.write(hdr + info + body)
        return {"path": path, "w": w, "h": h, "bytes": 54 + len(body)}
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, hdc)


# ==========================================================================
# 启动程序
# ==========================================================================
class _SHELLEXECUTEINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("fMask", ctypes.c_ulong),
                ("hwnd", wt.HWND), ("lpVerb", wt.LPCWSTR),
                ("lpFile", wt.LPCWSTR), ("lpParameters", wt.LPCWSTR),
                ("lpDirectory", wt.LPCWSTR), ("nShow", ctypes.c_int),
                ("hInstApp", wt.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                ("lpClass", wt.LPCWSTR), ("hkeyClass", wt.HKEY),
                ("dwHotKey", wt.DWORD), ("hIcon", wt.HANDLE),
                ("hProcess", wt.HANDLE)]


_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SW_SHOWNORMAL = 1


def shell_execute(target: str, args: str = "", wait: float = 1.5) -> dict:
    """用 ShellExecuteEx 打开程序/文档/网址，拿 pid 并确认进程真的活着。"""
    sei = _SHELLEXECUTEINFO()
    sei.cbSize = ctypes.sizeof(_SHELLEXECUTEINFO)
    sei.fMask = _SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = "open"
    sei.lpFile = target
    sei.lpParameters = args or None
    sei.nShow = _SW_SHOWNORMAL
    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        err = ctypes.get_last_error()
        raise WinError(f"打不开（错误码 {err}）")

    pid = 0
    if sei.hProcess:
        pid = kernel32.GetProcessId(sei.hProcess)
        time.sleep(min(2.0, max(0.0, wait)))
        alive = kernel32.WaitForSingleObject(sei.hProcess, 0) == 0x102  # WAIT_TIMEOUT
        kernel32.CloseHandle(sei.hProcess)
        if not alive:
            raise WinError("程序启动了但立刻退出了，可能路径不对")
    return {"pid": int(pid), "target": target}


# ==========================================================================
# 热键
# ==========================================================================
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
WM_HOTKEY = 0x0312

_VK_MAP = {
    "A": 0x41, "B": 0x42, "C": 0x43, "D": 0x44, "E": 0x45, "F": 0x46,
    "G": 0x47, "H": 0x48, "I": 0x49, "J": 0x4A, "K": 0x4B, "L": 0x4C,
    "M": 0x4D, "N": 0x4E, "O": 0x4F, "P": 0x50, "Q": 0x51, "R": 0x52,
    "S": 0x53, "T": 0x54, "U": 0x55, "V": 0x56, "W": 0x57, "X": 0x58,
    "Y": 0x59, "Z": 0x5A,
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74,
    "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79,
    "F11": 0x7A, "F12": 0x7B,
}


def parse_hotkey(combo: str) -> tuple[int, int]:
    """'Ctrl+Alt+Q' → (modifiers, vk)。解析失败抛 ``WinError``。"""
    mods = 0
    vk = 0
    for part in (combo or "").replace(" ", "").split("+"):
        p = part.strip().upper()
        if not p:
            continue
        if p in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
        elif p == "ALT":
            mods |= MOD_ALT
        elif p in ("SHIFT",):
            mods |= 0x0004
        elif p in ("WIN", "META"):
            mods |= 0x0008
        elif p in _VK_MAP:
            vk = _VK_MAP[p]
        elif len(p) == 1 and p.isalnum():
            vk = ord(p)
    if not vk:
        raise WinError(f"看不懂这个快捷键：{combo}")
    return mods | MOD_NOREPEAT, vk


def register_hotkey(hwnd: int, hotkey_id: int, combo: str) -> tuple[int, int]:
    mods, vk = parse_hotkey(combo)
    if not user32.RegisterHotKey(hwnd, hotkey_id, mods, vk):
        raise WinError(f"{combo} 被别的程序占用了")
    return mods, vk


def unregister_hotkey(hwnd: int, hotkey_id: int) -> None:
    try:
        user32.UnregisterHotKey(hwnd, hotkey_id)
    except Exception:
        pass


# ==========================================================================
# 开始菜单程序索引
# ==========================================================================
_SKIP_LNK = ("卸载", "uninstall", "帮助", "help", "readme", "说明", "文档")


def start_menu_links(limit: int = 400) -> dict[str, str]:
    """扫开始菜单里的 .lnk，返回 ``{小写程序名: 完整路径}``。

    只扫两层，跳过卸载/帮助这类没用的快捷方式。
    """
    roots = [
        os.path.join(os.environ.get("APPDATA", ""),
                     r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(os.environ.get("PROGRAMDATA", ""),
                     r"Microsoft\Windows\Start Menu\Programs"),
    ]
    out: dict[str, str] = {}
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath[len(root):].count(os.sep)
            if depth >= 2:
                dirnames[:] = []
            for fn in filenames:
                if not fn.lower().endswith(".lnk"):
                    continue
                low = fn.lower()
                if any(s in low for s in _SKIP_LNK):
                    continue
                name = os.path.splitext(fn)[0]
                out.setdefault(name.lower(), os.path.join(dirpath, fn))
                if len(out) >= limit:
                    return out
    return out


# ==========================================================================
# 其它
# ==========================================================================
def screen_size() -> tuple[int, int]:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass
    return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


def foreground_title() -> str:
    """当前前台窗口标题。**只在用户明确要求"我在用什么"时才用**，默认不采。"""
    h = user32.GetForegroundWindow()
    if not h:
        return ""
    n = user32.GetWindowTextLengthW(h)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, buf, n + 1)
    return buf.value.strip()[:60]


# ==========================================================================
# 原型声明
# ==========================================================================
# 这一段**必须**有。ctypes 默认把返回值当 32 位 int，而 64 位 Windows 上
# HANDLE / HDC / HWND 都是 64 位指针——不声明 restype 就会被截断成
# "access violation writing 0x0000000000000000"，而且报错点离真正的原因很远。
def _setup_prototypes() -> None:
    P = ctypes.c_void_p
    U = ctypes.c_uint
    I = ctypes.c_int
    L = ctypes.c_long
    D = wt.DWORD

    user32.GetDC.restype = P
    user32.GetDC.argtypes = [P]
    user32.ReleaseDC.argtypes = [P, P]
    user32.GetSystemMetrics.restype = I
    user32.GetSystemMetrics.argtypes = [I]
    user32.MonitorFromPoint.restype = P
    user32.MonitorFromPoint.argtypes = [wt.POINT, D]
    user32.GetForegroundWindow.restype = P
    user32.SetForegroundWindow.restype = wt.BOOL
    user32.SetForegroundWindow.argtypes = [P]
    user32.IsWindow.restype = wt.BOOL
    user32.IsWindow.argtypes = [P]
    user32.IsWindowVisible.restype = wt.BOOL
    user32.IsWindowVisible.argtypes = [P]
    user32.IsIconic.restype = wt.BOOL
    user32.IsIconic.argtypes = [P]
    user32.ShowWindow.restype = wt.BOOL
    user32.ShowWindow.argtypes = [P, I]
    user32.GetWindowTextLengthW.restype = I
    user32.GetWindowTextLengthW.argtypes = [P]
    user32.GetWindowTextW.restype = I
    user32.GetWindowTextW.argtypes = [P, wt.LPWSTR, I]
    user32.GetWindowRect.restype = wt.BOOL
    user32.GetWindowRect.argtypes = [P, ctypes.POINTER(wt.RECT)]
    user32.GetWindowThreadProcessId.restype = D
    user32.GetWindowThreadProcessId.argtypes = [P, ctypes.POINTER(D)]
    user32.EnumWindows.restype = wt.BOOL
    user32.EnumWindows.argtypes = [_WNDENUMPROC, wt.LPARAM]
    user32.AttachThreadInput.restype = wt.BOOL
    user32.AttachThreadInput.argtypes = [D, D, wt.BOOL]
    user32.OpenClipboard.restype = wt.BOOL
    user32.OpenClipboard.argtypes = [P]
    user32.EmptyClipboard.restype = wt.BOOL
    user32.CloseClipboard.restype = wt.BOOL
    user32.GetClipboardData.restype = P
    user32.GetClipboardData.argtypes = [U]
    user32.SetClipboardData.restype = P
    user32.SetClipboardData.argtypes = [U, P]
    user32.LockWorkStation.restype = wt.BOOL
    user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, D, ctypes.c_size_t]
    user32.RegisterHotKey.restype = wt.BOOL
    user32.RegisterHotKey.argtypes = [P, I, U, U]
    user32.UnregisterHotKey.restype = wt.BOOL
    user32.UnregisterHotKey.argtypes = [P, I]

    kernel32.GlobalAlloc.restype = P
    kernel32.GlobalAlloc.argtypes = [U, ctypes.c_size_t]
    kernel32.GlobalLock.restype = P
    kernel32.GlobalLock.argtypes = [P]
    kernel32.GlobalUnlock.restype = wt.BOOL
    kernel32.GlobalUnlock.argtypes = [P]
    kernel32.GetCurrentThreadId.restype = D
    kernel32.GetProcessId.restype = D
    kernel32.GetProcessId.argtypes = [P]
    kernel32.WaitForSingleObject.restype = D
    kernel32.WaitForSingleObject.argtypes = [P, D]
    kernel32.CloseHandle.restype = wt.BOOL
    kernel32.CloseHandle.argtypes = [P]

    gdi32.CreateCompatibleDC.restype = P
    gdi32.CreateCompatibleDC.argtypes = [P]
    gdi32.CreateCompatibleBitmap.restype = P
    gdi32.CreateCompatibleBitmap.argtypes = [P, I, I]
    gdi32.SelectObject.restype = P
    gdi32.SelectObject.argtypes = [P, P]
    gdi32.DeleteObject.restype = wt.BOOL
    gdi32.DeleteObject.argtypes = [P]
    gdi32.DeleteDC.restype = wt.BOOL
    gdi32.DeleteDC.argtypes = [P]
    gdi32.BitBlt.restype = wt.BOOL
    gdi32.BitBlt.argtypes = [P, I, I, I, I, P, I, I, D]
    gdi32.GetDIBits.restype = I
    gdi32.GetDIBits.argtypes = [P, P, U, U, P, P, U]

    shell32.ShellExecuteExW.restype = wt.BOOL
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFO)]

    ole32.CoInitialize.restype = L
    ole32.CoInitialize.argtypes = [P]
    ole32.CoUninitialize.restype = None
    ole32.CoCreateInstance.restype = L
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(_GUID), P, D, ctypes.POINTER(_GUID),
        ctypes.POINTER(P)]


_setup_prototypes()
