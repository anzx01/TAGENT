"""验证阶段2.2/2.3：跨窗口切换 + 等待步骤录制"""
import ctypes
import json
import subprocess
import sys
import time
import urllib.request

API = "http://127.0.0.1:18899"


def post(url, data=b"{}"):
    req = urllib.request.Request(url, method="POST", headers={"Content-Type": "application/json"}, data=data)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


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


r = post(API + "/record/stop")
print("cleanup stop:", r)
r = post(API + "/record/start")
rec_id = r["recording_id"]
print("record start:", rec_id)
time.sleep(0.5)

# 先在当前前台窗口（Brave）上点击一次，作为切换前的基准窗口
ctypes.windll.user32.SetCursorPos(400, 300)
ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
time.sleep(0.5)
print("clicked base window")

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
    cx = (rect[0] + rect[2]) // 2
    cy = (rect[1] + rect[3]) // 2
    click_at(cx, cy)
    time.sleep(0.5)
    type_text("Hello")
    time.sleep(0.5)

print("sleeping 4.5s...")
time.sleep(4.5)
type_text("World")
time.sleep(1)

r = post(API + "/record/stop")
print("record stop:", r.get("event_count"), "steps")
data = json.load(open(r["events_path"], encoding="utf-8"))
print("=== steps ===")
for i, s in enumerate(data.get("steps", [])):
    print(i, s.get("action"), "| win:", (s.get("window") or "")[:24],
          "| text:", s.get("text", ""), "| dur:", s.get("duration", ""))