"""阶段5.3 WPS 表格 E2E：录制(公式栏输入)->清空->回放->xlsx 内容验证"""
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
import win32gui
import win32clipboard
import uiautomation as ua

API = "http://127.0.0.1:18899"
_wps_roots = [os.environ.get("LOCALAPPDATA", ""), r"C:\Program Files", r"C:\Program Files (x86)"]
_et_candidates = []
for _root in _wps_roots:
    if _root and os.path.isdir(_root):
        _et_candidates.extend(__import__('glob').glob(os.path.join(_root, "Kingsoft", "WPS Office", "*", "office6", "et.exe")))
ET = _et_candidates[-1] if _et_candidates else ""
XLSX = r"D:\aiapp\TAGENT\recordings\_wps_test.xlsx"
u32 = ctypes.windll.user32


def post(url, data=b"{}"):
    req = urllib.request.Request(url, method="POST", headers={"Content-Type": "application/json"}, data=data)
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def click(x, y, dbl=False, delay=0.4):
    u32.SetCursorPos(x, y)
    time.sleep(0.1)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.03)
    u32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.12)
    if dbl:
        u32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.03)
        u32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(delay)


def combo(*vks):
    for v in vks:
        u32.keybd_event(v, 0, 0, 0)
        time.sleep(0.04)
    for v in reversed(vks):
        u32.keybd_event(v, 0, 2, 0)
        time.sleep(0.04)


def type_text(text):
    for ch in text:
        vk = u32.VkKeyScanW(ord(ch)) & 0xFF
        u32.keybd_event(vk, 0, 0, 0)
        u32.keybd_event(vk, 0, 2, 0)
        time.sleep(0.05)


def get_ui_table():
    found = []
    def cb(w, l):
        if win32gui.IsWindowVisible(w):
            c = win32gui.GetClassName(w)
            t = win32gui.GetWindowText(w)
            if c == "XLMAIN" or ("WPS" in t and "表格" in t):
                l.append(w)
        return True
    win32gui.EnumWindows(cb, found)
    return found[0] if found else None


def close_recover_banner(hwnd):
    try:
        ui = ua.ControlFromHandle(hwnd)
        for c in ua.WalkTree(ui, maxDepth=16):
            try:
                if c.ControlTypeName == "ButtonControl" and c.Name.strip() == "不再提醒":
                    c.Click()
                    print("关闭恢复横幅")
                    break
            except Exception:
                pass
    except Exception as e:
        print("横幅处理失败:", e)
    time.sleep(0.5)


print("=== 阶段5.3 WPS 表格 xlsx E2E ===")
post(API + "/record/stop")
subprocess.run([sys.executable,
                r"D:\aiapp\TAGENT\python\tests\make_xlsx.py"], check=True)

subprocess.Popen([ET, XLSX])
print("started et")
time.sleep(9)

hwnd = get_ui_table()
print("hwnd:", hwnd, repr(win32gui.GetWindowText(hwnd)) if hwnd else "")
if not hwnd:
    raise SystemExit("no et window")
close_recover_banner(hwnd)

# 公式栏坐标 (782,412,1292,440) -> 点击 (900,426)
Fx = (1030, 426)

r = post(API + "/record/start")
print("record:", r["recording_id"])
time.sleep(0.4)

# 先点击公式栏（录制 click 步骤），再输入
click(Fx[0], Fx[1], delay=0.6)
type_text("hello_wps")
u32.keybd_event(0x0D, 0, 0, 0)
u32.keybd_event(0x0D, 0, 2, 0)
time.sleep(0.5)

rs = post(API + "/record/stop")
print("stop steps:", rs.get("event_count"))
steps = json.load(open(rs["events_path"], encoding="utf-8")).get("steps", [])
for i, s in enumerate(steps):
    print(" ", i, s.get("action"), s.get("text", ""))

rec_id = rs["events_path"].split("recordings")[1].replace("\\", "/").split("/")[1]

# 验证录制内容已写入单元格 A1: 读取剪贴板（选中 A1 + Ctrl+C）
click(640, 480)
time.sleep(0.3)
combo(0x11, 0x43)
time.sleep(0.5)
try:
    win32clipboard.OpenClipboard()
    t = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
    win32clipboard.CloseClipboard()
except Exception:
    win32clipboard.CloseClipboard()
    t = None
print("A1 录制后:", repr(t))

# 清空 A1: 选中 + 删除
click(640, 480)
combo(0x11, 0x41)
time.sleep(0.2)
u32.keybd_event(0x2E, 0, 0, 0)
u32.keybd_event(0x2E, 0, 2, 0)
time.sleep(0.3)
# 清掉公式栏残留
click(Fx[0], Fx[1])
combo(0x11, 0x41)
time.sleep(0.1)
u32.keybd_event(0x2E, 0, 0, 0)
u32.keybd_event(0x2E, 0, 2, 0)
time.sleep(0.3)

# 回放
post(API + "/replay/start", json.dumps({"recording_id": rec_id}).encode())
print("replay started")
time.sleep(8)

# Ctrl+S 保存
combo(0x11, 0x53)
time.sleep(2)

# 验证: xlsx sharedStrings 内容
try:
    import zipfile
    z = zipfile.ZipFile(XLSX)
    ss = z.read("xl/sharedStrings.xml").decode("utf-8")
except Exception as e:
    print("读 xlsx 失败:", e)
    ss = ""
print("sharedStrings:", ss[:300])
print("PASS" if "hello-wps" in ss else "FAIL")
