"""诊断 mano-cua step 400：对比带/不带 User-Agent 的 step 响应"""
import json
import platform
import sys
import uuid

import requests

BASE = "https://mano.mininglamp.com"
UA = f"mano-cua/1.1.4 (Windows NT {platform.release()}; {platform.machine()}) Python/3.13"

import os
def device_id():
    p = os.path.expanduser("~/.myapp_device_id")
    if os.path.exists(p):
        return open(p).read().strip()
    d = uuid.uuid4().hex
    open(p, "w").write(d)
    return d

# 1) 创建 session（带 UA，模拟正常流程）
s = requests.post(f"{BASE}/v1/sessions",
                  json={"task": "测试：点击屏幕中央",
                        "device_id": device_id(),
                        "platform": "Windows"},
                  headers={"User-Agent": UA}, timeout=60)
print("session:", s.status_code)
sid = s.json()["session_id"]
print("sid:", sid)

# 2) step 不带 UA（复现 CLI 行为）
r1 = requests.post(f"{BASE}/v1/sessions/{sid}/step",
                   json={"request_id": str(uuid.uuid4()), "tool_results": []},
                   timeout=30)
print("step 无UA:", r1.status_code, r1.text[:300])

# 3) step 带 UA
if r1.status_code != 200:
    r2 = requests.post(f"{BASE}/v1/sessions/{sid}/step",
                       json={"request_id": str(uuid.uuid4()), "tool_results": []},
                       headers={"User-Agent": UA}, timeout=30)
    print("step 带UA:", r2.status_code, r2.text[:500])

# 收尾
try:
    requests.post(f"{BASE}/v1/sessions/{sid}/close?skip_eval=true&close_reason=DIAG",
                  json={}, timeout=15)
except Exception:
    pass
