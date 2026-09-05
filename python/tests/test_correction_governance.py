"""Correction approval and rollback remain explicit and reversible."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.corrections import CorrectionStore


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        store = CorrectionStore(Path(td))
        corr = store.add_correction([{"action": "click"}], [{"action": "click"}])
        # A newly captured patch is a proposal and must not affect formal
        # replay until explicitly approved.
        assert store.match([{"action": "click"}]) is None
        approved = store.approve(corr["id"])
        assert approved["approved"] and approved["trusted"]
        assert approved["approved_revision"] == 1
        assert approved["active_revision"] == 1
        assert store.match([{"action": "click"}]) == [{"action": "click"}]
        rolled = store.rollback(corr["id"])
        assert not rolled["approved"] and not rolled["trusted"]
        assert rolled["active_revision"] is None
        assert store.match([{"action": "click"}]) is None
        assert [x["action"] for x in rolled["history"]] == ["approve", "rollback"]
        assert json.loads(store.path.read_text(encoding="utf-8"))[0]["id"] == corr["id"]
    print("correction_governance PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
