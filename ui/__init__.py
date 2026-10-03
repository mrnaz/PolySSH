import tkinter as tk
from tkinter import ttk

from ui import theme


def center_on_parent(dialog: tk.Toplevel) -> None:
    dialog.update_idletasks()
    parent = dialog.master
    x = parent.winfo_rootx() + (parent.winfo_width()  - dialog.winfo_width())  // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - dialog.winfo_height()) // 2
    dialog.geometry(f"+{x}+{y}")


def section_header(parent: tk.Misc, row: int, title: str, columnspan: int = 3) -> int:
    """A bold title with a divider line running out to the right edge.
    Returns the next free grid row."""
    frame = ttk.Frame(parent)
    frame.grid(row=row, column=0, columnspan=columnspan, sticky="ew", padx=6, pady=(0 if row == 0 else 10, 2))
    frame.columnconfigure(1, weight=1)
    ttk.Label(frame, text=title, font=theme.FONT_BOLD, foreground=theme.SECTION_TITLE).grid(
        row=0, column=0, padx=(0, 8)
    )
    ttk.Separator(frame, orient="horizontal").grid(row=0, column=1, sticky="ew", pady=(6, 0))
    return row + 1
