"""seed-recording fixture API:编译管道纯数据驱动测试(移植 openskills seed API)。"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    os.environ["TAGENT_E2E_FIXTURES"] = "1"
    import routes_tasks
    from services.scheduler import Scheduler
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "recordings").mkdir()
        scheduler = Scheduler(root / "data" / "schedules.json", lambda rid: None)
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(routes_tasks.build_router(root / "recordings", scheduler))
        from fastapi.testclient import TestClient
        client = TestClient(app)

        # 未开环境开关时不可见
        with patch.dict(os.environ, {"TAGENT_E2E_FIXTURES": "0"}):
            assert not client.post("/tasks/dev/seed-recording",
                                   json={"events": []}).json()["ok"]

        # 连续输入合并为单步(移植 openskills pendingFill)
        r = client.post("/tasks/dev/seed-recording", json={"events": [
            {"action": "input_text", "text": "hel", "window": "W - Notepad",
             "t": 0.1, "screen_x": 1, "screen_y": 1, "hwnd": 1},
            {"action": "input_text", "text": "lo", "window": "W - Notepad",
             "t": 0.2, "screen_x": 1, "screen_y": 1, "hwnd": 1},
        ]}).json()
        assert r["ok"], r
        steps = r["task"]["steps"]
        assert len(steps) == 1 and steps[0]["text"] == "hello", steps
        assert steps[0].get("step_id") == "s1"

        # 双击合并:同位置 <0.5s 两个 click → double_click
        r2 = client.post("/tasks/dev/seed-recording", json={"events": [
            {"action": "click", "window": "W", "t": 1.0, "screen_x": 10, "screen_y": 10},
            {"action": "click", "window": "W", "t": 1.2, "screen_x": 10, "screen_y": 10},
        ]}).json()
        assert [s["action"] for s in r2["task"]["steps"]] == ["double_click"]

        # 自窗口过滤:TAGENT 自己的 UI 不入任务
        r3 = client.post("/tasks/dev/seed-recording", json={"events": [
            {"action": "click", "window": "智能操作助手", "t": 0.1, "screen_x": 1, "screen_y": 1},
            {"action": "click", "window": "W - 真实", "t": 0.2, "screen_x": 2, "screen_y": 2},
        ]}).json()
        assert [s.get("window") for s in r3["task"]["steps"]] == ["W - 真实"]
    print("compiler_seed PASS")


if __name__ == "__main__":
    main()
