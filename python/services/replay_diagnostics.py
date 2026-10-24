"""Safe labels for replay diagnostics shown outside the local report."""


def pause_category(reason: str) -> str:
    reason = str(reason or "")
    for marker, label in (
        ("能力完成条件未通过", "能力完成条件未通过"),
        ("等待目标窗口未出现", "等待目标窗口未出现"),
        ("破坏性操作需确认", "破坏性操作需确认"),
        ("会话步骤", "会话步骤需人工处理"),
        ("定位失败", "元素定位失败"),
        ("执行失败", "步骤执行失败"),
        ("执行操作失败", "步骤执行失败"),
    ):
        if reason.startswith(marker):
            return label
    return "执行暂停" if reason else ""
