"""Create and verify a WPS Writer document using screen-level input."""
from __future__ import annotations
import ctypes, glob, os, subprocess, time
from pathlib import Path

u32 = ctypes.windll.user32
def hotkey(*vks):
    for vk in vks: u32.keybd_event(vk,0,0,0)
    for vk in reversed(vks): u32.keybd_event(vk,0,2,0)
def paste(text):
    subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value " + repr(text)], check=True)
    hotkey(0x11,0x56)
def main():
    out = Path(os.environ.get("TAGENT_DATA_DIR", "recordings")) / "tagent_hello.docx"
    candidates = glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Kingsoft", "WPS Office", "*", "office6", "wps.exe"))
    if not candidates: raise SystemExit("WPS Writer executable not found")
    subprocess.Popen([candidates[-1]]); time.sleep(4)
    hotkey(0x11,0x4e); time.sleep(2); paste("您好"); time.sleep(.5)
    hotkey(0x11,0x53); time.sleep(1); paste(str(out)); hotkey(0x0d); time.sleep(3)
    if not out.exists(): raise SystemExit(f"WPS document was not saved: {out}")
    print(f"wps_writer PASS: {out}")
if __name__ == "__main__": main()
