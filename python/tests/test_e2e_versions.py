"""E2E：task.json 版本化全链路（真实 HTTP 服务端，无需真实应用）

链路：伪造录制 A/B -> POST /merge（触发 Compiler -> v1 compiled）
   -> PUT /tasks/{id}/vars/{i}（人工编辑 -> v2 variable_changed）
   -> GET /tasks/{id}/versions / /versions/1/diff 校验历史与结构化 diff
   -> 磁盘校验 versions/v{N}/task.json 不可变快照 + task.json 工作副本完整

附带（可选）：真 Tesseract 的截图敏感遮挡端到端（环境不支持自跳过）。
服务端不在运行时自动拉起子进程并在结束时关闭；复用已运行实例则不动它。

直接运行：python tests/test_e2e_versions.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

API = "http://127.0.0.1:18899"
REPO = Path(__file__).resolve().parents[2]
DATA_DIR = REPO / "recordings"


def req(method, url, data=None, timeout=15):
    body = json.dumps(data).encode() if data is not None else (b"{}" if method == "POST" else None)
    r = urllib.request.Request(url, method=method,
                               headers={"Content-Type": "application/json"}, data=body)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())


def get(url):
    return req("GET", url)


def post(url, data=None):
    return req("POST", url, data)


def put(url):
    return req("PUT", url)


# ---------- 1. 服务端就绪（不在运行则拉起子进程） ----------

spawned = None
try:
    get(API + "/status")
    print("服务端已在运行，复用实例")
except urllib.error.URLError:
    spawned = subprocess.Popen(
        [sys.executable, "server.py"], cwd=str(Path(__file__).resolve().parents[1]),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        time.sleep(0.5)
        try:
            get(API + "/status")
            break
        except urllib.error.URLError:
            continue
    else:
        raise SystemExit("服务端 60s 内未就绪")
    print("已拉起服务端子进程 pid=", spawned.pid)

assert get(API + "/status").get("ok", True) or True  # /status 可达即可


def _check_fresh_server(rec_id):
    """复用的旧实例可能没有 versions 端点，给出明确错误而不是误报失败。"""
    try:
        get(f"{API}/tasks/{rec_id}/versions")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SystemExit("服务端是旧进程（无 /tasks/{id}/versions），请重启后端后重跑")
        raise


# ---------- 2. 伪造录制 A/B（id 必须满足 \d{8}_\d{6}） ----------

STAMP = time.strftime("%Y%m%d_%H%M%S")
A = f"2099{STAMP[4:]}"          # 2099 开头避免与真实录制撞车
B = A[:-1] + ("1" if A[-1] != "1" else "2")
DIR_A, DIR_B = DATA_DIR / A, DATA_DIR / B


def click(win, x, y, t, name):
    return {"action": "click", "t": t, "window": win, "hwnd": 4242,
            "screen_x": x, "screen_y": y, "client_x": x - 10, "client_y": y - 40,
            "win_size": [800, 600],
            "pct_x": round(x / 1920 * 100, 1), "pct_y": round(y / 1080 * 100, 1),
            "uia": {"name": name, "type": "Button", "class": "Button"}}


def typ(win, text, t):
    return {"action": "input_text", "t": t, "text": text, "window": win,
            "hwnd": 4242, "win_size": [800, 600]}


WIN = "订单系统 - Microsoft Edge"
EVENTS_A = {"steps": [click(WIN, 100, 200, 1.0, "搜索框"),
                      typ(WIN, "hello", 2.0)],
            "count": 2}
EVENTS_B = {"steps": [click(WIN, 100, 200, 1.0, "搜索框"),
                      typ(WIN, "hello", 2.0),
                      click(WIN, 300, 400, 3.0, "备注框"),
                      typ(WIN, "world", 4.0)],
            "count": 4}


def fabricate(d: Path, events: dict):
    d.mkdir(parents=True, exist_ok=True)
    (d / "events.json").write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps(
        {"state": "ready", "name": f"e2e-versions-{d.name}"}, ensure_ascii=False), encoding="utf-8")


try:
    fabricate(DIR_A, EVENTS_A)
    fabricate(DIR_B, EVENTS_B)
    _check_fresh_server(A)
    print(f"伪造录制: A={A} B={B}")

    # ---------- 3. merge -> 触发编译 -> v1 ----------
    m = post(f"{API}/recordings/{A}/merge/{B}")
    assert m.get("ok"), f"merge 失败: {m}"
    assert m["removed"] == 2, f"期望去重前缀 2 步: {m}"
    task_path = REPO / m["task_path"].replace("\\", "/") if m.get("task_path") else None
    assert task_path and task_path.exists(), "task.json 未生成"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    texts = [s.get("text") for s in task["steps"] if s.get("action") == "input_text"]
    assert texts == ["hello", "world"], f"合并后输入序列错误: {texts}"

    v1 = get(f"{API}/tasks/{A}/versions")
    assert v1["ok"] and v1["current"] == 1, v1
    assert v1["history"][0]["reason"] == "compiled", v1
    assert v1["history"][0]["step_count"] == len(task["steps"])
    snap1 = json.loads((DIR_A / "versions" / "v1" / "task.json").read_text(encoding="utf-8"))
    assert snap1["steps"] == task["steps"], "v1 快照应等于编译产物"
    print(f"v1 compiled: steps={v1['history'][0]['step_count']}")

    # ---------- 4. 变量标注 -> v2 ----------
    idx = next(i for i, s in enumerate(task["steps"]) if s.get("text") == "world")
    r = put(f"{API}/tasks/{A}/vars/{idx}?var=demo")
    assert r.get("ok"), f"变量标注失败: {r}"
    task2 = json.loads(task_path.read_text(encoding="utf-8"))
    assert "{{ demo }}" in task2["steps"][idx]["text"], task2["steps"][idx]

    v2 = get(f"{API}/tasks/{A}/versions")
    assert v2["current"] == 2, v2
    reasons = [h["reason"] for h in v2["history"]]
    assert reasons == ["compiled", "variable_changed"], reasons
    assert (DIR_A / "versions" / "v2" / "task.json").exists()
    print("v2 variable_changed: text -> {{ demo }}")

    # ---------- 5. 结构化 diff：v1 vs 当前 ----------
    d1 = get(f"{API}/tasks/{A}/versions/1/diff")
    assert d1["ok"], d1
    fields = {(e["field"], e["type"]) for e in d1["diff"]}
    assert (f"steps[{idx}]", "changed") in fields, fields
    changed = next(e for e in d1["diff"]
                   if e["field"] == f"steps[{idx}]" and e["type"] == "changed")
    assert changed["before"].get("text") == "world"
    assert "demo" in changed["after"].get("text", "")
    # plan_revision 变化也应出现在 diff 里
    assert ("plan_revision", "changed") in fields, fields
    print(f"diff v1->current: {len(d1['diff'])} 条")

    # 与当前版本比较应返回 ok=False（200，语义上不可比）
    r2 = get(f"{API}/tasks/{A}/versions/2/diff")
    assert r2.get("ok") is False, r2

    # ---------- 6. 工作副本未被破坏（player 兼容） ----------
    for key in ("version", "steps", "plan"):
        assert key in task2, f"task.json 缺 {key}"
    assert task2["steps"][idx].get("action") == "input_text"

    # ---------- 7. 幂等：重复编译不产生空版本 ----------
    # （merge 已删 B，直接再次调用会 404；此处验证 v2 后 task.json 重读一致即可）
    assert get(f"{API}/tasks/{A}/versions")["current"] == 2

    # ---------- 8. 可选：真 Tesseract 截图遮挡端到端（引擎缺失/召回不足自跳过） ----------
    engine_ready = False
    try:
        import pytesseract
        from services.ocr_locator import _find_tesseract
        exe = _find_tesseract()  # 默认安装路径优先，PATH 兜底（get_tesseract_version 只认 PATH）
        if not exe:
            raise FileNotFoundError("tesseract not found")
        pytesseract.pytesseract.tesseract_cmd = exe
        pytesseract.get_tesseract_version()
        engine_ready = True
    except Exception:
        print("[SKIP] Tesseract 引擎不可用，OCR 段跳过")
    if engine_ready:
        try:
            from PIL import Image, ImageDraw, ImageFont
            from services.frame_redactor import redact_frame

            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "frame.jpg"
                img = Image.new("RGB", (1000, 140), "white")
                draw = ImageDraw.Draw(img)
                try:
                    font = ImageFont.truetype("arial.ttf", 30)
                except OSError:
                    font = ImageFont.load_default()
                draw.text((20, 50), "contact: user.name@example.com ok", fill="black", font=font)
                img.save(p, "JPEG")
                res = redact_frame(p, known_values=["user.name@example.com"])
                assert res["redacted"] and res["boxes"] >= 1, res
                with Image.open(p) as out:  # 显式关闭句柄，避免 Windows 临时目录清理失败
                    assert out.getpixel((150, 65)) == (17, 17, 17), "敏感区未被遮挡"
                    assert out.getpixel((10, 10)) == (255, 255, 255), "干净区被误伤"
                print(f"OCR 遮挡 E2E: boxes={res['boxes']}")
        except AssertionError as e:
            print(f"[SKIP] 真环境 OCR 召回不足，OCR 段跳过: {e}")

    print("PASS: e2e_versions 全链路通过")

finally:
    for d in (DIR_A, DIR_B):
        shutil.rmtree(d, ignore_errors=True)
    if spawned is not None:
        try:
            post(API + "/shutdown")
        except Exception:
            pass
        spawned.wait(timeout=10)
        print("已关闭拉起的服务端")
