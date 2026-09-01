"""web_channel 单测（直接运行：python tests/test_web_channel.py）
覆盖无 CDP 环境下的优雅降级路径（无需真实浏览器）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.input.web_channel import WebChannel


def test_degrade():
    w = WebChannel()
    # 非 dom locator：通道不参与
    assert w.maybe_click("Chrome", {"type": "coord"}) is None
    assert w.maybe_click("", {"type": "dom"}) is None
    # 未激活时输入/按键不参与
    assert w.maybe_type("Chrome", "hi") is None
    assert w.maybe_key("Chrome", "enter") is None
    # dom 定位但无调试端口：返回 None = 交由物理通道（不报错）
    assert w.maybe_click("Chrome - Google Chrome",
                         {"type": "dom", "selector": "text='确定'"}) is None
    # 浏览器名后缀剥离
    from services.input.web_channel import _strip_browser
    assert _strip_browser("智能操作助手 - Brave") == "智能操作助手"
    assert _strip_browser("标题 - Microsoft Edge") == "标题"
    assert _strip_browser("无后缀窗口") == "无后缀窗口"


def test_ensure_disabled():
    w = WebChannel()
    assert not w.ensure() or w._browser is not None, "无端口应失败或已连上"
    # 失败后有尝试缓冲：重复调用立即返回，不反复探测
    w2 = WebChannel()
    w2.ensure()
    assert w2._tried is True


# 无需浏览器即生效的路径
test_degrade()
test_ensure_disabled()
print("web_channel 全部断言通过")