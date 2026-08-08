"""验证回放：switch_window + wait + input_text 全链路"""
import json
import sys
import time
import urllib.request

API = "http://127.0.0.1:18899"

sys.path.insert(0, r"D:\aiapp\TAGENT\python")
from logger import get_logger

log = get_logger("e2e_switch")


def post(url, data=b"{}"):
    req = urllib.request.Request(url, method="POST", headers={"Content-Type": "application/json"}, data=data)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=10).read())


latest = max(get(API + "/tasks")["tasks"], key=lambda t: t["id"])
rec_id = latest["id"]
print("replay task:", rec_id)

r = post(API + "/replay/start", json.dumps({"recording_id": rec_id}).encode())
print("replay start:", r)

for _ in range(60):
    time.sleep(1)
    state = get(API + "/replay/status")
    if not state.get("running"):
        break
print("replay status:", state)
print("steps:", " / ".join(f"{s.get('action')}:{s.get('text', '')}" for s in state.get("steps", [])))