"""Discovery ranking is deterministic and based only on local run evidence."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.discovery import rank_candidates


def main() -> int:
    result = rank_candidates(
        [{"id": "a", "name": "Often", "steps": 3}, {"id": "b", "name": "Rare", "steps": 9}],
        {"a": [{"success": True, "pause_count": 0}] * 4,
         "b": [{"success": True, "pause_count": 1}]},
    )
    assert result[0]["task_id"] == "a"
    assert result[0]["repeat_score"] == 12
    assert result[0]["success_rate"] == 1.0
    print("discovery PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
