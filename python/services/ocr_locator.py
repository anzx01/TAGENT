"""
OCR 兜底定位模块（阶段1.1）
- 使用 Tesseract OCR 识别屏幕文本
- 当 UIA 和坐标定位都失败时，通过文本匹配定位元素
- 返回匹配文本中心的屏幕坐标
"""
import ctypes
import shutil
from pathlib import Path

from logger import get_logger

log = get_logger("ocr")

# Tesseract 可执行文件路径
_TESSERACT_PATH = None


def _find_tesseract():
    global _TESSERACT_PATH
    if _TESSERACT_PATH:
        return _TESSERACT_PATH
    # 常见安装路径
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for p in candidates:
        if Path(p).exists():
            _TESSERACT_PATH = p
            return p
    # PATH 中查找
    found = shutil.which("tesseract")
    if found:
        _TESSERACT_PATH = found
        return found
    return None


def is_available() -> bool:
    """OCR 是否可用"""
    return _find_tesseract() is not None


def find_text_on_screen(image, target_text):
    """在截图上查找文本，返回匹配位置的中心坐标 (x, y) 或 None。
    image: PIL.Image 对象（截图）
    target_text: 要查找的文本
    """
    if not is_available():
        log.debug("Tesseract 未安装，OCR 跳过")
        return None
    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = _find_tesseract()
        # 获取图片尺寸用于坐标换算
        w, h = image.size
        # OCR 识别，返回带位置的数据
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT,
                                         lang="chi_sim+eng")
        target_lower = target_text.lower().strip()
        n = len(data["text"])
        for i in range(n):
            word = data["text"][i].strip()
            if not word:
                continue
            # 精确匹配或包含匹配
            if target_lower in word.lower() or word.lower() in target_lower:
                x = data["left"][i] + data["width"][i] // 2
                y = data["top"][i] + data["height"][i] // 2
                log.info("OCR 命中 '%s' -> (%d, %d)", word, x, y)
                return (x, y)
        # 尝试按行匹配（多个词组合）
        lines = {}
        for i in range(n):
            word = data["text"][i].strip()
            if not word:
                continue
            block = data["block_num"][i]
            line = data["line_num"][i]
            par = data["par_num"][i]
            key = (par, block, line)
            if key not in lines:
                lines[key] = {"words": [], "left": data["left"][i],
                              "top": data["top"][i], "width": 0, "height": data["height"][i]}
            entry = lines[key]
            entry["words"].append(word)
            entry["width"] = max(entry["width"], data["left"][i] + data["width"][i] - entry["left"])
            entry["height"] = max(entry["height"], data["height"][i])
        for key, entry in lines.items():
            line_text = " ".join(entry["words"])
            if target_lower in line_text.lower():
                x = entry["left"] + entry["width"] // 2
                y = entry["top"] + entry["height"] // 2
                log.info("OCR 行匹配 '%s' -> (%d, %d)", line_text, x, y)
                return (x, y)
        log.debug("OCR 未找到 '%s'", target_text)
        return None
    except Exception as e:
        log.warning("OCR 识别失败: %s", e)
        return None


def capture_and_find(target_text, hwnd=None):
    """截图并查找文本位置。hwnd 指定窗口截图，None 则全屏截图。"""
    try:
        if hwnd:
            from capture import capture_window
            img = capture_window(hwnd)
        else:
            import mss
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                import mss.tools
                shot = sct.grab(monitor)
                from PIL import Image
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        if img is None:
            return None
        return find_text_on_screen(img, target_text)
    except Exception as e:
        log.warning("截图+OCR 失败: %s", e)
        return None
