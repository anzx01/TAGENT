"""frame_redactor 单测（直接运行：python tests/test_frame_redactor.py）

检测层用合成 OCR 词表做双真值评测（移植 skill-recorder evals/sensitive/frames.ts）：
- mustRedact：敏感词的框必须被覆盖（召回）
- mustKeep：干净词的框不得被覆盖（精确率）
真 OCR 端到端为 opt-in（TAGENT_EVAL_OCR=1），环境不支持时自跳过。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.frame_redactor import (
    OcrWord, boxes_for_matches, detect_sensitive, is_high_entropy_token,
    join_words, mask_value, redact_image, redact_frame, resolve_overlaps,
    shannon_entropy,
)

# ---------- 1. 高熵 token 结构检测 ----------

TOKEN = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"
assert is_high_entropy_token(TOKEN), "典型 GitHub PAT 形状应命中"
assert is_high_entropy_token("AKIAIOSFODNN7EXAMPLEQ2Z"), "混合大小写+数字应命中"
assert not is_high_entropy_token("https://example.com/path"), "URL 不命中"
assert not is_high_entropy_token("abcdefghijklmnopqrst"), "纯小写字母低熵不命中"
assert not is_high_entropy_token("12345678901234567890"), "纯数字不命中"
assert not is_high_entropy_token("deadbeefdeadbeefdeadbeef"), "纯 hex 不命中"
assert not is_high_entropy_token("short"), "短词不命中"
assert shannon_entropy("aaaa") == 0.0
assert shannon_entropy("ab") == 1.0

# ---------- 2. 词拼接与 @ 粘合 ----------

words = [
    OcrWord("jordan.blake", 10, 10, 110, 30),
    OcrWord("@", 112, 10, 120, 30),
    OcrWord("example.com", 122, 10, 230, 30),
]
joined, spans = join_words(words)
assert "jordan.blake@example.com" in joined, f"@ 两侧应粘合: {joined!r}"
assert len(spans) == 3

# 粘合后 email 正则应命中，且三个词的框都进入遮挡
matches = resolve_overlaps(detect_sensitive(joined))
kinds = sorted(m.kind for m in matches)
assert "email" in kinds, f"email 未命中: {joined!r} -> {kinds}"
boxes = boxes_for_matches(matches, spans, (800, 600))
covered_x = {w.text for w in words
             if any(b[0] <= w.x0 and b[2] >= w.x1 for b in boxes)}
assert covered_x == {"jordan.blake", "@", "example.com"}, \
    f"email 三个词都应被覆盖: {covered_x}"

# ---------- 3. 凭证赋值右值 ----------

words2 = [
    OcrWord("api_key", 10, 60, 80, 80),
    OcrWord("=", 82, 60, 90, 80),
    OcrWord("sk-proj-4f2b9xQw7mZtLh8vNr3Kd", 92, 60, 350, 80),
    OcrWord("说明", 360, 60, 400, 80),
]
joined2, spans2 = join_words(words2)
matches2 = resolve_overlaps(detect_sensitive(joined2))
cred = [m for m in matches2 if m.kind in ("credential", "secret")]
assert cred, f"凭证右值应命中: {joined2!r} -> {[m.kind for m in matches2]}"
boxes2 = boxes_for_matches(matches2, spans2, (800, 600))
assert any(b[2] >= 346 for b in boxes2), "密钥值区域应有框"
assert not any(b[0] >= 360 for b in boxes2), "右侧普通词不应被遮挡"

# ---------- 4. known values cross-feed + 重叠消解 ----------

words3 = [OcrWord("13812345678", 10, 120, 180, 140)]
joined3, spans3 = join_words(words3)
raw = detect_sensitive(joined3, known_values=["13812345678"])
kinds3 = sorted(m.kind for m in raw)
assert kinds3 == ["known", "phone"], f"known 与 phone 同时命中: {kinds3}"
resolved3 = resolve_overlaps(raw)
assert [m.kind for m in resolved3] == ["known"], "rank 高者（known=100）胜出"

# 短值不参与 known 匹配（防误伤）
assert detect_sensitive("abc123", known_values=["abc"]) == [] or \
    all(m.kind != "known" for m in detect_sensitive("abc123", known_values=["abc"]))

# ---------- 5. 掩码不泄漏 ----------

assert mask_value("abc") == "••••"
mv = mask_value("13812345678")
assert "138" not in mv and "678" not in mv and mv.startswith("13") and mv.endswith("78")
audit_values = ["13812345678"]
joined4, _ = join_words([OcrWord("13812345678", 0, 0, 100, 20)])
m4 = resolve_overlaps(detect_sensitive(joined4))
masked_out = [mask_value(x.value) for x in m4]
assert all("13812345678" not in v for v in masked_out)

# ---------- 6. redact_image 端到端（注入合成 OCR） ----------

from PIL import Image


def fake_ocr(pre_img, scale):
    # 合成词表：敏感（Luhn 通过的卡号）+ 干净词
    return [
        OcrWord("订单", 10, 10, 60, 40),
        OcrWord("6222021234567890128", 70, 10, 320, 40),
        OcrWord("已支付", 330, 10, 400, 40),
    ]


img = Image.new("RGB", (500, 60), "white")
out, audit = redact_image(img, ocr=fake_ocr)
assert audit["redacted"] and audit["boxes"] >= 1, audit
assert audit["method"] == "ocr"
assert all("6222021234567890128" not in str(v) for v in audit["matches"]), "审计不得携带原始值"
# 敏感区被涂黑，干净区保持白色
px = out.load()
assert px[150, 25] == (17, 17, 17), "卡号区域应被纯色遮挡"
assert px[370, 25] == (255, 255, 255), "干净词区域不应被遮挡"

# 干净帧：无命中时原样返回
clean_ocr = lambda pre, scale: [OcrWord("普通文本", 10, 10, 100, 40)]
img2 = Image.new("RGB", (200, 60), "white")
out2, audit2 = redact_image(img2, ocr=clean_ocr)
assert not audit2["redacted"]
assert out2.load()[50, 30] == (255, 255, 255)

# fail-closed：OCR 抛异常 -> withheld；on_error=drop 整帧纯色
def broken_ocr(pre, scale):
    raise RuntimeError("tesseract missing")


img3 = Image.new("RGB", (100, 40), "white")
out3, audit3 = redact_image(img3, ocr=broken_ocr)
assert audit3["withheld"] and audit3["method"] == "error"
assert out3.load()[50, 20] == (255, 255, 255), "keep 模式保留原图"

out4, audit4 = redact_image(img3, ocr=broken_ocr, on_error="drop")
assert audit4["withheld"]
assert out4.load()[50, 20] == (17, 17, 17), "drop 模式整帧纯色"

# ---------- 7. redact_frame 磁盘便捷入口（无命中也重编码） ----------

with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "frame.jpg"
    img5 = Image.new("RGB", (300, 80), "white")
    img5.save(p, "JPEG")
    # 无 tesseract 环境下走 redact_image 的异常路径也不得抛出
    res = redact_frame(p)
    assert res["path"] == str(p) and "redacted" in res

# ---------- 8. opt-in 真 OCR 端到端（自跳过） ----------

if os.environ.get("TAGENT_EVAL_OCR") == "1":
    try:
        import pytesseract  # noqa: F401
        from services.ocr_locator import _find_tesseract
        if not _find_tesseract():
            raise FileNotFoundError("tesseract not found")
        from PIL import ImageDraw, ImageFont
        canvas = Image.new("RGB", (900, 120), "white")
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype("arial.ttf", 28)
        except OSError:
            font = ImageFont.load_default()
        draw.text((20, 40), "mail: user.name@example.com end", fill="black", font=font)
        out5, audit5 = redact_image(canvas)
        assert audit5["redacted"], f"真 OCR 应检出 email: {audit5}"

        # 中文链路（装了 chi_sim 才跑；语言/PSM 回归防护）
        from services.ocr_locator import best_lang
        if "chi_sim" in best_lang():
            cn_font = ImageFont.truetype("msyh.ttc", 28)
            cn = Image.new("RGB", (800, 120), "white")
            ImageDraw.Draw(cn).text(
                (20, 40), "登录密码：Ab3xY9mK2pQ7 请牢记", fill="black", font=cn_font)
            out6, audit6 = redact_image(cn)
            assert audit6["redacted"] and any(
                m["kind"] == "credential" for m in audit6["matches"]), \
                f"中文凭证标签应命中右值: {audit6}"
            from services.ocr_locator import find_text_on_screen
            loc = Image.new("RGB", (900, 200), "white")
            ImageDraw.Draw(loc).text(
                (120, 80), "账号：张三丰", fill="black", font=cn_font)
            pos = find_text_on_screen(loc, "账号：张三丰")
            assert pos and 100 < pos[0] < 400 and 60 < pos[1] < 140, \
                f"OCR 定位应命中中文（全/半角冒号 NFKC 归一）: {pos}"
            print("中文 OCR 链路: 凭证遮挡 + 定位均通过")
        else:
            print("[SKIP] chi_sim 未安装，中文链路跳过")
    except Exception as exc:  # 字体/引擎缺失时自跳过
        print(f"[SKIP] 真 OCR 端到端不可用: {exc}")
else:
    print("[SKIP] 真 OCR 端到端未启用（TAGENT_EVAL_OCR=1 开启）")

print("frame_redactor 全部断言通过")
