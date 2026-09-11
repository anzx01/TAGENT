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


_server_proc = None


def ensure_server():
    """服务端探活；不在运行则拉起子进程（本环境后台 web_server 常被回收）"""
    global _server_proc
    try:
        get(API + "/status")
        print("服务端已在运行，复用")
        return
    except Exception:
        pass
    _server_proc = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=str(Path(__file__).resolve().parents[1]),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        time.sleep(0.5)
        try:
            get(API + "/status")
            print("已拉起服务端 pid=", _server_proc.pid)
            return
        except Exception:
            continue
    raise SystemExit("服务端 60s 未就绪")


def shutdown_server():
    if _server_proc is not None:
        try:
            post(API + "/shutdown")
        except Exception:
            pass
        _server_proc.wait(timeout=10)
        print("已关闭拉起的服务端")


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


def close_system_dialogs():
    """关掉挂在前台的系统级模态框（如 Alt+F4 误触桌面的「关闭 Windows」），
    否则它们会一直占住前台，后续任何置前/键入全部失效"""
    import win32con
    import win32gui
    targets = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h):
            t = win32gui.GetWindowText(h)
            c = win32gui.GetClassName(h)
            if c == "#32770" and any(k in t for k in
                                      ("关闭 Windows", "Closing Windows", "关闭这些应用")):
                targets.append((h, t))
                win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)

    win32gui.EnumWindows(cb, None)
    if targets:
        time.sleep(0.8)
        print("已关闭系统对话框:", [t for _, t in targets])


def force_foreground(hwnd):
    """强制窗口到前台（AttachThreadInput + ALT trick，与 player._force_foreground 同款）"""
    import win32gui
    import win32process
    fg = win32gui.GetForegroundWindow()
    if fg == hwnd:
        return True
    _, fg_tid = win32process.GetWindowThreadProcessId(fg)
    _, tid = win32process.GetWindowThreadProcessId(hwnd)
    key(0x12)
    key(0x12, KEYEVENTF_KEYUP)                     # ALT trick
    attached = fg_tid != tid
    if attached:
        u32.AttachThreadInput(fg_tid, tid, True)
    try:
        u32.ShowWindow(hwnd, 9)
        u32.BringWindowToTop(hwnd)
        u32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            u32.AttachThreadInput(fg_tid, tid, False)
    time.sleep(0.5)
    return win32gui.GetForegroundWindow() == hwnd


def clear_notepad_session():
    """删除 Win11 记事本会话恢复数据（TabState/WindowState）。
    会话恢复到指向不存在文件的标签时会卡死活动标签，Ctrl+N 全部被吞
    （实测），清掉会话可保证每次启动都是干净的「无标题 - Notepad」。"""
    import shutil
    base = Path(r"C:\Users\Administrator\AppData\Local\Packages"
                r"\Microsoft.WindowsNotepad_8wekyb3d8bbwe\LocalState")
    for sub in ("TabState", "WindowState"):
        shutil.rmtree(base / sub, ignore_errors=True)


def launch_notepad():
    close_system_dialogs()
    subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
    time.sleep(1.0)
    clear_notepad_session()
    subprocess.Popen(["notepad"])
    if not wait_foreground("Notepad", timeout=8.0):
        # 新进程偶发不抢前台：按类名找 Notepad 窗口强制置前再继续
        h = u32.FindWindowW("Notepad", None)
        if not h or not force_foreground(h):
            raise AssertionError(f"Notepad 无法置前，前台={foreground()!r}")
    time.sleep(1.0)


def align_untitled(retries=5):
    """确保 Notepad 活动标签为「无标题」，失败 fail-fast"""
    for _ in range(retries):
        if "无标题" in foreground() or "Untitled" in foreground():
            return
        combo(0x11, 0x4E, tail=1.2)
        time.sleep(0.5)
    raise AssertionError(f"无法对齐无标题标签，前台={foreground()!r}")


def demo_actions():
    """演示操作（录制期间执行）；回放走产品通道复现同一序列"""
    align_untitled()                                   # 会话恢复标签会抢占，验证+重试
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
ensure_server()
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
# 先重启并对齐、最后才删文件：若先删，会话恢复会恢复指向不存在文件的
# 123.txt 标签，恢复流程卡住活动标签，之后 Ctrl+N 全部被吞（实测）
launch_notepad()
align_untitled()
TARGET.unlink()
set_clip(str(TARGET))                                # 回放环境：预置剪贴板=路径
align_untitled()                                     # 二次验证：防 set_clip 期间被抢占
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
# REPORT 落盘在 BASE_DIR/runs（文件名 *_<rec_id>.json），不在录制目录下
runs_dir = Path(r"D:\aiapp\TAGENT\runs")
reports = sorted(runs_dir.glob(f"*_{rec_id}.json")) if runs_dir.exists() else []
if reports:
    report = json.loads(reports[-1].read_text(encoding="utf-8"))
    for st in report.get("steps", []):
        print("  ", st.get("action"), st.get("text", st.get("key", "")),
              "->", st.get("status"), st.get("method", ""))

ok = TARGET.exists() and TARGET.read_text(encoding="utf-8").strip() == "123"
content2 = TARGET.read_text(encoding="utf-8").strip() if TARGET.exists() else None
print("复现结果:", "PASS" if ok else "FAIL",
      f"(存在={TARGET.exists()}, 内容={content2!r})")

# ---------- 4. 清理 ----------
subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
if TARGET.exists():
    TARGET.unlink()
if ok and rec_dir.exists():
    import shutil
    shutil.rmtree(rec_dir, ignore_errors=True)
shutdown_server()
print("=== E2E 结束:", "PASS" if ok else "FAIL", "===")
sys.exit(0 if ok else 1)
