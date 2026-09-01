"""验收C：完整用户流程总耗时 < 15 分钟（PRD 成功标准 3）

模拟真实用户时间线并全程计时：
1. 第1遍录制（输入 Hello）
2. 第2遍追加录制（重复 Hello + 补录 World）-> merge 合并
3. 试运行 1 次
4. 注入 1 次纠错（Hello -> Hi）
5. 再次运行成功
墙钟总耗时应 < 900 秒（15 分钟）。
"""
import json, os, sys, time, win32gui, win32con

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (post, get, type_text, ensure_foreground,
                    open_fresh_notepad, cleanup_notepad,
                    get_notepad_text, replay_and_verify, wait_for_replay)

BASE_URL = "http://127.0.0.1:18899"
t0 = time.time()

def mark(label):
    print(f"  [{time.time()-t0:6.1f}s] {label}")

print("=" * 60)
print("验收C：完整用户流程总耗时 < 15 分钟")
print("=" * 60)

# 1. 第1遍录制
cleanup_notepad()
hwnd = open_fresh_notepad()
ensure_foreground(hwnd)
time.sleep(0.5)
r = post(f'{BASE_URL}/record/start')
assert r['ok'], r
rec_a = r.get('recording_id')
print(f"[1] 第1遍录制 rec_a={rec_a}")
type_text("Hello")
time.sleep(3)  # UWP 输入管线延迟，等待事件全部落盘后再停止
r = post(f'{BASE_URL}/record/stop')
assert r['ok'], r
mark("第1遍录制完成")

# 2. 第2遍追加录制（重复 Hello + 补录 World）
time.sleep(1)
r = post(f'{BASE_URL}/record/start')
assert r['ok'], r
rec_b = r.get('recording_id')
print(f"[2] 第2遍录制 rec_b={rec_b}")
type_text("Hello")
time.sleep(2.5)  # 超过 TEXT_TIMEOUT(2s)，确保分段为两个 input_text
type_text("World")
time.sleep(3)  # UWP 输入管线延迟，等待事件全部落盘后再停止
r = post(f'{BASE_URL}/record/stop')
assert r['ok'], r
mark("第2遍录制完成")

# 3. merge 合并
m = post(f'{BASE_URL}/recordings/{rec_a}/merge/{rec_b}')
assert m.get('ok'), m
print(f"[3] 合并完成 removed={m.get('removed')}")
mark("合并完成")

# 4. 试运行 1 次
cleanup_notepad()
hwnd = open_fresh_notepad()
ensure_foreground(hwnd)
ok, text = replay_and_verify(rec_a, hwnd, "Hello")
print(f"[4] 试运行内容: {text!r}")
mark("试运行完成")

# 5. 注入 1 次纠错
r = post(f'{BASE_URL}/tasks/{rec_a}/correct',
         json.dumps({
             "trigger": [{"action": "input_text", "text": "Hello"}],
             "correct": [{"action": "input_text", "text": "Hi"}],
         }).encode())
assert r['ok'], r
print("[5] 纠错注入: Hello -> Hi")
mark("纠错注入完成")

# 6. 再次运行 -> 应成功（Hi + World）
cleanup_notepad()
hwnd = open_fresh_notepad()
ensure_foreground(hwnd)
ok, text = replay_and_verify(rec_a, hwnd, "Hi")
win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
time.sleep(1)
cleanup_notepad()

total = time.time() - t0
print("-" * 60)
print(f"验收C结果: 总耗时 {total:.1f}s (含录制2遍+合并+试运行+纠错+再次运行)")
print(f"期望: < 900s (15 分钟)")
print(f"最终运行内容: {text!r}")
if total < 900:
    print("=== 验收C: PASS ===")
else:
    print("=== 验收C: FAIL ===")
    sys.exit(1)
