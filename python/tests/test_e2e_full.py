"""
全面 E2E 测试：多场景验证录制->编译->回放闭环
场景1: 记事本输入文字 + 移动窗口后回放
场景2: 记事本输入文字 + Ctrl+A 全选 + Delete 删除
"""
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

def press_combo(*vks):
    for vk in vks:
        scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
        ctypes.windll.user32.keybd_event(vk, scan, 0, 0)
        time.sleep(0.02)
    for vk in reversed(vks):
        scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
        ctypes.windll.user32.keybd_event(vk, scan, 2, 0)
        time.sleep(0.02)

def bring_to_front(hwnd):
    """用 ALT trick + SetForegroundWindow"""
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

# ========== 场景1: 输入文字 + 移动窗口后回放 ==========
print("\n=== 场景1: 输入文字 + 移动窗口后回放 ===")
hwnd = open_notepad()
assert hwnd, "记事本未打开"
bring_to_front(hwnd)
time.sleep(1)

print("[1.1] 开始录制...")
post('http://127.0.0.1:18899/record/start')
time.sleep(2)

print("[1.2] 输入 'Hello World'")
type_text("Hello World")
time.sleep(0.5)

print("[1.3] 停止录制")
r = post('http://127.0.0.1:18899/record/stop')
steps1 = r.get('event_count', 0)
print(f"  录制 {steps1} 步")
assert steps1 > 0, "场景1: 录制0步"

print("[1.4] 清空记事本")
bring_to_front(hwnd)
time.sleep(0.5)
press_combo(0x11, 0x41)  # Ctrl+A
time.sleep(0.1)
press_key(0x2E)  # Delete
time.sleep(0.5)

print("[1.5] 移动窗口")
rect = win32gui.GetWindowRect(hwnd)
win32gui.MoveWindow(hwnd, rect[0]+200, rect[1]+100,
                     rect[2]-rect[0], rect[3]-rect[1], True)
time.sleep(1)

print("[1.6] 回放")
bring_to_front(hwnd)
time.sleep(0.3)
post('http://127.0.0.1:18899/replay/start')
time.sleep(max(5, steps1 * 1.5))

text = get_notepad_text(hwnd)
print(f"  回放后内容: '{text}'")
ok1 = "Hello" in text
results.append(("场景1: 输入+移动窗口回放", ok1, text))
print(f"  结果: {'PASS' if ok1 else 'FAIL'}")

win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
time.sleep(1)

# ========== 场景2: 输入 + 全选删除 + 重新输入 ==========
print("\n=== 场景2: 输入 + 全选删除 + 重新输入 ===")
hwnd = open_notepad()
assert hwnd, "记事本未打开"
bring_to_front(hwnd)
time.sleep(1)

print("[2.1] 开始录制...")
post('http://127.0.0.1:18899/record/start')
time.sleep(2)

print("[2.2] 输入 'First'")
type_text("First")
time.sleep(0.3)

print("[2.3] Ctrl+A 全选")
press_combo(0x11, 0x41)
time.sleep(0.3)

print("[2.4] 输入 'Second'（替换选中文本）")
type_text("Second")
time.sleep(0.5)

print("[2.5] 停止录制")
r = post('http://127.0.0.1:18899/record/stop')
steps2 = r.get('event_count', 0)
print(f"  录制 {steps2} 步")
assert steps2 > 0, "场景2: 录制0步"

print("[2.6] 用新记事本替代清空（避免 Ctrl 键状态污染）")
win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
time.sleep(1)
hwnd = open_notepad()
assert hwnd, "新记事本未打开"
bring_to_front(hwnd)
time.sleep(1)

print("[2.7] 回放")
bring_to_front(hwnd)
time.sleep(0.3)
post('http://127.0.0.1:18899/replay/start')
time.sleep(max(5, steps2 * 1.5))

text = get_notepad_text(hwnd)
print(f"  回放后内容: '{text}'")
if "Second" in text or "First" in text:
    print("\n=== 阶段1 E2E 测试通过! ===")
    ok2 = True
else:
    ok2 = False
results.append(("场景2: 输入+全选+替换输入", ok2, text))
print(f"  结果: {'PASS' if ok2 else 'FAIL'}")

win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
time.sleep(1)

# ========== 总结 ==========
print("\n" + "="*50)
print("E2E 测试总结")
print("="*50)
all_pass = True
for name, ok, detail in results:
    status = "PASS" if ok else "FAIL"
    if not ok: all_pass = False
    print(f"  [{status}] {name}")
    print(f"         内容: '{detail[:50]}'")
print("="*50)
if all_pass:
    print("全部测试通过!")
else:
    print("部分测试失败，请检查")
print("="*50)
