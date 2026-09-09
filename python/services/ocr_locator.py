"""
OCR 兜底定位模块（阶段1.1）
- 使用 Tesseract OCR 识别屏幕文本
- 当 UIA 和坐标定位都失败时，通过文本匹配定位元素
- 返回匹配文本中心的屏幕坐标
"""
import ctypes
import shutil
import time
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


_LANG = None


def best_lang() -> str:
    """返回 OCR 语言组合：装了 chi_sim 则 chi_sim+eng（中文 UI 必需），否则 eng 降级。
    探测结果缓存；未装中文包时 chi_sim+eng 会让 tesseract 直接抛错，不能硬编码。"""
    global _LANG
    if _LANG:
        return _LANG
    exe = _find_tesseract()
    if exe:
        chi = Path(exe).parent / "tessdata" / "chi_sim.traineddata"
        _LANG = "chi_sim+eng" if chi.exists() else "eng"
    else:
        _LANG = "eng"
    return _LANG


import re as _re
_PUNCT_RE = _re.compile(r"[^\w]+", _re.UNICODE)


def _normalize(s):
    """文本比较归一化：NFKC(全角->半角) + 去空白与标点噪声 + 小写。

    OCR 常把菜单图标读成前导引号（'”新建'）、竖线等符号，
    剥掉非文字字符后短目标（如两字菜单项）才能精确命中。"""
    import unicodedata
    return _PUNCT_RE.sub("", unicodedata.normalize("NFKC", s)).lower()


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
        # --psm 6（单块文本）：截屏 UI 文本密集，自动分割(psm 3)对稀疏/混排行
        # 召回差（实测中文行被读成 'k='），psm 6 稳定（skill-recorder 同款选择）
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT,
                                         lang=best_lang(), config="--psm 6")
        target_norm = _normalize(target_text)
        if not target_norm:
            return None
        n = len(data["text"])

        # ---- 1. 行匹配优先（按 par/block/line 聚合，图标文字常被拆成多词）----
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
            entry["words"].append((data["left"][i], word))
            entry["width"] = max(entry["width"], data["left"][i] + data["width"][i] - entry["left"])
            entry["height"] = max(entry["height"], data["height"][i])

        # 中文菜单/图标文字常被 OCR 拆成 y 错位的相邻"行"（如"新"(98,342) +
        # "建"(142,335)），按 y 接近度把同一视觉行合并，否则行匹配必失败；
        # 词按 x 坐标排序拼接，避免错位行导致词序颠倒
        merged = []
        for entry in sorted(lines.values(), key=lambda e: (e["top"], e["left"])):
            for m in merged:
                same_row = abs((entry["top"] + entry["height"] / 2)
                               - (m["top"] + m["height"] / 2)) \
                    <= 0.6 * max(entry["height"], m["height"], 1)
                adjacent = entry["left"] <= m["left"] + m["width"] + 40
                if same_row and adjacent:
                    m["words"].extend(entry["words"])
                    m["width"] = max(m["width"],
                                     entry["left"] + entry["width"] - m["left"])
                    m["height"] = max(m["height"], entry["height"])
                    break
            else:
                merged.append(dict(entry))

        for entry in merged:
            line_norm = _normalize(" ".join(w for _, w in sorted(entry["words"])))
            if not line_norm:
                continue
            # 完整行匹配（最高优先）
            if line_norm == target_norm:
                x = entry["left"] + entry["width"] // 2
                y = entry["top"] + entry["height"] // 2
                log.info("OCR 行精确匹配 '%s' -> (%d, %d)", line_norm, x, y)
                return (x, y)
            # 长目标包含匹配（target >= 4 字符才允许子串，避免单字误命中）
            if len(target_norm) >= 4 and target_norm in line_norm:
                x = entry["left"] + entry["width"] // 2
                y = entry["top"] + entry["height"] // 2
                log.info("OCR 行匹配 '%s' -> (%d, %d)", line_norm, x, y)
                return (x, y)

        # ---- 2. 词匹配（完整词优先，长词包含兜底）----
        for i in range(n):
            word = data["text"][i].strip()
            if not word:
                continue
            word_norm = _normalize(word)
            if not word_norm:
                continue
            # 完整词匹配
            if word_norm == target_norm:
                x = data["left"][i] + data["width"][i] // 2
                y = data["top"][i] + data["height"][i] // 2
                log.info("OCR 词精确匹配 '%s' -> (%d, %d)", word, x, y)
                return (x, y)
            # 目标被 OCR 拆成词内片段时包含匹配（仅限长目标，避免 'x'->'.txt' 误命中）
            if len(target_norm) >= 4 and len(word_norm) >= 2 and target_norm in word_norm:
                x = data["left"][i] + data["width"][i] // 2
                y = data["top"][i] + data["height"][i] // 2
                log.info("OCR 词匹配 '%s' -> (%d, %d)", word, x, y)
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
            from PIL import Image
            img = None
            # 菜单/弹窗刚出现时 DWM 合成中，BitBlt 会偶发失败 —— 重试
            for _ in range(3):
                try:
                    with mss.mss() as sct:
                        shot = sct.grab(sct.monitors[1])
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    break
                except Exception:
                    time.sleep(0.25)
        if img is None:
            return None
        return find_text_on_screen(img, target_text)
    except Exception as e:
        log.warning("截图+OCR 失败: %s", e)
        return None
