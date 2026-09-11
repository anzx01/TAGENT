"""启动视觉接管任务（v2：强化协议后的重跑）"""
import json
import time
import urllib.request

TASK = ("在 Outlook 完成发邮件："
        "1.若屏幕已是新邮件编辑视图就直接用；否则点击工具栏「新邮件」按钮打开；"
        "2.先点击「收件人」输入框，确认焦点后再返回 type 动作填入 947057797@qq.com；"
        "3.点击「添加主题」框后 type 填入 TAGENT 测试邮件；"
        "4.点击正文区域后 type 填入：这是由 TAGENT 视觉接管通道(Qwen-VL)自动发送的测试邮件，请忽略。"
        "5.核对收件人和主题确实已填好，然后点击「发送」按钮发送。"
        "6.看到编辑视图关闭即任务完成，立即返回 done。")

req = urllib.request.Request(
    "http://127.0.0.1:18899/visual/run", method="POST",
    headers={"Content-Type": "application/json"},
    data=json.dumps({"task": TASK, "max_steps": 45}).encode("utf-8"))
print(urllib.request.urlopen(req, timeout=15).read().decode())

# 轮询
for i in range(40):
    time.sleep(15)
    st = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:18899/visual/status", timeout=10).read())
    s = st["state"]
    steps = s.get("steps", [])
    print(f"t={(i + 1) * 15}s running={st['running']} status={s.get('status')} "
          f"steps={len(steps)}", flush=True)
    for stp in steps[-2:]:
        print("   ", stp.get("step"), stp.get("action"), "|",
              str(stp.get("thought"))[:70], flush=True)
    if not st["running"]:
        print("FINAL:", s.get("status"), "|",
              s.get("summary") or s.get("error"), flush=True)
        for stp in steps:
            print("  *", stp.get("step"), stp.get("action"), "|",
                  str(stp.get("thought"))[:55], flush=True)
        break
