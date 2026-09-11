"""Run the existing visual agent for Writer and independently verify DOCX text."""
import json
import argparse
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.visual_agent import VisualAgent
from capture import capture_screen
from paths import BASE_DIR
from services.wps_save import save_writer_dialog


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    evidence = args.output.resolve().parent if args.output else BASE_DIR / "runs" / ("writer_" + time.strftime("%Y%m%d_%H%M%S"))
    evidence.mkdir(parents=True, exist_ok=True)
    output = args.output.resolve() if args.output else evidence / "hello.docx"
    if output.exists():
        raise RuntimeError('Output already exists; choose a fresh output path')
    def update(state):
        (evidence / "report.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf8")
        image = capture_screen()
        if image is not None:
            image.save(evidence / (str(len(state.get("steps", []))) + ".png"))
        print(state.get("status"), len(state.get("steps", [])), flush=True)
    agent = VisualAgent()
    save_requested = False
    def local_step():
        nonlocal save_requested
        if save_writer_dialog(output):
            return True
        steps = agent.state.get('steps', [])
        if not save_requested and steps and steps[-1].get('action') == 'type':
            import win32gui
            import win32process
            import psutil
            from services.visual_agent import _exec_hotkey
            _, pid = win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())
            if psutil.Process(pid).name().lower() == 'wps.exe':
                ok, reason = _exec_hotkey({'keys': 'ctrl+s'}, 1)
                if not ok:
                    raise RuntimeError(reason)
                save_requested = True
                return True
        return False
    agent.local_step = local_step
    agent.start("在已打开的WPS中新建空白文字文档（不是表格），输入且只输入：您好。另存为本地DOCX文件："
                + str(output) + "。只操作WPS及其保存对话框。不要操作邮件。保存完成后结束。", max_steps=20, on_update=update)
    while agent.running:
        time.sleep(0.5)
    if not output.is_file():
        raise SystemExit("Document missing; evidence: " + str(evidence))
    with zipfile.ZipFile(output) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    text = "".join(root.itertext())
    if text.strip() != "\u60a8\u597d":
        raise SystemExit("Document content mismatch; evidence: " + str(evidence))
    print("VERIFIED " + str(output), flush=True)

if __name__ == "__main__": main()
