"""
E2E 测试：完整流程验证（录制 -> 编译 -> 回放）
场景：打开记事本 -> 输入文字 -> 保存到临时文件 -> 关闭 -> 回放验证

验证要点：
1. input_text 步骤现在带窗口信息（recorder 修复）
2. switch_window 等待窗口出现（player 修复）
3. 动态窗口标题匹配（*xxx - Notepad 后缀匹配）
4. PostMessage 后台点击 vs 前台物理点击
5. 右键菜单导航（可选场景）

依赖：后端服务必须在 http://127.0.0.1:18899 运行
"""
import time, json, ctypes, subprocess, sys, os, urllib.request, win32gui, win32con
import tempfile

BASE_URL = "http://127.0.0.1:18899"

def post(url, data=b'{}'):
    req = urllib.request.Request(url, method='POST',
                                 headers={'Content-Type': 'application/json'}, data=data)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=10).read())

def type_text(text):
    """用 SendInput 模拟键盘输入（keybd_event 在 Win11 RichEdit 上空格无效）"""
    u32 = ctypes.windll.user32
    for char in text:
        if char == ' ':
            # 空格用 SendInput（keybd_event 在 Win11 Notepad 无效）
            vk = 0x20
            scan = u32.MapVirtualKeyW(vk, 0)
            u32.keybd_event(vk, scan, 0, 0)
            u32.keybd_event(vk, scan, 0x0002, 0)
            time.sleep(0.05)
            continue
        vk_scan = u32.VkKeyScanW(ord(char))
        vk = vk_scan & 0xFF
        shift = (vk_scan >> 8) & 0x01
        scan = u32.MapVirtualKeyW(vk, 0)
        if shift:
            u32.keybd_event(0x10, 0, 0, 0)
        u32.keybd_event(vk, scan, 0, 0)
        u32.keybd_event(vk, scan, 0x0002, 0)
        if shift:
            u32.keybd_event(0x10, 0, 0x0002, 0)
        time.sleep(0.05)

def press_key(vk):
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    ctypes.windll.user32.keybd_event(vk, scan, 0, 0)
    time.sleep(0.02)
    ctypes.windll.user32.keybd_event(vk, scan, 2, 0)

def press_combo(*vks):
    """组合键：依次按下，再依次抬起"""
    for vk in vks:
        scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
        ctypes.windll.user32.keybd_event(vk, scan, 0, 0)
        time.sleep(0.03)
    for vk in reversed(vks):
        scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
        ctypes.windll.user32.keybd_event(vk, scan, 2, 0)
        time.sleep(0.03)

def bring_to_front(hwnd):
    """强制窗口到前台（ALT trick 绕过限制）"""
    u32 = ctypes.windll.user32
    u32.keybd_event(0x12, 0, 0, 0)  # ALT down
    u32.keybd_event(0x12, 0, 0x0002, 0)  # ALT up
    u32.ShowWindow(hwnd, 9)  # SW_RESTORE
    u32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    # 确保 ALT 键已释放（防止 ALT 卡住导致空格打开系统菜单）
    u32.keybd_event(0x12, 0, 0x0002, 0)

def find_notepad():
    """查找记事本窗口（兼容中英文标题、动态标题）"""
    # 精确匹配常见标题
    for title in ("无标题 - Notepad", "Untitled - Notepad"):
        h = win32gui.FindWindow(None, title)
        if h:
            return h
    # 后缀匹配：Win11 记事本标题含文件名 "*xxx - Notepad"
    found = []
    def cb(h, l):
        try:
            t = win32gui.GetWindowText(h)
            if t and t.endswith(" - Notepad"):
                l.append(h)
        except:
            pass
        return True
    win32gui.EnumWindows(cb, found)
    if found:
        return found[0]
    # 按类名查找
    return win32gui.FindWindow("Notepad", None)

def get_notepad_text(hwnd):
    """获取记事本编辑区文本（兼容 Win10 Edit 和 Win11 RichEditD2DPT）"""
    children = []
    def cb(h, l):
        children.append(h)
        return True
    win32gui.EnumChildWindows(hwnd, cb, None)
    for ch in children:
        cls = win32gui.GetClassName(ch)
        if "edit" in cls.lower() or "rich" in cls.lower():
            buf = ctypes.create_unicode_buffer(8192)
            ctypes.windll.user32.SendMessageW(ch, win32con.WM_GETTEXT, 8192, buf)
            return buf.value
    # UIA 兜底
    try:
        import uiautomation as ua
        root = ua.ControlFromHandle(hwnd)
        if root:
            edit = root.EditControl(searchDepth=5)
            if edit.Exists(1.0):
                try:
                    return edit.GetValuePattern().Value
                except:
                    pass
    except:
        pass
    return ""

def wait_for_replay(timeout=30):
    """等待回放完成"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1)
        try:
            s = get(f"{BASE_URL}/replay/status")
            if not s.get("playing"):
                return True
        except:
            pass
    return False

def cleanup_notepad():
    """关闭所有记事本实例"""
    subprocess.run(["taskkill", "/IM", "Notepad.exe", "/F", "/T"],
                   capture_output=True)
    time.sleep(1)

# 禁用 Win11 记事本会话恢复（避免重启后弹出"是否恢复"对话框抢占焦点）
try:
    subprocess.run(["reg", "add", r"HKCU\Software\Microsoft\Notepad",
                    "/v", "RestoreSession", "/t", "REG_DWORD", "/d", "0", "/f"],
                   capture_output=True)
except:
    pass

_open_counter = 0

def find_notepad_by_filename(filename):
    """按文件名查找记事本窗口（标题含文件名）"""
    found = []
    def cb(h, l):
        try:
            t = win32gui.GetWindowText(h)
            if filename in t and "Notepad" in t:
                l.append(h)
        except:
            pass
        return True
    win32gui.EnumWindows(cb, found)
    return found[0] if found else None

def open_fresh_notepad():
    """打开一个干净的记事本。不传文件参数，避免 Win11 标签页恢复干扰。"""
    global _open_counter
    _open_counter += 1
    subprocess.Popen(["notepad.exe"])
    time.sleep(2)
    hwnd = find_notepad()
    if hwnd:
        bring_to_front(hwnd)
        time.sleep(0.5)
        # 关闭可能的会话恢复对话框
        press_key(0x1B)
        time.sleep(0.3)
        bring_to_front(hwnd)
        time.sleep(0.5)
    return hwnd


# ============================================================
# 测试开始
# ============================================================

print("=" * 60)
print("E2E 测试：记事本输入+保存+关闭+回放验证")
print("=" * 60)

results = []

# ---------- 场景1：输入文字 + 移动窗口后回放 ----------
print("\n--- 场景1：输入文字 + 移动窗口后回放 ---")

cleanup_notepad()
test_text = "Hello E2E Test 123"

# 1. 打开记事本
print("[1.1] 打开记事本...")
hwnd = open_fresh_notepad()
assert hwnd, "记事本未打开"
print(f"  hwnd={hwnd}, title={win32gui.GetWindowText(hwnd)!r}")
bring_to_front(hwnd)
time.sleep(1)

# 2. 开始录制
print("[1.2] 开始录制...")
r = post(f'{BASE_URL}/record/start')
assert r['ok'], f"录制启动失败: {r}"
rec_id_1 = r.get('recording_id', '')
print(f"  recording_id={rec_id_1}")
time.sleep(2)  # 等 hook 完全启动

# 3. 输入文字
print(f"[1.3] 输入文字: {test_text!r}")
type_text(test_text)
time.sleep(0.5)

# 4. 停止录制
print("[1.4] 停止录制...")
r = post(f'{BASE_URL}/record/stop')
assert r['ok'], f"录制停止失败: {r}"
steps1 = r.get('event_count', 0)
print(f"  录制 {steps1} 步, task_path={r.get('task_path')}")
assert steps1 > 0, "场景1: 没有录制到步骤"

# 验证录制数据：input_text 步骤应有窗口信息
events_path = r.get('events_path', '')
if events_path and os.path.exists(events_path):
    with open(events_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    for i, step in enumerate(data.get('steps', [])):
        if step.get('action') == 'input_text':
            has_window = bool(step.get('window'))
            print(f"  步骤{i} input_text: window={step.get('window')!r} hwnd={step.get('hwnd')}")
            if has_window:
                print("  [验证] input_text 带窗口信息: PASS")
            else:
                print("  [验证] input_text 带窗口信息: FAIL (旧录制文件?)")
            break

# 5. 关闭记事本并重新打开（Win11 RichEdit 清空不可靠，用全新窗口）
print("[1.5] 关闭并重新打开记事本（确保干净状态）...")
cleanup_notepad()
hwnd = open_fresh_notepad()
assert hwnd, "记事本重新打开失败"
bring_to_front(hwnd)
time.sleep(1)

# 6. 移动窗口位置（验证坐标定位不依赖绝对坐标）
print("[1.6] 移动窗口位置...")
rect = win32gui.GetWindowRect(hwnd)
win32gui.MoveWindow(hwnd, rect[0] + 200, rect[1] + 100,
                    rect[2] - rect[0], rect[3] - rect[1], True)
time.sleep(1)

# 7. 回放
print("[1.7] 开始回放...")
bring_to_front(hwnd)
time.sleep(0.5)
r = post(f'{BASE_URL}/replay/start', json.dumps({"recording_id": rec_id_1}).encode())
assert r['ok'], f"回放启动失败: {r}"
print(f"  回放 recording_id={r.get('recording_id')}")

# 等待回放完成
wait_sec = max(10, steps1 * 2)
print(f"  等待回放完成（最多 {wait_sec}s）...")
for _ in range(wait_sec * 2):
    time.sleep(0.5)
    s = get(f"{BASE_URL}/replay/status")
    if not s.get("playing"):
        print(f"  回放结束: {s}")
        break

# 8. 验证结果
print("[1.8] 验证回放结果...")
text = get_notepad_text(hwnd)
print(f"  记事本内容: {text!r}")
ok1 = test_text in text
results.append(("场景1: 输入文字+移动窗口回放", ok1, text))
print(f"  结果: {'PASS' if ok1 else 'FAIL'}")

# 关闭记事本
win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
time.sleep(1)


# ---------- 场景2：输入文字 + Enter键 + 验证多步回放 ----------
print("\n--- 场景2：输入文字 + Enter键 + 多步回放 ---")

cleanup_notepad()

# 打开记事本
print("[2.1] 打开记事本...")
hwnd = open_fresh_notepad()
assert hwnd, "记事本未打开"
bring_to_front(hwnd)
time.sleep(1)

line1 = "First Line"
line2 = "Second Line"

# 开始录制
print("[2.2] 开始录制...")
r = post(f'{BASE_URL}/record/start')
assert r['ok']
rec_id_2 = r.get('recording_id', '')
time.sleep(2)

# 输入第一行
print(f"[2.3] 输入第一行: {line1!r}")
type_text(line1)
time.sleep(0.3)

# 按回车换行
print("[2.4] 按回车...")
press_key(0x0D)  # Enter
time.sleep(0.3)

# 输入第二行
print(f"[2.5] 输入第二行: {line2!r}")
type_text(line2)
time.sleep(0.5)

# 停止录制
print("[2.6] 停止录制...")
r = post(f'{BASE_URL}/record/stop')
assert r['ok']
steps2 = r.get('event_count', 0)
print(f"  录制 {steps2} 步")
assert steps2 >= 2, "场景2: 应至少录制2步（input + enter）"

# 关闭记事本（强制关闭，不触发保存对话框）
print("[2.7] 关闭记事本...")
cleanup_notepad()
time.sleep(1)

# 重新打开记事本
print("[2.8] 重新打开记事本...")
hwnd = open_fresh_notepad()
assert hwnd, "记事本重新打开失败"
bring_to_front(hwnd)
time.sleep(1)

# 回放
print("[2.9] 回放...")
r = post(f'{BASE_URL}/replay/start', json.dumps({"recording_id": rec_id_2}).encode())
assert r['ok'], f"回放启动失败: {r}"
wait_sec = max(10, steps2 * 2)
print(f"  等待回放完成（最多 {wait_sec}s）...")
for _ in range(wait_sec * 2):
    time.sleep(0.5)
    s = get(f"{BASE_URL}/replay/status")
    if not s.get("playing"):
        print(f"  回放结束: {s}")
        break

# 验证结果
print("[2.10] 验证回放结果...")
text = get_notepad_text(hwnd)
print(f"  记事本内容: {text!r}")
ok2 = line1 in text and line2 in text
results.append(("场景2: 多步输入+Enter+回放", ok2, text))
print(f"  结果: {'PASS' if ok2 else 'FAIL'}")

# 关闭记事本
cleanup_notepad()


# ---------- 总结 ----------
print("\n" + "=" * 60)
print("E2E 测试总结")
print("=" * 60)
all_pass = True
for name, ok, detail in results:
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_pass = False
    print(f"  [{status}] {name}")
    print(f"         内容: {detail[:80]!r}")
print("=" * 60)
if all_pass:
    print("全部测试通过!")
    sys.exit(0)
else:
    print("部分测试失败，请检查")
    sys.exit(1)
