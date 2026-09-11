"""最小粘贴验证：视觉模型点击 Outlook 正文区并粘贴标记文本"""
import json
import time
import urllib.request

TASK = ("当前 Outlook 打开着一封新邮件编辑视图。"
        "请点击邮件正文输入区域（位于主题框下方的大片空白区域）获得焦点，"
        "然后用 type 动作粘贴文字 hello-visual-test，"
        "在截图中确认文字已出现在正文区域后返回 done。")

req = urllib.request.Request(
    "http://127.0.0.1:18899/visual/run", method="POST",
    headers={"Content-Type": "application/json"},
    data=json.dumps({"task": TASK, "max_steps": 12}).encode("utf-8"))
print(urllib.request.urlopen(req, timeout=15).read().decode(), flush=True)

for i in range(20):
    time.sleep(10)
    st = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:18899/visual/status", timeout=10).read())
    s = st["state"]
    steps = s.get("steps", [])
    print(f"t={(i + 1) * 10}s status={s.get('status')} steps={len(steps)}", flush=True)
    for stp in steps[-3:]:
        print("   ", stp.get("step"), stp.get("action"), "|",
              str(stp.get("thought"))[:70], "|", str(stp.get("raw"))[:120], flush=True)
    if not st["running"]:
        print("FINAL:", s.get("status"), "|", s.get("summary") or s.get("error"), flush=True)
        break
