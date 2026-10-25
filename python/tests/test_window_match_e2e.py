"""标题漂移全链路实测（需真实桌面与 18899 端口，设 TAGENT_LIVE_WINDOW_TEST=1 才运行）。

与 test_window_match_live 的区别：这次走运行中的 HTTP 服务——
1. 门禁对漂移标题放行（录制带状态后缀、实际窗口没有），回放自动完成不弹接管；
2. 真缺窗口时门禁仍然拦截（不误放）；
3. 跳过门禁强跑时暂停态为 call_user，且 /replay/status 带具体诊断 pause_detail。
"""
import json
import os
import shutil
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

BASE = Path(__file__).resolve().parents[1].parent  # 仓库根（server 源码模式的 BASE_DIR）
PY = BASE / ".venv" / "Scripts" / "python.exe"
URL = "http://127.0.0.1:18899"
DRIFT = "漂移测试 - 演示程序 [增强模式]"
REAL = "漂移测试 - 演示程序"


def wait_port(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{URL}/replay/status", timeout=1).status_code == 200:
                return True
        except Exception:
            time.sleep(0.3)
    return False


def main() -> int:
    if os.environ.get("TAGENT_LIVE_WINDOW_TEST") != "1":
        print("window_match_e2e SKIP (set TAGENT_LIVE_WINDOW_TEST=1)")
        return 0

    # ---- 准备：若已有服务在跑则用令牌停掉，起干净的源码服务 ----
    token = ""
    if wait_port(2):
        token = httpx.get(f"{URL}/api-token", timeout=2).json()["token"]
        httpx.post(f"{URL}/shutdown", headers={"X-TAGENT-Token": token}, timeout=3)
        time.sleep(1.5)
    server = subprocess.Popen(
        [str(PY), "server.py"], cwd=str(BASE / "python"),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert wait_port(), "后端未在 30s 内就绪"
    token = httpx.get(f"{URL}/api-token", timeout=2).json()["token"]

    rec_ok = BASE / "recordings" / time.strftime("%Y%m%d_%H%M%S")
    rec_missing = BASE / "recordings" / time.strftime("%Y%m%d_%H%M%S", time.localtime(time.time() + 1))
    for d in (rec_ok, rec_missing):
        assert not d.exists(), f"与真实录像冲突: {d}"
    ok_id, missing_id = rec_ok.name, rec_missing.name
    runs = BASE / "runs"
    try:
        for d in (rec_ok, rec_missing):
            d.mkdir(parents=True, exist_ok=True)
        (rec_ok / "task.json").write_text(json.dumps({"steps": [
            {"action": "switch_window", "window": DRIFT, "step_id": "s0"},
            {"action": "wait", "duration": 0.5, "step_id": "s1"},
        ]}, ensure_ascii=False), encoding="utf-8")
        (rec_missing / "task.json").write_text(json.dumps({"steps": [
            {"action": "switch_window", "window": "不存在窗口XYZ - 应用", "step_id": "s0"},
        ]}, ensure_ascii=False), encoding="utf-8")

        root = tk.Tk()
        root.title(REAL)
        root.geometry("320x200+80+80")
        root.update()

        def pump(sec):
            end = time.time() + sec
            while time.time() < end:
                root.update()
                time.sleep(0.05)

        try:
            # ---- 1. 漂移标题：门禁放行 + 回放自动完成，无接管 ----
            r = httpx.post(f"{URL}/replay/start",
                           json={"recording_id": ok_id}, timeout=5).json()
            assert r.get("ok"), f"门禁误拦漂移标题: {r}"
            paused_seen = []
            deadline = time.time() + 60
            while time.time() < deadline:
                st = httpx.get(f"{URL}/replay/status", timeout=2).json()
                root.update()
                if st.get("paused"):
                    paused_seen.append(st.get("state"))
                if not st.get("playing"):
                    break
                pump(0.2)
            assert not paused_seen, f"回放中途弹接管: {paused_seen}"
            # 排除 .context.json（计划上下文），取真正的运行报告
            reports = [f for f in runs.glob(f"*{ok_id}*.json")
                       if not f.name.endswith(".context.json")]
            assert reports, "未生成运行报告"
            rep = json.loads(reports[0].read_text(encoding="utf-8"))
            assert rep.get("success") is True, rep.get("steps")
            print("  1. 漂移标题门禁放行+自动回放完成: PASS")

            # ---- 2. 真缺窗口：门禁必须拦 ----
            r = httpx.post(f"{URL}/replay/start",
                           json={"recording_id": missing_id}, timeout=5).json()
            assert not r.get("ok") and "前置状态不符" in r.get("msg", ""), r
            print("  2. 真缺窗口门禁拦截: PASS")

            # ---- 3. 跳过门禁强跑：call_user 暂停 + 具体诊断 ----
            r = httpx.post(f"{URL}/replay/start",
                           json={"recording_id": missing_id,
                                 "skip_preflight": True}, timeout=5).json()
            assert r.get("ok"), r
            detail = ""
            # switch_window 失败重试链每轮约 7-8s（标题等待 5s+pywinauto 2s），
            # ACTION_RETRIES=4，到暂停需 ~40s
            deadline = time.time() + 90
            while time.time() < deadline:
                st = httpx.get(f"{URL}/replay/status", timeout=2).json()
                root.update()
                if st.get("state") == "call_user":
                    detail = st.get("pause_detail", "")
                    break
                pump(0.4)
            assert detail and "当前前台是" in detail, f"诊断缺失: {detail!r}"
            assert "不存在窗口XYZ" not in detail  # 诊断不含不可达目标的噪音
            httpx.post(f"{URL}/replay/stop", timeout=5)
            print(f"  3. 强跑暂停诊断: PASS -> {detail[:60]}…")
        finally:
            root.destroy()

        httpx.post(f"{URL}/shutdown", headers={"X-TAGENT-Token": token}, timeout=3)
    finally:
        shutil.rmtree(rec_ok, ignore_errors=True)
        shutil.rmtree(rec_missing, ignore_errors=True)
        for f in list(runs.glob(f"*{ok_id}*")) + list(runs.glob(f"*{missing_id}*")):
            f.unlink(missing_ok=True)

    print("window_match_e2e PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
