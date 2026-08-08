"""
阶段4 E2E测试：计算器点击按钮 (验证 UIA 定位 + 点击回放)
流程：打开计算器 -> 录制点击 5 + 3 = -> 停止 -> 清空 -> 回放 -> 验证结果
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

def bring_to_front(hwnd):
    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
    ctypes.windll.user32.ShowWindow(hwnd, 9)
    ctypes.windll.user32.SetForegroundWindow(hwnd)

def find_calc_button(hwnd, label):
    """用 UI Automation 找到计算器按钮并返回中心坐标"""
    try:
        import uiautomation as ua
        root = ua.ControlFromHandle(hwnd)
        if not root:
            return None
        # 搜索按钮
        btn = root.ButtonControl(Name=label)
        if btn.Exists(2.0):
            rect = btn.BoundingRectangle
            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2
            return (cx, cy)
    except Exception as e:
        print(f"  UIA查找失败: {e}")
    return None

def get_calc_result(hwnd):
    """获取计算器显示结果（用 AutomationId='CalculatorResults' 定位）"""
    try:
        import uiautomation as ua
        root = ua.ControlFromHandle(hwnd)
        if not root:
            return ""
        ctrl = root.TextControl(AutomationId="CalculatorResults")
        if ctrl.Exists(1.0):
            name = ctrl.Name  # 格式: "显示为 8"
            return name
    except Exception as e:
        print(f"  获取结果失败: {e}")
    return ""

print("=== 阶段4 E2E: 计算器点击测试 ===")

# 1. 打开计算器
print("[1] 打开计算器...")
subprocess.Popen(["calc.exe"])
time.sleep(3)

# 查找计算器窗口
hwnd = win32gui.FindWindow(None, "计算器")
if not hwnd:
    hwnd = win32gui.FindWindow(None, "Calculator")
if not hwnd:
    # 枚举窗口查找
    def cb(h, r):
        t = win32gui.GetWindowText(h)
        if "计算" in t or "calc" in t.lower():
            r.append(h)
        return True
    r = []
    win32gui.EnumWindows(cb, r)
    if r:
        hwnd = r[0]

assert hwnd, "找不到计算器窗口"
print(f"  hwnd={hwnd}")
bring_to_front(hwnd)
time.sleep(1)

# 2. 用 UIA 找到按钮位置
print("[2] 查找按钮位置...")
btn_5 = find_calc_button(hwnd, "五")
if not btn_5:
    btn_5 = find_calc_button(hwnd, "5")
btn_plus = find_calc_button(hwnd, "加")
if not btn_plus:
    btn_plus = find_calc_button(hwnd, "加（+）")
if not btn_plus:
    btn_plus = find_calc_button(hwnd, "Plus")
btn_3 = find_calc_button(hwnd, "三")
if not btn_3:
    btn_3 = find_calc_button(hwnd, "3")
btn_eq = find_calc_button(hwnd, "等于")
if not btn_eq:
    btn_eq = find_calc_button(hwnd, "等于（=）")
if not btn_eq:
    btn_eq = find_calc_button(hwnd, "Equals")

print(f"  5: {btn_5}, +: {btn_plus}, 3: {btn_3}, =: {btn_eq}")
assert btn_5 and btn_plus and btn_3 and btn_eq, "找不到所有按钮"

# 3. 开始录制
print("[3] 开始录制...")
bring_to_front(hwnd)
time.sleep(0.5)
post('http://127.0.0.1:18899/record/start')
time.sleep(2)

# 4. 点击 5 + 3 =
print("[4] 点击 5 + 3 =")
click_at(*btn_5)
time.sleep(0.3)
click_at(*btn_plus)
time.sleep(0.3)
click_at(*btn_3)
time.sleep(0.3)
click_at(*btn_eq)
time.sleep(0.5)

# 5. 停止录制
print("[5] 停止录制...")
r = post('http://127.0.0.1:18899/record/stop')
steps = r.get('event_count', 0)
print(f"  录制 {steps} 步")
assert steps > 0, "录制0步"

# 6. 清空计算器（按 C 或 Escape）
print("[6] 清空计算器...")
bring_to_front(hwnd)
time.sleep(0.5)
btn_c = find_calc_button(hwnd, "清除")
if not btn_c:
    btn_c = find_calc_button(hwnd, "清除 (C)")
if not btn_c:
    btn_c = find_calc_button(hwnd, "Clear")
if btn_c:
    click_at(*btn_c)
    time.sleep(0.3)
else:
    # 用 Escape 清空
    scan = ctypes.windll.user32.MapVirtualKeyW(0x1B, 0)
    ctypes.windll.user32.keybd_event(0x1B, scan, 0, 0)
    ctypes.windll.user32.keybd_event(0x1B, scan, 2, 0)
    time.sleep(0.3)

# 7. 回放
print("[7] 回放...")
bring_to_front(hwnd)
time.sleep(0.5)
post('http://127.0.0.1:18899/replay/start')
time.sleep(max(8, steps * 2))

# 8. 验证结果
print("[8] 验证结果...")
result = get_calc_result(hwnd)
print(f"  计算器显示: '{result}'")
if "8" in result:
    print("\n=== 计算器 E2E 测试通过! ===")
    print(f"  5+3=8, 回放后显示: '{result}'")
else:
    print(f"\n=== 计算器 E2E 测试失败 ===")
    print(f"  显示: '{result}'")

# 9. 关闭计算器
print("[9] 关闭计算器...")
win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
print("\n=== 测试结束 ===")
