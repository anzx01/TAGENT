"""Create and verify a WPS Writer document with deterministic local actions."""
from __future__ import annotations
import argparse, json, psutil, sys, time, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import win32gui, win32process
from capture import capture_screen
from paths import BASE_DIR
from player import _force_foreground
from services.visual_agent import _enter_physical_dpi, _exec_hotkey, _exec_type
from services.wps_save import save_writer_dialog

def writer_window():
    found=[]
    def collect(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd): return
        _, pid=win32process.GetWindowThreadProcessId(hwnd)
        try:
            if psutil.Process(pid).name().lower()=="wps.exe" and win32gui.GetClassName(hwnd)=="OpusApp": found.append(hwnd)
        except psutil.Error: pass
    win32gui.EnumWindows(collect,None)
    if not found: raise RuntimeError("WPS Writer window not found")
    return found[-1]

def verify_docx(path):
    with zipfile.ZipFile(path) as archive: root=ET.fromstring(archive.read("word/document.xml"))
    text="".join(root.itertext()).strip()
    if text != "您好": raise RuntimeError(f"WPS document content mismatch: {text!r}")

def main():
    p=argparse.ArgumentParser(); p.add_argument('--output',type=Path); a=p.parse_args()
    evidence=a.output.resolve().parent if a.output else BASE_DIR/"runs"/("writer_"+time.strftime("%Y%m%d_%H%M%S")); evidence.mkdir(parents=True,exist_ok=True)
    output=a.output.resolve() if a.output else evidence/"hello.docx"
    if output.exists(): raise RuntimeError("Output already exists")
    _enter_physical_dpi(); hwnd=writer_window()
    if not _force_foreground(hwnd): raise RuntimeError("WPS activation failed")
    _exec_hotkey({'keys':'esc'},1); _exec_hotkey({'keys':'esc'},1); _exec_hotkey({'keys':'ctrl+n'},1); time.sleep(1)
    ok, reason=_exec_type({'text':'您好'},1)
    if not ok: raise RuntimeError(reason)
    image=capture_screen()
    if image: image.save(evidence/"typed.png")
    _exec_hotkey({'keys':'ctrl+s'},1)
    deadline=time.monotonic()+15
    while time.monotonic()<deadline and not output.exists():
        save_writer_dialog(output); time.sleep(.25)
    if not output.is_file(): raise RuntimeError(f"Document missing: {evidence}")
    verify_docx(output)
    report={'status':'verified','output':str(output),'content':'您好','method':'deterministic_writer'}
    (evidence/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print('VERIFIED '+str(output),flush=True)
if __name__=='__main__': main()
