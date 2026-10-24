"""A failed action or wait cannot advance the resumable checkpoint."""
import json
import sys
import threading
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from player import Player
from services.input.common import StepResult


def run_case(steps, action_result=None, wait_result=None, pause_action="skip"):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        task = root / "task.json"
        task.write_text(json.dumps({"steps": steps}), encoding="utf-8")
        player = Player(task)
        player.checkpoint_path = root / "checkpoint.json"
        player.checkpoint_path.write_text(json.dumps({"next_step": 1}), encoding="utf-8")
        def pause(*_):
            if pause_action == "stop":
                player._stopped = True
            return "retry" if pause_action == "retry" else "skip"
        player._pause_for_human = pause
        player._capture_step_screenshot = lambda *_: None
        player._play_step_with_retry = lambda *_: action_result
        player._do_wait = lambda *_: wait_result
        played = []
        if action_result is not None:
            player._play_step_with_retry = lambda step: (played.append(step), action_result)[1]
        with patch("player._check_foreground"), patch("player._release_keys"), \
             patch("player.win32gui.GetForegroundWindow", return_value=0):
            result = player.play(step_delay=0, start_delay=0, max_wait=0)
        checkpoint = json.loads(player.checkpoint_path.read_text(encoding="utf-8")) if player.checkpoint_path.exists() else None
        return result, checkpoint, played


def main():
    verifier = Player.__new__(Player)
    verifier._try_locator = lambda loc, raw: (None, None)
    assert verifier._verify_step({"action": "input_text", "locator": [
        {"type": "ocr", "text": "Search"}]}) is False
    assert verifier._verify_step({"action": "click", "locator": [
        {"type": "ocr", "text": "Open"}]}) is None

    executor = Player.__new__(Player)
    executor._do_drag = lambda step: StepResult(True, step["action"])
    for gesture in ("swipe", "drag"):
        assert executor._play_step({"action": gesture}).ok
    unknown = executor._play_step({"action": "mystery_action"})
    assert not unknown.ok and unknown.reason == "unsupported_action"

    with tempfile.TemporaryDirectory() as td:
        task = Path(td) / "task.json"
        task.write_text(json.dumps({"steps": [{
            "action": "click", "text": "secret order number", "effect": "destructive"
        }]}), encoding="utf-8")
        player = Player(task)
        player._pause_for_human = lambda *_: "stop"
        player._capture_step_screenshot = lambda *_: None
        with patch("player._check_foreground"), patch("player._release_keys"), \
             patch("player.win32gui.GetForegroundWindow", return_value=0):
            player.play(step_delay=0, start_delay=0, max_wait=0)
        assert all("secret order number" not in item["reason"]
                   for item in player._pause_reasons)

    failed = StepResult(False, "input", "missing_variable")
    result, checkpoint, played = run_case([
        {"action": "input_text", "window": "Notepad", "text": "{{order_id}}"},
        {"action": "key_press", "window": "Notepad", "key": "enter"},
    ], action_result=failed, pause_action="stop")
    assert not result["success"] and len(played) == 1 and checkpoint is None

    result, checkpoint, played = run_case([
        {"action": "wait", "window": "Missing window", "duration": 0},
        {"action": "key_press", "window": "Notepad", "key": "enter"},
    ], action_result=StepResult(True, "input"), wait_result=False, pause_action="stop")
    assert not result["success"] and not played and checkpoint is None
    assert result["steps"][0]["status"] == "failed"

    with tempfile.TemporaryDirectory() as td:
        task = Path(td) / "task.json"
        task.write_text(json.dumps({"steps": [{
            "action": "click", "locator": [{"type": "uia", "name": "Missing"}]
        }]}), encoding="utf-8")
        player = Player(task)
        player.checkpoint_path = Path(td) / "checkpoint.json"
        player._check_locator_retry = lambda *_: False
        player._try_correction = lambda *_: False
        player._try_llm_heal = lambda *_: {"action": "click"}
        player._play_step = lambda *_: (_ for _ in ()).throw(AssertionError("proposal executed"))
        player._capture_step_screenshot = lambda *_: None
        player._pause_for_human = lambda *_: "stop"
        with patch("player._check_foreground"), patch("player._release_keys"), \
             patch("player.win32gui.GetForegroundWindow", return_value=0):
            result = player.play(step_delay=0, start_delay=0, max_wait=0)
        assert not result["success"] and not player.checkpoint_path.exists()

    with tempfile.TemporaryDirectory() as td:
        task = Path(td) / "task.json"
        task.write_text(json.dumps({"steps": [{"action": "key_press", "key": "enter"}]}), encoding="utf-8")
        player = Player(task)
        player.checkpoint_path = Path(td) / "checkpoint.json"
        player._capture_step_screenshot = lambda *_: None
        player._play_step_with_retry = lambda *_: StepResult(True, "test")
        with patch("pathlib.Path.replace", side_effect=OSError("disk full")), \
             patch("player._check_foreground"), patch("player._release_keys"), \
             patch("player.win32gui.GetForegroundWindow", return_value=0):
            result = player.play(step_delay=0, start_delay=0, max_wait=0)
        assert result["success"] and result["checkpoint_warning"]

    result, checkpoint, played = run_case([
        {"action": "input_text", "window": "Notepad", "text": "x"},
        {"action": "key_press", "window": "Notepad", "key": "enter"},
    ], action_result=failed)
    assert result["success"] and len(played) == 2
    assert result["steps"][0]["verification"] == "human_confirmed"
    assert checkpoint["next_step"] == 2

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        task = root / "task.json"
        task.write_text(json.dumps({"steps": [
            {"action": "verify_capability", "postcondition": {"type": "window", "value": "Saved"}},
            {"action": "key_press", "window": "Notepad", "key": "enter"},
        ]}), encoding="utf-8")
        player = Player(task)
        player.checkpoint_path = root / "checkpoint.json"
        player._capture_step_screenshot = lambda *_: None
        played = []
        player._play_step_with_retry = lambda step: (played.append(step), StepResult(True, "input"))[1]
        checks = iter([(False, "not saved"), (True, "Saved")])
        with patch("player._check_foreground"), patch("player._release_keys"), \
             patch("player.win32gui.GetForegroundWindow", return_value=0), \
             patch("services.capability_verifier.verify_current_screen",
                   side_effect=lambda _: next(checks)):
            thread = threading.Thread(
                target=lambda: result_holder.append(player.play(step_delay=0, start_delay=0)))
            result_holder = []
            thread.start()
            deadline = time.time() + 2
            while not player.paused and time.time() < deadline:
                time.sleep(0.01)
            assert player.paused and player._waiting_step == 0
            player.resume(retry=True)
            thread.join(2)
        assert not thread.is_alive()
        result = result_holder[0]
        checkpoint = json.loads(player.checkpoint_path.read_text(encoding="utf-8"))
    assert result["success"] and len(played) == 1
    assert result["steps"][0]["verification"] == "passed"
    assert checkpoint["next_step"] == 2
    print("replay_failure_boundary PASS")


if __name__ == "__main__":
    main()
