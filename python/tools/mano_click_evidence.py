"""证据实验：点击必产生视觉变化（收件箱->已发送切换），验证服务端点击是否真生效"""
import json
import time
import urllib.request

TASK = ("点击 Outlook 左侧边栏中的「已发送邮件」文件夹（切换到已发送视图），"
        "点击一次后立即返回 done，不要做任何其他操作，不要重复点击。")

req = urllib.request.Request(
    "http://127.0.0.1:18899/visual/run", method="POST",
    headers={"Content-Type": "application/json"},
    data=json.dumps({"task": TASK, "max_steps": 6}).encode("utf-8"))
print(urllib.request.urlopen(req, timeout=15).read().decode(), flush=True)

for i in range(12):
    time.sleep(8)
    st = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:18899/visual/status", timeout=10).read())
    s = st["state"]
    steps = s.get("steps", [])
    print(f"t={(i + 1) * 8}s status={s.get('status')} steps={len(steps)}", flush=True)
    for stp in steps:
        print("   ", stp.get("step"), stp.get("action"), "|",
              str(stp.get("raw"))[:150], flush=True)
    if not st["running"]:
        print("FINAL:", s.get("status"), "|", s.get("summary") or s.get("error"), flush=True)
        break
