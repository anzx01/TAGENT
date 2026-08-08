"""
Windows 全局钩子管理器 - 拷贝自 ScreenClaw
WH_MOUSE_LL + WH_KEYBOARD_LL 低级钩子，在守护线程中运行
回调只入队（< 1ms），不阻塞钩子链
"""
import ctypes
import ctypes.wintypes
import queue
import threading
import time

WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MOUSEWHEEL = 0x020A
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0106
WM_SYSKEYUP = 0x0107
WM_QUIT = 0x0012


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", ctypes.c_ulong),
        ("flags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.wintypes.DWORD),
        ("scanCode", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MSGSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.wintypes.HWND),
        ("message", ctypes.wintypes.UINT),
        ("wParam", ctypes.wintypes.WPARAM),
        ("lParam", ctypes.wintypes.LPARAM),
        ("time", ctypes.wintypes.DWORD),
        ("pt", POINT),
    ]


# 64位兼容
_user32 = ctypes.windll.user32
_user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p, ctypes.c_int,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
]
_user32.CallNextHookEx.restype = ctypes.wintypes.LPARAM

HOOKPROC = ctypes.CFUNCTYPE(
    ctypes.wintypes.LPARAM, ctypes.c_int,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
)

# 事件类型
LBDOWN, LBUP, RBDOWN, RBUP, WHEEL, KDOWN, KUP = range(7)


class RawEvent:
    """原始钩子事件"""
    __slots__ = ("event_type", "x", "y", "vk", "scan", "delta", "t_us")

    def __init__(self, event_type, x=0, y=0, vk=0, scan=0, delta=0, t_us=0):
        self.event_type = event_type
        self.x = x
        self.y = y
        self.vk = vk
        self.scan = scan
        self.delta = delta
        self.t_us = t_us


class HookManager:
    """全局钩子管理器"""

    def __init__(self):
        self._queue = queue.Queue()
        self._running = False
        self._start_us = 0
        self._thread_id = 0
        self._my_pid = 0
        self._thread = None
        self._mouse_cb = None
        self._kb_cb = None

    def start(self):
        if self._running:
            return
        self._my_pid = ctypes.windll.kernel32.GetCurrentProcessId()
        self._start_us = int(time.time() * 1_000_000)
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="Hook")
        self._thread.start()

    def stop(self):
        if not self._running:
            return
        self._running = False
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=3.0)

    def get_event(self, timeout=0.01):
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _loop(self):
        u32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        self._mouse_cb = HOOKPROC(self._mouse_proc)
        self._kb_cb = HOOKPROC(self._kb_proc)
        mouse_hook = u32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_cb, None, 0)
        kb_hook = u32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_cb, None, 0)
        msg = MSGSTRUCT()
        while self._running:
            ret = u32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
        if mouse_hook:
            u32.UnhookWindowsHookEx(mouse_hook)
        if kb_hook:
            u32.UnhookWindowsHookEx(kb_hook)

    def _is_my_window(self, sx, sy):
        """过滤自身窗口上的事件"""
        try:
            hwnd = ctypes.windll.user32.WindowFromPoint(POINT(sx, sy))
            if hwnd:
                pid = ctypes.wintypes.DWORD()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                return pid.value == self._my_pid
        except Exception:
            pass
        return False

    def _mouse_proc(self, nCode, wParam, lParam):
        try:
            if nCode >= 0 and self._running:
                mhs = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                sx, sy = mhs.pt.x, mhs.pt.y
                if self._is_my_window(sx, sy):
                    return _user32.CallNextHookEx(None, nCode, wParam, lParam)
                et = None
                delta = 0
                if wParam == WM_LBUTTONDOWN:
                    et = LBDOWN
                elif wParam == WM_LBUTTONUP:
                    et = LBUP
                elif wParam == WM_RBUTTONDOWN:
                    et = RBDOWN
                elif wParam == WM_RBUTTONUP:
                    et = RBUP
                elif wParam == WM_MOUSEWHEEL:
                    et = WHEEL
                    delta = ctypes.c_short(mhs.mouseData >> 16).value
                if et is not None:
                    now = int(time.time() * 1_000_000) - self._start_us
                    self._queue.put(RawEvent(et, x=sx, y=sy, delta=delta, t_us=now), block=False)
        except Exception:
            pass
        return _user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _kb_proc(self, nCode, wParam, lParam):
        try:
            if nCode >= 0 and self._running:
                khs = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                et = None
                if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    et = KDOWN
                elif wParam in (WM_KEYUP, WM_SYSKEYUP):
                    et = KUP
                if et is not None:
                    now = int(time.time() * 1_000_000) - self._start_us
                    self._queue.put(RawEvent(et, vk=khs.vkCode, scan=khs.scanCode, t_us=now), block=False)
        except Exception:
            pass
        return _user32.CallNextHookEx(None, nCode, wParam, lParam)
