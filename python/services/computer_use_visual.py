"""Screen-level computer-use helpers with OCR and post-action verification."""
from __future__ import annotations

import time
from pathlib import Path

from PIL import ImageGrab

from services.ocr_locator import _find_tesseract, best_lang


def screenshot(path: str | Path | None = None):
    image = ImageGrab.grab()
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
    return image


def ocr_words(image=None):
    """Return OCR word boxes in physical screen coordinates."""
    import pytesseract
    from pytesseract import Output
    image = image or screenshot()
    pytesseract.pytesseract.tesseract_cmd = _find_tesseract()
    data = pytesseract.image_to_data(image, lang=best_lang(), output_type=Output.DICT)
    return [
        {"text": text, "x": int(data["left"][i]), "y": int(data["top"][i]),
         "width": int(data["width"][i]), "height": int(data["height"][i]),
         "confidence": float(data["conf"][i])}
        for i, text in enumerate(data["text"]) if text.strip()
    ]


def find_text(text: str, image=None):
    """Find text by normalized substring and return its center coordinate."""
    target = "".join(text.casefold().split())
    words = ocr_words(image)
    for word in words:
        if target in "".join(word["text"].casefold().split()):
            return word["x"] + word["width"] // 2, word["y"] + word["height"] // 2
    return None


def wait_for_text(text: str, timeout: float = 5.0, interval: float = 0.4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        point = find_text(text)
        if point:
            return point
        time.sleep(interval)
    return None


def verify_text(text: str, timeout: float = 5.0) -> bool:
    return wait_for_text(text, timeout=timeout) is not None
