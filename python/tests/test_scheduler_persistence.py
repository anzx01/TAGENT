"""Scheduled work survives process restarts without firing twice."""
import sys
import threading
import tempfile
import time
from datetime import datetime as RealDateTime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.scheduler import Scheduler


def main():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "schedules.json"
        fired = []
        def run(rec_id):
            fired.append(rec_id)
            return True
        first = Scheduler(path, run)
        start = RealDateTime(2026, 10, 18, 8, 0).timestamp()
        with patch("services.scheduler.time.time", return_value=start):
            first.set_job("interval", {"kind": "interval", "minutes": 1})
        assert first.load()["interval"]["next_run_at"] == start + 60
        restarted = Scheduler(path, run)
        with patch("services.scheduler.datetime") as clock:
            clock.now.return_value = RealDateTime.fromtimestamp(start + 61)
            restarted._tick()
            restarted._tick()
        assert fired == ["interval"]
        assert restarted.load()["interval"]["next_run_at"] == start + 121

        original = restarted.load()["interval"]
        with patch("services.scheduler.time.time", return_value=start + 300):
            restarted.set_job("interval", original)
        assert restarted.load()["interval"]["next_run_at"] == start + 121

        restarted.set_job("interval", None)

        entered = threading.Event()
        release = threading.Event()

        def blocked_run(_):
            entered.set()
            assert release.wait(2)
            return True

        race = Scheduler(path, blocked_run)
        race.set_job("race", {"kind": "interval", "minutes": 1})
        config = race.load()
        config["race"]["next_run_at"] = 1
        race.save(config)
        attempting = threading.Event()
        deleted = threading.Event()

        def delete_during_tick():
            attempting.set()
            race.set_job("race", None)
            deleted.set()

        with patch("services.scheduler.datetime") as clock:
            clock.now.return_value = RealDateTime(2026, 10, 18, 9, 1)
            tick = threading.Thread(target=race._tick)
            tick.start()
            assert entered.wait(2)
            remove = threading.Thread(target=delete_during_tick)
            remove.start()
            assert attempting.wait(2)
            time.sleep(0.05)
            assert not deleted.is_set()
            release.set()
            tick.join(2)
            remove.join(2)

        assert not tick.is_alive() and not remove.is_alive()
        assert race.list_jobs() == {}

        restarted.set_job("daily", {"kind": "daily", "hour": 9, "minute": 0})
        with patch("services.scheduler.datetime") as clock:
            clock.now.return_value = RealDateTime(2026, 10, 18, 9, 1)
            restarted._tick()
            Scheduler(path, run)._tick()
        assert fired == ["interval", "daily"]

        attempts = []
        def succeeds_on_retry(rec_id):
            attempts.append(rec_id)
            return len(attempts) > 1
        def succeeds(rec_id):
            attempts.append(rec_id)
            return True
        busy = Scheduler(path, succeeds_on_retry)
        busy.set_job("daily", {"kind": "daily", "hour": 9, "minute": 0})
        with patch("services.scheduler.datetime") as clock:
            clock.now.return_value = RealDateTime(2026, 10, 19, 9, 1)
            busy._tick()
            assert busy.load()["daily"].get("last_run_day") is None
            Scheduler(path, succeeds)._tick()
        assert attempts == ["daily", "daily"]
        assert busy.load()["daily"]["last_run_day"] == "20261019"
        with patch("services.scheduler.time.time", return_value=start):
            busy.set_job("interval", {"kind": "interval", "minutes": 1})
        busy.set_job("daily", None)
        interval_attempts = []
        def interval_run(rec_id):
            interval_attempts.append(rec_id)
            return len(interval_attempts) > 1
        retrying = Scheduler(path, interval_run)
        with patch("services.scheduler.datetime") as clock:
            clock.now.return_value = RealDateTime.fromtimestamp(start + 61)
            retrying._tick()
            assert retrying.load()["interval"]["next_run_at"] == start + 60
            Scheduler(path, interval_run)._tick()
        assert interval_attempts == ["interval", "interval"]
        assert retrying.load()["interval"]["next_run_at"] == start + 121
        retrying.set_job("interval", None)
        with patch("services.scheduler.datetime") as clock:
            clock.now.return_value = RealDateTime(2026, 10, 19, 23, 59)
            restarted._tick()
        assert fired == ["interval", "daily"]
    print("scheduler_persistence PASS")


if __name__ == "__main__":
    main()
