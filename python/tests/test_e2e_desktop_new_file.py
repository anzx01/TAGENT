"""E2E：桌面新建文本文档写入 123 的录制->回放复现验证

环境适配结论（本机踩坑记录，详见探针过程）：
- 「桌面右键->新建」子菜单无「文本文档」项（WPS/DOCX 项占据）
- Win11 系统 UI（运行框/开始菜单）拒绝键盘注入，IME 随机吃字母输入
- Notepad 单实例 + 会话恢复会抢占标签；另存为默认目录带"上次目录"记忆

链路（每步均可录制可回放）：
  预启动: notepad（应用启动由测试代劳，同 test_e2e_wps 模式）
  录制:  Ctrl+N 新标签 -> Ctrl+A 清残留 -> 键盘输入 123（数字 IME 免疫）
         -> Ctrl+S 另存为 -> 剪贴板粘贴完整路径到文件名框 -> 回车保存
         -> Alt+F4 关闭
  回放前环境: 重启 notepad + 预置剪贴板=目标文件完整路径

验证：录制后桌面 123.txt 内容 123；删除 -> 回放 -> 复现且内容 123。

直接运行：python tests/test_e2e_desktop_new_file.py（需后端 18899 + 真实桌面会话）
"""
import ctypes
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ctypes.windll.user32.SetProcessDPIAware()
u32 = ctypes.windll.user32

API = "http://127.0.0.1:18899"
DESKTOP = Path(r"C:\Users\Administrator\Desktop")
TARGET = DESKTOP / "123.txt"

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


def combo(*vks, tail=0.4):
    for vk in vks:
        key(vk)
        time.sleep(0.06)
    for vk in reversed(vks):
        key(vk, KEYEVENTF_KEYUP)
        time.sleep(0.06)
    time.sleep(tail)


def type_digits(text):
    for ch in text:
        vk = ord(ch.upper())
        key(vk)
        key(vk, KEYEVENTF_KEYUP)
        time.sleep(0.12)


def set_clip(text):
    subprocess.run(["powershell", "-command", f"Set-Clipboard -Value '{text}'"],
                   check=True)


import win32gui


def foreground():
    return win32gui.GetWindowText(win32gui.GetForegroundWindow())


def wait_foreground(substr, timeout=8.0):
    subs = substr if isinstance(substr, tuple) else (substr,)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(s in foreground() for s in subs):
            return True
        time.sleep(0.3)
    return False


def post(url, data=b"{}"):
    req = urllib.request.Request(url, method="POST",
                                 headers={"Content-Type": "application/json"}, data=data)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=10).read())


def launch_notepad():
    subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
    time.sleep(1.0)
    subprocess.Popen(["notepad"])
    assert wait_foreground("Notepad"), f"Notepad 未启动，前台={foreground()!r}"
    time.sleep(1.5)


def demo_actions():
    """演示操作（录制期间执行）；回放走产品通道复现同一序列"""
    for _ in range(3):                                # 会话恢复标签可能抢占，重试
        combo(0x11, 0x4E, tail=1.2)                   # Ctrl+N 无标题新标签
        if "无标题" in foreground() or "Untitled" in foreground():
            break
        time.sleep(0.5)
    else:
        raise AssertionError(f"Ctrl+N 未切到无标题标签，前台={foreground()!r}")
    combo(0x11, 0x41, tail=0.3)                       # Ctrl+A 清会话残留
    type_digits("123")                                # 内容 123（数字 IME 免疫）
    time.sleep(0.6)
    combo(0x11, 0x53, tail=2.0)                       # Ctrl+S -> 另存为
    assert wait_foreground(("另存为", "Save As")), f"另存为未弹，前台={foreground()!r}"
    set_clip(str(TARGET))                             # 文件名=完整路径（唯一粘贴）
    combo(0x11, 0x41, tail=0.3)                       # 文件名框全选
    combo(0x11, 0x56, tail=0.6)                       # 粘贴完整路径
    combo(0x0D, tail=2.0)                             # 回车保存
    time.sleep(1.0)
    combo(0x12, 0x73)                                 # Alt+F4
    time.sleep(1.0)


print("=== E2E: 桌面新建文本文档写入 123 ===")
assert get(API + "/status") is not None, "服务端不可用"
TARGET.unlink(missing_ok=True)

# ---------- 1. 录制 ----------
try:
    launch_notepad()
    s = post(API + "/record/start")
    assert s["ok"], s
    rec_id = s["recording_id"]
    print("录制开始:", rec_id)
    time.sleep(0.6)
    demo_actions()
except Exception:
    post(API + "/record/stop")
    combo(0x12, 0x73)
    raise

r = post(API + "/record/stop")
assert r["ok"], r
print("录制停止: steps =", r["event_count"])

# ---------- 2. 录制产物检查 ----------
rec_dir = Path(r"D:\aiapp\TAGENT\recordings") / rec_id
task = json.loads((rec_dir / "task.json").read_text(encoding="utf-8"))
meta = json.loads((rec_dir / "meta.json").read_text(encoding="utf-8"))
print("编译步骤:", [f"{st.get('action')}:{st.get('text', st.get('key', ''))}"
                   for st in task["steps"]])
print("隐私审计:", meta.get("privacy"))
mf = json.loads((rec_dir / "versions" / "manifest.json").read_text(encoding="utf-8"))
assert mf["current"] >= 1, "版本快照缺失"
print("版本快照: v%d (%s)" % (mf["current"], mf["history"][-1]["reason"]))
assert TARGET.exists(), "录制后 123.txt 不存在"
content = TARGET.read_text(encoding="utf-8")
assert content.strip() == "123", f"录制内容不是 123: {content!r}"
print("录制产物 OK:", TARGET, "内容 =", content.strip())

# ---------- 3. 删除文件，回放复现 ----------
TARGET.unlink()
launch_notepad()
# 对齐回放环境：录制的 key_press 锚定「无标题 - Notepad」窗口，
# 会话恢复的标签标题不同会导致 no_window 暂停，先 Ctrl+N 对齐
for _ in range(3):
    combo(0x11, 0x4E, tail=1.2)
    if "无标题" in foreground() or "Untitled" in foreground():
        break
    time.sleep(0.5)
set_clip(str(TARGET))                                # 回放环境：预置剪贴板=路径
print("已删除 123.txt、重开 Notepad 对齐无标题标签、预置剪贴板，开始回放...")

post(API + "/replay/start", json.dumps({"recording_id": rec_id}).encode())
state = {}
paused_since = None
for _ in range(150):
    time.sleep(1)
    state = get(API + "/replay/status")
    if state.get("paused"):
        if paused_since is None:
            paused_since = time.time()
            print("回放暂停:", state.get("pause_reason"), "@", state.get("step_desc"))
        elif time.time() - paused_since > 60:
            break                                     # 暂停等人工超时
        continue
    paused_since = None
    if not state.get("playing"):
        break
print("回放状态:", state.get("state"), "| 暂停原因:", state.get("pause_reason"))
runs_dir = rec_dir / "runs"
if runs_dir.exists():
    reports = sorted(runs_dir.glob("*.json"))
    if reports:
        report = json.loads(reports[-1].read_text(encoding="utf-8"))
        for st in report.get("steps", []):
            print("  ", st.get("action"), st.get("text", st.get("key", "")),
                  "->", st.get("status"))

ok = TARGET.exists() and TARGET.read_text(encoding="utf-8").strip() == "123"
content2 = TARGET.read_text(encoding="utf-8").strip() if TARGET.exists() else None
print("复现结果:", "PASS" if ok else "FAIL",
      f"(存在={TARGET.exists()}, 内容={content2!r})")

# ---------- 4. 清理 ----------
try:
    combo(0x12, 0x73)
except Exception:
    pass
if TARGET.exists():
    TARGET.unlink()
if ok and rec_dir.exists():
    import shutil
    shutil.rmtree(rec_dir, ignore_errors=True)
print("=== E2E 结束:", "PASS" if ok else "FAIL", "===")
sys.exit(0 if ok else 1)
