"""
WPS 文字 E2E 测试：录制输入文字 + 回放验证
验证 WPS Writer 的文本输入和 UIA 兼容性
"""
import time, json, ctypes, urllib.request, win32gui, win32con

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

def get_wps_text(hwnd):
    """用 UIA 获取 WPS 文档文本"""
    try:
        import uiautomation as ua
        root = ua.ControlFromHandle(hwnd)
        if not root:
            return ""
        for ctrl_type in ["EditControl", "DocumentControl", "TextControl"]:
            ctrl = getattr(root, ctrl_type)(searchDepth=32)
            if ctrl.Exists(2.0):
                try:
                    vp = ctrl.GetValuePattern()
                    if vp:
                        val = vp.Value
                        if val and val.strip():
                            return val.strip()[:200]
                except:
                    pass
                try:
                    tp = ctrl.GetTextPattern()
                    if tp:
                        vals = tp.GetDocumentRange().GetText()
                        if vals and vals.strip():
                            return vals.strip()[:200]
                except:
                    pass
    except Exception as e:
        print(f"  UIA获取文本失败: {e}")
    return ""

def find_wps_window():
    """查找 WPS 文字窗口"""
    r = []
    def cb(h, r):
        t = win32gui.GetWindowText(h)
        c = win32gui.GetClassName(h)
        if c == "OpusApp" and "WPS" in t:
            r.append(h)
        return True
    win32gui.EnumWindows(cb, r)
    return r[0] if r else None

SEARCH_TEXT = "Hello WPS Test"
print("\n=== WPS 文字 E2E 测试 ===")

# 1. 查找已打开的 WPS 窗口
print("[1] 查找 WPS 窗口...")
hwnd = find_wps_window()
assert hwnd, "找不到 WPS 窗口，请先手动打开 WPS 文字"
print(f"  hwnd={hwnd} title='{win32gui.GetWindowText(hwnd)}'")
bring_to_front(hwnd)
time.sleep(2)

# 2. 录制
print("[2] 开始录制...")
post('http://127.0.0.1:18899/record/start')
time.sleep(2)

print(f"[3] 输入 '{SEARCH_TEXT}'")
type_text(SEARCH_TEXT)
time.sleep(0.5)

print("[4] 停止录制...")
r = post('http://127.0.0.1:18899/record/stop')
steps = r.get('event_count', 0)
print(f"  录制 {steps} 步")
assert steps > 0, "录制0步"

text_after_record = get_wps_text(hwnd)
print(f"  录制后文本: '{text_after_record[:60]}'")

# 3. Ctrl+A 全选删除
print("[5] 清空文档...")
bring_to_front(hwnd)
time.sleep(0.5)
press_ctrl(0x41)  # Ctrl+A
time.sleep(0.3)
# Delete
scan = ctypes.windll.user32.MapVirtualKeyW(0x2E, 0)
ctypes.windll.user32.keybd_event(0x2E, scan, 0, 0)
ctypes.windll.user32.keybd_event(0x2E, scan, 2, 0)
time.sleep(1)

# 4. 回放
print("[6] 回放...")
bring_to_front(hwnd)
time.sleep(0.5)
post('http://127.0.0.1:18899/replay/start')
time.sleep(max(8, steps * 2))

# 5. 验证
print("[7] 验证...")
time.sleep(1)
result = get_wps_text(hwnd)
print(f"  回放后文本: '{result[:80]}'")
if SEARCH_TEXT[:5].lower() in result.lower() or "hello" in result.lower():
    print("\n=== WPS E2E PASS ===")
    print(f"  输入 '{SEARCH_TEXT}' 回放成功")
else:
    print(f"\n=== WPS E2E FAIL ===")
    print(f"  期望包含 '{SEARCH_TEXT[:5]}', 实际: '{result[:60]}'")

print("done")
