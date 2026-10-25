"""模拟邮件客户端靶标:收件人 → 回车 → 主题 → 回车 → 正文 → 回车发送。
发送只写本地 MAIL 文件,绝不真实外发。
用法: python mail_app.py <mail_file_path>
"""
import sys
import tkinter as tk


def main() -> None:
    state_path = sys.argv[1] if len(sys.argv) > 1 else "mock_mail.txt"
    root = tk.Tk()
    root.title("TAGENT邮箱")
    root.geometry("460x240+200+200")
    to = tk.StringVar()
    body = tk.StringVar()
    status = tk.StringVar(value="DRAFT")
    e1 = tk.Entry(root, textvariable=to, width=40)
    e2 = tk.Entry(root, textvariable=body, width=40)

    def send():
        status.set(f"SENT:{to.get()}|{body.get()}")
        with open(state_path, "w", encoding="utf-8") as fh:
            fh.write(status.get())

    def advance(widget):
        widget.focus_set()
        root.after(120, widget.focus_set)  # 回放输入节奏快,二次确保焦点落位

    e1.bind("<Return>", lambda e: advance(e2))
    e2.bind("<Return>", lambda e: send())
    tk.Button(root, text="发送", command=send).pack(pady=6)
    tk.Label(root, textvariable=status).pack(pady=4)
    e1.pack(padx=20, pady=6)
    e2.pack(padx=20, pady=6)
    e1.focus_set()
    root.mainloop()


if __name__ == "__main__":
    main()
