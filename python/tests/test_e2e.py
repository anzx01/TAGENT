"""E2E 测试：自动打开记事本 -> 录制输入文字 -> 停止 -> 回放验证"""
import time, json, ctypes, subprocess, urllib.request, os

def post(url, data=b'{}'):
    req = urllib.request.Request(url, method='POST',
                                 headers={'Content-Type': 'application/json'},
                                 data=data)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())

def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=5).read)

def type_text(text):
    """用 keybd_event 输入文本"""
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

print("=== E2E 测试开始 ===")

# 1. 打开记事本
print("[1] 打开记事本...")
notepad = subprocess.Popen(["notepad.exe"])
time.sleep(2)

# 获取记事本窗口位置
import win32gui
hwnd = win32gui.FindWindow(None, "无标题 - Notepad")
if not hwnd:
    hwnd = win32gui.FindWindow(None, "Untitled - Notepad")
if not hwnd:
    # 用类名查找
    hwnd = win32gui.FindWindow("Notepad", None)
if not hwnd:
    print("  ERROR: 找不到记事本窗口")
    exit(1)
print(f"  记事本窗口 hwnd={hwnd}")

# 把记事本放到前台
def bring_to_front(hwnd):
    """强制将窗口设为前台（用 ALT 键 trick 绕过限制）"""
    import ctypes
    # 先发一个 ALT 键 down-up，让系统认为用户在操作
    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # ALT down
    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)  # ALT up
    ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    ctypes.windll.user32.SetForegroundWindow(hwnd)

bring_to_front(hwnd)
time.sleep(1)

# 2. 开始录制
print("[2] 开始录制...")
r = post('http://127.0.0.1:18899/record/start')
print(f"  {r}")
assert r['ok'], "录制启动失败"
time.sleep(2)  # 等 hook 线程完全启动

# 3. 在记事本中输入文字
print("[3] 输入文字 'Hello World'...")
type_text("Hello World")
time.sleep(0.5)

# 4. Ctrl+S 保存
print("[4] 按 Ctrl+S...")
ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)  # Ctrl down
press_key(0x53)  # S
ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)  # Ctrl up
time.sleep(0.5)

# 按 Escape 关闭保存对话框（如果有）
press_key(0x1B)
time.sleep(0.3)

# 5. 停止录制
print("[5] 停止录制...")
r = post('http://127.0.0.1:18899/record/stop')
print(f"  {r}")
assert r['ok'], "录制停止失败"
step_count = r.get('event_count', 0)
print(f"  录制了 {step_count} 步")
assert step_count > 0, "没有录制到任何步骤"

# 6. 清空记事本内容（Ctrl+A -> Delete）
print("[6] 清空记事本...")
bring_to_front(hwnd)  # 确保记事本在前台
time.sleep(0.5)
ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)  # Ctrl down
press_key(0x41)  # A
ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)  # Ctrl up
time.sleep(0.1)
press_key(0x2E)  # Delete
time.sleep(0.5)

# 7. 回放
print("[7] 开始回放...")
bring_to_front(hwnd)  # 确保记事本在前台
time.sleep(0.5)
r = post('http://127.0.0.1:18899/replay/start', json.dumps({"recording_id": None}).encode())
print(f"  {r}")
assert r['ok'], "回放启动失败"

# 等待回放完成（按步数估算）
wait_sec = max(5, step_count * 2)
print(f"  等待回放完成（最多 {wait_sec}s）...")
for _ in range(wait_sec * 2):
    time.sleep(0.5)
    # 检查回放是否还在进行
    # 简单等待即可

# 8. 验证：检查记事本中是否有文字
print("[8] 验证回放结果...")
import win32con
# 尝试多种方式获取记事本文本
text = ""

# 方法1: 查找 Edit 控件
edit_hwnd = win32gui.FindWindowEx(hwnd, None, "Edit", None)
if edit_hwnd:
    buf = ctypes.create_unicode_buffer(4096)
    ctypes.windll.user32.SendMessageW(edit_hwnd, win32con.WM_GETTEXT, 4096, ctypes.addressof(buf))
    text = buf.value
    print(f"  [Edit] text='{text}'")

# 方法2: 枚举所有子窗口
if not text:
    children = []
    def enum_cb(h, l):
        children.append(h)
        return True
    win32gui.EnumChildWindows(hwnd, enum_cb, None)
    for ch in children:
        cls = win32gui.GetClassName(ch)
        buf = ctypes.create_unicode_buffer(4096)
        ret = ctypes.windll.user32.SendMessageW(ch, win32con.WM_GETTEXT, 4096, buf)
        if buf.value and cls != "Non Client Input Sink Window":
            print(f"  [Child {cls}] text='{buf.value[:100]}'")
            text = buf.value
            break  # 取到编辑控件文本就停

# 方法3: 用 UI Automation
if not text:
    try:
        import uiautomation as ua
        root = ua.ControlFromHandle(hwnd)
        if root:
            for ctrl in root.GetChildren():
                cls = ctrl.ControlTypeName
                try:
                    val = ctrl.GetValuePattern().Value
                    if val:
                        print(f"  [UIA {cls}] value='{val[:100]}'")
                        text = val
                        break
                except:
                    pass
                try:
                    val = ctrl.GetLegacyIAccessiblePattern().Value
                    if val:
                        print(f"  [UIA Legacy {cls}] value='{val[:100]}'")
                        text = val
                        break
                except:
                    pass
    except Exception as e:
        print(f"  UIA error: {e}")

print(f"  最终记事本内容: '{text}'")
if "Hello" in text or "hello" in text:
    print("\n=== E2E 测试通过! ===")
    print(f"  录制 {step_count} 步, 回放后记事本包含输入的文字")
else:
    print(f"\n=== E2E 测试失败 ===")
    print(f"  记事本内容不包含 'Hello': '{text}'")

# 9. 清理：关闭记事本
print("[9] 关闭记事本...")
win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
print("\n=== E2E 测试结束 ===")
