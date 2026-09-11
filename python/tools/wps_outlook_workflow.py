"""Run Writer creation, content verification, attachment send and sent verification."""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import BASE_DIR


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--to', required=True)
    parser.add_argument('--send', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', args.run_id):
        raise ValueError('Invalid run ID')
    evidence = BASE_DIR / 'runs' / ('workflow_' + args.run_id)
    evidence.mkdir(parents=True, exist_ok=True)
    report = evidence / 'workflow.json'
    if report.exists():
        raise RuntimeError('Existing run; inspect report before retrying')
    state = {'status':'running', 'started':time.time(), 'recipient':args.to, 'stages':[]}
    with report.open('x', encoding='utf8') as stream:
        json.dump(state, stream)
    tools = Path(__file__).resolve().parent
    try:
        import win32gui
        from player import _force_foreground
        from services.visual_agent import _enter_physical_dpi, _exec_hotkey
        _enter_physical_dpi()
        hwnd = win32gui.FindWindow('OpusApp', None)
        if not hwnd:
            raise RuntimeError('Open WPS Writer before running this workflow')
        win32gui.ShowWindow(hwnd, 3)
        _force_foreground(hwnd)
        if win32gui.GetForegroundWindow() != hwnd:
            raise RuntimeError('WPS activation failed')
        _exec_hotkey({'keys':'esc'},1)
        _exec_hotkey({'keys':'esc'},1)
        _exec_hotkey({'keys':'ctrl+n'},1)
        time.sleep(1)
        output = evidence / 'hello.docx'
        subprocess.run([sys.executable, str(tools/'wps_visual_verify.py'), '--output', str(output)], check=True)
        state['stages'].append('writer_content_verified')
        command = [sys.executable, str(tools/'outlook_keyboard_compose.py'), '--to', args.to,
                   '--subject', 'TAGENT-WPS-'+args.run_id, '--attach', str(output)]
        if args.send:
            command.append('--send')
        subprocess.run(command, check=True)
        state['stages'].append('sent_record_verified' if args.send else 'attachment_draft_verified')
        state['status'] = 'complete' if args.send else 'draft_ready'
    except BaseException as exc:
        state['status'] = 'failed'
        state['error'] = str(exc)
        raise
    finally:
        state['finished'] = time.time()
        report.write_text(json.dumps(state, indent=2), encoding='utf8')
    print('WORKFLOW ' + state['status'] + ': ' + str(report), flush=True)

if __name__ == '__main__': main()
