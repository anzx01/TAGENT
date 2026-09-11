"""type 链路验证：收件箱 -> 新邮件 -> 点正文 -> type -> 确认"""
import json
import time
import urllib.request

TASK = ("在 Outlook 中：1.点击左栏「收件箱」回到收件箱视图；"
        "2.点击工具栏「新邮件」按钮打开新邮件编辑；"
        "3.点击邮件正文空白区域；4.用 type 动作粘贴文字 hello-visual-test；"
        "5.在截图中确认文字出现在正文区域后返回 done。")

req = urllib.request.Request(
    "http://127.0.0.1:18899/visual/run", method="POST",
    headers={"Content-Type": "application/json"},
    data=json.dumps({"task": TASK, "max_steps": 16}).encode("utf-8"))
print(urllib.request.urlopen(req, timeout=15).read().decode(), flush=True)

for i in range(25):
    time.sleep(10)
    st = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:18899/visual/status", timeout=10).read())
    s = st["state"]
    steps = s.get("steps", [])
    print(f"t={(i + 1) * 10}s status={s.get('status')} steps={len(steps)}", flush=True)
    for stp in steps[-4:]:
        print("   ", stp.get("step"), stp.get("action"), "|",
              str(stp.get("raw"))[:160], flush=True)
    if not st["running"]:
        print("FINAL:", s.get("status"), "|", s.get("summary") or s.get("error"), flush=True)
        break
