"""OCR and model assisted compose field location with bounded fallback."""
import math


def field_points(recipient, subject, size, origin=(0, 0)):
    if recipient is None or subject is None:
        raise ValueError("Compose labels missing")
    width, height = size
    rx, ry = recipient
    sx, sy = subject
    gap = sy - ry
    if not all(math.isfinite(float(v)) for v in (*recipient, *subject)) or not 10 <= gap <= height / 3:
        raise ValueError("Invalid compose field layout")
    points = {"recipient": (rx + gap * 2, ry), "subject": (sx + gap, sy), "body": (sx + gap, sy + gap)}
    if any(not (0 <= x < width and 0 <= y < height) for x, y in points.values()):
        raise ValueError("Compose field outside window")
    return {key: (round(x + origin[0]), round(y + origin[1])) for key, (x, y) in points.items()}


def locate_fields(image, origin=(0, 0)):
    import io
    import pytesseract
    from services.ocr_locator import _find_tesseract, best_lang, _normalize
    pytesseract.pytesseract.tesseract_cmd = _find_tesseract()
    data = pytesseract.image_to_data(image, lang=best_lang(), config="--psm 11", output_type=pytesseract.Output.DICT, timeout=15)
    words = [(data["text"][i], data["left"][i], data["top"][i], data["width"][i], data["height"][i]) for i in range(len(data["text"])) if data["text"][i].strip()]

    def find(labels):
        for label in labels:
            target = _normalize(label)
            for i in range(len(words)):
                for count in range(1, 5):
                    group = words[i:i + count]
                    if _normalize("".join(w[0] for w in group)) != target:
                        continue
                    left, top = min(w[1] for w in group), min(w[2] for w in group)
                    right = max(w[1] + w[3] for w in group)
                    bottom = max(w[2] + w[4] for w in group)
                    return ((left + right) // 2, (top + bottom) // 2)
        return None

    recipient = find(("收件人", "To"))
    subject = find(("添加主题", "主题", "Add a subject", "Subject"))
    try:
        return field_points(recipient, subject, image.size, origin)
    except ValueError:
        # Final bounded fallback for full-screen New Outlook compose layout.
        width, height = image.size
        if width < 3000 or height < 1800:
            raise
        return field_points((int(width * .27), int(height * .19)), (int(width * .27), int(height * .235)), image.size, origin)
