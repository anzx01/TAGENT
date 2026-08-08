"""
阶段1 E2E测试：验证 UI Automation 定位（移动窗口后回放仍成功）
流程：打开记事本 -> 录制输入文字 -> 停止 -> 编译 -> 移动记事本窗口 -> 回放 -> 验证
"""
import time, json, ctypes, subprocess, urllib.request, os, win32gui, win32con

def post(url, data=b'{}'):
    req = urllib.request.Request(url, method='POST',
                                 headers={'Content-Type': 'application/json'},
                                 data=data)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())

def type_text(text):
    for char in text:
        vk_scan = ctypes.windll.user32.VkKeyScanW(ord(char))
        vk = vk_scan & 0xFF
        shift = (vk_scan >> 8) & 0x01
        scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
        if shift:
            ctypes.windll.user32.keybd_event(0x10, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, scan, 0, 0)
        ctypes.windll.user32.keybd_event(vk, scan, 2, 0)
        if shift:
            ctypes.windll.user32.keybd_event(0x10, 0, 2, 0)
        time.sleep(0.05)

def press_key(vk):
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    ctypes.windll.user32.keybd_event(vk, scan, 0, 0)
    time.sleep(0.02)
    ctypes.windll.user32.keybd_event(vk, scan, 2, 0)

def bring_to_front(hwnd):
    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
    ctypes.windll.user32.ShowWindow(hwnd, 9)
    ctypes.windll.user32.SetForegroundWindow(hwnd)

def get_notepad_text(hwnd):
    """获取记事本文本"""
    children = []
    def cb(h, l):
        children.append(h)
        return True
    win32gui.EnumChildWindows(hwnd, cb, None)
    for ch in children:
        cls = win32gui.GetClassName(ch)
        if cls == "RichEditD2DPT":
            buf = ctypes.create_unicode_buffer(4096)
            ctypes.windll.user32.SendMessageW(ch, win32con.WM_GETTEXT, 4096, buf)
            return buf.value
    return ""

print("=== 阶段1 E2E 测试开始 ===")
print("目标：验证移动窗口后回放仍能正确定位输入")

# 1. 打开记事本
print("[1] 打开记事本...")
subprocess.Popen(["notepad.exe"])
time.sleep(2)
hwnd = win32gui.FindWindow(None, "无标题 - Notepad")
if not hwnd:
    hwnd = win32gui.FindWindow("Notepad", None)
assert hwnd, "找不到记事本"
print(f"  hwnd={hwnd}")

# 记录原始窗口位置
orig_rect = win32gui.GetWindowRect(hwnd)
print(f"  原始位置: {orig_rect}")

# 2. 放到前台并录制
bring_to_front(hwnd)
time.sleep(1)

print("[2] 开始录制...")
r = post('http://127.0.0.1:18899/record/start')
assert r['ok'], "录制启动失败"
time.sleep(2)

# 3. 输入文字
print("[3] 输入文字 'Test123'...")
type_text("Test123")
time.sleep(0.5)

# 4. 停止录制（自动编译）
print("[4] 停止录制...")
r = post('http://127.0.0.1:18899/record/stop')
assert r['ok'], "录制停止失败"
step_count = r.get('event_count', 0)
task_path = r.get('task_path')
print(f"  录制 {step_count} 步, task.json: {task_path}")
assert step_count > 0, "没有录制到步骤"

# 5. 清空记事本
print("[5] 清空记事本...")
bring_to_front(hwnd)
time.sleep(0.5)
ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
press_key(0x41)
ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
time.sleep(0.1)
press_key(0x2E)
time.sleep(0.5)
text = get_notepad_text(hwnd)
print(f"  清空后内容: '{text}'")

# 6. 移动记事本窗口到新位置（关键：验证非死坐标）
new_x = orig_rect[0] + 200
new_y = orig_rect[1] + 100
print(f"[6] 移动记事本窗口到 ({new_x},{new_y})...")
win32gui.MoveWindow(hwnd, new_x, new_y, 
                     orig_rect[2] - orig_rect[0], 
                     orig_rect[3] - orig_rect[1], True)
time.sleep(1)

# 7. 回放
bring_to_front(hwnd)
time.sleep(0.5)
print("[7] 开始回放（窗口已移动）...")
r = post('http://127.0.0.1:18899/replay/start', json.dumps({"recording_id": None}).encode())
assert r['ok'], "回放启动失败"

# 等待回放完成
wait_sec = max(5, step_count * 2)
print(f"  等待回放完成（最多 {wait_sec}s）...")
for _ in range(wait_sec * 2):
    time.sleep(0.5)

# 8. 验证
print("[8] 验证回放结果...")
text = get_notepad_text(hwnd)
print(f"  记事本内容: '{text}'")
if "Test" in text:
    print("\n=== 阶段1 E2E 测试通过! ===")
    print(f"  移动窗口后回放成功，记事本包含 'Test': '{text}'")
else:
    print(f"\n=== 阶段1 E2E 测试失败 ===")
    print(f"  记事本内容: '{text}'")

# 9. 关闭记事本
print("[9] 关闭记事本...")
win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
print("\n=== 测试结束 ===")
