"""FastAPI 服务：连接前端 UI 与录制/回放引擎。"""

import os
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from recorder import Recorder
from player import Player

app = FastAPI(title="智能操作助手")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = Path(__file__).parent.parent / "recordings"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 当前录制会话
_recorder: Recorder | None = None
_recorder_lock = threading.Lock()


class ReplayRequest(BaseModel):
    recording_id: str | None = None


@app.get("/status")
def status():
    """当前状态。"""
    return {
        "recording": _recorder is not None and _recorder.recording,
        "recordings": _list_recordings(),
    }


@app.post("/record/start")
def start_recording():
    """开始录制。"""
    global _recorder
    with _recorder_lock:
        if _recorder and _recorder.recording:
            return {"ok": False, "msg": "已在录制中"}
        import time
        rec_id = time.strftime("%Y%m%d_%H%M%S")
        out_dir = DATA_DIR / rec_id
        _recorder = Recorder(str(out_dir))
        _recorder.start()
        return {"ok": True, "recording_id": rec_id}


@app.post("/record/stop")
def stop_recording():
    """停止录制。"""
    global _recorder
    with _recorder_lock:
        if not _recorder or not _recorder.recording:
            return {"ok": False, "msg": "未在录制"}
        path = _recorder.stop()
        event_count = len(_recorder.events)
        return {"ok": True, "events_path": path, "event_count": event_count}


@app.post("/replay/start")
def start_replay(req: ReplayRequest):
    """开始回放。"""
    global _recorder
    # 不能同时录制和回放
    if _recorder and _recorder.recording:
        return {"ok": False, "msg": "正在录制，请先停止"}

    # 找到要回放的录制
    if req.recording_id:
        rec_dir = DATA_DIR / req.recording_id
    else:
        # 默认最新的
        dirs = sorted([d for d in DATA_DIR.iterdir() if d.is_dir()])
        if not dirs:
            return {"ok": False, "msg": "没有可回放的录制"}
        rec_dir = dirs[-1]

    events_path = rec_dir / "events.json"
    if not events_path.exists():
        return {"ok": False, "msg": f"找不到 {events_path}"}

    # 在后台线程回放（不阻塞 API）
    def _run():
        try:
            p = Player(str(events_path))
            p.play()
        except Exception as e:
            print(f"回放出错: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"ok": True, "recording_id": rec_dir.name}


@app.get("/recordings")
def list_recordings():
    """列出所有录制。"""
    return {"recordings": _list_recordings()}


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
            import json
            with open(events_file, "r", encoding="utf-8") as f:
                count = json.load(f).get("count", 0)
        result.append({"id": d.name, "events": count})
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=18899)
