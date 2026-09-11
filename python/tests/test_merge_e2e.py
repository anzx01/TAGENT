"""阶段3.4 E2E：追加录制合并去重（WPS 表格链路，文件落盘验证）
1. 录制任务A：WPS 公式栏输入 Alpha + 回车
2. 追加录制任务B：先重复 Alpha+回车，再输入 Bravo+回车
3. 合并 -> 去重 removed=3，剩余 [click, Alpha, enter, Bravo, enter]
4. 回放合并后任务 + Ctrl+S -> sharedStrings 含 Alpha/Bravo
"""
import ctypes
import json
import os
import re
import subprocess
import time
import urllib.request
import zipfile
import win32gui

API = "http://127.0.0.1:18899"
import glob
ET = next(iter(glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Kingsoft", "WPS Office", "*", "office6", "et.exe"))), "")
XLSX = r"D:\aiapp\TAGENT\recordings\_wps_test.xlsx"
u32 = ctypes.windll.user32
FX = (1030, 426)  # WPS 公式栏


def post(url, data=b"{}"):
    req = urllib.request.Request(url, method="POST",
                                 headers={"Content-Type": "application/json"}, data=data)
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=15).read())


def click_at(x, y, delay=0.5):
    u32.SetCursorPos(x, y)
    time.sleep(0.15)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.03)
    u32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(delay)


def type_text(text):
    for ch in text:
        vk_scan = u32.VkKeyScanW(ord(ch))
        vk = vk_scan & 0xFF
        shift = (vk_scan >> 8) & 0x01
        scan = u32.MapVirtualKeyW(vk, 0)
        if shift:
            u32.keybd_event(0x10, 0, 0, 0)
        u32.keybd_event(vk, scan, 0, 0)
        u32.keybd_event(vk, scan, 2, 0)
        if shift:
            u32.keybd_event(0x10, 0, 2, 0)
        time.sleep(0.06)


def press_enter():
    u32.keybd_event(0x0D, 0, 0, 0)
    u32.keybd_event(0x0D, 0, 2, 0)
    time.sleep(0.6)


def combo(vk1, vk2):
    u32.keybd_event(vk1, 0, 0, 0)
    time.sleep(0.05)
    u32.keybd_event(vk2, 0, 0, 0)
    time.sleep(0.05)
    u32.keybd_event(vk2, 0, 2, 0)
    u32.keybd_event(vk1, 0, 2, 0)
    time.sleep(0.2)


def find_et():
    def cb(h, l):
        if win32gui.IsWindowVisible(h):
            t = win32gui.GetWindowText(h)
            if "wps_test" in t.lower() and "wps office" in t.lower():
                l.append(h)
        return True

    w = []
    win32gui.EnumWindows(cb, w)
    return w[0] if w else None


def make_xlsx():
    subprocess.run([r"D:\aiapp\TAGENT\.venv\Scripts\python.exe",
                    r"D:\aiapp\TAGENT\python\tests\make_xlsx.py"], check=True)


print("=== 阶段3.4 追加录制合并 E2E（WPS 表格）===")
post(API + "/record/stop")
make_xlsx()
subprocess.Popen([ET, XLSX])
time.sleep(8)
if not find_et():
    raise SystemExit("WPS 未启动")

# ---- 1. 录制任务A: 点击公式栏 + Alpha + 回车 ----
r = post(API + "/record/start")
print("record A:", r["recording_id"])
time.sleep(0.4)
click_at(FX[0], FX[1], 0.6)
type_text("Alpha")
press_enter()
rs = post(API + "/record/stop")
rec_a = rs["recording_id"]
print("A steps:", rs.get("event_count"))

# ---- 2. 追加录制 B: 重复 Alpha+回车，再输入 Bravo（回车后光标在下一单元格，直接输入）----
time.sleep(1)
r = post(API + "/record/start")
print("record B:", r["recording_id"])
time.sleep(0.4)
click_at(FX[0], FX[1], 0.6)
type_text("Alpha")
press_enter()
time.sleep(2.5)  # 超过 TEXT_TIMEOUT(2s)，确保分段
type_text("Bravo")
press_enter()
rs = post(API + "/record/stop")
rec_b = rs["recording_id"]
print("B steps:", rs.get("event_count"))
steps_b = get(f"{API}/recordings/{rec_b}/steps")["steps"]
print("B 实际步骤:", [(s.get("action"), s.get("text", "")) for s in steps_b])

# ---- 3. 合并 ----
m = post(f"{API}/recordings/{rec_a}/merge/{rec_b}")
print("merge:", m)
assert m.get("ok"), "merge failed"
assert m["removed"] == 3, f"期望去重 3, 实际 {m['removed']}"
steps = get(f"{API}/recordings/{rec_a}/steps")["steps"]
texts = [s.get("text", "") for s in steps if s.get("action") == "input_text"]
print("合并后 input_text:", texts)
assert texts == ["Alpha", "Bravo"], "去重失败"
assert not os.path.isdir(rf"recordings\{rec_b}"), "追加录制目录未删除"

# ---- 4. 回放合并后任务 + 保存 ----
post(API + "/replay/start", json.dumps({"recording_id": rec_a}).encode())
time.sleep(10)
combo(0x11, 0x53)  # Ctrl+S
time.sleep(2.5)

# 验证 xlsx
z = zipfile.ZipFile(XLSX)
ss = z.read("xl/sharedStrings.xml").decode("utf-8")
print("sharedStrings:", ss[:300])
ok = "Alpha" in ss and "Bravo" in ss
print("PASS" if ok else "FAIL")
