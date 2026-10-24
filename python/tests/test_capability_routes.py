"""Capability ranges use executable steps, not task metadata fields."""
import json
import shutil
import sys
import tempfile
from unittest.mock import patch
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routes_tasks import build_router
from routes_tasks import _run_succeeded
from services.scheduler import Scheduler
from services.capability_registry import CapabilityRegistry
from services.workflow_plan import build_plan, plan_source_hash


def main():
    assert not _run_succeeded({"success": False, "steps": [{"status": "ok"}]})
    assert _run_succeeded({"success": True, "steps": [{"status": "ok"}]})
    assert not _run_succeeded({"steps": [{"status": "failed"}]})
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "recordings"
        root.mkdir()
        task_dir = root / "20261018_000001"
        task_dir.mkdir()
        (task_dir / "task.json").write_text(json.dumps({
            "version": 2, "source": "demo", "steps": [
                {"index": i, "action": "input_text", "text": "{{ order_id }}"} for i in range(5)
            ]
        }), encoding="utf-8")
        (task_dir / "meta.json").write_text(json.dumps({
            "var_sources": {"order_id": {"type": "clipboard"}}, "session_steps": [3]
        }), encoding="utf-8")
        empty_dir = root / "20261018_000002"
        empty_dir.mkdir()
        (empty_dir / "task.json").write_text('{"steps": []}', encoding="utf-8")
        app = FastAPI()
        scheduler = Scheduler(root.parent / "schedules.json", lambda _: True)
        autostart_events = []
        app.include_router(build_router(root, scheduler, autostart_events.append))
        client = TestClient(app)
        checkpoint_dir = root.parent / "data"
        checkpoint_dir.mkdir()
        checkpoint_file = checkpoint_dir / f"ckpt_{task_dir.name}.json"
        task_data = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        checkpoint_file.write_text(json.dumps({
            "next_step": 2, "source_hash": plan_source_hash(task_data)
        }), encoding="utf-8")
        task_item = next(item for item in client.get("/tasks").json()["tasks"]
                         if item["id"] == task_dir.name)
        assert task_item["checkpoint"] == {"next_step": 2, "total": 5}
        checkpoint_file.write_text(json.dumps({"next_step": 2, "source_hash": "old"}),
                                   encoding="utf-8")
        task_item = next(item for item in client.get("/tasks").json()["tasks"]
                         if item["id"] == task_dir.name)
        assert task_item["checkpoint"] is None
        checkpoint_file.unlink()
        run_dir = root.parent / "runs"
        run_dir.mkdir()
        run_file = run_dir / f"1781760000000-{task_dir.name}_{task_dir.name}.json"
        run_file.write_text(json.dumps({
            "rec_id": task_dir.name, "trigger": "schedule", "success": False,
            "steps": [{"step": 0, "action": "click", "status": "failed",
                       "detail": "窗口未找到: secret text", "verification": "failed"}],
            "pause_reasons": [{"step": 0, "reason": "等待目标窗口未出现: secret text"}],
            "summary": "已中断", "plan_revision": 3,
        }), encoding="utf-8")
        history = client.get(f"/tasks/{task_dir.name}/runs").json()["runs"]
        assert len(history) == 1 and not history[0]["success"] and history[0]["trigger"] == "schedule"
        detail = client.get(f"/tasks/{task_dir.name}/runs/{run_file.stem}").json()
        assert "secret text" not in json.dumps(detail, ensure_ascii=False)
        assert detail["report"]["pause_reasons"][0]["reason"] == "等待目标窗口未出现"
        assert detail["report"]["plan_revision"] == 3
        assert not client.get(f"/tasks/{task_dir.name}/runs/invalid.id").json()["ok"]
        assert not client.get(f"/tasks/{empty_dir.name}/runs/{run_file.stem}").json()["ok"]
        discovery = client.get("/tasks/discovery").json()["candidates"]
        assert next(item for item in discovery if item["task_id"] == task_dir.name)["success_rate"] == 0
        url = "/tasks/capabilities"
        valid = client.post(url, json={"name": "保存订单", "source_task": task_dir.name,
                                       "step_range": [2, 5]}).json()
        assert valid["ok"] and valid["capability"]["step_range"] == [2, 5]
        second = client.post(url, json={"name": "打开订单", "source_task": task_dir.name,
                                        "step_range": [0, 2]}).json()
        assert second["ok"]
        with patch("services.task_versions.TaskVersions.snapshot", side_effect=OSError("disk full")):
            failed = client.post(url + "/compose", json={"name": "失败任务", "capability_ids": [
                second["capability"]["id"]]}).json()
        assert not failed["ok"]
        assert len([d for d in root.iterdir() if d.is_dir()]) == 2
        assert next(c for c in client.get(url).json()["capabilities"]
                    if c["id"] == second["capability"]["id"])["runs"] == 0
        composed = client.post(url + "/compose", json={"name": "处理订单", "capability_ids": [
            second["capability"]["id"], valid["capability"]["id"]]}).json()
        assert composed["ok"]
        result = json.loads((root / composed["task"]["id"] / "task.json").read_text(encoding="utf-8"))
        assert [step["index"] for step in result["steps"]] == list(range(7))
        assert len(result["capability_sources"]) == 2
        assert result["capability_sources"][1]["composed_range"] == [3, 7]
        assert result["steps"][3]["capability"]["name"] == "保存订单"
        assert result["plan"]["steps"][3]["capability"]["name"] == "保存订单"
        assert (root / composed["task"]["id"] / "plan.json").exists()
        meta = json.loads((root / composed["task"]["id"] / "meta.json").read_text(encoding="utf-8"))
        assert meta["var_sources"] == {"order_id": {"type": "clipboard"}}
        assert meta["session_steps"] == [4]
        assert result["plan"]["steps"][4]["mode"] == "human_required"
        assert any("人工核实结果" in q["question"] for q in result["plan"]["open_questions"])
        review_url = "/tasks/" + composed["task"]["id"] + "/review"
        assert not client.put(review_url, json={"approved": True}).json()["ok"]
        schedule_url = "/tasks/" + composed["task"]["id"] + "/schedule"
        assert not client.put(schedule_url, json={"kind": "daily", "hour": 9}).json()["ok"]
        assert client.put(f"/tasks/{composed['task']['id']}/var_sources/order_id",
                          json={"type": "clipboard"}).json()["ok"]
        answers = {str(q["step"]): "人工确认后继续" for q in result["plan"]["open_questions"]}
        assert client.put(review_url, json={"approved": True, "answers": answers}).json()["ok"]
        assert client.put(f"/tasks/{composed['task']['id']}/var_sources/order_id",
                          json={"type": "clipboard"}).json()["ok"]
        assert client.get(review_url).json()["review"]["approved"] is True
        assert client.put(schedule_url, json={"kind": "daily", "hour": 25}).status_code == 422
        assert client.put(schedule_url, json={"kind": "daily", "hour": 9}).json()["ok"]
        assert autostart_events[-1] is True
        with patch("routes_tasks.TaskStore.set_schedule", side_effect=OSError("disk full")):
            failed_revoke = client.put(review_url, json={"approved": False}).json()
        assert not failed_revoke["ok"]
        assert client.get(review_url).json()["review"]["approved"] is True
        assert scheduler.list_jobs()[composed["task"]["id"]]["kind"] == "daily"
        assert client.put(review_url, json={"approved": False}).json()["ok"]
        assert composed["task"]["id"] not in scheduler.list_jobs()
        assert next(item for item in client.get("/tasks").json()["tasks"]
                    if item["id"] == composed["task"]["id"])["schedule"] is None
        assert client.put(review_url, json={"approved": True}).json()["ok"]
        assert client.put(schedule_url, json={"kind": "daily", "hour": 9}).json()["ok"]
        with patch("routes_tasks.TaskStore.set_schedule", side_effect=OSError("disk full")):
            failed_delete = client.delete(schedule_url).json()
        assert not failed_delete["ok"]
        assert scheduler.list_jobs()[composed["task"]["id"]]["kind"] == "daily"
        assert autostart_events[-2:] == [False, True]
        changed = client.put(f"/tasks/{composed['task']['id']}/vars/3?var=order_number").json()
        assert changed["ok"]
        assert composed["task"]["id"] not in scheduler.list_jobs()
        assert autostart_events[-1] is False
        assert client.get(review_url).json()["review"]["approved"] is False
        assert next(item for item in client.get("/tasks").json()["tasks"]
                    if item["id"] == composed["task"]["id"])["schedule"] is None
        approved_again = client.put(review_url, json={"approved": True}).json()
        assert approved_again["ok"]
        source_change = client.put(f"/tasks/{composed['task']['id']}/var_sources/order_number",
                                   json={"type": "ui", "control_name": "订单号"}).json()
        assert source_change["ok"]
        assert client.get(review_url).json()["review"]["approved"] is False
        assert not client.put(f"/tasks/{composed['task']['id']}/var_sources/order_number",
                              json={"type": "ui"}).json()["ok"]
        unbound_dir = root / "20261018_000005"
        unbound_dir.mkdir()
        unbound_task = {"version": 2, "steps": [{"index": 0, "action": "input_text",
                                                  "window": "Notepad", "text": "{{ missing }}"}]}
        unbound_plan = build_plan(unbound_task)
        (unbound_dir / "task.json").write_text(json.dumps(unbound_task), encoding="utf-8")
        (unbound_dir / "plan.json").write_text(json.dumps(unbound_plan), encoding="utf-8")
        (unbound_dir / "review.json").write_text(json.dumps({"approved": True, "revision": 1,
                                                               "source_hash": unbound_plan["source_hash"]}), encoding="utf-8")
        assert not client.put(f"/tasks/{unbound_dir.name}/schedule",
                              json={"kind": "daily", "hour": 9}).json()["ok"]
        versions = json.loads((root / composed["task"]["id"] / "versions" / "manifest.json").read_text(encoding="utf-8"))
        assert versions["current"] == 2
        for source, span in ((task_dir.name, [2, 6]), (empty_dir.name, [0, 1])):
            assert not client.post(url, json={"name": "bad", "source_task": source,
                                              "step_range": span}).json()["ok"]
        assert len(client.get(url).json()["capabilities"]) == 2
        original = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        original["steps"][0]["action"] = "changed"
        (task_dir / "task.json").write_text(json.dumps(original), encoding="utf-8")
        assert client.post(url + "/compose", json={"name": "快照能力", "capability_ids": [
            second["capability"]["id"]]}).json()["ok"]
        # A second task may reuse the same variable name, but a different
        # source must be resolved before any executable composition is saved.
        other_dir = root / "20261018_000003"
        other_dir.mkdir()
        (other_dir / "task.json").write_text(json.dumps({"version": 2, "steps": [
            {"index": 0, "action": "input_text", "text": "{{ order_id }}"}
        ]}), encoding="utf-8")
        (other_dir / "meta.json").write_text(json.dumps({
            "var_sources": {"order_id": {"type": "ui", "control_name": "Order"}}
        }), encoding="utf-8")
        other = client.post(url, json={"name": "另一来源", "source_task": other_dir.name,
                                       "step_range": [0, 1]}).json()
        assert other["ok"]
        assert not client.post(url, json={"name": "bad condition", "source_task": other_dir.name,
                                          "step_range": [0, 1], "postcondition": {"type": "url", "value": "x"}}).json()["ok"]
        checked = client.post(url, json={"name": "确认保存", "source_task": other_dir.name,
                                         "step_range": [0, 1],
                                         "postcondition": {"type": "text", "value": "提交成功"}}).json()
        assert checked["ok"]
        checked_task = client.post(url + "/compose", json={"name": "带验证任务", "capability_ids": [
            checked["capability"]["id"]]}).json()
        assert checked_task["ok"]
        checked_steps = json.loads((root / checked_task["task"]["id"] / "task.json").read_text(encoding="utf-8"))["steps"]
        assert checked_steps[-1]["action"] == "verify_capability"
        assert checked_steps[-1]["postcondition"] == {"type": "text", "value": "提交成功"}
        # Re-registering a composed capability must preserve its verifier once.
        composed_id = checked_task["task"]["id"]
        nested = client.post(url, json={"name": "嵌套能力", "source_task": composed_id,
                                        "step_range": [0, len(checked_steps)]}).json()
        assert nested["ok"]
        nested_task = client.post(url + "/compose", json={"name": "二次组合", "capability_ids": [
            nested["capability"]["id"]]}).json()
        assert nested_task["ok"]
        nested_steps = json.loads((root / nested_task["task"]["id"] / "task.json").read_text(encoding="utf-8"))["steps"]
        assert sum(step["action"] == "verify_capability" for step in nested_steps) == 1
        clipboard_dir = root / "20261018_000004"
        clipboard_dir.mkdir()
        (clipboard_dir / "task.json").write_text(json.dumps({"version": 2, "steps": [
            {"index": 0, "action": "input_text", "text": "{{ order_id }}"}
        ]}), encoding="utf-8")
        (clipboard_dir / "meta.json").write_text(json.dumps({
            "var_sources": {"order_id": {"type": "clipboard"}}
        }), encoding="utf-8")
        clipboard = client.post(url, json={"name": "剪贴板来源", "source_task": clipboard_dir.name,
                                           "step_range": [0, 1]}).json()
        assert clipboard["ok"]
        conflict = client.post(url + "/compose", json={"name": "冲突任务", "capability_ids": [
            clipboard["capability"]["id"], other["capability"]["id"]]}).json()
        assert not conflict["ok"] and "order_id" in conflict["msg"]
        unrelated_dir = root / "20261018_000006"
        unrelated_dir.mkdir()
        (unrelated_dir / "task.json").write_text(json.dumps({"version": 2, "steps": [
            {"index": 0, "action": "wait", "seconds": 0}
        ]}), encoding="utf-8")
        (unrelated_dir / "meta.json").write_text(json.dumps({
            "var_sources": {"order_id": {"type": "ui", "control_name": "Unused"}}
        }), encoding="utf-8")
        unrelated = client.post(url, json={"name": "不使用订单号", "source_task": unrelated_dir.name,
                                           "step_range": [0, 1]}).json()
        assert unrelated["ok"]
        relevant_only = client.post(url + "/compose", json={"name": "只绑定实际输入", "capability_ids": [
            clipboard["capability"]["id"], unrelated["capability"]["id"]]}).json()
        assert relevant_only["ok"]
        relevant_meta = json.loads((root / relevant_only["task"]["id"] / "meta.json").read_text(encoding="utf-8"))
        assert relevant_meta["var_sources"] == {"order_id": {"type": "clipboard"}}
        snapshot = next(c for c in client.get(url).json()["capabilities"]
                        if c["id"] == valid["capability"]["id"])
        assert len(snapshot["steps"]) == 3 and snapshot["session_steps"] == [1]
        CapabilityRegistry(root / "capabilities.json").detach_source(task_dir.name,
            json.loads((task_dir / "task.json").read_text(encoding="utf-8")),
            json.loads((task_dir / "meta.json").read_text(encoding="utf-8")))
        shutil.rmtree(task_dir)
        standalone = client.post(url + "/compose", json={"name": "独立能力任务",
                                    "capability_ids": [valid["capability"]["id"]]}).json()
        assert standalone["ok"]
    print("capability_routes PASS")


if __name__ == "__main__":
    main()
