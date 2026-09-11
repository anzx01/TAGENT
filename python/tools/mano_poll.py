"""轮询视觉接管任务状态直到结束"""
import json
import sys
import time
import urllib.request

for i in range(30):
    time.sleep(20)
    try:
        st = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:18899/visual/status", timeout=10).read())
        s = st["state"]
        steps = s.get("steps", [])
        print(f"t={(i + 1) * 20}s running={st['running']} status={s.get('status')} "
              f"steps={len(steps)}", flush=True)
        for stp in steps[-3:]:
            print("   ", stp.get("step"), stp.get("action"), "|",
                  str(stp.get("thought"))[:80], flush=True)
        if not st["running"]:
            print("FINAL:", s.get("status"), "|",
                  s.get("summary") or s.get("error"), flush=True)
            for stp in steps:
                print("  *", stp.get("step"), stp.get("action"), "|",
                      str(stp.get("thought"))[:60], flush=True)
            break
    except Exception as e:
        print("err", e, flush=True)
