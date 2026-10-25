"""
PII 掩码模块（移植自 skill-recorder common/sensitive.ts）
- 纯函数：扫描文本中的敏感信息（信用卡/银行卡、手机号、身份证号、邮箱）
- 录制落盘前调用，防止真实个人信息写入 recordings/
- 无 I/O、无依赖，可独立测试
"""
import re

__all__ = ["PiiMatch", "MaskResult", "scan_pii", "mask_pii", "PII_KINDS"]

# 掩码占位符（按种类）
PII_KINDS = ("card", "id_card", "phone", "email")
MASK_TEMPLATE: dict[str, str] = {
    "card": "{{CARD}}",
    "id_card": "{{ID_CARD}}",
    "phone": "{{PHONE}}",
    "email": "{{EMAIL}}",
}


class PiiMatch:
    """一处 PII 命中（半开区间 [start, end)，掩码文本）"""

    __slots__ = ("kind", "start", "end", "value", "masked")

    def __init__(self, kind: str, start: int, end: int, value: str):
        self.kind = kind
        self.start = start
        self.end = end
        self.value = value
        self.masked = MASK_TEMPLATE[kind]

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: "PiiMatch") -> bool:
        return self.start < other.end and other.start < self.end

    def __repr__(self) -> str:
        return f"PiiMatch({self.kind}, {self.start}:{self.end})"


class MaskResult:
    """掩码结果：替换后的文本 + 命中明细"""

    def __init__(self, text: str, matches: list[PiiMatch]):
        self.text = text
        self.matches = matches

    @property
    def hit(self) -> bool:
        return bool(self.matches)

    def __repr__(self) -> str:
        return f"MaskResult(hit={self.hit}, text={self.text!r})"


# ---------- 规则 ----------

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# 中国大陆手机号：1[3-9] 开头 11 位（或 3-4-4 连字符）；前后不得再跟数字（避免截断银行卡）
_PHONE_RE = re.compile(r"(?<![\d])(1[3-9]\d{9}|1[3-9]\d-\d{4}-\d{4})(?![\d])")
# 18 位身份证：17 位数字 + 末位数字/X
_ID_RE = re.compile(r"(?<![\d])(\d{17}[\dXx])(?![\d])")
# 银行卡：13~19 位连续数字（或含空格/连字符），用 Luhn 校验进一步过滤
_CARD_RAW_RE = re.compile(r"(?<![\d])(\d[\d\s\-]{11,22}\d)(?![\d])")
_MAX_PLAIN_NUMBER = 12  # 小于等于该长度的纯数字不做 PII（防误伤金额/验证码）


def _luhn_valid(digits: str) -> bool:
    """Luhn 校验（银行卡/信用卡）"""
    n = 0
    total = 0
    for ch in reversed(digits):
        d = ord(ch) - 48
        if n % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        n += 1
    return total % 10 == 0


def _card_candidates(text: str) -> list[tuple[int, int, str]]:
    """找出疑似银行卡的纯数字片段（已做 Luhn 校验）"""
    out: list[tuple[int, int, str]] = []
    for m in _CARD_RAW_RE.finditer(text):
        digits = re.sub(r"[\s\-]", "", m.group(0))
        if len(digits) < 13 or len(digits) > 19:
            continue
        if len(set(digits)) == 1:
            continue  # 全相同数字（如 1111...）不是卡号
        if _luhn_valid(digits):
            out.append((m.start(), m.end(), digits))
    return out


def _id_card_valid(seq: str) -> bool:
    """GB 11643-1999 身份证校验码验证（seq 为 17 位数字 + 校验位）"""
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    code = "10X98765432"
    total = sum((ord(ch) - 48) * w for ch, w in zip(seq[:17], weights))
    return code[total % 11] == seq[17].upper()


def _resolve_overlaps(matches: list[PiiMatch]) -> list[PiiMatch]:
    """区间重叠消解：保留更长者；同等长度按优先级 card > id_card > phone > email"""
    order = {k: i for i, k in enumerate(PII_KINDS)}
    matches = sorted(matches, key=lambda m: (-m.length, order[m.kind], m.start))
    kept: list[PiiMatch] = []
    for m in matches:
        if any(m.overlaps(k) for k in kept):
            continue
        kept.append(m)
    return sorted(kept, key=lambda m: m.start)


def scan_pii(text: str) -> list[PiiMatch]:
    """扫描文本中的 PII，返回去重且无重叠的命中列表（不修改文本）"""
    if not text or len(text) < 4:
        return []
    matches: list[PiiMatch] = []

    # 邮箱（优先级最高，先扫描避免被卡号/手机号误切）
    for m in _EMAIL_RE.finditer(text):
        matches.append(PiiMatch("email", m.start(), m.end(), m.group(0)))

    # 手机号
    for m in _PHONE_RE.finditer(text):
        matches.append(PiiMatch("phone", m.start(), m.end(), m.group(0)))

    # 身份证（带校验）
    for m in _ID_RE.finditer(text):
        if _id_card_valid(m.group(1)):
            matches.append(PiiMatch("id_card", m.start(), m.end(), m.group(1)))

    # 银行卡（Luhn）
    for start, end, digits in _card_candidates(text):
        matches.append(PiiMatch("card", start, end, digits))

    return _resolve_overlaps(matches)


def mask_pii(text: str) -> MaskResult:
    """掩码文本中的 PII，返回替换结果与命中明细"""
    matches = scan_pii(text)
    if not matches:
        return MaskResult(text, [])
    parts: list[str] = []
    cursor = 0
    for m in matches:
        parts.append(text[cursor:m.start])
        parts.append(m.masked)
        cursor = m.end
    parts.append(text[cursor:])
    return MaskResult("".join(parts), matches)


if __name__ == "__main__":
    # 自检示例
    samples = [
        "我的邮箱是 test.user@gmail.com 请查收",
        "手机号 13812345678 已登记",
        "证件号码 110105199003074536",
        "储蓄卡 6222021234567890123 已绑定",
        "金额 520 元，验证码 123456 无需掩码",
    ]
    for s in samples:
        r = mask_pii(s)
        print(f"{r.hit!s:5} {s!r} -> {r.text!r}")