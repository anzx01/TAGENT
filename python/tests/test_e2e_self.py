"""自定义 E2E(2026-10 迭代功能覆盖):真实录制 → 编译 → 回放全链路。

场景A 变量注入:录制固定文本 → task.json 改成 {{order_id}} → 带变量回放,
     验证上屏的是变量值而非录制值;重复回放验证幂等(replace 清旧内容)。
场景B 断点续跑:三步任务(输入/回车/输入)回放中途 stop → checkpoint 落盘,
     重开记事本后 from_step 续跑,验证只补跑剩余步骤。
场景C 定时调度:方案审阅批准 → interval=1min 定时 → 等待触发,
     验证 trigger=schedule 的运行报告与窗口内容,最后注销调度。

依赖:后端 127.0.0.1:18899 运行中(源码模式);会在真实桌面上合成键鼠。
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import win32con
import win32gui

BASE_URL = "http://127.0.0.1:18899"
TOKEN_A_REC = "E2EVAR-0001"
TOKEN_A_VAR = "E2EVAR-9999"
TOKEN_B1 = "PART1TOKEN"
TOKEN_B2 = "PART2TOKEN-0123456789"


def post(url, payload=None):
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, method="POST", data=data,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def put(url, payload=None):
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, method="PUT", data=data,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def api_delete(url):
    req = urllib.request.Request(url, method="DELETE")
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=15).read())


def type_text(text):
    u32 = ctypes.windll.user32
    for char in text:
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
        time.sleep(0.04)


def press_key(vk):
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    ctypes.windll.user32.keybd_event(vk, scan, 0, 0)
    time.sleep(0.02)
    ctypes.windll.user32.keybd_event(vk, scan, 2, 0)


def bring_to_front(hwnd):
    u32 = ctypes.windll.user32
    u32.keybd_event(0x12, 0, 0, 0)
    u32.keybd_event(0x12, 0, 0x0002, 0)
    u32.ShowWindow(hwnd, 9)
    u32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    u32.keybd_event(0x12, 0, 0x0002, 0)


def ensure_foreground(hwnd, retries=6, gap=0.5):
    for _ in range(retries):
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        bring_to_front(hwnd)
        time.sleep(gap)
    return win32gui.GetForegroundWindow() == hwnd


def find_notepad_by_filename(filename):
    found = []
    def cb(h, l):
        try:
            t = win32gui.GetWindowText(h)
            if filename in t and "Notepad" in t:
                l.append(h)
        except Exception:
            pass
        return True
    win32gui.EnumWindows(cb, found)
    return found[0] if found else None


def get_notepad_text(hwnd):
    try:
        import uiautomation as ua
        focused = ua.GetFocusedControl()
        if focused and focused.ControlTypeName in ("EditControl", "DocumentControl", "TextControl"):
            try:
                return focused.GetValuePattern().Value or ""
            except Exception:
                pass
        root = ua.ControlFromHandle(hwnd)
        if root:
            edit = root.EditControl(searchDepth=10)
            if edit.Exists(1.0):
                try:
                    return edit.GetValuePattern().Value or ""
                except Exception:
                    pass
    except Exception:
        pass
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
    return ""


def cleanup_notepad():
    subprocess.run(["taskkill", "/IM", "Notepad.exe", "/F", "/T"], capture_output=True)
    time.sleep(1)
    pkg_root = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages",
                            "Microsoft.WindowsNotepad_8wekyb3d8bbwe", "LocalState")
    for sub in ("TabState", "WindowState"):
        d = os.path.join(pkg_root, sub)
        if os.path.isdir(d):
            for f in os.listdir(d):
                p = os.path.join(d, f)
                try:
                    shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
                except OSError:
                    pass


def open_fresh_notepad(tag):
    fname = f"tagent_self_e2e_{os.getpid()}_{tag}.txt"
    fpath = os.path.join(tempfile.gettempdir(), fname)
    with open(fpath, "w", encoding="utf-8"):
        pass
    subprocess.Popen(["notepad.exe", fpath])
    time.sleep(2.5)
    hwnd = find_notepad_by_filename(fname)
    if hwnd:
        press_key(0x1B)
        time.sleep(0.2)
        ensure_foreground(hwnd)
    return fname, fpath, hwnd


def wait_replay_done(timeout=60, auto_resume=True):
    """轮询到回放结束;意外暂停时自动跳过(最多3次)并记录。"""
    resumes = 0
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        s = get(f"{BASE_URL}/replay/status")
        if s.get("paused") and auto_resume and resumes < 3:
            resumes += 1
            print(f"    [auto-resume #{resumes}] paused: {s.get('pause_reason')}")
            post(f"{BASE_URL}/replay/resume")
            continue
        if not s.get("playing"):
            return s, resumes
    raise TimeoutError("replay did not finish")


def record_typing(rec_text, tag, extra_keys=None):
    """打开记事本→录制输入→停止编译,返回 (recording_id, 文件名, 文件路径)。"""
    fname, fpath, hwnd = open_fresh_notepad(tag)
    assert hwnd, "记事本未打开(录制前置失败)"
    ensure_foreground(hwnd)
    r = post(f"{BASE_URL}/record/start")
    assert r.get("ok"), f"录制启动失败: {r}"
    rec_id = r["recording_id"]
    time.sleep(1.5)
    type_text(rec_text)
    for vk in (extra_keys or []):
        time.sleep(0.3)
        press_key(vk)
    time.sleep(0.5)
    stop = post(f"{BASE_URL}/record/stop")
    assert stop.get("ok") and stop.get("replayable"), f"录制停止失败: {stop}"
    return rec_id, fname, fpath


def main():
    subprocess.run(["reg", "add", r"HKCU\Software\Microsoft\Notepad",
                    "/v", "RestoreSession", "/t", "REG_DWORD", "/d", "0", "/f"],
                   capture_output=True)
    results = []
    made = {"recordings": [], "tempfiles": []}

    try:
        # ================= 场景A:变量注入 + 幂等 =================
        print("\n===== 场景A:变量注入回放 =====")
        cleanup_notepad()
        rec_a, fname_a, fpath_a = record_typing(TOKEN_A_REC, "a")
        made["recordings"].append(rec_a)
        made["tempfiles"].append(fpath_a)
        task_file = os.path.join("recordings", rec_a, "task.json")
        task = json.loads(open(task_file, encoding="utf-8").read())
        input_steps = [i for i, s in enumerate(task["steps"])
                       if s.get("action") == "input_text" and TOKEN_A_REC in s.get("text", "")]
        assert len(input_steps) == 1, f"录制文本未落在单个输入步骤: {[s.get('action') for s in task['steps']]}"
        # 正规变量路径:变量标注 API(内部会刷新 plan provenance;手改 task.json 会被防护拒绝)
        r = put(f"{BASE_URL}/tasks/{rec_a}/vars/{input_steps[0]}?var=order_id")
        assert r.get("ok") and "order_id" in json.dumps(r.get("vars", [])), f"变量标注失败: {r}"
        print(f"  [A.1] 录制 {rec_a} 完成,步骤 {input_steps[0]} 已通过 API 标注为 {{{{ order_id }}}}")

        r = post(f"{BASE_URL}/replay/start", {"recording_id": rec_a,
                                              "variables": {"order_id": TOKEN_A_VAR}})
        assert r.get("ok"), f"变量回放启动失败: {r}"
        s, resumes = wait_replay_done()
        hwnd = find_notepad_by_filename(fname_a)
        content = get_notepad_text(hwnd) if hwnd else ""
        assert TOKEN_A_VAR in content and TOKEN_A_REC not in content, \
            f"[A.2] 变量回放内容错误: {content!r} (auto_resume={resumes})"
        print(f"  [A.2] 变量回放上屏正确: {content!r}")

        r = post(f"{BASE_URL}/replay/start", {"recording_id": rec_a,
                                              "variables": {"order_id": TOKEN_A_VAR}})
        assert r.get("ok"), f"第二次回放启动失败: {r}"
        wait_replay_done()
        content2 = get_notepad_text(hwnd) if hwnd else ""
        assert content2.count(TOKEN_A_VAR) == 1, f"[A.3] 幂等失败,内容重复: {content2!r}"
        print(f"  [A.3] 重复回放幂等: 内容仍为单次 {content2!r}")

        runs = get(f"{BASE_URL}/tasks/{rec_a}/runs")["runs"]
        assert len(runs) == 2, f"运行记录数错误: {len(runs)}"
        detail = get(f"{BASE_URL}/tasks/{rec_a}/runs/{runs[0]['run_id']}")
        assert detail["ok"] and "steps" in detail["report"]
        assert TOKEN_A_VAR not in json.dumps(detail, ensure_ascii=False), "报告泄漏变量值"
        print("  [A.4] 两条运行记录 + 详情脱敏通过")
        results.append(("A 变量注入+幂等+脱敏", True, ""))
        cleanup_notepad()

        # ================= 场景B:中断 → 断点续跑 =================
        print("\n===== 场景B:中断断点续跑 =====")
        # 单次录制三步:输入 PART1 → 回车 → 输入 PART2(回车把输入步骤隔开,编译为三步)
        fname_b, fpath_b, hwnd = open_fresh_notepad("b")
        made["tempfiles"].append(fpath_b)
        assert hwnd, "记事本未打开(场景B前置失败)"
        ensure_foreground(hwnd)
        r = post(f"{BASE_URL}/record/start")
        assert r.get("ok"), f"录制启动失败: {r}"
        rec_b = r["recording_id"]
        made["recordings"].append(rec_b)
        time.sleep(1.5)
        type_text(TOKEN_B1)
        time.sleep(0.3)
        press_key(0x0D)
        time.sleep(0.3)
        type_text(TOKEN_B2)
        time.sleep(0.5)
        stop = post(f"{BASE_URL}/record/stop")
        assert stop.get("ok") and stop.get("replayable"), f"录制停止失败: {stop}"
        task_b = json.loads(open(os.path.join("recordings", rec_b, "task.json"),
                                 encoding="utf-8").read())
        assert len(task_b["steps"]) == 3, \
            f"[B.1] 编译未产出三步: {[s.get('action') for s in task_b['steps']]}"
        print(f"  [B.1] 三步任务编译完成: {[s.get('action') for s in task_b['steps']]}")

        _, _, hwnd = open_fresh_notepad("b")  # 同名文件重开,回放目标
        made["tempfiles"].append(os.path.join(tempfile.gettempdir(),
                                              f"tagent_self_e2e_{os.getpid()}_b.txt"))
        r = post(f"{BASE_URL}/replay/start", {"recording_id": rec_b})
        assert r.get("ok"), f"场景B回放启动失败: {r}"
        stopped_at = None
        deadline = time.time() + 30
        while time.time() < deadline:
            st = get(f"{BASE_URL}/replay/status")
            if st.get("current_step", 0) >= 1:
                post(f"{BASE_URL}/replay/stop")
                stopped_at = st["current_step"]
                break
            time.sleep(0.08)
        assert stopped_at is not None, "未能拦截到回放中途"
        wait_replay_done(timeout=15)
        ckpt_path = os.path.join("data", f"ckpt_{rec_b}.json")
        ckpt = json.loads(open(ckpt_path, encoding="utf-8").read()) if os.path.exists(ckpt_path) else None
        assert ckpt and "next_step" in ckpt, f"[B.2] 中断后无断点: {ckpt}"
        print(f"  [B.2] 于步骤 {stopped_at} 拦截停止,checkpoint next_step={ckpt['next_step']}")

        cleanup_notepad()  # 丢弃未保存内容,重开空文件验证"只补跑剩余步骤"
        _, _, hwnd = open_fresh_notepad("b")
        r = post(f"{BASE_URL}/replay/start", {"recording_id": rec_b,
                                              "from_step": ckpt["next_step"]})
        assert r.get("ok"), f"续跑启动失败: {r}"
        wait_replay_done()
        content = get_notepad_text(hwnd) if hwnd else ""
        assert TOKEN_B1 not in content, f"[B.3] 续跑重放了已完成步骤: {content!r}"
        assert TOKEN_B2 in content, f"[B.3] 续跑未补跑剩余步骤: {content!r}"
        print(f"  [B.3] 续跑只补跑剩余步骤,内容: {content!r}")
        results.append(("B 中断断点续跑", True, ""))

        # ================= 场景C:定时调度触发 =================
        print("\n===== 场景C:定时调度 E2E(interval=1min) =====")
        review = get(f"{BASE_URL}/tasks/{rec_b}/review")
        if not review.get("review", {}).get("approved"):
            r = put(f"{BASE_URL}/tasks/{rec_b}/review", {"approved": True})
            assert r.get("ok"), f"方案批准失败: {r}"
        r = put(f"{BASE_URL}/tasks/{rec_b}/schedule",
                {"kind": "interval", "minutes": 1})
        assert r.get("ok"), f"调度设置失败: {r}"
        life = get(f"{BASE_URL}/lifecycle")
        assert rec_b in life.get("schedules", {}), f"lifecycle 未出现调度: {life}"
        print("  [C.1] 调度已注册,等待定时触发(最长 150s)...")

        before = {run["run_id"] for run in get(f"{BASE_URL}/tasks/{rec_b}/runs")["runs"]}
        fired = None
        deadline = time.time() + 150
        while time.time() < deadline:
            time.sleep(5)
            runs = get(f"{BASE_URL}/tasks/{rec_b}/runs")["runs"]
            new = [run for run in runs if run["run_id"] not in before]
            if new and new[0].get("trigger") == "schedule":
                fired = new[0]
                break
        assert fired, "定时运行未在期限内触发"
        s = get(f"{BASE_URL}/replay/status")
        if s.get("playing"):
            wait_replay_done(timeout=60)
        time.sleep(1)
        runs = get(f"{BASE_URL}/tasks/{rec_b}/runs")["runs"]
        sched_run = next(run for run in runs if run["run_id"] == fired["run_id"])
        print(f"  [C.2] 定时运行触发: trigger={sched_run['trigger']} "
              f"success={sched_run['success']} steps={sched_run['step_count']}")
        assert sched_run["trigger"] == "schedule"
        content = get_notepad_text(hwnd) if hwnd else ""
        assert TOKEN_B1 in content and TOKEN_B2 in content, \
            f"[C.3] 定时回放内容不完整: {content!r}"
        print(f"  [C.3] 定时回放窗口内容完整: {content!r}")

        d = api_delete(f"{BASE_URL}/tasks/{rec_b}/schedule")
        assert d.get("ok"), f"调度注销失败: {d}"
        life = get(f"{BASE_URL}/lifecycle")
        assert rec_b not in life.get("schedules", {}), "调度注销后仍在 lifecycle"
        print("  [C.4] 调度已注销,lifecycle 已清空该任务")
        results.append(("C 定时调度触发+报告+注销", True, ""))
    except AssertionError as exc:
        results.append(("失败", False, str(exc)))
        raise
    finally:
        print("\n===== 清理 =====")
        for rec_id in made["recordings"]:
            try:
                api_delete(f"{BASE_URL}/recordings/{rec_id}")
                print(f"  已删除录制 {rec_id}")
            except Exception as exc:
                shutil.rmtree(os.path.join("recordings", rec_id), ignore_errors=True)
                print(f"  已删除录制目录 {rec_id} (API 失败: {exc})")
        for f in made["tempfiles"]:
            try:
                os.remove(f)
            except OSError:
                pass
        cleanup_notepad()

    print("\n===== 自定义 E2E 结果 =====")
    for name, ok, msg in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name} {msg}")
    if not all(ok for _, ok, _ in results):
        sys.exit(1)
    print("自定义 E2E 全部通过")


if __name__ == "__main__":
    main()
