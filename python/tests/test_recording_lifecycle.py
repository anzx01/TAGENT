"""Deleting a recording must also remove its scheduled background job."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.recording_lifecycle import delete_recording
from services.scheduler import Scheduler
from services.capability_registry import CapabilityRegistry


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        schedule_path = root / "schedules.json"
        recording = root / "20261018_000001"
        recording.mkdir()
        (recording / "task.json").write_text("{}", encoding="utf-8")
        scheduler = Scheduler(schedule_path, lambda _: True)
        scheduler.set_job(recording.name, {"kind": "daily", "hour": 9, "minute": 0})
        scheduler.set_job("other", {"kind": "daily", "hour": 10, "minute": 0})
        sync_calls = []

        result = delete_recording(recording.name, recording, scheduler, sync_calls.append)
        assert result["ok"] and not recording.exists()
        assert scheduler.list_jobs() == {"other": {"kind": "daily", "hour": 10, "minute": 0}}
        assert sync_calls == [True]

        registry = CapabilityRegistry(root / "capabilities.json")
        registry.register("saved", recording.name, [0, 1], steps=[{"action": "wait"}])
        recording.mkdir()
        result = delete_recording(recording.name, recording, scheduler,
                                  detach_capabilities=registry.detach_source)
        assert result["ok"] and registry.list()[0]["source_task"] == ""
        assert registry.list()[0]["steps"] == [{"action": "wait"}]

        recording.mkdir()
        previous = {"kind": "daily", "hour": 9, "minute": 0}
        scheduler.set_job("other", None)
        scheduler.set_job(recording.name, previous)

        def reject_disable(enabled):
            if not enabled:
                raise RuntimeError("Task Scheduler denied delete")

        result = delete_recording(recording.name, recording, scheduler, reject_disable)
        assert not result["ok"] and recording.exists()
        assert scheduler.list_jobs()[recording.name] == previous

        result = delete_recording(recording.name, recording, scheduler,
                                  lambda _: None,
                                  lambda _: (_ for _ in ()).throw(OSError("locked")))
        assert not result["ok"] and recording.exists()
        assert scheduler.list_jobs()[recording.name] == previous
        print("recording_lifecycle PASS")


if __name__ == "__main__":
    main()
