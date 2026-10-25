"""窗口标题漂移模糊匹配回归（真实案例:20261025_154519 任务 switch_window no_window）。
录制标题带应用状态后缀（[管理员模式]/扩展名），回放时后缀消失导致
单向包含匹配失效；同应用不同文档必须拒绝，避免切错窗口。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import player


class _FakeDesktop:
    """把 win32gui 枚举替换为固定的窗口/对话框/前台。"""

    def __init__(self, windows, dialogs=(), foreground=0):
        self.windows = windows
        self.dialogs = dict(dialogs)
        self.foreground = foreground
        self._saved = {}

    def __enter__(self):
        allwin = dict(self.windows)
        allwin.update(self.dialogs)
        for name, fn in (
            ("FindWindow", lambda cls, title: 0),
            ("EnumWindows", lambda cb, arg: [cb(w, arg)
                                             for w, _t in list(self.windows) + list(self.dialogs.items())]),
            ("IsWindowVisible", lambda w: True),
            ("GetWindowText", lambda w: allwin.get(w, "")),
            ("GetForegroundWindow", lambda: self.foreground),
            ("IsIconic", lambda w: False),
            ("GetClassName", lambda w: "#32770" if w in self.dialogs else "Normal"),
        ):
            self._saved[name] = getattr(player.win32gui, name)
            setattr(player.win32gui, name, fn)
        return self

    def __exit__(self, *exc):
        for name, fn in self._saved.items():
            setattr(player.win32gui, name, fn)


def main():
    # 案例来自 20261025_154519 任务:Everything 管理员标记在回放时消失
    everything = (101, "维多利亚 - Everything")
    wps = (102, "各种用户名和密码20260719（维多利亚） - WPS Office")
    wechat = (103, "微信")

    with _FakeDesktop([everything, wps, wechat]):
        # 1. 录制比实际多后缀（真实故障:switch_window 第4/33步 no_window）
        got = player._find_window_by_title("维多利亚 - Everything [管理员模式]")
        assert got == 101, got
        # 2. WPS 文档扩展名显隐漂移（真实故障:第9/33步连锁 no_window）
        got = player._find_window_by_title(
            "各种用户名和密码20260719（维多利亚）.docx - WPS Office")
        assert got == 102, got
        # 3. 未保存 * 标记 + 大小写归一
        got = player._find_window_by_title("*维多利亚 - everything [管理员模式]")
        assert got == 101, got

    # 4. 同应用不同文档:短文档名必须拒绝（应用后缀占全串大头，全串相似度失真）
    with _FakeDesktop([(201, "报告B - WPS Office"), (202, "微信")]):
        got = player._find_window_by_title("报告A - WPS Office")
        assert got is None, got
    # 5. 不同应用同文档名:后缀不同拒绝
    with _FakeDesktop([(301, "维多利亚 - WPS Office")]):
        got = player._find_window_by_title("维多利亚 - Everything")
        assert got is None, got
    # 6. 完全无关窗口拒绝
    with _FakeDesktop([(401, "计算器")]):
        got = player._find_window_by_title("微信")
        assert got is None, got

    # 7. 原有单向包含仍生效（两个都命中包含级，按 Z 序取枚举第一个）
    with _FakeDesktop([(501, "维多利亚 - Everything"), (502, "Everything")]):
        got = player._find_window_by_title("Everything")
        assert got == 501, got

    # 8. 前台兜底同样接受标题漂移
    with _FakeDesktop([]):
        player.win32gui.GetForegroundWindow = lambda: 601
        player.win32gui.GetWindowText = lambda w: "维多利亚 - Everything"
        parts_fg = player._title_parts("维多利亚 - Everything")
        assert player._fuzzy_title_score(
            player._title_parts("维多利亚 - Everything [管理员模式]"), parts_fg) > 0
        assert player._fuzzy_title_score(
            player._title_parts("报告A - WPS Office"),
            player._title_parts("报告B - WPS Office")) == 0

    # 9. _same_window_kind（陈旧句柄/前台兜底判定）同样容忍标题漂移
    with _FakeDesktop([(601, "维多利亚 - Everything")]):
        assert player._same_window_kind(601, "维多利亚 - Everything [管理员模式]")
        assert not player._same_window_kind(601, "报告A - WPS Office")

    # 10. 接管诊断：漂移窗口在但标题变了 → 提示相似窗口；有 #32770 → 提示弹窗
    with _FakeDesktop(
            [(701, "维多利亚 - Everything"), (702, "微信")],
            dialogs=[(703, "是否保存更改?")], foreground=702):
        detail = player._pause_detail_text(
            {"action": "switch_window", "window": "维多利亚 - Everything [管理员模式]"},
            "执行失败: switch_window (no_window)")
        assert "相似窗口「维多利亚 - Everything」" in detail, detail
        assert "当前前台是「微信」" in detail, detail
        assert "检测到弹窗" in detail and "是否保存更改?" in detail, detail
        # 无窗口信息的步骤不给诊断
        assert player._pause_detail_text({"action": "wait"}, "") == ""
        assert player._visible_dialog_titles() == ["是否保存更改?"]

    # 11. 前缀追加语义：短应用名+状态标记（difflib 相似度失真的实机案例）
    assert player.titles_similar("漂移测试 - 演示程序 [增强模式]", "漂移测试 - 演示程序")
    # 扩展名追加
    assert player.titles_similar("报告 - WPS Office", "报告.txt - WPS Office")
    # 长文档名单字差异（非前缀、<0.8）拒绝——同应用不同文档
    assert not player.titles_similar("月度报告A - WPS Office", "月度报告B - WPS Office")
    # 长文档名小幅修改（≥6字且相似度≥0.8）接受
    assert player.titles_similar(
        "项目验收意见汇总表2026 - WPS Office", "项目验收意见汇总表2025 - WPS Office")

    print("window_match PASS")


if __name__ == "__main__":
    main()
