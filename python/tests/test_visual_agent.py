"""VisualAgent 纯逻辑单测（不含桌面注入/模型调用）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.visual_agent import (_EXECUTORS, _map_xy, _parse_action_json,
                                   _parse_keys, get_visual_agent)


def test_parse_action_json_fenced():
    r = _parse_action_json('好的，下一步：\n```json\n{"action":"click","x":10,"y":20}\n```')
    assert r == {"action": "click", "x": 10, "y": 20}


def test_parse_action_json_bare():
    r = _parse_action_json('前置说明 {"thought":"t","action":"type","text":"你好"} 后缀')
    assert r["action"] == "type" and r["text"] == "你好"


def test_parse_action_json_invalid():
    assert _parse_action_json("完全没有 JSON") is None
    assert _parse_action_json("[1,2]") is None
    assert _parse_action_json("") is None


def test_map_xy_scaling():
    # 视图 1280 宽（缩放 1/3），物理 3840：视图 (640,360) -> 物理 (1920,1080)
    assert _map_xy({"x": 640, "y": 360}, 1 / 3) == (1920, 1080)
    assert _map_xy({"x": 640, "y": 360}, 1.0) == (640, 360)


def test_parse_keys():
    assert _parse_keys("ctrl+s") == [0x11, 0x53]
    assert _parse_keys("win r") == [0x5B, 0x52]
    assert _parse_keys("ctrl+alt+enter") == [0x11, 0x12, 0x0D]
    assert _parse_keys("garbage!!") == []


def test_executors_registry():
    for name in ("click", "double_click", "right_click", "type", "hotkey",
                 "scroll", "drag", "wait", "done", "fail"):
        # done/fail 不在执行器表（由主循环处理），其余必须在
        if name in ("done", "fail"):
            assert name not in _EXECUTORS
        else:
            assert name in _EXECUTORS


def test_agent_state_init():
    va = get_visual_agent()
    assert va.running is False
    assert va.state == {}


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("全部通过")
