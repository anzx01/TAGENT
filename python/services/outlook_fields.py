"""Locate compose fields from labels within one captured Outlook window."""
import math


def field_points(recipient, subject, size, origin=(0, 0)):
    """Map label centers to input points; reject incomplete or implausible forms."""
    if recipient is None or subject is None:
        raise ValueError("Compose labels missing")
    width, height = size
    rx, ry = recipient
    sx, sy = subject
    gap = sy - ry
    if not all(math.isfinite(v) for v in (*recipient, *subject)) or not 10 <= gap <= height / 3:
        raise ValueError("Invalid compose field layout")
    points = {"recipient": (rx + gap * 2, ry),
              "subject": (sx + gap, sy), "body": (sx + gap, sy + gap)}
    if any(not (0 <= x < width and 0 <= y < height) for x, y in points.values()):
        raise ValueError("Compose field outside window")
    return {key: (round(x + origin[0]), round(y + origin[1])) for key, (x, y) in points.items()}


def locate_fields(image, origin=(0, 0)):
    import pytesseract
    from services.ocr_locator import _find_tesseract, best_lang, _normalize
    pytesseract.pytesseract.tesseract_cmd = _find_tesseract()
    data = pytesseract.image_to_data(image, lang=best_lang(), config="--psm 11", output_type=pytesseract.Output.DICT, timeout=15)
    words = [(data["text"][i], data["left"][i], data["top"][i], data["width"][i], data["height"][i]) for i in range(len(data["text"])) if data["text"][i].strip()]
    def find(labels):
        for label in labels:
            for i in range(len(words)):
                for count in range(1, 5):
                    group = words[i:i+count]
                    if _normalize("".join(w[0] for w in group)) != _normalize(label):
                        continue
                    left = min(w[1] for w in group)
                    top = min(w[2] for w in group)
                    right = max(w[1]+w[3] for w in group)
                    bottom = max(w[2]+w[4] for w in group)
                    if bottom-top <= 2*max(w[4] for w in group):
                        return ((left+right)//2, (top+bottom)//2)
        return None
    recipient = find(("\u6536\u4ef6\u4eba", "To"))
    subject = find(("\u6dfb\u52a0\u4e3b\u9898", "\u4e3b\u9898", "Add a subject", "Subject"))
    if recipient is None or subject is None:
        import io
        from services.llm_heal import get_healer
        buffer = io.BytesIO()
        view = image.copy()
        view.thumbnail((1280, 1280))
        view.save(buffer, format="PNG")
        def locate(label):
            result = get_healer().plan_visual(
                f"Locate only the center of the {label} label in this compose window. "
                f"Return x,y in image pixels (width={view.width}, height={view.height}), action=identify. "
                "Return null if absent. Do not locate text inside the email body.", buffer.getvalue())
            if not isinstance(result, dict):
                return None
            x, y = result.get("x"), result.get("y")
            if type(x) not in (int, float) or type(y) not in (int, float):
                return None
            if not (0 <= x < view.width and 0 <= y < view.height):
                return None
            return x * image.width / view.width, y * image.height / view.height
        recipient = recipient or locate("recipient / To")
        subject = subject or locate("Add a subject / Subject")
    return field_points(recipient, subject, image.size, origin)
