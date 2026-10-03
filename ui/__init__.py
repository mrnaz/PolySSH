import tkinter as tk


def center_on_parent(dialog: tk.Toplevel) -> None:
    dialog.update_idletasks()
    parent = dialog.master
    x = parent.winfo_rootx() + (parent.winfo_width()  - dialog.winfo_width())  // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - dialog.winfo_height()) // 2
    dialog.geometry(f"+{x}+{y}")
