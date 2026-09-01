"""网页通道 E2E：录制浏览器点击+输入 -> 编译 -> 回放（web 通道接管，全程无人值守）
前置：Chrome/Edge 需以 --remote-debugging-port=9222 --user-data-dir=<临时> 启动并加载 _web_test.html
直接运行：python tests/run_web_e2e.py
"""
import ctypes
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:18899"
HTML_URL = "file:///D:/aiapp/TAGENT/recordings/_web_test.html"
HERE = Path(__file__).resolve().parent


def call(method, path, body=None):
    data = json.dumps(body or {}).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, method=method, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15).read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=15).read())


def phys_click(x, y):
    u32 = ctypes.windll.user32
    u32.SetCursorPos(x, y)
    time.sleep(0.15)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    u32.mouse_event(0x0004, 0, 0, 0, 0)


def phys_type(text):
    u32 = ctypes.windll.user32
    for ch in text:
        vk = 0x41 + (ord(ch.upper()) - ord('A')) if ch.isalpha() else None
        if vk is None:
            continue
        u32.keybd_event(vk, 0, 0, 0)
        u32.keybd_event(vk, 0, 2, 0)
        time.sleep(0.05)


def element_screen_pos(page_info):
    """通过 Playwright CDP 拿元素视口 rect，换算屏幕坐标（窗口 rect + Chrome UI 偏移 110px 估）
    返回 ({btn, inp}, chrome_hwnd)"""
    from playwright.sync_api import sync_playwright
    import win32gui

    def _enum(hwnd, acc):
        if "Google Chrome" in win32gui.GetWindowText(hwnd):
            acc.append(hwnd)
        return True

    wins = []
    win32gui.EnumWindows(_enum, wins)
    if not wins:
        raise RuntimeError("找不到 Chrome 窗口")
    # 多实例时取面积最大的（活动窗口）
    wins.sort(key=lambda h: (lambda r: (r[2] - r[0]) * (r[3] - r[1]))(win32gui.GetWindowRect(h)),
              reverse=True)
    rect = win32gui.GetWindowRect(wins[0])
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=8000)
        try:
            page = next(pg for ctx in browser.contexts for pg in ctx.pages
                        if "_web_test.html" in pg.url)
            points = page.evaluate(
                "(() => { const c = (sel) => { const r = "
                "document.querySelector(sel).getBoundingClientRect(); "
                "return {x: r.x + r.width/2, y: r.y + r.height/2}; };"
                " return {btn: c('#btn'), inp: c('#inp')}; })()")
        finally:
            browser.close()
    off_x, off_y = rect[0], rect[1] + 110
    return ({k: (int(off_x + v["x"]), int(off_y + v["y"])) for k, v in points.items()},
            wins[0])


def ensure_chrome_foreground(hwnd):
    """录制钩子按前台窗口记录事件，物理点击前须把 Chrome 置前。
    VS Code 以管理员运行，普通 SetForegroundWindow 无效，需 ALT trick + 轮询备份。"""
    import win32gui
    import win32con
    import win32process
    u32 = ctypes.windll.user32
    if win32gui.GetForegroundWindow() == hwnd:
        return
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    for _ in range(3):
        try:
            fg = win32gui.GetForegroundWindow()
            _, fg_tid = win32process.GetWindowThreadProcessId(fg)
            _, t_tid = win32process.GetWindowThreadProcessId(hwnd)
            u32.keybd_event(0x12, 0, 0, 0)  # ALT down（模拟用户交互）
            u32.keybd_event(0x12, 0, 2, 0)  # ALT up
            if fg_tid != t_tid:
                u32.AttachThreadInput(fg_tid, t_tid, True)
            win32gui.SetForegroundWindow(hwnd)
            if fg_tid != t_tid:
                u32.AttachThreadInput(fg_tid, t_tid, False)
        except Exception:
            pass
        if win32gui.GetForegroundWindow() == hwnd:
            break
        if not win32gui.IsIconic(hwnd) and win32gui.IsWindowVisible(hwnd):
            u32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)  # HWND_TOP 不激活
        time.sleep(0.4)
    print(f"[0] 前台是否 Chrome: {win32gui.GetForegroundWindow() == hwnd}")


def main():
    # 0. 前置检查：CDP
    try:
        pages = get("/json") if False else json.loads(
            urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5).read())
    except Exception as e:
        print(f"FAIL 前置: CDP 9222 不可用（{e}）")
        return False
    page = next((p for p in pages if p.get("type") == "page"
                 and "_web_test.html" in p.get("url", "")), None)
    if not page:
        print("FAIL 前置: 未找到 _web_test.html 页面")
        return False
# Chrome 可能复用旧标签加载了旧版 html，确保刷新到最新内容
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=8000)
        try:
            pg = next(x for ctx in browser.contexts for x in ctx.pages
                      if "_web_test.html" in x.url)
            if not pg.query_selector("#inp"):
                print("[0] 页面为旧版，刷新")
                pg.reload()
                time.sleep(1.2)
            pg.evaluate("window.scrollTo(0, 0)")
        finally:
            browser.close()
    pts, chrome_hwnd = element_screen_pos(page)
    sx, sy = pts["btn"]
    print(f"[0] 按钮屏幕坐标: ({sx},{sy})")
    ix, iy = pts["inp"]
    print(f"[0] 输入框屏幕坐标: ({ix},{iy})")

    # 1. 录制
    ensure_chrome_foreground(chrome_hwnd)
    r = call("POST", "/record/start")
    if not r.get("ok"):
        print(f"FAIL 录制启动: {r}")
        return False
    rec_id = r["recording_id"]
    print(f"[1] 录制 {rec_id}")
    time.sleep(1.0)
    phys_click(sx, sy)               # 点击按钮
    time.sleep(0.8)
    phys_click(ix, iy)               # 点击输入框
    time.sleep(0.6)
    phys_type("abc")                 # 输入 abc
    time.sleep(0.8)
    r = call("POST", "/record/stop")
    print(f"[2] 停止录制 task_path={r.get('task_path')} events={r.get('event_count')}")
    if not r.get("task_path"):
        print("FAIL 编译未产出 task.json")
        return False

    # 2. 编译产物检查：浏览器步骤应含 dom locator
    task = json.loads(Path(r["task_path"]).read_text(encoding="utf-8"))
    dom_steps = [s for s in task["steps"] if any(
        l.get("type") == "dom" for l in s.get("locator", []))]
    print(f"[3] task.json 共 {task['step_count']} 步，含 dom locator 的步骤: {len(dom_steps)}")
    for s in dom_steps:
        print(f"    - [{s['index']}] {s['action']} dom: {[l for l in s['locator'] if l.get('type')=='dom']}")
    if not dom_steps:
        print("FAIL 编译未产出 dom locator（录制时浏览器窗口 UIA 未捕获）")
        return False

    # 3. 回放（web 通道接管）
    r = call("POST", "/replay/start", {"recording_id": rec_id})
    if not r.get("ok"):
        print(f"FAIL 回放启动: {r}")
        return False
    print(f"[4] 回放启动")
    result = None
    for _ in range(60):
        time.sleep(1.0)
        st = get("/replay/status")
        if not st.get("playing"):
            result = st
            break
    if result is None:
        print("FAIL 回放超时未结束")
        return False

    # 4. 结果检查
    print(f"[5] 回放 summary: {result.get('summary')}")
    web_steps = []
    for s in result.get("steps", []):
        print(f"    [{s['step']}] {s['action']} {s['status']} {s['detail']}")
        if "web_click" in s["detail"] or "web_type" in s["detail"]:
            web_steps.append(s)
    ok_flags = all(s.get("status") == "ok" for s in result.get("steps", []))
    print(f"[6] 全步骤 ok={ok_flags}，经网页通道的步骤: {len(web_steps)}")
    if not ok_flags:
        print("FAIL 存在失败/暂停步骤")
        return False
    if not web_steps:
        print("FAIL 没有步骤走网页通道")
        return False

    print("\n===== WEB E2E PASS =====")
    return True


if __name__ == "__main__":
    sys.path.insert(0, str(HERE.parent))
    ok = main()
    sys.exit(0 if ok else 1)
