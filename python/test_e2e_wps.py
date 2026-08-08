"""
WPS 文字 E2E 测试：录制输入文字 + 回放验证
验证 WPS Writer 的文本输入和 UIA 兼容性
"""
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
        # WPS 文档区域通常是 Edit 或 Document 控件
        for ctrl_type in ["EditControl", "DocumentControl", "TextControl"]:
            ctrl = getattr(root, ctrl_type)(searchDepth=32)
            if ctrl.Exists(2.0):
                try:
                    vp = ctrl.GetValuePattern()
                    if vp:
                        val = vp.Value
                        if val and val.strip():
                            return val.strip()
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

# 查找 WPS 路径
wps_paths = []
base = os.path.expandvars(r"%LocalAppData%\Packages\Microsoft.MicrosoftOfficeHub_8wekyb3d8bbwe\LocalCache\Local\Kingsoft\WPS Office")
if os.path.isdir(base):
    for d in sorted(os.listdir(base), reverse=True):
        p = os.path.join(base, d, "office6", "wps.exe")
        if os.path.exists(p):
            wps_paths.append(p)

wps_exe = wps_paths[0] if wps_paths else None
assert wps_exe, "找不到 WPS"
print(f"WPS 路径: {wps_exe}")

SEARCH_TEXT = "Hello WPS Test"

print("\n=== WPS 文字 E2E 测试 ===")

# 1. 打开 WPS 文字
print("[1] 打开 WPS 文字...")
# UWP 应用需要用 ShellExecute 启动
import win32api
win32api.ShellExecute(0, "open", wps_exe, "", "", 1)
time.sleep(6)

# 查找 WPS 窗口
hwnd = None
for _ in range(10):
    r = []
    def cb(h, r):
        t = win32gui.GetWindowText(h)
        cls = win32gui.GetClassName(h)
        if ("WPS" in t or "wps" in t.lower() or "文档" in t) and cls:
            r.append((h, t, cls))
        return True
    win32gui.EnumWindows(cb, r)
    if r:
        # 优先选择标题含"WPS"或"文档"的窗口
        for h, t, c in r:
            if "WPS" in t or "文档" in t:
                hwnd = h
                break
        if not hwnd and r:
            hwnd = r[0][0]
    if hwnd:
        break
    time.sleep(1)

assert hwnd, "找不到 WPS 窗口"
print(f"  hwnd={hwnd} title='{win32gui.GetWindowText(hwnd)}' class='{win32gui.GetClassName(hwnd)}'")
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

# 3. 获取录制后的文本
text_after_record = get_wps_text(hwnd)
print(f"  录制后文本: '{text_after_record[:50]}'")

# 4. 新建文档（清空）
print("[5] 新建文档...")
bring_to_front(hwnd)
time.sleep(0.5)
# Ctrl+N 新建
ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
scan_n = ctypes.windll.user32.MapVirtualKeyW(0x4E, 0)
ctypes.windll.user32.keybd_event(0x4E, scan_n, 0, 0)
ctypes.windll.user32.keybd_event(0x4E, scan_n, 2, 0)
ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
ctypes.windll.user32.keybd_event(0xA2, 0, 2, 0)
ctypes.windll.user32.keybd_event(0xA3, 0, 2, 0)
time.sleep(3)

# 可能弹出新文档选择窗口，按 Escape 关闭
ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
ctypes.windll.user32.keybd_event(0x1B, 0, 2, 0)
time.sleep(1)

# 重新查找窗口（可能新建后窗口变了）
r2 = []
def cb2(h, r2):
    t = win32gui.GetWindowText(h)
    if "WPS" in t or "文档" in t:
        r2.append(h)
    return True
win32gui.EnumWindows(cb2, r2)
if r2:
    hwnd = r2[0]
bring_to_front(hwnd)
time.sleep(1)

# 5. 回放
print("[6] 回放...")
bring_to_front(hwnd)
time.sleep(0.5)
post('http://127.0.0.1:18899/replay/start')
time.sleep(max(8, steps * 2))

# 6. 验证
print("[7] 验证...")
time.sleep(1)
result = get_wps_text(hwnd)
print(f"  回放后文本: '{result[:80]}'")
if SEARCH_TEXT[:5].lower() in result.lower() or "hello" in result.lower():
    print("\n=== WPS E2E 测试通过! ===")
    print(f"  输入 '{SEARCH_TEXT}' 回放成功")
else:
    print(f"\n=== WPS E2E 测试失败 ===")
    print(f"  期望包含 '{SEARCH_TEXT[:5]}', 实际: '{result[:60]}'")

# 关闭 WPS
print("[8] 关闭 WPS...")
win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
print("done")
