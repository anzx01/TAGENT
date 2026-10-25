"""标题漂移容忍实机验证（需真实桌面，设 TAGENT_LIVE_WINDOW_TEST=1 才运行，
与 e2e 同类：不在 run_tests.sh 常规回归中）。

真实 win32 + tkinter 固件窗口，验证三件事：
1. switch_window 对漂移标题(多状态后缀)能切成功；
2. 百分比定位器对漂移标题仍落在窗口内；
3. 暂停诊断 _pause_detail 报出前台/相似窗口，并落进 pause_reasons。
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tkinter as tk

import player
from player import Player


def make_fixture(title):
    root = tk.Tk()
    root.title(title)
    root.geometry("320x200+80+80")
    root.update()
    time.sleep(0.3)
    return root


def main() -> int:
    if os.environ.get("TAGENT_LIVE_WINDOW_TEST") != "1":
        print("window_match_live SKIP (set TAGENT_LIVE_WINDOW_TEST=1)")
        return 0
    base = "漂移测试 - 演示程序"
    drifted = base + " [增强模式]"
    root = make_fixture(base)
    try:
        hwnd = int(root.frame(), 16) if False else player.win32gui.FindWindow(None, base)
        assert hwnd, "tkinter 固件窗口未找到"

        # 1. 漂移标题 switch_window 成功
        p = object.__new__(Player)
        p._stopped = False
        ok = p._do_switch_window({"action": "switch_window", "window": drifted})
        assert ok, "漂移标题切换失败"
        assert player.win32gui.GetForegroundWindow() == hwnd, "前台不是固件窗口"

        # 2. 百分比定位器：漂移标题仍能定位到窗口中心
        x, y = p._locate_by_coord({"client_pct_x": 50, "client_pct_y": 50, "window": drifted})
        rect = player.win32gui.GetWindowRect(hwnd)
        assert rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3], (x, y, rect)

        # 2.5 同应用唯一窗口兜底（WPS 标签页模式：文档在既有窗口打开、标题不变）
        ok = p._do_switch_window({"action": "switch_window",
                                  "window": "不存在文档X - 演示程序"})
        assert ok, "同应用唯一窗口兜底失败"
        assert player.win32gui.GetForegroundWindow() == hwnd, "前台不是固件窗口"

        # 3. 暂停诊断：目标不存在 → 报前台；目标漂移存在 → 报相似窗口
        with tempfile.TemporaryDirectory() as td:
            task = Path(td) / "task.json"
            task.write_text(json.dumps({"steps": [
                {"action": "switch_window", "window": "不存在窗口XYZ - 应用"}]}),
                encoding="utf-8")
            pl = Player(task)
            pl._pause_reasons.append({"step": 0, "reason": "执行失败: switch_window (no_window)"})
            # _pause_detail 在暂停期间有效、恢复后清空（与 /replay/status 一致），
            # 因此在 on_pause 回调内捕获
            seen: list[str] = []
            pl.on_pause = lambda i, r: (seen.append(pl._pause_detail),
                                        pl.resume(skip=True))
            # update()/topmost 不改变前台；走生产路径 _force_foreground 置前台
            # （系统前台锁会拒绝裸 SetForegroundWindow，这正是它的用途）
            root.attributes("-topmost", True)
            root.lift()
            root.update()
            assert player._force_foreground(hwnd), "固件窗口无法置前台"
            time.sleep(0.2)
            pl._pause_for_human(0, "执行失败: switch_window (no_window)")
            root.attributes("-topmost", False)
            assert len(seen) == 1, seen
            assert "当前前台是「%s」" % base in seen[0], seen
            assert "相似窗口" not in seen[0], seen
            assert pl._pause_reasons[-1].get("detail") == seen[0]

            task2 = Path(td) / "task2.json"
            task2.write_text(json.dumps({"steps": [
                {"action": "switch_window", "window": drifted}]}), encoding="utf-8")
            pl2 = Player(task2)
            pl2._pause_reasons.append({"step": 0, "reason": "执行失败: switch_window (no_window)"})
            seen2: list[str] = []
            pl2.on_pause = lambda i, r: (seen2.append(pl2._pause_detail),
                                         pl2.resume(skip=True))
            pl2._pause_for_human(0, "执行失败: switch_window (no_window)")
            assert "发现相似窗口「%s」" % base in seen2[0], seen2
    finally:
        root.destroy()
    print("window_match_live PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
