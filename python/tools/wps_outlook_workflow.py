"""Checkpointed WPS Writer -> Outlook workflow.

Each stage is idempotent. A retry resumes from the last verified checkpoint and
never sends a message twice for the same recipient/subject pair.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import BASE_DIR


STAGES = ("WRITER_VERIFIED", "MAIL_DRAFT_VERIFIED", "MAIL_SENT_VERIFIED")


def write_state(path: Path, state: dict):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf8")
    tmp.replace(path)


def read_doc_text(path: Path) -> str:
    import zipfile
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(path) as archive:
        return "".join(ET.fromstring(archive.read("word/document.xml")).itertext()).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--to", required=True)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.run_id):
        raise SystemExit("Invalid run ID")

    root = BASE_DIR / "runs" / ("workflow_" + args.run_id)
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "workflow.json"
    if state_path.exists():
        if not args.resume:
            raise SystemExit("Existing run; use --resume to continue it")
        state = json.loads(state_path.read_text(encoding="utf8"))
    else:
        state = {"status": "running", "run_id": args.run_id, "recipient": args.to,
                 "subject": "TAGENT-WPS-" + args.run_id, "content": "您好", "stage": None,
                 "stages": [], "started": time.time()}
        write_state(state_path, state)

    output = root / "hello.docx"
    tools = Path(__file__).resolve().parent
    try:
        # Stage 1: deterministic Writer creation and XML verification.
        if "WRITER_VERIFIED" not in state["stages"]:
            subprocess.run([sys.executable, str(tools / "wps_com_verify.py"), "--output", str(output)], check=True)
            if read_doc_text(output) != "您好":
                raise RuntimeError("WPS document content verification failed")
            state["stages"].append("WRITER_VERIFIED")
            state["stage"] = "WRITER_VERIFIED"
            write_state(state_path, state)

        # Stage 2/3: Outlook handles OCR/UIA, attachment verification and sent record verification.
        if args.send:
            if "MAIL_SENT_VERIFIED" not in state["stages"]:
                command = [sys.executable, str(tools / "outlook_keyboard_compose.py"),
                           "--to", args.to, "--subject", state["subject"],
                           "--attach", str(output), "--send"]
                if "MAIL_DRAFT_VERIFIED" in state["stages"]:
                    command.append("--verify-only")
                subprocess.run(command, check=True)
                state["stages"].append("MAIL_SENT_VERIFIED")
                state["stage"] = "MAIL_SENT_VERIFIED"
                write_state(state_path, state)
        elif "MAIL_DRAFT_VERIFIED" not in state["stages"]:
            subprocess.run([sys.executable, str(tools / "outlook_keyboard_compose.py"),
                            "--to", args.to, "--subject", state["subject"],
                            "--attach", str(output)], check=True)
            state["stages"].append("MAIL_DRAFT_VERIFIED")
            state["stage"] = "MAIL_DRAFT_VERIFIED"
            write_state(state_path, state)

        state["status"] = "complete" if args.send else "draft_ready"
    except BaseException as exc:
        state["status"] = "failed"
        state["error"] = str(exc)
        write_state(state_path, state)
        raise
    finally:
        state["finished"] = time.time()
        write_state(state_path, state)
    print("WORKFLOW " + state["status"] + ": " + str(state_path), flush=True)


if __name__ == "__main__":
    main()
