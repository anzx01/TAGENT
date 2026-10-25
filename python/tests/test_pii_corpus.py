"""PII 脱敏语料单测:mustRedact(召回)/mustKeep(精度)双指标
(移植 skill-recorder evals/sensitive 的双指标思想,语料离线确定性)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.pii_guard import mask_pii

# 必须脱敏(召回):任何一条漏掉即失败
MUST_REDACT = [
    "zhang.san@example.com",       # email
    "13812345678",                 # 手机号
    "6222021234567890128",         # 银行卡(Luhn 通过)
    "110101199003078515",          # 身份证
    "联系 139-1234-5678 勿外传",   # 带分隔符手机号
]
# 不应误伤(精度):正常业务文本保持原样
MUST_KEEP = [
    "订单号 20261025_083000 已提交",
    "共 123456 个结果",
    "工号 A1234567",
]


def main():
    missed = []
    for text in MUST_REDACT:
        result = mask_pii(text)
        if not getattr(result, "hit", False):
            missed.append(text)
    assert not missed, f"漏脱敏: {missed}"

    wrong = []
    for text in MUST_KEEP:
        result = mask_pii(text)
        if getattr(result, "hit", False):
            wrong.append(text)
    assert not wrong, f"误伤: {wrong}"
    print("pii_corpus PASS")


if __name__ == "__main__":
    main()
