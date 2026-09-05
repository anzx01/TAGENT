"""Deterministic regression checks for pause/stop/retry control paths."""
from __future__ import annotations

import threading
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from player import Player
import player


def make_player() -> Player:
    p = object.__new__(Player)
    p._stopped = False
    p._paused = False
    p._resume = None
    p._resume_action = None
    p._waiting_step = None
    p._pause_reason = ""
    p._last_pause_reason = ""
    p._step_results = []
    p._pause_reasons = []
    return p


def main() -> int:
    assert player.WAIT_HARD_TIMEOUT <= 30
    p = make_player()
    result: list[str] = []
    t = threading.Thread(
        target=lambda: result.append(p._pause_for_human(2, "test")), daemon=True
    )
    t.start()
    deadline = time.time() + 2
    while not p.paused and time.time() < deadline:
        time.sleep(0.01)
    assert p.paused and p._waiting_step == 2
    p.resume(retry=True)
    t.join(2)
    assert result == ["retry"]
    assert not p.paused

    p = make_player()
    t = threading.Thread(target=lambda: p._pause_for_human(0, "test"), daemon=True)
    t.start()
    deadline = time.time() + 2
    while not p.paused and time.time() < deadline:
        time.sleep(0.01)
    p.stop()
    t.join(2)
    assert not t.is_alive() and p._stopped
    print("replay_control PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
