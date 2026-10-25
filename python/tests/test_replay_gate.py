"""回放前置门禁单测(replay_gate)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.replay_gate import check_preflight, window_exists


def main():
    titles = ["记事本 - Notepad", "Chrome - 浏览器"]
    assert window_exists("notepad", lambda: titles)
    assert window_exists("*记事本", lambda: titles)  # 去掉未保存标记再匹配
    assert not window_exists("计算器", lambda: titles)
    assert window_exists("", lambda: titles)

    # 标题漂移：录制带状态后缀、回放时后缀消失，门禁不能误拦
    assert window_exists("维多利亚 - Everything [管理员模式]",
                         lambda: ["维多利亚 - Everything", "微信"])
    assert window_exists("各种用户名和密码（维多利亚）.docx - WPS Office",
                         lambda: ["各种用户名和密码（维多利亚） - WPS Office"])
    # 同应用不同文档仍要拦（避免门禁形同虚设）
    assert not window_exists("报告A - WPS Office", lambda: ["报告B - WPS Office"])

    task = {"steps": [
        {"action": "wait", "duration": 1},
        {"action": "input_text", "window": "*报告.txt - Notepad", "text": "x"},
    ]}
    ok, fails = check_preflight(task, probe_window=lambda v: False)
    assert not ok and fails == ["目标窗口未打开: 报告.txt - Notepad"], fails
    ok, _ = check_preflight(task, probe_window=lambda v: True)
    assert ok
    ok, fails = check_preflight({"steps": [{"action": "wait"}]}, probe_window=lambda v: False)
    assert ok and not fails  # 无可推导的窗口时不拦
    ok, _ = check_preflight({"preflight": [{"kind": "window", "value": "X"}],
                             "steps": []}, probe_window=lambda v: True)
    assert ok
    print("replay_gate PASS")


if __name__ == "__main__":
    main()
