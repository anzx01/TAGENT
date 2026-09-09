"""E2E：桌面新建文本文档写入 123 的录制->回放复现验证

链路（人工演示，菜单用 OCR 导航保证布局无关）：
  Win+D 显示桌面 -> 右键桌面空白 -> 新建 -> 文本文档
  -> 重命名态输入 123 回车 -> 双击 123.txt 打开记事本
  -> 输入 123 -> Ctrl+S -> Alt+F4

验证：
  1. 录制产物：events/task.json/versions 快照/截图隐私审计
  2. 录制后桌面存在 123.txt 且内容 123
  3. 删除 123.txt -> 回放 -> 123.txt 复现且内容为 "123"

注意：Win+D 用 SendInput 带扩展键标志注入（keybd_event 的 LWIN 会被吞），
且 Win+D 是 toggle——注入后必须校验前台为 Program Manager，否则重按。

直接运行：python tests/test_e2e_desktop_new_file.py（需后端 18899 + 真实桌面会话）
"""
import ctypes
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ctypes.windll.user32.SetProcessDPIAware()
u32 = ctypes.windll.user32
SW, SH = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)

import mss
import win32gui
from PIL import Image
from services.ocr_locator import capture_and_find, find_text_on_screen

API = "http://127.0.0.1:18899"
DESKTOP = Path(r"C:\Users\Administrator\Desktop")
TARGET = DESKTOP / "123.txt"

# SendInput 键盘注入
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ki", KEYBDINPUT), ("pad", ctypes.c_ubyte * 8)]


def key(vk, flags=0):
    ki = KEYBDINPUT(vk, 0, flags, 0, None)
    u32.SendInput(1, ctypes.byref(INPUT(INPUT_KEYBOARD, ki)), ctypes.sizeof(INPUT))


def foreground():
    return win32gui.GetWindowText(win32gui.GetForegroundWindow())


def on_desktop():
    return "Program Manager" in foreground()


def win_d():
    """Win+D（toggle！）-> 校验到桌面，未到则再按一次"""
    for _ in range(2):
        key(0x5B, KEYEVENTF_EXTENDEDKEY)
        key(0x44)
        key(0x44, KEYEVENTF_KEYUP)
        key(0x5B, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP)
        time.sleep(1.2)
        if on_desktop():
            return
    raise AssertionError(f"Win+D 未到桌面，前台={foreground()!r}")


def post(url, data=b"{}"):
    req = urllib.request.Request(url, method="POST",
                                 headers={"Content-Type": "application/json"}, data=data)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=10).read())


def click_at(x, y, delay=0.6, button="left", double=False):
    u32.SetCursorPos(x, y)
    time.sleep(0.2)
    down, up = (0x0002, 0x0004) if button == "left" else (0x0008, 0x0010)
    for _ in range(2 if double else 1):
        u32.mouse_event(down, 0, 0, 0, 0)
        time.sleep(0.04)
        u32.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.12 if double else delay)


def hover(x, y, delay=0.9):
    u32.SetCursorPos(x, y)
    time.sleep(delay)


def type_digits(text):
    for ch in text:
        vk = ord(ch)
        u32.keybd_event(vk, 0, 0, 0)
        u32.keybd_event(vk, 0, 2, 0)
        time.sleep(0.08)


def press_enter():
    key(0x0D)
    key(0x0D, KEYEVENTF_KEYUP)
    time.sleep(0.5)


def combo(vk1, vk2):
    key(vk1)
    time.sleep(0.05)
    key(vk2)
    time.sleep(0.05)
    key(vk2, KEYEVENTF_KEYUP)
    key(vk1, KEYEVENTF_KEYUP)
    time.sleep(0.3)


def ocr_click(text, roi=None, button="left", double=False, retries=4):
    """OCR 找文本并点击其中心；roi=(x0,y0,x1,y1) 限定搜索区域（防屏幕他处同名文本误匹配）"""
    for _ in range(retries):
        pos = None
        if roi:
            x0, y0, x1, y1 = roi
            for _ in range(3):  # 截图重试
                try:
                    with mss.mss() as sct:
                        shot = sct.grab({"left": x0, "top": y0,
                                         "width": x1 - x0, "height": y1 - y0})
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    break
                except Exception:
                    time.sleep(0.25)
            else:
                img = None
            if img is not None:
                rel = find_text_on_screen(img, text)
                if rel:
                    pos = (rel[0] + x0, rel[1] + y0)
        else:
            pos = capture_and_find(text)
        if pos:
            click_at(*pos, button=button, double=double)
            return pos
        time.sleep(0.4)
    raise AssertionError(f"OCR 未找到 {text!r} (roi={roi})")


def close_notepad():
    combo(0x12, 0x73)  # Alt+F4


print("=== E2E: 桌面新建文本文档写入 123 ===")
assert get(API + "/status") is not None, "服务端不可用"

# ---------- 1. 录制 ----------
try:
    s = post(API + "/record/start")
    assert s["ok"], s
    rec_id = s["recording_id"]
    print("录制开始:", rec_id)
    time.sleep(0.6)

    win_d()                                               # 切桌面（录进任务）
    CX, CY = SW // 2, SH // 2
    click_at(CX, CY, delay=0.5)                           # 单击桌面空白（激活 FolderView）
    # Shift+F10 弹旧式完整菜单（Win11 新右键菜单是 XAML 半透明，OCR/UIA 均读不出；
    # 旧菜单弹在焦点图标处，GDI 渲染 OCR 友好）
    key(0x10)
    key(0x79)
    key(0x79, KEYEVENTF_KEYUP)
    key(0x10, KEYEVENTF_KEYUP)
    time.sleep(1.2)
    p_new = ocr_click("新建")                             # 左键点"新建"（展开子菜单）
    hover(*p_new, 1.0)                                    # 悬停等子菜单稳定
    # 子菜单必在"新建"右侧邻近，限定 ROI 防止屏幕他处同名文本误匹配
    ocr_click("文本文档", roi=(p_new[0], p_new[1] - 60, p_new[0] + 440, p_new[1] + 320))
    time.sleep(1.0)                                       # 进入重命名态
    type_digits("123")                                    # 文件名 -> 123
    press_enter()                                         # 确认
    time.sleep(1.2)
    assert TARGET.exists(), "录制阶段未生成 123.txt"
    print("文件已创建:", TARGET)

    time.sleep(2.5)                                       # 分段间隔
    ocr_click("123.txt", double=True)                     # 双击打开
    time.sleep(2.5)                                       # 等记事本
    type_digits("123")                                    # 内容 123
    combo(0x11, 0x53)                                     # Ctrl+S
    time.sleep(1.0)
    close_notepad()
    time.sleep(1.0)
except Exception:
    post(API + "/record/stop")                            # 兜底停录（已停则无害）
    u32.keybd_event(0x1B, 0, 0, 0)                        # 兜底关菜单
    u32.keybd_event(0x1B, 0, 2, 0)
    raise

r = post(API + "/record/stop")
assert r["ok"], r
print("录制停止: steps =", r["event_count"])

# ---------- 2. 录制产物检查 ----------
rec_dir = Path(r"D:\aiapp\TAGENT\recordings") / rec_id
task = json.loads((rec_dir / "task.json").read_text(encoding="utf-8"))
meta = json.loads((rec_dir / "meta.json").read_text(encoding="utf-8"))
print("编译步骤:", [f"{st.get('action')}:{st.get('text', st.get('window', '')[:12])}"
                   for st in task["steps"]])
print("隐私审计:", meta.get("privacy"))
mf = json.loads((rec_dir / "versions" / "manifest.json").read_text(encoding="utf-8"))
assert mf["current"] >= 1, "版本快照缺失"
print("版本快照: v%d (%s)" % (mf["current"], mf["history"][-1]["reason"]))
assert TARGET.read_text(encoding="utf-8").strip() == "123", "录制内容不是 123"

# ---------- 3. 删除文件，回放复现 ----------
TARGET.unlink()
assert not TARGET.exists()
print("已删除 123.txt，开始回放复现...")

post(API + "/replay/start", json.dumps({"recording_id": rec_id}).encode())
state = {}
for _ in range(120):
    time.sleep(1)
    state = get(API + "/replay/status")
    if not state.get("running"):
        break
print("回放状态:", state.get("status"), "| 暂停原因:", state.get("pause_reasons"))
for st in state.get("steps", []):
    print("  ", st.get("action"), st.get("text", ""), "->", st.get("status"))

ok = TARGET.exists() and TARGET.read_text(encoding="utf-8").strip() == "123"
content = TARGET.read_text(encoding="utf-8").strip() if TARGET.exists() else None
print("复现结果:", "PASS" if ok else "FAIL", f"(存在={TARGET.exists()}, 内容={content!r})")

# ---------- 4. 清理 ----------
try:
    close_notepad()
except Exception:
    pass
if TARGET.exists():
    TARGET.unlink()
print("=== E2E 结束:", "PASS" if ok else "FAIL", "===")
sys.exit(0 if ok else 1)
