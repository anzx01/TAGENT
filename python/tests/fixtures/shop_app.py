"""模拟购物靶标:商品编号 → 回车 → 数量 → 回车下单,订单写入 state 文件。
用法: python shop_app.py <order_file_path>
"""
import sys
import tkinter as tk


def main() -> None:
    state_path = sys.argv[1] if len(sys.argv) > 1 else "shop_order.txt"
    root = tk.Tk()
    root.title("TAGENT商店")
    root.geometry("420x200+200+200")
    product = tk.StringVar()
    qty = tk.StringVar()
    status = tk.StringVar(value="READY")
    e1 = tk.Entry(root, textvariable=product, width=30)
    e2 = tk.Entry(root, textvariable=qty, width=10)

    def order():
        status.set(f"ORDERED:{product.get()}x{qty.get()}")
        with open(state_path, "w", encoding="utf-8") as fh:
            fh.write(status.get())

    def step1(_e=None):
        e2.focus_set()

    e1.bind("<Return>", step1)
    e2.bind("<Return>", lambda e: order())
    tk.Button(root, text="下单", command=order).pack(pady=6)
    tk.Label(root, textvariable=status).pack(pady=4)
    e1.pack(padx=20, pady=8)
    e2.pack(padx=20, pady=8)
    e1.focus_set()
    root.mainloop()


if __name__ == "__main__":
    main()
