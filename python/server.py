"""FastAPI 服务：连接前端 UI 与录制/回放引擎。"""

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from paths import BASE_DIR
from recorder import Recorder
from player import Player
from compiler import Compiler
from logger import get_logger
from routes_tasks import build_router
from services.scheduler import Scheduler
from services.security import get_security, BLACKLIST

log = get_logger("server")

app = FastAPI(title="智能操作助手")

# CORS：只允许同源 UI（tauri 壳或本机浏览器），避免任意网页跨站调用
ALLOWED_ORIGINS = {
    "http://127.0.0.1:18899",
    "http://localhost:18899",
    "tauri://localhost",
    "http://tauri.localhost",
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def guard(request: Request, call_next):
    """写操作校验 Origin，防跨站请求伪造。
    策略：浏览器请求必须带允许的 Origin；无 Origin 的非浏览器客户端（curl/测试脚本）放行。
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return await call_next(request)
    origin = request.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS:
        log.warning("拒绝跨站写请求: %s %s origin=%s",
                    request.method, request.url.path, origin)
        return JSONResponse({"ok": False, "msg": "forbidden"}, status_code=403)
    return await call_next(request)


DATA_DIR = BASE_DIR / "recordings"
DATA_DIR.mkdir(parents=True, exist_ok=True)

TASKS_DB = BASE_DIR / "data" / "schedules.json"

# 当前录制会话
_recorder: Recorder | None = None
_recorder_lock = threading.Lock()

# 当前回放实例
_player: Player | None = None
_player_lock = threading.Lock()

_scheduler: Scheduler | None = None


def _scheduled_run(rec_id: str):
    """定时触发：后台回放任务（阶段4.2）"""
    log.info("[定时] 执行任务 %s", rec_id)
    try:
        with _player_lock:
            if _player:
                log.warning("[定时] 有回放进行中，跳过 %s", rec_id)
                return
        _launch_replay(rec_id, None)
    except Exception:
        log.exception("[定时] 启动失败 %s", rec_id)


# 任务库路由 + 定时调度器（模块级初始化，确保 uvicorn 启动时也注册）
_scheduler = Scheduler(TASKS_DB, _scheduled_run)
_scheduler.start()
app.include_router(build_router(DATA_DIR, _scheduler))

# 预热安全单例（密钥初始化/UIA 缓存），避免录制主循环中首次调用阻塞
from services.security import get_security  # noqa: E402
get_security()


# 对话框/临时窗口关键词：这些窗口大概率在回放过程中才出现（另存为/打开/确认等）
_DIALOG_KEYWORDS = ("另存为", "保存为", "打开", "确认", "替换", "覆盖",
                    "属性", "选项", "设置", "主机弹出窗口", "消息", "提示")


def _window_created_in_recording(task: dict, window: str) -> bool:
    """判断窗口是否可能由录制/回放过程自身创建（而非必须预先存在）。
    判定依据：
    1. 窗口名含对话框关键词（另存为/打开/确认等）
    2. 步骤序列中，第一次 switch_window 到它之前，存在指向相似名称的
       桌面点击（如点击桌面图标"新建 文本文档.txt" -> 记事本"xx.txt - Notepad"）
    3. 第一次 switch_window 之前存在 wait 步骤（等待窗口自动出现）
    """
    w = window.lstrip("*").lower()
    if any(k in w for k in _DIALOG_KEYWORDS):
        return True
    if not window.startswith("*") and "- notepad" in w:
        return True
    # 检查第一次 switch 前是否存在创建性步骤
    created_window_names = []
    for step in task.get("steps", []):
        action = step.get("action", "")
        if action in ("click", "double_click"):
            for loc in step.get("locator", []):
                name = loc.get("name", "")
                if name:
                    created_window_names.append(name.lower())
        elif action == "wait":
            t = step.get("window", "")
            if t:
                created_window_names.append(t.lower())
    for name in created_window_names:
        if name and (name in w or w in name):
            return True
        base = name.rsplit(" - ", 1)[0] if " - " in name else name
        if base and base in w:
            return True
    return False


def _check_window_dependencies(rec_dir) -> list[str]:
    """检查回放所需的窗口是否存在，返回缺失窗口列表。
    只检查必须预先存在的窗口（排除回放过程中会自行创建的窗口/对话框）。
    标题匹配与 player 一致：支持部分包含匹配。"""
    import win32gui
    task_file = rec_dir / "task.json"
    if not task_file.exists():
        return []
    try:
        with open(task_file, "r", encoding="utf-8") as f:
            task = json.load(f)
    except Exception:
        return []
    missing = []
    required_windows = set()
    for step in task.get("steps", []):
        if step.get("action") == "switch_window":
            window = step.get("window", "")
            if window:
                required_windows.add(window)

    def _title_exists(title: str) -> bool:
        """部分匹配查找窗口：精确 -> 去星 -> 单向包含"""
        if not title:
            return False
        t = title.lstrip("*")
        if win32gui.FindWindow(None, title):
            return True
        if t != title and win32gui.FindWindow(None, t):
            return True
        found = []
        def cb(h, l):
            try:
                l.append(win32gui.GetWindowText(h))
            except Exception:
                pass
            return True
        win32gui.EnumWindows(cb, found)
        return any(t in w for w in found if w)

    for window in required_windows:
        if _window_created_in_recording(task, window):
            continue  # 回放中会自行创建，不要求预先存在
        if not _title_exists(window):
            missing.append(window)
    return missing


def _launch_replay(rec_id: str, variables: dict | None, from_step: int = 0):
    """启动回放线程（复用 /replay/start 逻辑）。返回 rec_id 或 None"""
    global _player
    rec_dir = _resolve_rec_dir(rec_id)
    if rec_dir is None:
        return None
    task_file = rec_dir / "task.json"
    events_file = rec_dir / "events.json"
    # 统一用最新 compiler 重编译：旧录制自动获得修复（双击合并/定位链升级）
    if events_file.exists() and not task_file.exists():
        try:
            Compiler(str(rec_dir)).compile()
        except Exception:
            log.exception("回放前重新编译失败，使用已有 task.json")
    path = task_file if task_file.exists() else events_file
    if not path.exists():
        return None

    # 回放前窗口检测（仅警告，不拦截：player 遇到缺失窗口会自动暂停并弹接管对话框）
    missing_windows = _check_window_dependencies(rec_dir)
    if missing_windows:
        log.warning("回放前检测到可能缺失的窗口（若未打开将在该步暂停）: %s",
                    missing_windows)

    # 阶段2.4：会话步骤（登录/2FA）
    from services.task_store import TaskStore
    _ts = TaskStore(DATA_DIR)
    session_steps = _ts.load_meta(rec_id).get("session_steps", [])
    var_sources = _ts.get_var_sources(rec_id)  # 阶段2.1：变量来源

    # 阶段3.3：纠错库（自愈），回放时优先尝试人工纠正动作
    from services.corrections import CorrectionStore
    corrections = CorrectionStore(rec_dir).load()

    # 阶段5.1：checkpoint 文件（断点续跑）
    ckpt_dir = BASE_DIR / "data"
    ckpt_dir.mkdir(exist_ok=True)
    ckpt_file = ckpt_dir / f"ckpt_{rec_id}.json"

    def _run():
        global _player
        try:
            _player = Player(str(path), variables=variables or {},
                             session_steps=session_steps,
                             corrections=corrections,
                             var_sources=var_sources)
            _player.checkpoint_path = ckpt_file
            _player._rec_id = rec_id
            # 阶段1.2：回放截图目录
            ss_dir = BASE_DIR / "runs" / "screenshots" / rec_id
            _player.screenshot_dir = str(ss_dir)
            log.info("开始回放: %s (%d 步)", path, len(_player.steps))
            result = _player.play(from_step=from_step)
            log.info("回放完成: %s", path)
            ckpt_file.unlink(missing_ok=True)  # 成功后清断点
            _write_report(rec_id, result)
        except Exception:
            log.exception("回放出错: %s", path)
            _write_report(rec_id, {"summary": "回放异常", "steps": [], "pause_reasons": []})
        finally:
            with _player_lock:
                _player = None
                log.info("回放实例已释放")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return rec_id


def _write_report(rec_id: str, result):
    """阶段5.2：执行日志 REPORT（逐步结果+暂停原因）"""
    try:
        report_dir = BASE_DIR / "runs"
        report_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{time.strftime('%Y%m%d_%H%M%S')}_{rec_id}.json"
        # 兼容字符串和 dict 两种格式
        if isinstance(result, str):
            report = {"rec_id": rec_id, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "summary": result, "steps": [], "pause_reasons": []}
        else:
            report = {"rec_id": rec_id, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            report.update(result)
        _attach_eval(rec_id, report)
        (report_dir / fname).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception:
        log.exception("REPORT 写入失败")


def _attach_eval(rec_id: str, report: dict):
    """移植：skill-recorder scoring - 回放 REPORT 附评分（期望=录制 events.json）"""
    try:
        if not isinstance(report.get("steps"), list):
            return
        import services.eval_scorer as eval_scorer
        rec_dir = BASE_DIR / "recordings" / rec_id
        events_file = rec_dir / "events.json"
        if not events_file.exists():
            return
        expected = json.loads(events_file.read_text(encoding="utf-8")).get("steps", [])
        if not expected:
            return
        report["eval"] = eval_scorer.score(expected, report["steps"]).to_dict()
    except Exception:
        log.exception("eval 评分失败")

# 录制 id 格式：时间戳，防路径穿越
_REC_ID_RE = re.compile(r"^\d{8}_\d{6}$")


class ReplayRequest(BaseModel):
    recording_id: str | None = None
    variables: dict[str, str] | None = None
    from_step: int = 0


@app.get("/")
def root():
    return RedirectResponse(url="/ui/")


@app.post("/shutdown")
def shutdown():
    """前端 ✕ 按钮调用：停止录制/回放并退出后端进程"""
    global _recorder, _player
    with _recorder_lock:
        if _recorder and _recorder.recording:
            try:
                _recorder.stop()
            except Exception:
                pass
    with _player_lock:
        if _player:
            try:
                _player.stop()
            except Exception:
                pass
    log.info("收到退出信号，后端即将关闭")
    threading.Thread(target=_exit_server, daemon=True).start()
    return {"ok": True}


def _exit_server():
    import time as _t
    _t.sleep(0.3)
    os._exit(0)


@app.get("/status")
def status():
    return {
        "recording": _recorder is not None and _recorder.recording,
        "recordings": _list_recordings(),
        "schedules": _scheduler.list_jobs() if _scheduler else {},
        "security": {
            "blacklist": sorted(BLACKLIST),
        },
    }


@app.post("/record/start")
def start_recording():
    """开始录制。阶段4.5：录制前检测前台应用边界（黑名单进程即时提示）。"""
    global _recorder
    with _recorder_lock:
        if _recorder and _recorder.recording:
            return {"ok": False, "msg": "已在录制中"}
        # 4.5 跨应用边界提示：前台窗口进程在黑名单则拒绝录制
        exe = _foreground_exe()
        if exe and exe.lower() in BLACKLIST:
            return {"ok": False, "msg": f"检测到系统管理工具 {exe}，为避免风险已拒绝录制"}
        rec_id = time.strftime("%Y%m%d_%H%M%S")
        out_dir = DATA_DIR / rec_id
        _recorder = Recorder(str(out_dir))
        _recorder.start()
        return {"ok": True, "recording_id": rec_id, "warning": exe or None}


@app.post("/record/stop")
def stop_recording():
    """停止录制。"""
    global _recorder
    with _recorder_lock:
        if not _recorder or not _recorder.recording:
            return {"ok": False, "msg": "未在录制"}
        path = _recorder.stop()
        step_count = len(_recorder.steps)
        # 自动编译为 task.json
        try:
            c = Compiler(str(_recorder.output_dir))
            task_path = c.compile()
        except Exception as e:
            task_path = None
            log.exception("编译失败: %s", e)
        return {"ok": True, "recording_id": _recorder.output_dir.name,
                "events_path": path, "event_count": step_count,
                "task_path": task_path}


@app.post("/replay/start")
def start_replay(req: ReplayRequest):
    """开始回放。"""
    if _recorder and _recorder.recording:
        return {"ok": False, "msg": "正在录制，请先停止"}

    with _player_lock:
        if _player and not _player.paused:
            return {"ok": False, "msg": "已有回放进行中"}
        if _player and _player.paused:
            # 暂停残留（等待人工接管）被新的回放指令替换
            _player.stop()
    if _player:
        time.sleep(1.0)  # 等旧回放线程退出（finally 置 None）

    with _player_lock:
        # 找到要回放的录制
        if req.recording_id:
            rec_dir = _resolve_rec_dir(req.recording_id)
            if rec_dir is None:
                return {"ok": False, "msg": "找不到该录制"}
        else:
            rec_dir = _latest_rec_dir()
            if rec_dir is None:
                return {"ok": False, "msg": "没有可回放的录制"}
        if _launch_replay(rec_dir.name, req.variables or {}, req.from_step):
            return {"ok": True, "recording_id": rec_dir.name}
        return {"ok": False, "msg": "没有可回放的内容"}


@app.post("/replay/stop")
def stop_replay():
    """停止回放。"""
    global _player
    if _player:
        _player.stop()
        return {"ok": True, "msg": "已发送停止信号"}
    return {"ok": False, "msg": "没有正在进行的回放"}


@app.post("/replay/pause")
def pause_replay():
    """阶段3.1：暂停回放，等待人工接管"""
    global _player
    if _player and not _player.paused:
        _player.pause()
        return {"ok": True, "msg": "已暂停，请接管操作"}
    return {"ok": False, "msg": "没有进行中的回放可暂停"}


@app.post("/replay/resume")
def resume_replay():
    """阶段3.1：人工接管完成，继续回放"""
    global _player
    if _player and _player.paused:
        _player.resume()
        return {"ok": True, "msg": "已继续回放"}
    return {"ok": False, "msg": "回放不在暂停状态"}


@app.get("/replay/status")
def replay_status():
    """回放状态（阶段4 状态小窗：步骤简述 + 暂停/错误态）"""
    if not _player:
        return {"ok": True, "playing": False}
    current = len(_player._step_results)
    step_desc = ""
    step_error = False
    if current < len(_player.steps):
        step = _player.steps[current]
        action = step.get("action", "")
        text = step.get("text", step.get("key", ""))
        step_desc = f"{action} {text}".strip()[:40]
    # 检查最近步骤是否有错误
    if _player._step_results:
        last = _player._step_results[-1]
        step_error = last.get("status") == "paused" and "失败" in last.get("detail", "")
    return {
        "ok": True,
        "playing": True,
        "paused": _player.paused,
        "total": len(_player.steps),
        "current_step": current,
        "step_desc": step_desc,
        "error": step_error,
    }


@app.post("/replay/rerecord")
def rerecord_step():
    """阶段3.1：'重新演示此步' -- 停止回放，开始录制替代操作"""
    global _player, _recorder
    if not _player:
        return {"ok": False, "msg": "没有进行中的回放"}
    # 记录当前步骤索引
    current_step = len(_player._step_results)
    rec_id = getattr(_player, '_rec_id', '')
    # 停止回放
    _player.stop()
    with _player_lock:
        _player = None
    # 开始录制
    with _recorder_lock:
        if _recorder is not None:
            _recorder.stop()
        from recorder import Recorder
        _recorder = Recorder()
        _recorder.start()
    log.info("重新演示此步：从步骤 %d 开始录制", current_step)
    return {"ok": True, "msg": "已开始录制，请演示正确操作",
            "from_step": current_step, "rec_id": rec_id}


@app.get("/replay/checkpoint/{rec_id}")
def get_checkpoint(rec_id: str):
    """阶段5.1：查询断点位置（上次中断处）"""
    if _resolve_rec_dir(rec_id) is None:
        return {"ok": False, "msg": "找不到任务"}
    ckpt = BASE_DIR / "data" / f"ckpt_{rec_id}.json"
    if not ckpt.exists():
        return {"ok": True, "checkpoint": None}
    import json as _json
    return {"ok": True, "checkpoint": _json.loads(ckpt.read_text(encoding="utf-8"))}


@app.get("/recordings")
def list_recordings():
    return {"recordings": _list_recordings()}


@app.get("/recordings/{rec_id}/steps")
def get_steps(rec_id: str):
    """获取录制的步骤详情。"""
    rec_dir = _resolve_rec_dir(rec_id)
    if rec_dir is None:
        return {"ok": False, "msg": "找不到录制"}
    task_file = rec_dir / "task.json"
    events_file = rec_dir / "events.json"
    path = task_file if task_file.exists() else events_file
    if not path.exists():
        return {"ok": False, "msg": "找不到录制"}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {"ok": True, "steps": data.get("steps", [])}


@app.put("/recordings/{rec_id}/name")
def rename_recording(rec_id: str, name: str = ""):
    """重命名录制（写入 meta.json）"""
    rec_dir = _resolve_rec_dir(rec_id)
    if rec_dir is None:
        return {"ok": False, "msg": "找不到录制"}
    if len(name) > 100:
        return {"ok": False, "msg": "名称过长"}
    meta_path = rec_dir / "meta.json"
    meta = {}
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    meta["name"] = name
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    return {"ok": True}


@app.delete("/recordings/{rec_id}")
def delete_recording(rec_id: str):
    """删除录制"""
    rec_dir = _resolve_rec_dir(rec_id)
    if rec_dir is None:
        return {"ok": False, "msg": "找不到录制"}
    shutil.rmtree(rec_dir)
    return {"ok": True}


def _merge_warning(base: list[dict], extra: list[dict]) -> str | None:
    """PRD 4.1：按起止应用一致性判定两次录制是否可能是同一任务的不同分支。
    取两次录制的起始/结束窗口标题，任一方向无公共子串（>=5 字符）即视为不同应用，
    提示用户避免误合并。"""
    def _ends(steps: list[dict]) -> tuple[str | None, str | None]:
        start = end = None
        for s in steps:
            w = s.get("window") or s.get("win")
            if not w:
                continue
            if start is None:
                start = w
            end = w
        return start, end

    def _same_app(a: str, b: str) -> bool:
        if a == b:
            return True
        la, lb = len(a), len(b)
        if la > lb:
            a, b = b, a
        # 最长公共子串 >= 5 视为同一应用（容忍标题动态部分，如 *Second - Notepad）
        n = len(a)
        best = 0
        for i in range(n):
            for j in range(i + best + 1, n + 1):
                if b.find(a[i:j]) != -1:
                    best = j - i
                else:
                    break
        return best >= 5

    b0, b1 = _ends(base)
    e0, e1 = _ends(extra)
    if b0 and e0 and not _same_app(b0, e0):
        return f"两次录制的起始应用不同（{e0} vs {b0}），这看起来像另一个任务，仍要合并吗？"
    if b1 and e1 and not _same_app(b1, e1):
        return f"两次录制的结束应用不同（{e1} vs {b1}），这看起来像另一个任务，仍要合并吗？"
    return None


def _same_step(a: dict, b: dict) -> bool:
    """阶段3.4：判定两步操作是否等价（用于追加录制去重）"""
    if a.get("action") != b.get("action"):
        return False
    act = a.get("action")
    if act == "input_text":
        return a.get("text") == b.get("text")
    if act == "key_press":
        return a.get("key") == b.get("key")
    ax, ay = a.get("pct_x"), a.get("pct_y")
    bx, by = b.get("pct_x"), b.get("pct_y")
    if ax is None or bx is None:
        return False
    return abs(ax - bx) < 2.0 and abs(ay - by) < 2.0


def _merge_steps(base: list[dict], extra: list[dict]) -> tuple[list[dict], int]:
    """把 extra 追加到 base 尾部：extra 头部若与 base 尾部重叠（同一段操作重复录制），
    重叠部分只保留一份。返回 (合并后步骤, 去重步数)。"""
    max_m = min(len(base), len(extra))
    m = max_m
    while m > 0:
        overlap = True
        for j in range(m):
            if not _same_step(base[len(base) - m + j], extra[j]):
                overlap = False
                break
        if overlap:
            break
        m -= 1
    merged = list(base) + extra[m:]
    # 时间戳重排为连续序列（步骤间 1s，等待步骤保持间隔）
    t = 0.0
    for s in merged:
        if s.get("action") == "wait" and s.get("duration"):
            t += s["duration"]
        else:
            t += 1.0
        s["t"] = round(t, 1)
    return merged, m


@app.post("/recordings/{rec_id}/merge/{sub_rec_id}")
def merge_recording(rec_id: str, sub_rec_id: str, body: dict | None = None):
    """阶段3.4：把第二次追加录制的步骤合并进目标任务（重复前缀去重），并删除追加录制目录。
    body.dry=true 时仅做校验（起止应用一致性），不执行合并、不删除追加录制。"""
    target = _resolve_rec_dir(rec_id)
    sub = _resolve_rec_dir(sub_rec_id)
    if target is None or sub is None:
        return {"ok": False, "msg": "找不到录制"}
    if target == sub:
        return {"ok": False, "msg": "不能与自己合并"}

    def _load_steps(d: Path) -> list[dict]:
        p = d / "events.json"
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("steps", [])
        except Exception:
            return []

    base = _load_steps(target)
    extra = _load_steps(sub)
    if not extra:
        return {"ok": False, "msg": "追加录制没有内容"}
    if not base:
        return {"ok": False, "msg": "目标任务为空，请先完成首次录制"}

    warning = _merge_warning(base, extra)
    if body and body.get("dry"):
        return {"ok": True, "warning": warning}

    merged, removed = _merge_steps(base, extra)

    # 复制追加录制的截图，重命名避免冲突
    shot_dir = target / "screenshots"
    next_idx = len(list(shot_dir.glob("shot_*.png"))) if shot_dir.exists() else 0
    for s in extra[removed:]:
        name = s.get("screenshot")
        if not name:
            continue
        src = sub / "screenshots" / name
        if not src.exists():
            continue
        new_name = f"shot_{next_idx:04d}.png"
        next_idx += 1
        shutil.copy2(src, shot_dir / new_name)
        s["screenshot"] = new_name

    (target / "events.json").write_text(
        json.dumps({"steps": merged, "count": len(merged)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    shutil.rmtree(sub)

    task_path = None
    try:
        task_path = Compiler(str(target)).compile()
    except Exception as e:
        log.exception("合并后重编译失败: %s", e)

    return {"ok": True, "steps": len(merged), "removed": removed,
            "warning": warning, "task_path": task_path}


def _resolve_rec_dir(rec_id: str) -> Path | None:
    """校验录制 id 并解析目录，防止路径穿越。"""
    if not _REC_ID_RE.fullmatch(rec_id):
        log.warning("非法录制 id: %r", rec_id)
        return None
    rec_dir = DATA_DIR / rec_id
    return rec_dir if rec_dir.is_dir() else None


def _foreground_exe() -> str:
    """当前前台窗口的进程名（为空表示不可得）"""
    try:
        import ctypes
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, None)
        if not pid:
            return ""
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        return ""


def _latest_rec_dir() -> Path | None:
    dirs = [d for d in DATA_DIR.iterdir() if d.is_dir()]
    if not dirs:
        return None
    return sorted(dirs)[-1]


def _list_recordings() -> list[dict]:
    result = []
    if not DATA_DIR.exists():
        return result
    for d in sorted(DATA_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        events_file = d / "events.json"
        count = 0
        if events_file.exists():
            with open(events_file, "r", encoding="utf-8") as f:
                count = json.load(f).get("count", 0)
        # 读取自定义名称
        name = ""
        meta_file = d / "meta.json"
        if meta_file.exists():
            with open(meta_file, "r", encoding="utf-8") as f:
                name = json.load(f).get("name", "")
        result.append({"id": d.name, "events": count, "name": name})
    return result


if __name__ == "__main__":
    import uvicorn
    # 挂载前端静态文件（不依赖 Tauri 也能浏览器访问）
    frontend_dir = BASE_DIR / "src"
    if frontend_dir.exists():
        app.mount("/ui", StaticFiles(directory=str(frontend_dir), html=True), name="ui")

    # LLM 自愈配置 API
    @app.get("/llm/config")
    def get_llm_config():
        from services.llm_heal import get_healer
        h = get_healer()
        return {"ok": True, "config": h.get_config(), "enabled": h.enabled}

    @app.put("/llm/config")
    def set_llm_config(body: dict):
        from services.llm_heal import get_healer
        h = get_healer()
        h.set_config(body)
        return {"ok": True, "enabled": h.enabled}

    uvicorn.run(app, host="127.0.0.1", port=18899)
