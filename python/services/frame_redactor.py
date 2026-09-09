"""
关键帧敏感信息遮挡（移植自 skill-recorder electron/sensitive/frame-redact.ts + frame-heuristics.ts）

管线：预处理(放大+灰度+对比度归一化) -> OCR 词框 -> 词拼接 -> 四层检测
      -> 重叠消解 -> 词框映射 -> 纯色不透明遮挡 -> 重编码

四层检测（统一中间表示 SensitiveMatch，按 rank 排序）：
  1. known values 字面匹配（文本层 pii_guard 已检出的原始值 cross-feed 到帧，rank 100）
  2. 凭证赋值右值（api_key=xxx / 密码：xxx，rank 90）
  3. 高熵裸 token（结构检测：OCR 读不准字形但结构不依赖读准，rank 80）
  4. PII 正则（复用 services.pii_guard.scan_pii：邮箱/手机号/身份证/银行卡，rank 40~60）

安全不变量：
  - 审计结果永不携带原始值，只有固定宽掩码 + 计数（mask_value: 前2+••••+后2）
  - fail-closed：OCR 异常 -> withheld=True，绝不把未检查像素当作已检查
  - 遮挡用纯色不透明块(17,17,17)而非模糊（不可逆更安全）
"""
import math
import re
from dataclasses import dataclass
from pathlib import Path

from services.pii_guard import scan_pii

__all__ = [
    "OcrWord", "SensitiveMatch", "shannon_entropy", "is_high_entropy_token",
    "join_words", "detect_sensitive", "resolve_overlaps", "boxes_for_matches",
    "redact_image", "redact_frame",
]

BOX_COLOR = (17, 17, 17)      # 纯色不透明遮挡（skill-recorder 同款）
BOX_PADDING = 4               # 词框外扩像素
PRESCALE = 3                  # OCR 预处理放大倍数
PRESCALE_MAX_WIDTH = 3600     # 预处理宽度上限（px）
_MIN_REDACT_LEN = 4           # known value 最短长度（更短的字面匹配误伤率高）


@dataclass
class OcrWord:
    """一个 OCR 词及其在源图上的像素框"""
    text: str
    x0: int
    y0: int
    x1: int
    y1: int


@dataclass
class SensitiveMatch:
    """检测命中（joined 文本上的半开区间 [start, end)）"""
    kind: str          # known / credential / secret / card / id_card / phone / email
    severity: str      # high / medium
    rank: int          # 越大越优先（重叠消解用）
    start: int
    end: int
    value: str         # 原始值（只存在于内存，审计输出前必须过 mask_value）

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: "SensitiveMatch") -> bool:
        return self.start < other.end and other.start < self.end


# ---------- 纯检测逻辑（无 I/O，可独立测试） ----------

_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_CRED_RE = re.compile(
    r"(api[_\-]?key|access[_\-]?key|secret|token|password|passwd|bearer|"
    r"credential|private[_\-]?key|密码|口令|令牌|密钥)[\"'` ]?\s*[:=：]\s*[\"'`]?"
    r"(\S{6,})",
    re.IGNORECASE,
)


def shannon_entropy(s: str) -> float:
    """字符级 Shannon 熵（bits/char）"""
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def is_high_entropy_token(s: str) -> bool:
    """高熵裸 token 判定（recall 优先的结构检测，不枚举厂商）：
    长且密集、高熵、混合字符类 —— 典型 API key/JWT 片段的形状"""
    if len(s) < 20 or "://" in s or _HEX_RE.fullmatch(s):
        return False
    alnum = sum(ch.isalnum() for ch in s)
    if alnum / len(s) < 0.75:
        return False
    has_digit = any(ch.isdigit() for ch in s)
    has_alpha = any(ch.isalpha() for ch in s)
    classes = sum([any(ch.islower() for ch in s),
                   any(ch.isupper() for ch in s),
                   has_digit,
                   any(not ch.isalnum() for ch in s)])
    return has_digit and has_alpha and classes >= 2 and shannon_entropy(s) >= 3.2


def join_words(words: list[OcrWord]) -> tuple[str, list[tuple[int, int, OcrWord]]]:
    """把 OCR 词拼接成一段文本，记录每词的字符区间。

    "@" 两侧的空格要粘合：Tesseract 常把 email 拆成
    ``jordan.blake @ example.com`` 三个词，不粘合则正则匹配不到。
    """
    parts: list[str] = []
    spans: list[tuple[int, int, OcrWord]] = []
    pos = 0
    for w in words:
        text = w.text
        if not text:
            continue
        glue = parts and (parts[-1].endswith("@") or text.startswith("@"))
        if parts and not glue:
            parts.append(" ")
            pos += 1
        parts.append(text)
        spans.append((pos, pos + len(text), w))
        pos += len(text)
    return "".join(parts), spans


def detect_sensitive(joined: str, known_values=()) -> list[SensitiveMatch]:
    """四层检测，返回未去重叠的全部命中"""
    matches: list[SensitiveMatch] = []

    # 1) known values（文本层已检出的原始值 cross-feed 到帧）
    for value in known_values:
        value = str(value)
        if len(value) < _MIN_REDACT_LEN:
            continue
        start = 0
        while True:
            idx = joined.find(value, start)
            if idx < 0:
                break
            matches.append(SensitiveMatch("known", "high", 100, idx, idx + len(value), value))
            start = idx + len(value)

    # 2) 凭证赋值右值
    for m in _CRED_RE.finditer(joined):
        g = m.group(2)
        matches.append(SensitiveMatch("credential", "high", 90, m.start(2), m.end(2), g))

    # 3) 高熵裸 token（按空白切词，与词框天然对齐）
    for m in re.finditer(r"\S+", joined):
        tok = m.group(0)
        if is_high_entropy_token(tok):
            matches.append(SensitiveMatch("secret", "high", 80, m.start(), m.end(), tok))

    # 4) PII 正则（复用 pii_guard：邮箱/手机号/身份证/银行卡，带校验）
    pii_rank = {"card": 60, "id_card": 60, "phone": 45, "email": 40}
    pii_sev = {"card": "high", "id_card": "high", "phone": "medium", "email": "medium"}
    for p in scan_pii(joined):
        matches.append(SensitiveMatch(
            p.kind, pii_sev.get(p.kind, "medium"), pii_rank.get(p.kind, 40),
            p.start, p.end, p.value))

    return matches


def resolve_overlaps(matches: list[SensitiveMatch]) -> list[SensitiveMatch]:
    """区间重叠消解：rank 高者优先，其次更长；保序输出"""
    ordered = sorted(matches, key=lambda m: (-m.rank, -m.length, m.start))
    kept: list[SensitiveMatch] = []
    for m in ordered:
        if any(m.overlaps(k) for k in kept):
            continue
        kept.append(m)
    return sorted(kept, key=lambda m: m.start)


def boxes_for_matches(matches: list[SensitiveMatch],
                      spans: list[tuple[int, int, OcrWord]],
                      img_size: tuple[int, int],
                      padding: int = BOX_PADDING) -> list[tuple[int, int, int, int]]:
    """命中区间与词字符区间相交 -> 词像素框（外扩 padding 并 clamp 到图内）"""
    w, h = img_size
    boxes: list[tuple[int, int, int, int]] = []
    for m in matches:
        for s_start, s_end, word in spans:
            if s_start < m.end and m.start < s_end:
                x0 = max(0, word.x0 - padding)
                y0 = max(0, word.y0 - padding)
                x1 = min(w, word.x1 + padding)
                y1 = min(h, word.y1 + padding)
                boxes.append((x0, y0, x1, y1))
    # 去重（同一词被多个命中映射时只画一次）
    return sorted(set(boxes))


def mask_value(value: str) -> str:
    """固定宽掩码：<=6 全掩；否则 前2+••••+后2（防长度泄漏）"""
    if len(value) <= 6:
        return "••••"
    return value[:2] + "••••" + value[-2:]


# ---------- OCR 与绘制 ----------

def _preprocess(img, scale: float):
    """放大 + 灰度 + 对比度归一化（小字 OCR 召回的关键）"""
    from PIL import Image, ImageOps
    w, h = img.size
    if int(w * scale) > PRESCALE_MAX_WIDTH:
        scale = PRESCALE_MAX_WIDTH / w
    if scale > 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return ImageOps.autocontrast(ImageOps.grayscale(img)), scale


def _ocr_words(img, scale: float) -> list[OcrWord]:
    """对预处理图跑 OCR，词框坐标除以 scale 映射回源图像素空间"""
    import pytesseract
    # 复用 ocr_locator 的引擎探测（默认安装路径优先，PATH 兜底），
    # 未装/未进 PATH 时抛异常 -> 上层 fail-closed
    try:
        from services.ocr_locator import _find_tesseract, best_lang
        cmd = _find_tesseract()
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        lang = best_lang()  # chi_sim+eng：中文凭证标签(密码：xxx)/中文 PII 才能命中
    except Exception:
        lang = "eng"
    data = pytesseract.image_to_data(img, lang=lang, config="--psm 6",
                                     output_type=pytesseract.Output.DICT)
    words: list[OcrWord] = []
    for i, text in enumerate(data.get("text", [])):
        text = (text or "").strip()
        if not text:
            continue
        # 注意：不做置信度过滤。高熵敏感串（token/email）恰是 OCR 最没把握的
        # 输入，按 conf 过滤会在隐私扫描里系统性损失召回（recall-first）。
        x0 = int(data["left"][i] / scale)
        y0 = int(data["top"][i] / scale)
        x1 = int((data["left"][i] + data["width"][i]) / scale)
        y1 = int((data["top"][i] + data["height"][i]) / scale)
        words.append(OcrWord(text, x0, y0, x1, y1))
    return words


def redact_image(img, known_values=(), ocr=None, on_error: str = "keep"):
    """对 PIL Image 做敏感遮挡，返回 (image, audit)。

    - ocr: 可注入的 OCR 函数(preprocessed_img, scale) -> list[OcrWord]（测试用）
    - on_error: "keep" 保留原图并标记 withheld（本地优先）；
                "drop" 整帧替换为纯色（导出/外发场景的 fail-closed）
    - 审计 dict 不含原始值
    """
    audit = {"redacted": False, "boxes": 0, "matches": [], "method": "none"}
    try:
        pre, scale = _preprocess(img, PRESCALE)
        words = ocr(pre, scale) if ocr else _ocr_words(pre, scale)
        if not words:
            return img, audit
        joined, spans = join_words(words)
        matches = resolve_overlaps(detect_sensitive(joined, known_values))
        boxes = boxes_for_matches(matches, spans, img.size)
        if boxes:
            from PIL import ImageDraw
            canvas = img.copy()
            draw = ImageDraw.Draw(canvas)
            for x0, y0, x1, y1 in boxes:
                draw.rectangle((x0, y0, x1, y1), fill=BOX_COLOR)
            audit.update(
                redacted=True, boxes=len(boxes), method="ocr",
                matches=[{"kind": m.kind, "severity": m.severity,
                          "value": mask_value(m.value)} for m in matches],
            )
            return canvas, audit
        return img, audit
    except Exception as exc:  # fail-closed：OCR 失败 = 无法证明该帧干净
        audit.update(method="error", error=type(exc).__name__, withheld=True)
        if on_error == "drop":
            from PIL import Image
            return Image.new("RGB", img.size, BOX_COLOR), audit
        return img, audit


def redact_frame(path: str | Path, output: str | Path | None = None,
                 known_values=(), jpeg_quality: int = 85) -> dict:
    """对磁盘图片做敏感遮挡（无命中也重编码，保证输出必然经过本层）"""
    from PIL import Image
    src = Path(path)
    dst = Path(output) if output else src
    result = {"path": str(dst)}
    with Image.open(src) as fp:
        img = fp.convert("RGB")
    img, audit = redact_image(img, known_values=known_values)
    img.save(dst, "JPEG", quality=jpeg_quality)
    result.update(audit)
    return result


if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:]:
        print(arg, redact_frame(arg))
