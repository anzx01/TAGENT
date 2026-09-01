"""验收测试共享辅助：记事本环境 + 录制/回放/验证（参考 test_e2e_notepad_full）"""
import time, json, ctypes, subprocess, sys, os, shutil, urllib.request, win32gui, win32con
import tempfile
from pathlib import Path

BASE_URL = "http://127.0.0.1:18899"

def post(url, data=b'{}'):
    req = urllib.request.Request(url, method='POST',
                                 headers={'Content-Type': 'application/json'}, data=data)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

def put(url, data=b'{}'):
    req = urllib.request.Request(url, method='PUT',
                                 headers={'Content-Type': 'application/json'}, data=data)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=10).read())

def type_text(text):
    u32 = ctypes.windll.user32
    for char in text:
        vk_scan = u32.VkKeyScanW(ord(char))
        vk = vk_scan & 0xFF
        shift = (vk_scan >> 8) & 0x01
        scan = u32.MapVirtualKeyW(vk, 0)
        if shift:
            u32.keybd_event(0x10, 0, 0, 0)
        u32.keybd_event(vk, scan, 0, 0)
        u32.keybd_event(vk, scan, 0x0002, 0)
        if shift:
            u32.keybd_event(0x10, 0, 0x0002, 0)
        time.sleep(0.05)

def press_key(vk):
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    ctypes.windll.user32.keybd_event(vk, scan, 0, 0)
    time.sleep(0.02)
    ctypes.windll.user32.keybd_event(vk, scan, 2, 0)

def bring_to_front(hwnd):
    u32 = ctypes.windll.user32
    u32.keybd_event(0x12, 0, 0, 0)
    u32.keybd_event(0x12, 0, 0x0002, 0)
    u32.ShowWindow(hwnd, 9)
    u32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    u32.keybd_event(0x12, 0, 0x0002, 0)

def ensure_foreground(hwnd, retries=6, gap=0.5):
    for _ in range(retries):
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        bring_to_front(hwnd)
        time.sleep(gap)
    return win32gui.GetForegroundWindow() == hwnd

def find_notepad():
    for title in ("无标题 - Notepad", "Untitled - Notepad"):
        h = win32gui.FindWindow(None, title)
        if h:
            return h
    found = []
    def cb(h, l):
        try:
            t = win32gui.GetWindowText(h)
            if t and t.endswith(" - Notepad"):
                l.append(h)
        except:
            pass
        return True
    win32gui.EnumWindows(cb, found)
    if found:
        return found[0]
    return win32gui.FindWindow("Notepad", None)

def get_notepad_text(hwnd):
    try:
        import uiautomation as ua
        focused = ua.GetFocusedControl()
        if focused and focused.ControlTypeName in ("EditControl", "DocumentControl", "TextControl"):
            try:
                return focused.GetValuePattern().Value or ""
            except:
                pass
        root = ua.ControlFromHandle(hwnd)
        if root:
            edit = root.EditControl(searchDepth=10)
            if edit.Exists(1.0):
                try:
                    return edit.GetValuePattern().Value or ""
                except:
                    pass
    except:
        pass
    children = []
    def cb(h, l):
        children.append(h)
        return True
    win32gui.EnumChildWindows(hwnd, cb, None)
    for ch in children:
        cls = win32gui.GetClassName(ch)
        if "edit" in cls.lower() or "rich" in cls.lower():
            buf = ctypes.create_unicode_buffer(8192)
            ctypes.windll.user32.SendMessageW(ch, win32con.WM_GETTEXT, 8192, buf)
            return buf.value
    return ""

def wait_for_replay(timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            s = get(f"{BASE_URL}/replay/status")
            if not s.get("playing"):
                return True
        except:
            pass
    return False

def cleanup_notepad():
    subprocess.run(["taskkill", "/IM", "Notepad.exe", "/F", "/T"], capture_output=True)
    subprocess.run(["taskkill", "/IM", "Notepad.exe", "/F", "/T"], capture_output=True)
    time.sleep(1)
    pkg_root = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages",
                            "Microsoft.WindowsNotepad_8wekyb3d8bbwe", "LocalState")
    for sub in ("TabState", "WindowState"):
        d = os.path.join(pkg_root, sub)
        if os.path.isdir(d):
            for f in os.listdir(d):
                try:
                    p = os.path.join(d, f)
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        os.remove(p)
                except Exception:
                    pass

def open_fresh_notepad():
    fname = f"tagent_acc_{os.getpid()}_{int(time.time())}.txt"
    fpath = os.path.join(tempfile.gettempdir(), fname)
    with open(fpath, "w", encoding="utf-8"):
        pass
    subprocess.Popen(["notepad.exe", fpath])
    time.sleep(2.5)
    hwnd = find_notepad()
    if hwnd:
        press_key(0x1B)
        time.sleep(0.3)
        ensure_foreground(hwnd)
    return hwnd

def set_clipboard(text):
    import win32clipboard
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
            win32clipboard.CloseClipboard()
            return True
        except Exception:
            win32clipboard.CloseClipboard()
            time.sleep(0.3)
    return False

def replay_and_verify(rec_id, hwnd, expected):
    # Normalize restored Notepad tabs before measuring replay output.
    ensure_foreground(hwnd)
    u32 = ctypes.windll.user32
    u32.keybd_event(0x11, 0, 0, 0); u32.keybd_event(0x41, 0, 0, 0)
    u32.keybd_event(0x41, 0, 2, 0); u32.keybd_event(0x11, 0, 2, 0)
    press_key(0x08)
    time.sleep(0.3)
    """回放指定录制，验证记事本内容包含 expected"""
    r = post(f"{BASE_URL}/replay/start", json.dumps({"recording_id": rec_id}).encode())
    if not r.get('ok'):
        return False, f"回放启动失败: {r}"
    if not wait_for_replay():
        return False, "回放超时"
    text = get_notepad_text(hwnd)
    return (expected in text), text

def move_window_random(hwnd):
    rect = win32gui.GetWindowRect(hwnd)
    dx = (int(hwnd) % 180) - 90
    dy = ((int(hwnd) // 7) % 120) - 60
    win32gui.MoveWindow(hwnd, rect[0] + dx, rect[1] + dy,
                        rect[2] - rect[0], rect[3] - rect[1], True)
    time.sleep(0.8)
