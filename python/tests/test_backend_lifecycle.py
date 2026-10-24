"""Closing the shell must not interrupt active desktop work."""
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


def main():
    exited = threading.Event()
    old_player, old_recorder = server._player, server._recorder
    try:
        with patch.object(server, "_delayed_exit", lambda _: exited.set()), \
             patch.object(server, "startup_preference", return_value=False), \
             patch.object(server._scheduler, "list_jobs", return_value={}):
            server._player = object()
            server._recorder = None
            assert server.lifecycle_status()["replaying"] is True
            assert server.detach_lifecycle()["ok"]
            assert not exited.wait(0.1)
            server._player = None
            server._exit_if_detached_and_idle()
            assert exited.wait(1)

            exited.clear()
            server.attach_lifecycle()
            server._recorder = SimpleNamespace(recording=True)
            server.detach_lifecycle()
            assert not exited.wait(0.1)
            server._recorder.recording = False
            server._exit_if_detached_and_idle()
            assert exited.wait(1)

            exited.clear()
            server.attach_lifecycle()
            server.detach_lifecycle()
            server.attach_lifecycle()
        generation = server._lifecycle_generation
        with patch("server.time.sleep"), patch("server.os._exit") as exit_process:
            server._delayed_exit(generation - 1)
            exit_process.assert_not_called()
            server._lifecycle_detached.set()
            server._delayed_exit(generation)
            exit_process.assert_called_once_with(0)
    finally:
        server.attach_lifecycle()
        server._player, server._recorder = old_player, old_recorder
    print("backend_lifecycle PASS")


if __name__ == "__main__":
    main()
