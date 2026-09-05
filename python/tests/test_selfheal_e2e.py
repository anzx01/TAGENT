"""阶段3.3 自愈 E2E：在纠错库注入 trigger，正确动作时回放自动纠正"""
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request

API = "http://127.0.0.1:18899"


def post(url, data=b"{}"):
    req = urllib.request.Request(url, method="POST", headers={"Content-Type": "application/json"}, data=data)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=10).read())


def click_at(x, y):
    ctypes.windll.user32.SetCursorPos(x, y)
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.02)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.1)


def type_text(text):
    for ch in text:
        vk = ctypes.windll.user32.VkKeyScanW(ord(ch)) & 0xFF
        scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
        ctypes.windll.user32.keybd_event(vk, scan, 0, 0)
        ctypes.windll.user32.keybd_event(vk, scan, 2, 0)
        time.sleep(0.05)


# 1. 录制一个"打开记事本 -> 输入 hlelo" 的任务
subprocess.run(["taskkill", "/IM", "Notepad.exe", "/F", "/T"], capture_output=True)
time.sleep(1.5)
post(API + "/record/stop")
r = post(API + "/record/start")
rec_id = r["recording_id"]
print("record start:", rec_id)
time.sleep(0.5)

subprocess.Popen(["notepad.exe"])
time.sleep(2.5)
import win32gui

hwnd = win32gui.FindWindow("Notepad", None)
if not hwnd:
    def cb(w, l):
        t = win32gui.GetWindowText(w)
        if "记事本" in t or "Notepad" in t:
            l.append(w)
        return True
    w = []
    win32gui.EnumWindows(cb, w)
    hwnd = w[0] if w else None
print("notepad hwnd:", hwnd)
if hwnd:
    rect = win32gui.GetWindowRect(hwnd)
    click_at((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
    time.sleep(0.5)
    type_text("hlelo")
    time.sleep(0.5)

r = post(API + "/record/stop")
print("record stop:", r.get("event_count"), "steps")
ev = json.load(open(r["events_path"], encoding="utf-8"))
for i, s in enumerate(ev["steps"]):
    print(" ", i, s.get("action"), s.get("text", ""))

# 2. 注入纠错：trigger=输入hlelo，correct=输入hello
cr = post(API + f"/tasks/{rec_id}/correct", json.dumps({
    "trigger": [{"action": "input_text", "text": "hlelo"}],
    "correct": [{"action": "input_text", "text": "hello"}],
}).encode())
print("add correction:", cr)
correction_id = cr.get("correction", {}).get("id")
if not correction_id:
    raise AssertionError(f"correction id missing: {cr}")
approved = post(API + f"/tasks/{rec_id}/corrections/{correction_id}/approve")
print("approve correction:", approved)
if not approved.get("ok"):
    raise AssertionError(f"correction approval failed: {approved}")

# 3. 回放，应自动纠正为 hello
r = post(API + "/replay/start", json.dumps({"recording_id": rec_id}).encode())
print("replay start:", r)
time.sleep(12)
state = get(API + "/replay/status")
print("replay done, playing:", state.get("playing"))

# 4. 验证记事本内容已纠正为 hello（而非 hlelo）: 全选+复制读剪贴板
import win32clipboard

click_at(1024, 616)
time.sleep(0.3)
import win32api
import win32con

u32 = ctypes.windll.user32


def combo(vk1, vk2):
    u32.keybd_event(vk1, 0, 0, 0)
    time.sleep(0.05)
    u32.keybd_event(vk2, 0, 0, 0)
    time.sleep(0.05)
    u32.keybd_event(vk2, 0, 2, 0)
    u32.keybd_event(vk1, 0, 2, 0)
    time.sleep(0.1)


combo(0x11, 0x41)  # Ctrl+A
combo(0x11, 0x43)  # Ctrl+C
time.sleep(0.5)
for _ in range(5):
    try:
        win32clipboard.OpenClipboard()
        text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        break
    except Exception:
        win32clipboard.CloseClipboard()
        time.sleep(1)
print("记事本内容:", repr(text))
print("PASS" if "hello" in text and "hlelo" not in text else "FAIL")
