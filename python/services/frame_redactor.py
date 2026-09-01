"""关键帧隐私处理：优先 OCR，失败时保持原图并返回审计结果。"""
from pathlib import Path


def redact_frame(path: str | Path, output: str | Path | None = None) -> dict:
    src = Path(path); dst = Path(output) if output else src
    result = {"path": str(dst), "redacted": False, "matches": 0, "method": "none"}
    try:
        import pytesseract
        from PIL import Image, ImageDraw
        image = Image.open(src)
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        sensitive = ("password", "passwd", "token", "secret", "验证码", "密码", "身份证")
        draw = ImageDraw.Draw(image)
        for i, text in enumerate(data.get("text", [])):
            if any(k in text.lower() for k in sensitive):
                x, y, w, h = (data["left"][i], data["top"][i], data["width"][i], data["height"][i])
                draw.rectangle((x, y, x + w, y + h), fill="black")
                result["matches"] += 1
        if result["matches"]:
            image.save(dst)
            result.update(redacted=True, method="ocr")
    except Exception as exc:
        result["error"] = type(exc).__name__
    return result
