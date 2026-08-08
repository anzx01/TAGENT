"""验证 merge 一致性校验（dry 模式）"""
import json
import time
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:18899"
REC = Path(r"D:\aiapp\TAGENT\recordings")
_seq = [0]
now = lambda: time.strftime("%Y%m%d_%H%M%S")


def make_rec(steps):
    time.sleep(1.1)  # 保证秒级唯一
    rid = time.strftime("%Y%m%d_%H%M%S")
    d = REC / rid
    d.mkdir()
    (d / "events.json").write_text(json.dumps({"steps": steps, "count": len(steps)}), encoding="utf-8")
    (d / "screenshots").mkdir()
    return rid


def post(url, data=None):
    body = json.dumps(data).encode() if data is not None else b"{}"
    req = urllib.request.Request(url, method="POST",
                                 headers={"Content-Type": "application/json"}, data=body)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def step(act, window, text=""):
    return {"action": act, "window": window, "text": text}


# 同应用（记事本，容忍动态标题）
a = make_rec([step("click", "无标题 - Notepad"), step("input_text", "无标题 - Notepad", "hello")])
b = make_rec([step("click", "*Second - Notepad"), step("input_text", "*Second - Notepad", "hello")])
r = post(f"{API}/recordings/{a}/merge/{b}", {"dry": True})
print("同应用 完整响应:", r, "| ids:", a, b)
assert r.get("warning") is None

# 不同应用
c = make_rec([step("click", "设置"), step("click", "设置")])
r2 = post(f"{API}/recordings/{a}/merge/{c}", {"dry": True})
print("不同应用(记事本 vs 设置) 完整响应:", r2)
assert r2.get("warning")

# dry 模式不应删除追加录制
assert (REC / c).exists(), "dry 模式不应删除追加录制"

# 真实合并仍工作（同应用）
r3 = post(f"{API}/recordings/{a}/merge/{b}")
print("真实合并:", r3.get("ok"), "removed:", r3.get("removed"))
assert r3.get("ok") is True
assert not (REC / b).exists(), "真实合并后应删除追加录制"

# 清理验证用录制
import shutil
for rid in (a, c):
    shutil.rmtree(REC / rid, ignore_errors=True)
print("PASS")