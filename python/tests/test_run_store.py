"""运行历史 SQLite 索引单测。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import run_store


def main():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "runs.db"
        run_store.index_one(db, {"run_id": "r1", "rec_id": "t1", "ts": "1",
                                 "success": True, "plan_revision": 3,
                                 "steps": [{"step_id": "s1", "action": "click",
                                            "status": "failed", "method": "uia"}]}, "r1.json")
        run_store.index_one(db, {"run_id": "r2", "rec_id": "t2", "ts": "2", "success": False,
                                 "steps": []}, "r2.json")
        rows = run_store.query(db)
        assert [r["run_id"] for r in rows] == ["r2", "r1"]  # rowid 倒序
        only = run_store.query(db, rec_id="t1")
        assert only[0]["plan_revision"] == 3 and only[0]["success"] == 1
        assert run_store.query(db, success=False)[0]["run_id"] == "r2"
        assert run_store.query(db, limit=1).__len__() == 1

        (Path(td) / "runs").mkdir()
        (Path(td) / "runs" / "x.context.json").write_text("{}", encoding="utf-8")
        (Path(td) / "runs" / "1789-r3_t3.json").write_text(
            json.dumps({"rec_id": "t3", "ts": "3", "success": True, "steps": []}), encoding="utf-8")
        db2 = Path(td) / "all.db"
        assert run_store.index_all(Path(td) / "runs", db2) == 1  # context 文件被跳过
        assert run_store.query(db2, rec_id="t3")
    print("run_store PASS")


if __name__ == "__main__":
    main()
