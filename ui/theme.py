import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

BG = "#f4f4f5"
SURFACE = "#ffffff"
STRIPE = "#fbfbfd"
BORDER = "#d4d4d8"
FG = "#18181b"
FG_MUTED = "#71717a"
ACCENT = "#2563eb"
SELECTION = "#dbeafe"
BUTTON_BORDER = "#d0d5db"
RADIO_OFF = "#666666"
RADIO_ON = "#333333"
SECTION_TITLE = "#333333"

FONT_FAMILY = "Segoe UI"
FONT = (FONT_FAMILY, 10)
FONT_BOLD = (FONT_FAMILY, 10, "bold")


def apply(root: tk.Misc) -> None:
    """Apply a flat, neutral ttk theme process-wide. ttk.Style() is global,
    so this only needs to run once at startup and every Toplevel dialog
    picks it up automatically; only each window's raw background (not a
    ttk-managed property) needs to be set individually."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=BG, foreground=FG, font=FONT)

    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG)

    style.configure(
        "TButton",
        background=SURFACE,
        foreground=FG,
        bordercolor=BUTTON_BORDER,
        relief="solid",
        borderwidth=1,
        padding=(10, 3),
    )
    style.map(
        "TButton",
        background=[("active", "#e4e4e7"), ("pressed", "#d4d4d8")],
        bordercolor=[("focus", ACCENT)],
    )

    style.configure("TEntry", fieldbackground=SURFACE, bordercolor=BORDER, padding=5)
    style.map("TEntry", bordercolor=[("focus", ACCENT)])

    style.configure(
        "TCombobox", fieldbackground=SURFACE, background=SURFACE, bordercolor=BORDER, padding=5
    )
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE)])

    style.configure("TSpinbox", fieldbackground=SURFACE, bordercolor=BORDER, padding=5)

    style.configure("TSeparator", background=BORDER)

    # A hardcoded rowheight can end up smaller than the font's own line
    # height once DPI-aware scaling is in effect, clipping descenders
    # (g, j, ...) against the row boundary. Measuring the actual font's
    # metrics keeps this correct regardless of DPI/scale factor instead of
    # relying on a fixed pixel guess that only happens to fit today.
    row_font = tkfont.Font(root=root, family=FONT_FAMILY, size=FONT[1])
    row_height = row_font.metrics("linespace") + 8

    style.configure(
        "Treeview",
        background=STRIPE,
        fieldbackground=STRIPE,
        foreground=FG,
        bordercolor=BORDER,
        rowheight=row_height,
        relief="solid",
        borderwidth=1,
    )
    style.map("Treeview", background=[("selected", SELECTION)], foreground=[("selected", FG)])

    style.configure(
        "Treeview.Heading", background=BG, foreground=FG_MUTED, relief="flat", font=FONT_BOLD
    )
    style.map("Treeview.Heading", background=[("active", BG)])

    style.configure(
        "Vertical.TScrollbar", background=BG, troughcolor=BG, bordercolor=BG, arrowcolor=FG_MUTED
    )
