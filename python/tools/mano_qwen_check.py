"""Qwen-VL 连通性验证：发一张截图，验证视觉调用与 JSON 解析"""
import base64
import io
import json
import sys
import urllib.request

sys.path.insert(0, ".")
from capture import capture_screen
from services.visual_agent import _parse_action_json

img = capture_screen()
img = img.resize((img.width // 3, img.height // 3))
buf = io.BytesIO()
img.save(buf, format="JPEG", quality=80)
b64 = base64.b64encode(buf.getvalue()).decode()

cfg = json.loads(open(r"d:\aiapp\TAGENT\data\llm_config.json", encoding="utf-8").read())
body = {
    "model": cfg["model"],
    "messages": [{"role": "user", "content": [
        {"type": "text", "text": '用一句话描述当前屏幕上的主要窗口或应用，'
                                 '然后返回 JSON {"action":"wait","seconds":0.2}'},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
    ]}],
    "max_tokens": 200,
}
req = urllib.request.Request(
    cfg["api_base"].rstrip("/") + "/chat/completions", method="POST",
    headers={"Content-Type": "application/json",
             "Authorization": "Bearer " + cfg["api_key"]},
    data=json.dumps(body).encode())
r = json.loads(urllib.request.urlopen(req, timeout=60).read())
c = r["choices"][0]["message"]["content"]
print("MODEL_OUTPUT:", c[:300])
print("PARSED:", _parse_action_json(c))
print("USAGE:", r.get("usage", {}).get("total_tokens"))
