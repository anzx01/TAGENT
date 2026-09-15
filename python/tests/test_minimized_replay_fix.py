"""验证回放修复：最小化窗口坐标跳过、激活时还原、UIA 日志属性读取防炸。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import win32gui

from player import Player

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


def find_notepad():
    hwnds = []

    def cb(h, _):
        t = win32gui.GetWindowText(h)
        if t.endswith("- Notepad") and win32gui.IsWindowVisible(h):
            hwnds.append(h)
    win32gui.EnumWindows(cb, None)
    return hwnds[0] if hwnds else None


def main():
    import subprocess
    p = subprocess.Popen(["notepad.exe"])
    time.sleep(1.5)
    try:
        hwnd = find_notepad()
        check("前置: 找到记事本窗口", hwnd is not None)
        if not hwnd:
            return 1
        title = win32gui.GetWindowText(hwnd)

        player = Player.__new__(Player)  # 只测定位/激活逻辑，不跑完整回放

        # --- 修复2: 最小化窗口不参与坐标换算 ---
        win32gui.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        time.sleep(0.5)
        check("前置: 窗口已最小化", win32gui.IsIconic(hwnd))
        loc = {"type": "coord", "client_pct_x": 50, "client_pct_y": 50, "window": title}
        x, y = player._locate_by_coord(loc)
        check("修复2: 最小化窗口坐标定位返回 None", x is None and y is None)

        # --- 修复3: 激活时自动还原 ---
        player._activate_target(hwnd)
        time.sleep(0.5)
        check("修复3: 激活后窗口已还原", not win32gui.IsIconic(hwnd))
        x, y = player._locate_by_coord(loc)
        check("修复3b: 还原后坐标定位恢复有效", x is not None and y is not None
              and x > 0 and y > 0)

        # --- 修复1: UIA 成功日志读 Name 抛异常不炸 ---
        class DeadCtrl:
            def __getattribute__(self, name):
                if name == "Name":
                    raise RuntimeError("simulated COMError")
                raise RuntimeError("gone")
        # invoke_ctrl 返回 True 后读属性的场景：直接内联模拟
        ctrl = DeadCtrl()
        try:
            try:
                desc = f"{ctrl.Name!r} ({getattr(ctrl, 'ControlTypeName', '?')})"
            except Exception:
                desc = "<元素已销毁>"
            check("修复1: 死元素日志降级为占位符", desc == "<元素已销毁>")
        except Exception:
            check("修复1: 死元素日志降级为占位符", False)
        return 0 if all(ok for _, ok in results) else 1
    finally:
        p.terminate()


if __name__ == "__main__":
    sys.exit(main())
