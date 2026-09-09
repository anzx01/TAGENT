"""调试33：产品 engine.key_press(hwnd, 'ctrl s') 能否弹出另存为"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import win32gui
from services.input.engine import InputEngine

subprocess = __import__("subprocess")
subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
time.sleep(1.0)
subprocess.Popen(["notepad"])
time.sleep(2.5)

hwnd = win32gui.GetForegroundWindow()
print("前台:", repr(win32gui.GetWindowText(hwnd)))

engine = InputEngine()
res = engine.key_press(hwnd, "ctrl s")
print("key_press ctrl s ->", res.ok, res.method)
time.sleep(2.0)
print("之后前台:", repr(win32gui.GetWindowText(win32gui.GetForegroundWindow())))
