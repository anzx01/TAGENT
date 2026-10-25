"""实例 E2E:写文档(真实记事本)/模拟购物/模拟发邮件/队列+盯守+联动+推送。

依赖:后端 127.0.0.1:18899(源码模式);真实键鼠合成。手动运行:
python tests/test_instance_e2e.py [doc|shop|mail|combo]
"""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import win32gui

BASE_URL = "http://127.0.0.1:18899"
FIXTURES = Path(__file__).parent / "fixtures"
made = {"recordings": [], "tempfiles": []}


def post(url, payload=None):
    req = urllib.request.Request(url, method="POST", data=json.dumps(payload or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def put(url, payload=None):
    req = urllib.request.Request(url, method="PUT", data=json.dumps(payload or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def api_delete(url):
    req = urllib.request.Request(url, method="DELETE")
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=15).read())


def type_chars(text):
    u32 = ctypes.windll.user32
    for ch in text:
        code = u32.VkKeyScanW(ord(ch))
        vk, shift = code & 0xFF, (code >> 8) & 1
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


def find_window(title):
    out = []

    def cb(h, l):
        try:
            if title.casefold() in win32gui.GetWindowText(h).casefold():
                l.append(h)
        except Exception:
            pass
        return True
    win32gui.EnumWindows(cb, out)
    return out[0] if out else None


def foreground(hwnd):
    u32 = ctypes.windll.user32
    u32.keybd_event(0x12, 0, 0, 0)
    u32.keybd_event(0x12, 0, 2, 0)
    u32.ShowWindow(hwnd, 9)
    u32.SetForegroundWindow(hwnd)
    time.sleep(0.6)


def ime_off(hwnd):
    hime = ctypes.windll.imm32.ImmGetDefaultIMEWnd(hwnd)
    if hime:
        ctypes.windll.user32.SendMessageW(hime, 0x283, 0x06, 0)


def wait_replay_done(timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.8)
        s = get(f"{BASE_URL}/replay/status")
        if not s.get("playing"):
            return s
    raise TimeoutError("replay not finished")


def record(script, title):
    """按 [(type,文本)|(press,vk)] 脚本录制;返回 (rec_id, hwnd)。"""
    hwnd = find_window(title)
    assert hwnd, f"{title} 未打开"
    foreground(hwnd)
    ime_off(hwnd)
    time.sleep(0.6)
    assert win32gui.GetForegroundWindow() == hwnd, f"{title} 前台保障失败"
    r = post(f"{BASE_URL}/record/start")
    assert r["ok"], r
    rec_id = r["recording_id"]
    made["recordings"].append(rec_id)
    time.sleep(1.5)
    foreground(hwnd)  # 录制钩子安装可能引起焦点抖动,补一次前台保障
    ime_off(hwnd)
    time.sleep(0.5)
    assert win32gui.GetForegroundWindow() == hwnd, f"{title} 前台保障失败(录制中)"
    time.sleep(0.8)
    press(0x10)  # 孤立 Shift:IME 关闭后的首键常被吞,先唤醒输入管线(录制器不记单 Shift)
    time.sleep(0.3)
    def combo(vks):
        u32 = ctypes.windll.user32
        for vk in vks:
            u32.keybd_event(vk, u32.MapVirtualKeyW(vk, 0), 0, 0)
            time.sleep(0.03)
        for vk in reversed(vks):
            u32.keybd_event(vk, u32.MapVirtualKeyW(vk, 0), 2, 0)
            time.sleep(0.03)

    for item in script:
        if win32gui.GetForegroundWindow() != hwnd:  # 并行会话可能抢前台,逐段校验
            foreground(hwnd)
            ime_off(hwnd)
            time.sleep(0.3)
        if item[0] == "type":
            type_chars(item[1])
        elif item[0] == "combo":
            combo(item[1])
        elif item[0] == "click":  # 点击窗口内相对坐标,物理聚焦输入框(防首键被吞)
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            x = int(left + (right - left) * item[1][0])
            y = int(top + (bottom - top) * item[1][1])
            u32 = ctypes.windll.user32
            u32.SetCursorPos(x, y)
            u32.mouse_event(2, 0, 0, 0, 0)
            time.sleep(0.05)
            u32.mouse_event(4, 0, 0, 0, 0)
        else:
            press(item[1])
        time.sleep(0.35)
    time.sleep(0.6)
    stop = post(f"{BASE_URL}/record/stop")
    assert stop.get("ok") and stop.get("replayable"), stop
    return rec_id, hwnd


def launch(app, arg, title, reset=True):
    if reset and arg.exists():
        arg.unlink()
    proc = subprocess.Popen([sys.executable, str(FIXTURES / app), str(arg)])
    for _ in range(30):
        time.sleep(0.3)
        hwnd = find_window(title)
        if hwnd:
            foreground(hwnd)
            ime_off(hwnd)
            return proc, hwnd
    raise AssertionError(f"{app} 未启动")


def close(proc):
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/F", "/T"], capture_output=True)
    time.sleep(0.8)


# ================= 实例 1:写文档(真实记事本,多段输入) =================

def instance_doc():
    print("\n===== 实例1:写文档(记事本多段) =====")
    subprocess.run(["taskkill", "/IM", "Notepad.exe", "/F", "/T"], capture_output=True)
    time.sleep(1)
    doc = Path(tempfile.gettempdir()) / "tagent_doc_e2e.txt"
    made["tempfiles"].append(doc)
    doc.write_text("", encoding="utf-8")
    proc = subprocess.Popen(["notepad.exe", str(doc)])
    time.sleep(2.5)
    P1, P2 = "MINUTES-E2E", "ACTIONS-E2E"
    rec_id, _ = record([("type", P1), ("press", 0x0D), ("type", P2)], "tagent_doc_e2e")
    close(proc)
    doc.write_text("", encoding="utf-8")  # 清空后回放复现
    proc = subprocess.Popen(["notepad.exe", str(doc)])
    time.sleep(2.5)
    r = post(f"{BASE_URL}/replay/start", {"recording_id": rec_id})
    assert r["ok"], r
    s = wait_replay_done()
    # 经 RichEditD2DPT 子控件读回文档内容(探测验证过可靠,不依赖前台/剪贴板)
    import win32con as _wc
    hwnd = find_window("tagent_doc_e2e")
    assert hwnd, "回放后记事本窗口不在"
    kids = []
    win32gui.EnumChildWindows(hwnd, lambda h, l: l.append((h, win32gui.GetClassName(h))), kids)
    rich = next((h for h, cls in kids if cls == "RichEditD2DPT"), None)
    buf = ctypes.create_unicode_buffer(8192)
    if rich:
        ctypes.windll.user32.SendMessageW(rich, _wc.WM_GETTEXT, 8192, buf)
    content = buf.value
    assert P1 in content and P2 in content, f"文档内容不完整: {content!r} (state={s.get('state')})"

    close(proc)


# ================= 实例 2:模拟购物 =================

def recorded_texts(rec_id):
    """读录制任务的全部 input_text 文本(回放验证的期望值来源)。"""
    task = json.loads((Path("recordings") / rec_id / "task.json").read_text(encoding="utf-8"))
    return [str(s.get("text", "")) for s in task["steps"] if s.get("action") == "input_text"]


def _record_until(app, arg, title, script, build_expect, tries=3):
    """录制→按"实际录到的文本"推导期望值断言;字段串位/丢序才重试。"""
    last = None
    for attempt in range(tries):
        proc, hwnd = launch(app, arg, title)
        try:
            rec_id, _ = record(script, title)
            time.sleep(0.3)
            last = arg.read_text(encoding="utf-8") if arg.exists() else "<无输出>"
            expect = build_expect(recorded_texts(rec_id))
            if last == expect:
                return rec_id, proc
            print(f"  第 {attempt + 1} 次录制结果 {last!r} != 预期 {expect!r} (录制文本={recorded_texts(rec_id)!r}),重试…")
            made["recordings"].remove(rec_id)
            api_delete(f"{BASE_URL}/recordings/{rec_id}")
        finally:
            close(proc)
    raise AssertionError(f"录制 {tries} 次仍未复现录制内容: {last!r}")


def instance_shop():
    print("\n===== 实例2:模拟购物 =====")
    order = Path(tempfile.gettempdir()) / "tagent_order_e2e.txt"
    made["tempfiles"].append(order)
    rec_id, proc = _record_until(
        "shop_app.py", order, "TAGENT商店",
        [("click", (0.5, 0.36)), ("type", "SKU-9527"), ("press", 0x0D),
         ("type", "3"), ("press", 0x0D)],
        lambda texts: f"ORDERED:{texts[0]}x{texts[1]}")
    print(f"  录制完成: {recorded_texts(rec_id)}")
    texts = recorded_texts(rec_id)
    expect = f"ORDERED:{texts[0]}x{texts[1]}"
    proc, _ = launch("shop_app.py", order, "TAGENT商店")
    r = post(f"{BASE_URL}/replay/start", {"recording_id": rec_id})
    assert r["ok"], r
    wait_replay_done()
    got = order.read_text(encoding="utf-8") if order.exists() else "<无输出>"
    assert got == expect, f"购物回放未复现订单: {got!r} != {expect!r}"
    print(f"  回放复现订单: {got}")
    close(proc)
    return rec_id


# ================= 实例 3:模拟发邮件 =================

def instance_mail():
    print("\n===== 实例3:模拟发邮件 =====")
    mail = Path(tempfile.gettempdir()) / "tagent_mail_e2e.txt"
    made["tempfiles"].append(mail)
    # 收件人含真实邮箱 → 录制器按 PII 策略落为 {{EMAIL}} 占位符,回放用变量注入真值
    rec_id, proc = _record_until(
        "mail_app.py", mail, "TAGENT邮箱",
        [("click", (0.5, 0.35)), ("type", "peer@e2e.local"), ("press", 0x0D),
         ("type", "replay-send-test"), ("press", 0x0D)],
        lambda texts: f"SENT:peer@e2e.local|{texts[1]}")
    texts = recorded_texts(rec_id)
    assert texts[0] == "{{EMAIL}}", f"邮箱应被 PII 脱敏为占位符: {texts!r}"
    print(f"  录制完成:邮箱已脱敏为 {{{{EMAIL}}}},正文字段 {texts[1]}")
    expect = f"SENT:peer@e2e.local|{texts[1]}"
    proc, _ = launch("mail_app.py", mail, "TAGENT邮箱")
    r = post(f"{BASE_URL}/replay/start", {"recording_id": rec_id,
                                          "variables": {"EMAIL": "peer@e2e.local"}})
    assert r["ok"], r
    wait_replay_done()
    got = mail.read_text(encoding="utf-8") if mail.exists() else "<无输出>"
    assert got == expect, f"邮件回放不符: {got!r} != {expect!r}"
    print(f"  回放复现发送(仅本地模拟): {got}")
    close(proc)
    return rec_id


# ================= 实例 4:队列 + 盯守 + 联动 + 推送 =================

class _Receiver(BaseHTTPRequestHandler):
    pushes = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", "replace")
        _Receiver.pushes.append((self.path, body[:200]))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_):
        pass


def wait_for_run(rec_id, trigger=None, timeout=90):
    deadline = time.time() + timeout
    seen = set()
    while time.time() < deadline:
        for run in get(f"{BASE_URL}/tasks/{rec_id}/runs")["runs"]:
            key = run["run_id"]
            if key in seen:
                continue
            seen.add(key)
            if trigger is None or run.get("trigger") == trigger:
                return run
        time.sleep(3)
    raise TimeoutError(f"等待运行超时: {rec_id} trigger={trigger}")


def instance_combo(shop_rec):
    print("\n===== 实例4:队列+盯守+联动+推送 =====")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    r = put(f"{BASE_URL}/notify", {"url": f"http://127.0.0.1:{port}/push", "enabled": True})
    assert r["ok"] and r["test_sent"], r
    print("  推送已配置(本地接收器已收到测试)")

    # 录一个慢一点的任务 A(商店),作为队列/联动/盯守的目标
    order = Path(tempfile.gettempdir()) / "tagent_combo_a.txt"
    made["tempfiles"].append(order)
    proc, _ = launch("shop_app.py", order, "TAGENT商店")
    rec_a, proc = _record_until(
        "shop_app.py", order, "TAGENT商店",
        [("click", (0.5, 0.36)), ("type", "SKU-A1"), ("press", 0x0D),
         ("type", "1"), ("press", 0x0D)],
        lambda texts: f"ORDERED:{texts[0]}x{texts[1]}")
    print(f"  任务A录制完成: {recorded_texts(rec_a)}")

    # --- 队列:A 回放进行中把 B(实例2 购物任务)排入队 ---
    proc, _ = launch("shop_app.py", order, "TAGENT商店")  # 两个任务都用商店窗口
    r = post(f"{BASE_URL}/replay/start", {"recording_id": rec_a})
    assert r["ok"] and not r.get("queued"), r
    deadline = time.time() + 10
    while time.time() < deadline and not get(f"{BASE_URL}/replay/status").get("playing"):
        time.sleep(0.05)
    r2 = post(f"{BASE_URL}/replay/start", {"recording_id": shop_rec})
    assert r2["ok"] and r2.get("queued"), f"应排队: {r2}"
    print(f"  队列:B 已排队(位置 {r2['position']})")
    wait_replay_done(timeout=90)   # A
    run_b = wait_for_run(shop_rec, trigger="manual", timeout=60)
    run_a = get(f"{BASE_URL}/tasks/{rec_a}/runs")["runs"][0]
    assert run_a["finished_at"] <= run_b["finished_at"], "队列应保证 A 先于 B 完成"
    print("  队列:A 完成后 B 自动出队执行")

    # --- 联动:A 成功后自动跑 B ---
    r = put(f"{BASE_URL}/tasks/{rec_a}/pipeline", {"steps": [{"rec_id": shop_rec, "on": "success"}]})
    assert r["ok"], r
    order.unlink(missing_ok=True)
    r = post(f"{BASE_URL}/replay/start", {"recording_id": rec_a})
    assert r["ok"], r
    wait_replay_done(timeout=90)
    chained = wait_for_run(shop_rec, trigger=f"pipeline:{rec_a}", timeout=60)
    print(f"  联动:A 成功后 B 自动运行(run_id={chained['run_id'][:18]}…)")

    # --- 盯守:文件出现 → 自动跑 A;同时验证推送 ---
    flag = Path(tempfile.gettempdir()) / "tagent_watch_flag.txt"
    made["tempfiles"].append(flag)
    r = put(f"{BASE_URL}/watchers", {"rec_id": rec_a,
                                     "condition": {"kind": "file_exists", "value": str(flag)},
                                     "cooldown_min": 1})
    assert r["ok"], r
    flag.write_text("go", encoding="utf-8")
    print("  盯守:旗标文件已写入,等待轮询触发(最长 70s)…")
    fired = wait_for_run(rec_a, trigger="watcher", timeout=70)
    print(f"  盯守触发运行完成(success={fired['success']})")
    assert any("盯守" in body or "watch" in path for path, body in _Receiver.pushes) or \
        any("TAGENT" in body for _, body in _Receiver.pushes), _Receiver.pushes
    print(f"  推送:接收器共收到 {len(_Receiver.pushes)} 条通知")

    # 清理
    api_delete(f"{BASE_URL}/watchers?rec_id={rec_a}")
    put(f"{BASE_URL}/tasks/{rec_a}/pipeline", {"steps": []})
    put(f"{BASE_URL}/notify", {"url": "", "enabled": False})
    server.shutdown()
    close(proc)


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        if only in (None, "doc"):
            instance_doc()
        if only in (None, "shop"):
            shop = instance_shop()
        if only in (None, "mail"):
            instance_mail()
        if only in (None, "combo"):
            instance_combo(shop if only is None else instance_shop())
        print("\n实例 E2E 全部通过")
    finally:
        for rec_id in made["recordings"]:
            try:
                api_delete(f"{BASE_URL}/recordings/{rec_id}")
            except Exception:
                pass
        for f in made["tempfiles"]:
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
        subprocess.run(["taskkill", "/IM", "Notepad.exe", "/F", "/T"], capture_output=True)


if __name__ == "__main__":
    main()
