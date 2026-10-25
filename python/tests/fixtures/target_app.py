"""TAGENT 确定性靶标应用(移植 openskills demo-app 思想)。

一个无外部依赖的 tkinter 表单:输入内容停顿 1.5 秒自动提交(不依赖回车键,
避免中文 IME 把 Enter 当作组合提交),提交结果写入 state 文件。
供 e2e 测试做"录制→编译→回放→验证"全链路,不受记事本版本/IME/前台抢占影响。
用法: python target_app.py <state_file_path>
"""
import sys
import tkinter as tk

IDLE_MS = 1500


def main() -> None:
    state_path = sys.argv[1] if len(sys.argv) > 1 else "target_state.txt"
    root = tk.Tk()
    root.title("TAGENT靶标")
    root.geometry("420x160+200+200")
    value = tk.StringVar()
    status = tk.StringVar(value="READY")
    timer = {"id": None}

    def submit():
        status.set("DONE:" + value.get())
        with open(state_path, "w", encoding="utf-8") as fh:
            fh.write(status.get())

    def on_change(*_):
        if timer["id"] is not None:
            root.after_cancel(timer["id"])
        timer["id"] = root.after(IDLE_MS, submit)

    value.trace_add("write", on_change)
    entry = tk.Entry(root, textvariable=value, width=40)
    tk.Button(root, text="提交", command=submit).pack(pady=8)
    tk.Label(root, textvariable=status).pack(pady=6)
    entry.pack(padx=20, pady=10)
    entry.focus_set()
    root.mainloop()


if __name__ == "__main__":
    main()
