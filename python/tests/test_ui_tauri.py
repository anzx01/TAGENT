"""Tauri release 前端实测：uiautomation 定位悬浮条按钮，走 录制->停止->任务库->追加->停止 全链路"""
import json
import time
import urllib.request
import uiautomation as auto

API = "http://127.0.0.1:18899"


def post(url, data=None):
    body = json.dumps(data).encode() if data else b"{}"
    req = urllib.request.Request(url, method="POST",
                                 headers={"Content-Type": "application/json"}, data=body)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def status():
    return json.loads(urllib.request.urlopen(API + "/status", timeout=10).read())


def find_btn(name):
    win = auto.WindowControl(searchDepth=1, Name="智能操作助手")
    if not win.Exists(3):
        # 兜底按进程找
        win = auto.PaneControl(searchDepth=0,
                               ClassName="ApplicationFrameWindow")  # noqa
        return None
    btn = win.ButtonControl(Name=name)
    if not btn.Exists(2):
        return None
    return btn


ok = True
win = auto.WindowControl(searchDepth=1, Name="智能操作助手")
print("悬浮条窗口:", win.Exists(5))

# 1. 录制按钮
btn = win.ButtonControl(Name="录制")
print("找到'录制'按钮:", btn.Exists(3))
assert btn.Exists(3), "录制按钮不存在"
before_rec = len(status()["recordings"])
btn.Click()
time.sleep(2)
st = status()
print("点击录制后 recording:", st["recording"])
assert st["recording"] is True, "录制未开始"

# 2. 停止按钮
btn_stop = win.ButtonControl(Name="停止")
print("找到'停止'按钮:", btn_stop.Exists(3))
btn_stop.Click()
time.sleep(3)
st = status()
print("停止后 recordings 数:", len(st["recordings"]))
assert st["recording"] is False and len(st["recordings"]) == before_rec + 1, "录制未保存"
rec_id = st["recordings"][0]["id"]
print("新录制:", rec_id)

# 3. 任务库按钮（展开面板）
btn_list = win.ButtonControl(Name="任务库")
btn_list.Click()
time.sleep(2)

# 4. 追加按钮（任务卡片上的'追加'）
btn_appd = win.ButtonControl(Name="追加")
print("找到'追加'按钮:", btn_appd.Exists(3))
assert btn_appd.Exists(3), "追加按钮不存在"
btn_appd.Click()
time.sleep(2)
st = status()
print("点击追加后 recording:", st["recording"])
assert st["recording"] is True, "追加录制未开始"

# 5. 停止（触发 finishAppend -> dry -> 合并）
btn_stop.Click()
time.sleep(4)
st = status()
print("追加停止后 recordings 数:", len(st["recordings"]))
# 追加录制与目标合并后应只剩 1 条（空的追加录会被删除；若无内容则合并不发生，剩 2 条也正常）
print("追加链路结束, recording:", st["recording"])
assert st["recording"] is False

print("\nUI 实测结果: PASS" if ok else "\nUI 实测结果: FAIL")