"""
阶段4 E2E测试：Edge浏览器搜索 (验证 点击地址栏 + 文本输入 + 回车)
流程：打开Edge -> 录制点击地址栏+输入搜索词+回车 -> 停止 -> 回到首页 -> 回放 -> 验证搜索结果
"""
import time, json, ctypes, subprocess, urllib.request, win32gui, win32con

def post(url, data=b'{}'):
    req = urllib.request.Request(url, method='POST',
                                 headers={'Content-Type': 'application/json'}, data=data)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())

def click_at(x, y):
    ctypes.windll.user32.SetCursorPos(x, y)
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.02)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.1)

def type_text(text):
    try:
        import uiautomation as ua
        ua.SendKeys(text)
        time.sleep(0.5)
        return
    except ImportError:
        pass
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

def bring_to_front(hwnd):
    """抢焦点：先最小化占用前台的窗口（管理员权限 VS Code 会挡住 SetForegroundWindow）"""
    fg = ctypes.windll.user32.GetForegroundWindow()
    if fg and fg != hwnd:
        ctypes.windll.user32.ShowWindow(fg, 6)
        time.sleep(0.3)
    ctypes.windll.user32.BringWindowToTop(hwnd)
    ctypes.windll.user32.ShowWindow(hwnd, 9)
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)

def find_edge_address_bar(hwnd):
    """用 UIA 找到 Edge 地址栏"""
    try:
        import uiautomation as ua
        root = ua.ControlFromHandle(hwnd)
        if not root:
            return None
        # Edge 地址栏 AutomationId 通常是 "searchInput" 或 "addressBarQueryBox"
        for aid in ["searchInput", "addressBarQueryBox", "EdgeAddressBar"]:
            ctrl = root.EditControl(AutomationId=aid)
            if ctrl.Exists(2.0):
                rect = ctrl.BoundingRectangle
                return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
        # 退而求其次：找地址栏区域的 Edit
        edit = root.EditControl(ClassName="Edit")
        if edit.Exists(1.0):
            rect = edit.BoundingRectangle
            return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
    except Exception as e:
        print(f"  UIA查找失败: {e}")
    return None

def get_edge_url(hwnd):
    """获取 Edge 当前 URL"""
    try:
        import uiautomation as ua
        root = ua.ControlFromHandle(hwnd)
        if not root:
            return ""
        for aid in ["searchInput", "addressBarQueryBox"]:
            ctrl = root.EditControl(AutomationId=aid)
            if ctrl.Exists(1.0):
                try:
                    val = ctrl.GetValuePattern().Value
                    if val:
                        return val
                except:
                    pass
    except:
        pass
    return ""

SEARCH_TERM = "hello world"

print("=== 阶段4 E2E: Edge浏览器搜索 ===")

# 1. 打开 Edge
print("[1] 打开 Edge...")
import os
edge_paths = [
    os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
    os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
]
edge_exe = next((p for p in edge_paths if os.path.exists(p)), None)
assert edge_exe, "找不到 Edge"
subprocess.Popen([edge_exe, "--new-window", "about:blank"])
time.sleep(4)

# 查找 Edge 窗口
hwnd = None
def cb(h, r):
    t = win32gui.GetWindowText(h)
    if "Edge" in t or "edge" in t.lower():
        cls = win32gui.GetClassName(h)
        if "Chrome_WidgetWin" in cls:
            r.append(h)
    return True
r = []
win32gui.EnumWindows(cb, r)
if r:
    hwnd = r[0]
assert hwnd, "找不到 Edge 窗口"
print(f"  hwnd={hwnd}")
bring_to_front(hwnd)
time.sleep(1)

# 2. 用 Ctrl+L 聚焦地址栏（比坐标点击更可靠）
print("[2] 用 Ctrl+L 聚焦地址栏...")
bring_to_front(hwnd)
time.sleep(0.5)
ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)  # Ctrl down
scan_l = ctypes.windll.user32.MapVirtualKeyW(0x4C, 0)
ctypes.windll.user32.keybd_event(0x4C, scan_l, 0, 0)  # L down
ctypes.windll.user32.keybd_event(0x4C, scan_l, 2, 0)  # L up
ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)  # Ctrl up
# 释放扩展 Ctrl
ctypes.windll.user32.keybd_event(0xA2, 0, 2, 0)
ctypes.windll.user32.keybd_event(0xA3, 0, 2, 0)
time.sleep(0.5)

# 3. 开始录制
print("[3] 开始录制...")
bring_to_front(hwnd)
time.sleep(0.5)
post('http://127.0.0.1:18899/record/start')
time.sleep(2)

# 4. 点击地址栏 + 输入搜索词 + 回车
print(f"[4] Ctrl+L + 输入 '{SEARCH_TERM}' + 回车")
# 优先用 UIA 定位地址栏并点击（比 Ctrl+L 更可靠）
addr = find_edge_address_bar(hwnd)
if addr:
    click_at(*addr)
    time.sleep(0.5)
else:
    # 兜底：Ctrl+L 聚焦地址栏
    ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x4C, scan_l, 0, 0)
    ctypes.windll.user32.keybd_event(0x4C, scan_l, 2, 0)
    ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
    ctypes.windll.user32.keybd_event(0xA2, 0, 2, 0)
    ctypes.windll.user32.keybd_event(0xA3, 0, 2, 0)
    time.sleep(0.5)
type_text(SEARCH_TERM)
time.sleep(0.3)
press_key(0x0D)  # Enter
time.sleep(1)

# 5. 停止录制
print("[5] 停止录制...")
r = post('http://127.0.0.1:18899/record/stop')
steps = r.get('event_count', 0)
print(f"  录制 {steps} 步")
assert steps > 0, "录制0步"

# 6. 等待搜索结果加载，然后开新标签页
print("[6] 等待搜索结果加载...")
time.sleep(3)
title1 = win32gui.GetWindowText(hwnd)
print(f"  搜索后标题: '{title1[:80]}'")

# 打开新标签页
bring_to_front(hwnd)
time.sleep(0.5)
press_key(0x11)  # Ctrl
press_key(0x54)  # T
# 释放 Ctrl
ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
ctypes.windll.user32.keybd_event(0xA2, 0, 2, 0)
ctypes.windll.user32.keybd_event(0xA3, 0, 2, 0)
time.sleep(2)

# 7. 回放
print("[7] 回放...")
bring_to_front(hwnd)
time.sleep(0.5)
post('http://127.0.0.1:18899/replay/start')
time.sleep(max(10, steps * 2))

# 8. 验证：用窗口标题判断（Edge 搜索后标题会变化）
print("[8] 验证结果...")
time.sleep(2)
title2 = win32gui.GetWindowText(hwnd)
print(f"  回放后标题: '{title2[:80]}'")
# Edge 地址栏输入文字+回车后，标题应该包含搜索词或搜索引擎名
if SEARCH_TERM.split()[0].lower() in title2.lower() or "bing" in title2.lower() or "search" in title2.lower() or "搜索" in title2:
    print("\n=== Edge E2E 测试通过! ===")
    print(f"  回放后标题包含搜索相关内容")
else:
    print(f"\n=== Edge E2E 测试失败 ===")
    print(f"  标题: '{title2[:100]}'")

# 9. 关闭 Edge
print("[9] 关闭 Edge...")
win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
print("\n=== 测试结束 ===")
