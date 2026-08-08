"""计算器 E2E：点击 5+3= 验证"""
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

def find_btn(hwnd, label):
    try:
        import uiautomation as ua
        root = ua.ControlFromHandle(hwnd)
        if not root: return None
        btn = root.ButtonControl(Name=label)
        if btn.Exists(2.0):
            r = btn.BoundingRectangle
            return (r.left + r.right) // 2, (r.top + r.bottom) // 2
    except: pass
    return None

def get_result(hwnd):
    try:
        import uiautomation as ua
        root = ua.ControlFromHandle(hwnd)
        if not root: return ""
        ctrl = root.TextControl(AutomationId="CalculatorResults")
        if ctrl.Exists(1.0):
            return ctrl.Name
    except: pass
    return ""

print("=== 计算器 E2E ===")
subprocess.Popen(["calc.exe"])
time.sleep(3)
hwnd = win32gui.FindWindow(None, "计算器")
if not hwnd: hwnd = win32gui.FindWindow(None, "Calculator")
assert hwnd
bring_to_front(hwnd)
time.sleep(1)

b5 = find_btn(hwnd, "五") or find_btn(hwnd, "5")
bp = find_btn(hwnd, "加") or find_btn(hwnd, "Plus")
b3 = find_btn(hwnd, "三") or find_btn(hwnd, "3")
be = find_btn(hwnd, "等于") or find_btn(hwnd, "Equals")
assert b5 and bp and b3 and be, f"按钮: 5={b5} +={bp} 3={b3} ={be}"

bring_to_front(hwnd)
time.sleep(0.5)
post('http://127.0.0.1:18899/record/start')
time.sleep(2)
click_at(*b5); time.sleep(0.3)
click_at(*bp); time.sleep(0.3)
click_at(*b3); time.sleep(0.3)
click_at(*be); time.sleep(0.5)
r = post('http://127.0.0.1:18899/record/stop')
steps = r.get('event_count', 0)
print(f"  录制 {steps} 步")
assert steps > 0

# 清空
bring_to_front(hwnd)
time.sleep(0.5)
bc = find_btn(hwnd, "清除") or find_btn(hwnd, "Clear")
if bc: click_at(*bc); time.sleep(0.3)

# 回放
bring_to_front(hwnd)
time.sleep(0.5)
post('http://127.0.0.1:18899/replay/start')
time.sleep(max(8, steps * 2))

result = get_result(hwnd)
print(f"  结果: '{result}'")
if "8" in result:
    print("=== PASS ===")
else:
    print("=== FAIL ===")
win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
