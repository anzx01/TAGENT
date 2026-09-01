"""验收B：最多 3 次纠错后自主成功率 > 90%（PRD 成功标准 3）

任务：记事本输入 "hlelo"（模拟用户操作有误）。
0 纠错试运行 -> 内容错误（失败）。
注入 1 次纠错（trigger=hlelo -> correct=hello）后试运行 6 轮，成功率应 > 90%。
"""
import json, os, sys, time, win32gui, win32con

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (post, get, type_text, press_key, ensure_foreground,
                    open_fresh_notepad, cleanup_notepad,
                    get_notepad_text, replay_and_verify)

BASE_URL = "http://127.0.0.1:18899"

print("=" * 60)
print("验收B：3 次纠错后成功率 > 90%")
print("=" * 60)

# 1. 录制任务（模拟用户操作有误：第2步输入 hlelo）
# 注意：player 对第 0 步不应用纠错（防误纠正任务入口），故错误放在第 2 步
cleanup_notepad()
hwnd = open_fresh_notepad()
assert hwnd, "记事本未打开"
ensure_foreground(hwnd)
time.sleep(0.5)

r = post(f'{BASE_URL}/record/start')
assert r['ok'], r
rec_id = r.get('recording_id')
print(f"[1] 录制开始 rec_id={rec_id}")

type_text("Start")
press_key(0x0D)
type_text("hlelo")
time.sleep(3)  # UWP 输入管线延迟，等待事件全部落盘后再停止

r = post(f'{BASE_URL}/record/stop')
steps = r.get('event_count', 0)
print(f"[2] 录制停止，{steps} 步")
assert steps >= 2, "步骤不足"

# 2. 0 纠错试运行 1 轮 -> 应为失败（内容 hlelo 而非 hello）
print("[3] 0 纠错试运行（预期失败：内容为 hlelo）...")
cleanup_notepad()
hwnd = open_fresh_notepad()
ensure_foreground(hwnd)
ok, text = replay_and_verify(rec_id, hwnd, "hello")
print(f"  内容: {text!r}")
first_fail = ("hello" in text)
if first_fail:
    print("  注意：0 纠错居然成功（本应失败），继续纠错验证")

# 3. 注入 1 次纠错
r = post(f'{BASE_URL}/tasks/{rec_id}/correct',
         json.dumps({
             "trigger": [{"action": "input_text", "text": "hlelo"}],
             "correct": [{"action": "input_text", "text": "hello"}],
         }).encode())
assert r['ok'], r
print(f"[4] 已注入纠错: hlelo -> hello")

# 4. 纠错后试运行 6 轮
ROUNDS = 6
ok_count = 0
for i in range(ROUNDS):
    cleanup_notepad()
    hwnd = open_fresh_notepad()
    ensure_foreground(hwnd)
    ok, text = replay_and_verify(rec_id, hwnd, "hello")
    if ok:
        ok_count += 1
        print(f"  [5.{i+1}] 成功: {text!r}")
    else:
        print(f"  [5.{i+1}] 失败: {text!r}")
    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    time.sleep(1)

cleanup_notepad()
rate = ok_count / ROUNDS
print("-" * 60)
print(f"验收B结果: 纠错后 {ok_count}/{ROUNDS} 成功 = {rate*100:.0f}%")
print(f"期望: >90% (PRD: 最多3次纠错后成功率>90%)")
if rate > 0.9:
    print("=== 验收B: PASS ===")
else:
    print("=== 验收B: FAIL ===")
    sys.exit(1)
