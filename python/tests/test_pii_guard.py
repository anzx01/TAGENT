"""pii_guard 纯函数单测（直接运行：python tests/test_pii_guard.py）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.pii_guard import scan_pii, mask_pii, PiiMatch


def kinds(text):
    return sorted(m.kind for m in scan_pii(text))


# 1. 邮箱
assert kinds("联系 test.user@gmail.com 处理") == ["email"]
assert kinds("联系test@example.com处理") == ["email"], "无空格也应命中"

# 2. 手机号
assert kinds("电话 13812345678 打给我") == ["phone"]
assert kinds("13812345678") == ["phone"]
assert kinds("号码 13812345678123 结尾") == [], "12 位连续数字不是手机号"
assert kinds("1381234567891234") == [], "16 位随机数字无 Luhn 不掩码"

# 3. 身份证（校验位）
assert kinds("证件号 110105199003074536") == ["id_card"]
assert kinds("110105199003074539") == [], "校验位错误不算身份证"

# 4. 银行卡（Luhn）
assert kinds("卡号 6222021234567890128") == ["card"]
assert kinds("622202 1234 5678 9012 8") == ["card"], "带空格也应命中"
assert kinds("123456789012345") == [], "随机数字不通过 Luhn"
assert kinds("1111111111111111") == [], "全相同数字排除"

# 5. 普通文本不误伤
assert kinds("金额 520 元，验证码 123456，总计 9999") == [], "短数字不掩码"
assert kinds("hello world this is a normal sentence") == []

# 6. 多重叠加（邮箱 + 手机）
r = mask_pii("a@b.com 和 13812345678 都在")
assert r.hit
assert "{{EMAIL}}" in r.text and "{{PHONE}}" in r.text
assert "a@b.com" not in r.text and "13812345678" not in r.text

# 7. 重叠消解：银行卡含手机号时不重复掩码
r2 = mask_pii("6222021234567890128 登记")
count = r2.text.count("{{")
assert count == 1, f"重叠应只掩码一次, got {count} -> {r2.text}"

# 8. 性命中明细
ms = scan_pii("邮箱 x@y.com，电话 13812345678")
assert all(isinstance(m, PiiMatch) for m in ms)
assert all(ms[i].start < ms[i + 1].start for i in range(len(ms) - 1)), "按位置排序"

print("pii_guard 全部断言通过")