"""
全面 E2E 测试：多场景验证录制->编译->回放闭环
场景1: 记事本输入文字 + 移动窗口后回放
场景2: WPS 表格输入 + Ctrl+A 全选替换（UWP 记事本前台受限，改 win32 应用验证）
"""
import time, json, ctypes, subprocess, sys, urllib.request, win32gui, win32con
import zipfile, os

BASE = os.path.dirname(os.path.abspath(__file__))
XLSX = os.path.join(BASE, "..", "..", "recordings", "_wps_test.xlsx")
import glob
ET = next(iter(glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Kingsoft", "WPS Office", "*", "office6", "et.exe"))), "")
FX = (1030, 426)

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

def find_wps():
    def cb(h, l):
        if win32gui.IsWindowVisible(h):
            t = win32gui.GetWindowText(h)
            if "wps_test" in t.lower() and "wps office" in t.lower():
                l.append(h)
        return True
    w = []
    win32gui.EnumWindows(cb, w)
    return w[0] if w else None

def click_at(x, y):
    ctypes.windll.user32.SetCursorPos(x, y)
    time.sleep(0.15)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.03)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.5)

subprocess.run(["taskkill", "/IM", "Notepad.exe", "/F", "/T"], capture_output=True)
time.sleep(1.5)
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

# ========== 场景2: 输入 + 全选替换（WPS 公式栏） ==========
print("\n=== 场景2: 输入 + 全选替换 + 重新输入（WPS 公式栏） ===")
for proc in ("et.exe", "wps.exe", "wpscloudsvr.exe"):
    subprocess.run(["taskkill", "/IM", proc, "/F"], capture_output=True)
time.sleep(2)
subprocess.run(["taskkill", "/IM", "Notepad.exe", "/F", "/T"], capture_output=True)
time.sleep(1)
subprocess.run([sys.executable, os.path.join(BASE, "make_xlsx.py")], check=True)
subprocess.Popen([ET, XLSX])
time.sleep(8)
hwnd = find_wps()
assert hwnd, "WPS 未打开"
bring_to_front(hwnd)
time.sleep(1)

print("[2.1] 开始录制...")
post('http://127.0.0.1:18899/record/start')
time.sleep(1)

print("[2.2] 点击公式栏并输入 'First'")
click_at(FX[0], FX[1])
type_text("First")
time.sleep(0.3)

print("[2.3] Ctrl+A 全选")
press_combo(0x11, 0x41)
time.sleep(0.3)

print("[2.4] 输入 'Second'（替换选中文本）")
type_text("Second")
time.sleep(0.3)

print("[2.5] 回车 + 停止录制")
press_key(0x0D)
time.sleep(1)
r = post('http://127.0.0.1:18899/record/stop')
steps2 = r.get('event_count', 0)
print(f"  录制 {steps2} 步")
assert steps2 > 0, "场景2: 录制0步"

print("[2.6] 清空公式栏残留")
click_at(FX[0], FX[1])
press_combo(0x11, 0x41)
press_key(0x2E)
time.sleep(0.5)

print("[2.7] 回放")
bring_to_front(hwnd)
time.sleep(0.3)
post('http://127.0.0.1:18899/replay/start')
time.sleep(max(8, steps2 * 1.8))

print("[2.8] Ctrl+S 保存并验证")
bring_to_front(hwnd)
time.sleep(0.5)
press_combo(0x11, 0x53)
time.sleep(2.5)
import os
z = zipfile.ZipFile(XLSX)
names = z.namelist()
print(f"  xlsx 成员({os.path.getsize(XLSX)}B): ", "sharedStrings" in "".join(names), "sheet1" in "".join(names))
if "xl/sharedStrings.xml" in names:
    ss = z.read("xl/sharedStrings.xml").decode("utf-8")
else:
    ss = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
ok2 = ("Second" in ss) and ("First" not in ss)
print(f"  sharedStrings: {ss[:200]}")
results.append(("场景2: 输入+全选+替换输入", ok2, ss))
print(f"  结果: {'PASS' if ok2 else 'FAIL'}")

for proc in ("et.exe", "wps.exe", "wpscloudsvr.exe"):
    subprocess.run(["taskkill", "/IM", proc, "/F"], capture_output=True)
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
