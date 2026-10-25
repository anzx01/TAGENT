"""靶标应用 E2E:录制→编译→回放→状态文件验证(确定性,无坐标依赖)。

依赖:后端 127.0.0.1:18899(源码模式);真实键鼠合成。不在 run_tests.sh 内,
与其它 e2e 一样按需手动运行:python tests/test_target_e2e.py
"""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import win32gui

BASE_URL = "http://127.0.0.1:18899"
TOKEN = "TARGET-E2E-9527"


def post(url, payload=None):
    req = urllib.request.Request(url, method="POST", data=json.dumps(payload or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=15).read())


def type_chars(text):
    u32 = ctypes.windll.user32
    for ch in text:
        scan_code = u32.VkKeyScanW(ord(ch))
        vk, shift = scan_code & 0xFF, (scan_code >> 8) & 1
        scan = u32.MapVirtualKeyW(vk, 0)
        if shift:
            u32.keybd_event(0x10, 0, 0, 0)
        u32.keybd_event(vk, scan, 0, 0)
        u32.keybd_event(vk, scan, 2, 0)
        if shift:
            u32.keybd_event(0x10, 0, 2, 0)
        time.sleep(0.05)


def press(vk):
    u32 = ctypes.windll.user32
    u32.keybd_event(vk, u32.MapVirtualKeyW(vk, 0), 0, 0)
    time.sleep(0.02)
    u32.keybd_event(vk, u32.MapVirtualKeyW(vk, 0), 2, 0)


def ime_off(hwnd):
    """对目标窗口线程关闭输入法打开状态(否则字母进入组合缓冲,Entry 收不到)。"""
    imm = ctypes.windll.imm32
    hime = imm.ImmGetDefaultIMEWnd(hwnd)
    if hime:
        ctypes.windll.user32.SendMessageW(hime, 0x283, 0x06, 0)  # WM_IME_CONTROL, IMC_SETOPENSTATUS(0)


def find_target():
    return win32gui.FindWindow(None, "TAGENT靶标")


def foreground(hwnd):
    u32 = ctypes.windll.user32
    u32.keybd_event(0x12, 0, 0, 0)
    u32.keybd_event(0x12, 0, 2, 0)
    u32.ShowWindow(hwnd, 9)
    u32.SetForegroundWindow(hwnd)
    time.sleep(0.5)


def main():
    state = Path(tempfile.gettempdir()) / "tagent_target_state.txt"
    state.unlink(missing_ok=True)
    app = [None]

    def launch():
        state.unlink(missing_ok=True)
        app[0] = subprocess.Popen([sys.executable, str(Path(__file__).parent / "fixtures" / "target_app.py"),
                                   str(state)])
        for _ in range(30):
            time.sleep(0.3)
            hwnd = find_target()
            if hwnd:
                foreground(hwnd)
                ime_off(hwnd)
                return hwnd
        raise AssertionError("靶标应用未启动")

    try:
        # 1. 录制:输入 + 回车提交
        hwnd = launch()
        time.sleep(0.8)
        assert win32gui.GetForegroundWindow() == hwnd, "靶标未在前台"
        rec = post(f"{BASE_URL}/record/start")
        assert rec["ok"]
        time.sleep(1.2)
        type_chars(TOKEN)
        assert win32gui.GetForegroundWindow() == hwnd, "输入期间靶标失去前台"
        time.sleep(2.2)  # 等 1.5s 空闲自动提交
        stop = post(f"{BASE_URL}/record/stop")
        rec_id = stop["recording_id"]
        assert stop["ok"] and stop["replayable"], stop
        assert state.read_text(encoding="utf-8") == f"DONE:{TOKEN}"
        task = json.loads((Path("recordings") / rec_id / "task.json").read_text(encoding="utf-8"))
        assert task["steps"][0].get("step_id"), task["steps"]
        print(f"[1] 录制 {rec_id}: {[s['action'] for s in task['steps']]},状态={state.read_text(encoding='utf-8')!r}")

        # 2. 关闭重开(清空状态),回放应复现提交
        subprocess.run(["taskkill", "/PID", str(app[0].pid), "/F", "/T"], capture_output=True)
        time.sleep(1)
        replay_hwnd = launch()
        ime_off(replay_hwnd)
        r = post(f"{BASE_URL}/replay/start", {"recording_id": rec_id})
        assert r["ok"], r
        for _ in range(40):
            time.sleep(1)
            if not get(f"{BASE_URL}/replay/status")["playing"]:
                break
        assert state.exists() and state.read_text(encoding="utf-8") == f"DONE:{TOKEN}", \
            f"回放未复现提交: {state.read_text(encoding='utf-8') if state.exists() else '<无状态文件>'!r}"
        runs = get(f"{BASE_URL}/tasks/{rec_id}/runs")["runs"]
        assert runs and runs[0]["success"]
        assert runs[0].get("baseline_pass") is None, runs[0]  # 首次成功=建立基线
        print(f"[2] 回放复现提交 success={runs[0]['success']}(基线已建立)")
        # 第二次回放与基线比对:动作序列须与黄金基线一致
        subprocess.run(["taskkill", "/PID", str(app[0].pid), "/F", "/T"], capture_output=True)
        time.sleep(1)
        replay_hwnd = launch()
        ime_off(replay_hwnd)
        r = post(f"{BASE_URL}/replay/start", {"recording_id": rec_id})
        assert r["ok"], r
        for _ in range(40):
            time.sleep(1)
            if not get(f"{BASE_URL}/replay/status")["playing"]:
                break
        assert state.read_text(encoding="utf-8") == f"DONE:{TOKEN}"
        runs = get(f"{BASE_URL}/tasks/{rec_id}/runs")["runs"]
        assert runs[0].get("baseline_pass") is True, runs[0]
        print(f"[2b] 二次回放与黄金基线比对通过 baseline_pass={runs[0]['baseline_pass']}")

        # 3. 门禁:关闭靶标后直接回放应被拒绝
        subprocess.run(["taskkill", "/PID", str(app[0].pid), "/F", "/T"], capture_output=True)
        time.sleep(1)
        r = post(f"{BASE_URL}/replay/start", {"recording_id": rec_id})
        assert not r["ok"] and "前置状态不符" in r["msg"], r
        print(f"[3] 门禁拦截生效: {r['msg'][:40]}")
        print("target_e2e PASS")
    finally:
        if app[0] and app[0].poll() is None:
            subprocess.run(["taskkill", "/PID", str(app[0].pid), "/F", "/T"], capture_output=True)
        try:
            subprocess.run(["curl", "-s", "-X", "DELETE", f"{BASE_URL}/recordings/{rec_id}"], capture_output=True)
        except Exception:
            pass
        state.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
