"""单独测试场景2：排除场景1的状态污染"""
import time, json, ctypes, subprocess, urllib.request, win32gui, win32con

def post(url, data=b'{}'):
    req = urllib.request.Request(url, method='POST',
                                 headers={'Content-Type': 'application/json'}, data=data)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())

def type_text(text):
    for char in text:
        vk_scan = ctypes.windll.user32.VkKeyScanW(ord(char))
        vk = vk_scan & 0xFF
        shift = (vk_scan >> 8) & 0x01
        scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
        if shift: ctypes.windll.user32.keybd_event(0x10, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, scan, 0, 0)
        ctypes.windll.user32.keybd_event(vk, scan, 2, 0)
        if shift: ctypes.windll.user32.keybd_event(0x10, 0, 2, 0)
        time.sleep(0.05)

def bring_to_front(hwnd):
    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
    ctypes.windll.user32.ShowWindow(hwnd, 9)
    ctypes.windll.user32.SetForegroundWindow(hwnd)

def find_notepad():
    """按窗口标题后缀查找记事本（兼容 Win11 文件名标题）"""
    found = []
    def cb(h, l):
        try:
            t = win32gui.GetWindowText(h)
            if t and (t.endswith(" - Notepad") or t.endswith(" - 记事本")):
                l.append(h)
        except:
            pass
        return True
    win32gui.EnumWindows(cb, found)
    return found[0] if found else None

def get_notepad_text(hwnd):
    try:
        import uiautomation as ua
        root = ua.ControlFromHandle(hwnd)
        edit = root.EditControl(searchDepth=10)
        if edit.Exists(1.0):
            return edit.GetValuePattern().Value or ""
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
            buf = ctypes.create_unicode_buffer(4096)
            ctypes.windll.user32.SendMessageW(ch, win32con.WM_GETTEXT, 4096, buf)
            return buf.value
    return ""

print("=== 单独测试场景2 ===")
subprocess.Popen(["notepad.exe"])
time.sleep(2)
hwnd = find_notepad()
if not hwnd:
    hwnd = win32gui.FindWindow("Notepad", None)
assert hwnd, "记事本未打开"
bring_to_front(hwnd)
time.sleep(1)

print("[1] 开始录制...")
post('http://127.0.0.1:18899/record/start')
time.sleep(2)

print("[2] 输入 'TestText'")
type_text("TestText")
time.sleep(0.5)

print("[3] 停止录制")
r = post('http://127.0.0.1:18899/record/stop')
steps = r.get('event_count', 0)
print(f"  录制 {steps} 步")
assert steps > 0

print("[4] 关闭旧记事本，打开新的（避免 Ctrl 键状态污染）")
win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
time.sleep(1)
subprocess.Popen(["notepad.exe"])
time.sleep(2)
hwnd = find_notepad()
if not hwnd:
    hwnd = win32gui.FindWindow("Notepad", None)
assert hwnd, "新记事本未打开"
bring_to_front(hwnd)
time.sleep(1)

print("[5] 回放")
bring_to_front(hwnd)
time.sleep(0.3)
post('http://127.0.0.1:18899/replay/start')
time.sleep(max(5, steps * 1.5))

text = get_notepad_text(hwnd)
print(f"  回放后: '{text}'")
if "Test" in text:
    print("=== PASS ===")
else:
    print("=== FAIL ===")

win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
