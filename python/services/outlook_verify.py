"""Verify a particular sent message, rather than a folder label or draft."""
import re
import time
import json


def sent_detail_matches(text, recipient, subject, body, attachment=None):
    normalized = "".join(text.split()).casefold()
    values = [recipient, subject, body] + ([attachment] if attachment else [])
    if not all(v and "".join(v.split()).casefold() in normalized for v in values):
        return False
    if any(v in text for v in ("\u4fdd\u5b58\u8349\u7a3f", "\u6dfb\u52a0\u4e3b\u9898", "Saved draft", "Add a subject")):
        return False
    return (any(v in text for v in ("\u6536\u4ef6\u4eba", "To:"))
            and any(v in text for v in ("\u56de\u590d", "\u7b54\u590d", "Reply"))
            and any(v in text for v in ("\u8f6c\u53d1", "Forward"))
            and bool(re.search(r"\d{1,2}:\d{2}", text)))


def verify_sent(recipient, subject, body, attachment, directory):
    import win32gui
    import win32process
    import psutil
    from PIL import ImageGrab
    import pytesseract
    from player import _force_foreground
    from services.ocr_locator import _find_tesseract, best_lang
    from services.outlook_attachment import visual_click
    windows = []
    def collect(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd).endswith("Outlook"):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if psutil.Process(pid).name().lower() == "olk.exe":
                windows.append(hwnd)
    win32gui.EnumWindows(collect, None)
    if not windows:
        raise RuntimeError("Main Outlook mailbox window not found")
    hwnd = windows[0]
    win32gui.ShowWindow(hwnd, 3)
    _force_foreground(hwnd)
    from services.visual_agent import _exec_hotkey
    _exec_hotkey({"keys": "esc"}, 1)
    visual_click(hwnd, "the Sent Items / \u5df2\u53d1\u9001\u90ae\u4ef6 folder in the left navigation")
    time.sleep(2)
    visual_click(hwnd, "the message row with exact subject " + subject, double=True)
    time.sleep(2)
    directory.mkdir(parents=True, exist_ok=True)
    pytesseract.pytesseract.tesseract_cmd = _find_tesseract()
    for attempt in range(4):
        current = win32gui.GetForegroundWindow()
        _, pid = win32process.GetWindowThreadProcessId(current)
        if psutil.Process(pid).name().lower() != "olk.exe":
            raise RuntimeError("Focus changed during verification")
        image = ImageGrab.grab().crop(win32gui.GetWindowRect(current))
        image.save(directory / "sent_detail.png")
        text = pytesseract.image_to_string(image, lang=best_lang(), config="--psm 11", timeout=15)
        (directory / "sent_detail.txt").write_text(text, encoding="utf8")
        if sent_detail_matches(text, recipient, subject, body, attachment):
            (directory / "verified.json").write_text(json.dumps({"to": recipient, "subject": subject, "attachment": attachment, "status": "sent_record_verified"}), encoding="utf8")
            return True
        time.sleep(1)
    return False
