"""验收A：首次试运行（0 纠错）自主成功率 > 50%（PRD 成功标准 1）

任务：记事本输入两行（第一行含可变数据 → 标注 {{order_id}} 绑定剪贴板）
6 轮试运行，每轮改变：剪贴板数据值、窗口位置。
预期 0 纠错状态下成功率 100%。
"""
import json, os, sys, time, win32gui, win32con

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (post, put, get, type_text, press_key, ensure_foreground,
                    open_fresh_notepad, cleanup_notepad, wait_for_replay,
                    get_notepad_text, set_clipboard, replay_and_verify,
                    move_window_random)

BASE_URL = "http://127.0.0.1:18899"

print("=" * 60)
print("验收A：0 纠错自主成功率 > 50%")
print("=" * 60)

# 1. 录制任务
cleanup_notepad()
hwnd = open_fresh_notepad()
assert hwnd, "记事本未打开"
ensure_foreground(hwnd)
time.sleep(0.5)

r = post(f'{BASE_URL}/record/start')
assert r['ok'], r
rec_id = r.get('recording_id')
print(f"[1] 录制开始 rec_id={rec_id}")

# 用户操作：输入 "Order 5201314 Ready" + Enter + "Ship To: Beijing"
type_text("Order 5201314 Ready")
press_key(0x0D)
type_text("Ship To: Beijing")
time.sleep(3)  # UWP 输入管线延迟，等待事件全部落盘后再停止

r = post(f'{BASE_URL}/record/stop')
steps = r.get('event_count', 0)
print(f"[2] 录制停止，{steps} 步")
assert steps >= 2, "步骤不足"

# 2. 标注变量：找到第一行输入步骤 → {{order_id}}，来源剪贴板
task = json.load(open(r['task_path'], encoding='utf-8'))
steps_list = task.get('steps', [])
target = None
for i, s in enumerate(steps_list):
    if s.get('action') == 'input_text' and '5201314' in s.get('text', ''):
        target = i
        break
assert target is not None, "未找到可变数据输入步骤"
r = put(f'{BASE_URL}/tasks/{rec_id}/vars/{target}?var=order_id')
assert r['ok'], r
r = put(f'{BASE_URL}/tasks/{rec_id}/var_sources/order_id',
        json.dumps({"type": "clipboard"}).encode())
assert r['ok'], r
print(f"[3] 变量标注: step {target} -> {{order_id}} (来源: 剪贴板)")

# 3. 试运行 6 轮（0 纠错状态）
ROUNDS = 6
values = ["88001", "99302", "10403", "77504", "66005", "88106"]
ok_count = 0
results = []

for i in range(ROUNDS):
    val = values[i]
    set_clipboard(f"Order {val} Ready")
    cleanup_notepad()
    hwnd = open_fresh_notepad()
    assert hwnd, f"第{i+1}轮记事本未打开"
    ensure_foreground(hwnd)
    time.sleep(0.5)
    move_window_random(hwnd)

    print(f"[4.{i+1}] 回放（剪贴板={val}，窗口已移动）...")
    ok, detail = replay_and_verify(rec_id, hwnd, f"Order {val} Ready")
    expected = f"Order {val} Ready\nShip To: Beijing"
    # replay_and_verify 检查含第一行；这里再核对完整两行
    text = detail
    if ok and "Ship To: Beijing" in text:
        ok_count += 1
        print(f"  -> 成功: {text!r}")
    else:
        print(f"  -> 失败: {text!r}")
    results.append(ok and "Ship To: Beijing" in text)
    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    time.sleep(1)

cleanup_notepad()
rate = ok_count / ROUNDS
print("-" * 60)
print(f"验收A结果: {ok_count}/{ROUNDS} 成功 = {rate*100:.0f}%")
print(f"期望: >50% (PRD 首次试运行成功率)")
if rate > 0.5:
    print("=== 验收A: PASS ===")
else:
    print("=== 验收A: FAIL ===")
    sys.exit(1)
