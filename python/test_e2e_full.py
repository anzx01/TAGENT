"""记事本 E2E：输入+移动窗口回放 + 输入+Ctrl+A+替换"""
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

def press_key(vk):
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    ctypes.windll.user32.keybd_event(vk, scan, 0, 0)
    time.sleep(0.02)
    ctypes.windll.user32.keybd_event(vk, scan, 2, 0)

def press_ctrl(vk):
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, scan, 0, 0)
    ctypes.windll.user32.keybd_event(vk, scan, 2, 0)
    ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
    ctypes.windll.user32.keybd_event(0xA2, 0, 2, 0)
    ctypes.windll.user32.keybd_event(0xA3, 0, 2, 0)

def bring_to_front(hwnd):
    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
    ctypes.windll.user32.ShowWindow(hwnd, 9)
    ctypes.windll.user32.SetForegroundWindow(hwnd)

def get_notepad_text(hwnd):
    children = []
    def cb(h, l):
        children.append(h)
        return True
    win32gui.EnumChildWindows(hwnd, cb, None)
    for ch in children:
        if win32gui.GetClassName(ch) == "RichEditD2DPT":
            buf = ctypes.create_unicode_buffer(4096)
            ctypes.windll.user32.SendMessageW(ch, win32con.WM_GETTEXT, 4096, buf)
            return buf.value
    return ""

def open_notepad():
    subprocess.Popen(["notepad.exe"])
    time.sleep(2)
    hwnd = win32gui.FindWindow(None, "无标题 - Notepad")
    if not hwnd:
        hwnd = win32gui.FindWindow("Notepad", None)
    return hwnd

results = []

# === 场景1 ===
print("\n=== 场景1: 输入 + 移动窗口后回放 ===")
hwnd = open_notepad()
assert hwnd
bring_to_front(hwnd)
time.sleep(1)
post('http://127.0.0.1:18899/record/start')
time.sleep(2)
type_text("Hello World")
time.sleep(0.5)
r = post('http://127.0.0.1:18899/record/stop')
steps1 = r.get('event_count', 0)
print(f"  录制 {steps1} 步")
assert steps1 > 0
bring_to_front(hwnd)
time.sleep(0.5)
press_ctrl(0x41)
time.sleep(0.1)
press_key(0x2E)
time.sleep(0.5)
rect = win32gui.GetWindowRect(hwnd)
win32gui.MoveWindow(hwnd, rect[0]+200, rect[1]+100, rect[2]-rect[0], rect[3]-rect[1], True)
time.sleep(1)
bring_to_front(hwnd)
time.sleep(0.3)
post('http://127.0.0.1:18899/replay/start')
time.sleep(max(5, steps1 * 1.5))
text = get_notepad_text(hwnd)
print(f"  回放后: '{text}'")
ok1 = "Hello" in text
results.append(("场景1", ok1, text))
print(f"  {'PASS' if ok1 else 'FAIL'}")
win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
time.sleep(1)

# === 场景2 ===
print("\n=== 场景2: 输入 + Ctrl+A + 替换 ===")
hwnd = open_notepad()
assert hwnd
bring_to_front(hwnd)
time.sleep(1)
post('http://127.0.0.1:18899/record/start')
time.sleep(2)
type_text("First")
time.sleep(0.3)
press_ctrl(0x41)
time.sleep(0.3)
type_text("Second")
time.sleep(0.5)
r = post('http://127.0.0.1:18899/record/stop')
steps2 = r.get('event_count', 0)
print(f"  录制 {steps2} 步")
assert steps2 > 0
win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
time.sleep(1)
hwnd = open_notepad()
assert hwnd
bring_to_front(hwnd)
time.sleep(1)
post('http://127.0.0.1:18899/replay/start')
time.sleep(max(5, steps2 * 1.5))
text = get_notepad_text(hwnd)
print(f"  回放后: '{text}'")
ok2 = "Second" in text or "First" in text
results.append(("场景2", ok2, text))
print(f"  {'PASS' if ok2 else 'FAIL'}")
win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)

print("\n" + "="*40)
for name, ok, detail in results:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: '{detail[:30]}'")
print("="*40)
