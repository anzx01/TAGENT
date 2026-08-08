"""Edge浏览器搜索 E2E 测试"""
import time, json, ctypes, subprocess, urllib.request, win32gui, win32con, os

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

SEARCH = "hello world"
print("=== Edge E2E: 搜索测试 ===")

edge = next((p for p in [
    os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
] if os.path.exists(p)), None)
assert edge, "找不到 Edge"
subprocess.Popen([edge, "--new-window", "about:blank"])
time.sleep(4)

hwnd = None
r = []
def cb(h, r):
    if "Chrome_WidgetWin" in win32gui.GetClassName(h) and "Edge" in win32gui.GetWindowText(h):
        r.append(h)
    return True
win32gui.EnumWindows(cb, r)
if r: hwnd = r[0]
assert hwnd, "找不到 Edge"
print(f"  hwnd={hwnd}")
bring_to_front(hwnd)
time.sleep(1)

print("[1] 录制...")
post('http://127.0.0.1:18899/record/start')
time.sleep(2)

print(f"[2] Ctrl+L + '{SEARCH}' + Enter")
press_ctrl(0x4C)  # Ctrl+L
time.sleep(0.5)
type_text(SEARCH)
time.sleep(0.3)
press_key(0x0D)  # Enter
time.sleep(1)

print("[3] 停止...")
r = post('http://127.0.0.1:18899/record/stop')
steps = r.get('event_count', 0)
print(f"  {steps} 步")
assert steps > 0

print("[4] 回放...")
bring_to_front(hwnd)
time.sleep(0.5)
post('http://127.0.0.1:18899/replay/start')
time.sleep(max(10, steps * 2))

print("[5] 验证...")
time.sleep(2)
title = win32gui.GetWindowText(hwnd)
print(f"  标题: '{title[:80].replace(chr(0x200b), '')}'")
if SEARCH.split()[0].lower() in title.lower() or "bing" in title.lower() or "google" in title.lower():
    print("\n=== Edge E2E PASS ===")
else:
    print(f"\n=== Edge E2E FAIL: '{title[:60].replace(chr(0x200b), '')}' ===")

win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
print("done")
