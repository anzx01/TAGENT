import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import wps_outlook_workflow as workflow


class WorkflowStateTests(unittest.TestCase):
    def test_atomic_state_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.json"
            workflow.write_state(path, {"stage": "WRITER_VERIFIED"})
            self.assertEqual(json.loads(path.read_text())["stage"], "WRITER_VERIFIED")
            self.assertFalse((Path(tmp) / "workflow.tmp").exists())

    def test_stage_order_is_explicit(self):
        self.assertEqual(workflow.STAGES, ("WRITER_VERIFIED", "MAIL_DRAFT_VERIFIED", "MAIL_SENT_VERIFIED"))


if __name__ == "__main__":
    unittest.main()
