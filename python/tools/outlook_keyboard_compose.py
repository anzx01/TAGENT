"""Reliable New Outlook compose flow based on the successful 2026-09-09 recording."""
from __future__ import annotations
import argparse, ctypes, subprocess, time, os, glob
import sys
import json
import hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import win32gui
import win32process
import psutil
from services.visual_agent import _set_clipboard_text, _enter_physical_dpi
from paths import BASE_DIR


def message_directory(recipient, subject, root=BASE_DIR):
    identity = hashlib.sha256(json.dumps([recipient.strip().lower(), subject], ensure_ascii=False).encode()).hexdigest()[:24]
    return Path(root) / "runs" / "outlook_messages" / identity


def claim_send(directory, recipient, subject):
    directory.mkdir(parents=True, exist_ok=True)
    # Exclusive creation persists before injection and serializes concurrent attempts.
    with (directory / "send_attempt.json").open("x", encoding="utf8") as stream:
        json.dump({"to": recipient, "subject": subject, "status": "attempted", "at": time.time()}, stream)
        stream.flush()
        os.fsync(stream.fileno())


def capture_text(directory, stage):
    from PIL import ImageGrab
    import pytesseract
    from services.ocr_locator import _find_tesseract, best_lang
    assert_outlook()
    hwnd = win32gui.GetForegroundWindow()
    image = ImageGrab.grab().crop(win32gui.GetWindowRect(hwnd))
    directory.mkdir(parents=True, exist_ok=True)
    image.save(directory / (stage + ".png"))
    assert_outlook()
    pytesseract.pytesseract.tesseract_cmd = _find_tesseract()
    text = pytesseract.image_to_string(image, lang=best_lang(), config="--psm 11", timeout=15)
    (directory / (stage + ".txt")).write_text(text, encoding="utf8")
    return text


def fields_visible(text, recipient, subject, body):
    normalized = "".join(text.split()).casefold()
    return all(value.strip() and "".join(value.split()).casefold() in normalized for value in (recipient, subject, body))

u32 = ctypes.windll.user32
def focus_outlook():
    hwnd = 0
    for candidate in ("Outlook Host",):
        hwnd = u32.FindWindowW(candidate, None)
        if hwnd: break
    if not hwnd:
        paths = glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", "olk.exe"))
        if paths: subprocess.Popen([paths[0]])
        else: os.startfile("shell:AppsFolder\\Microsoft.OutlookForWindows_8wekyb3d8bbwe!Microsoft.OutlookforWindows")
        for _ in range(30):
            time.sleep(.5); hwnd = u32.FindWindowW("Outlook Host", None)
            if hwnd: break
    if not hwnd: raise SystemExit("Outlook window not found")
    u32.ShowWindow(hwnd, 3)
    u32.SetForegroundWindow(hwnd)
    time.sleep(.8)

def assert_outlook():
    hwnd = u32.GetForegroundWindow()
    title = ctypes.create_unicode_buffer(512)
    u32.GetWindowTextW(hwnd, title, 512)
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    if win32gui.GetClassName(hwnd) != "Outlook Host" or psutil.Process(pid).name().lower() != "olk.exe":
        raise SystemExit(f"foreground window is not Outlook: {title.value!r}")
def key(vk, down=True): u32.keybd_event(vk, 0, 0 if down else 2, 0)
def hotkey(*vks):
    assert_outlook()
    for vk in vks: key(vk)
    for vk in reversed(vks): key(vk, False)
def paste(text):
    assert_outlook()
    from services.visual_agent import _exec_type
    ok, reason = _exec_type({"text": text}, 1)
    if not ok:
        raise RuntimeError(reason)
def main():
    p=argparse.ArgumentParser(); p.add_argument('--to',required=True); p.add_argument('--body',default='您好'); p.add_argument('--send',action='store_true'); p.add_argument('--subject', default='TAGENT-hello'); p.add_argument('--attach', type=Path); p.add_argument('--verify-only',action='store_true'); a=p.parse_args()
    attachment = a.attach.resolve(strict=True) if a.attach else None
    if attachment and not attachment.is_file():
        raise SystemExit('Attachment must be a file')
    directory = message_directory(a.to, a.subject)
    from services.outlook_verify import verify_sent
    if a.verify_only:
        _enter_physical_dpi()
        if not verify_sent(a.to, a.subject, a.body, attachment.name if attachment else None, directory):
            raise SystemExit('Sent record not verified')
        print('Sent record verified; no message sent')
        return
    if (directory / 'send_attempt.json').exists():
        raise SystemExit('Prior send attempt exists; inspect its sent record before retrying.')
    _enter_physical_dpi()
    focus_outlook(); assert_outlook()
    hotkey(0x11,0x4e); time.sleep(3.8)
    assert_outlook()
    from PIL import ImageGrab
    from services.outlook_fields import locate_fields
    from services.visual_agent import _exec_click
    rect = win32gui.GetWindowRect(win32gui.GetForegroundWindow())
    image = ImageGrab.grab().crop(rect)
    directory.mkdir(parents=True, exist_ok=True)
    image.save(directory / 'empty.png')
    points = locate_fields(image, rect[:2])
    (directory / 'field_points.json').write_text(json.dumps({'rect': rect, 'points': points}), encoding='utf8')
    for field, value in (("recipient", a.to), ("subject", a.subject), ("body", a.body)):
        assert_outlook()
        x, y = points[field]
        ok, reason = _exec_click({"x": x, "y": y}, 1)
        if not ok:
            raise SystemExit(reason)
        paste(value)
        if field == "recipient":
            capture_text(directory, 'recipient_before_enter')
            hotkey(0x0d)
        capture_text(directory, field)
    time.sleep(.8)
    assert_outlook()
    before = capture_text(directory, 'before')
    if not fields_visible(before, a.to, a.subject, a.body):
        raise SystemExit('Compose content not verified; inspect before.png. Nothing sent.')
    if attachment:
        from services.outlook_attachment import attach_file
        attach_file(attachment)
        time.sleep(2)
        attached = capture_text(directory, 'attachment')
        if attachment.name.casefold() not in attached.casefold():
            raise SystemExit('Attachment not verified; nothing sent.')
    if a.send:
        claim_send(directory, a.to, a.subject)
        hotkey(0x11,0x0d)
    if a.send:
        time.sleep(2)
        if not verify_sent(a.to, a.subject, a.body, attachment.name if attachment else None, directory):
            raise SystemExit("Send attempted; result unknown. Do not automatically retry.")
        print('Sent record verified')
        return
    print('Draft prepared; not sent')
if __name__ == '__main__': main()
