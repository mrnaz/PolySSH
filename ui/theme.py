import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Optional

from ui import render

BG = "#f5f6f8"
SURFACE = "#ffffff"
BORDER = "#d8dce2"
FG = "#1b1f24"
FG_MUTED = "#6b7280"
ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
ACCENT_PRESSED = "#1e40af"
ACCENT_DISABLED = "#a9c1f5"
SELECTION = "#e6eefd"
HOVER = "#eef0f4"
BUTTON_BORDER = "#cdd2d9"
BUTTON_HOVER = "#f1f3f6"
BUTTON_PRESSED = "#e4e7ec"
DISABLED_FILL = "#eef0f3"
DISABLED_FG = "#9ca3af"
SECTION_TITLE = "#333333"
RADIO_OFF = "#666666"
RADIO_ON = "#333333"
BADGE = "#eceff4"

FONT_FAMILY = "Segoe UI"
FONT = (FONT_FAMILY, 10)
FONT_BOLD = (FONT_FAMILY, 10, "bold")
FONT_SMALL = (FONT_FAMILY, 9)
FONT_LARGE = (FONT_FAMILY, 11)

# Vertical padding shared by buttons and every input, so they are the same
# height by construction (logical px, on top of the element's own border).
FIELD_PAD_TOP = 0
FIELD_PAD_BOTTOM = 2

_scale = 1.0
_root: Optional[tk.Misc] = None
_images: dict[tuple, tk.PhotoImage] = {}


def px(logical: float) -> int:
    """A size in logical pixels converted to real pixels at the current
    display scale."""
    return max(1, round(logical * _scale))


def photo(key: tuple, make_png) -> tk.PhotoImage:
    """A cached PhotoImage; keeping a reference is also what stops Tk
    discarding the image while an element or canvas item still uses it."""
    image = _images.get(key)
    if image is None:
        image = tk.PhotoImage(master=_root, data=render.to_tk_data(make_png()))
        _images[key] = image
    return image


def _box(
    fill: Optional[str], border: Optional[str], radius: float = 6, border_px: Optional[int] = None,
    center: Optional[int] = None,
) -> tk.PhotoImage:
    """A 9-slice-able rounded box. ttk fills a widget by tiling the image's
    centre and edges, and every tile is a separate (slow) draw, so the centre
    must be large: a 2px one made a single wide entry take 1.5s to paint."""
    r = px(radius)
    bw = border_px if border_px is not None else max(1, round(_scale))
    center = center if center is not None else px(40)
    size = 2 * r + center
    return photo(("box", fill, border, r, bw, center), lambda: render.rounded_rect(size, size, r, fill, border, bw))


def _image_element(style: ttk.Style, name: str, default: tk.PhotoImage, *states, border=None, **options) -> None:
    """Create a ttk element drawn from images. states are (statespec..., image)
    tuples, first match wins."""
    spec = [(*s[:-1], str(s[-1])) for s in states]
    if border is not None:
        options["border"] = border
        # The image is bigger than the smallest the element can be (its large
        # centre tile is only for fast painting), so size comes from content.
        options.setdefault("width", 0)
        options.setdefault("height", 0)
    style.element_create(name, "image", str(default), *spec, **options)


def _button_images(fill, border, hover, pressed, disabled_fill, disabled_border, focus_border):
    return (
        _box(fill, border),
        [
            (("disabled",), _box(disabled_fill, disabled_border)),
            (("pressed",), _box(pressed, border)),
            (("active",), _box(hover, border)),
            (("focus",), _box(fill, focus_border, border_px=max(2, round(_scale * 1.5)))),
        ],
    )


def apply(root: tk.Misc) -> None:
    """Apply a flat, rounded, light theme process-wide. ttk.Style() is global,
    so this only needs to run once at startup and every Toplevel dialog picks
    it up automatically; only each window's raw background (not a
    ttk-managed property) needs to be set individually."""
    global _scale, _root, FONT_FAMILY, FONT, FONT_BOLD, FONT_SMALL, FONT_LARGE
    _root = root
    _scale = root.winfo_fpixels("1i") / 96

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    # The platform's own UI font, rather than a hard-coded family that only
    # exists on one OS.
    FONT_FAMILY = tkfont.nametofont("TkDefaultFont").actual("family")
    FONT = (FONT_FAMILY, 10)
    FONT_BOLD = (FONT_FAMILY, 10, "bold")
    FONT_SMALL = (FONT_FAMILY, 9)
    FONT_LARGE = (FONT_FAMILY, 11)
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
        tkfont.nametofont(name).configure(family=FONT_FAMILY, size=10)

    style.configure(".", background=BG, foreground=FG, font=FONT, bordercolor=BORDER, focuscolor=BG)
    # clam tints a widget's own background in the disabled/active states,
    # which shows through the transparent corners of the rounded images.
    style.map(".", background=[("disabled", BG), ("active", BG), ("pressed", BG)])
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("TSeparator", background=BORDER)

    _style_buttons(style)
    _style_inputs(style)
    _style_radiobutton(style)
    _style_scrollbar(style)
    _style_card(style)

    # Dropdown list of a Combobox is a plain Tk listbox, not ttk-styled.
    root.option_add("*TCombobox*Listbox.background", SURFACE)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", SELECTION)
    root.option_add("*TCombobox*Listbox.selectForeground", FG)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    root.option_add("*TCombobox*Listbox.highlightThickness", 1)
    root.option_add("*TCombobox*Listbox.highlightColor", BORDER)
    root.option_add("*TCombobox*Listbox.highlightBackground", BORDER)
    root.option_add("*TCombobox*Listbox.font", FONT)


def _style_buttons(style: ttk.Style) -> None:
    pad = (px(14), px(FIELD_PAD_TOP), px(14), px(FIELD_PAD_BOTTOM))

    default, states = _button_images(
        SURFACE, BUTTON_BORDER, BUTTON_HOVER, BUTTON_PRESSED, DISABLED_FILL, BORDER, ACCENT
    )
    _image_element(style, "Round.Button.field", default, *states, border=(px(6),) * 4, sticky="nswe")
    style.layout("TButton", [
        ("Round.Button.field", {"sticky": "nswe", "children": [
            ("Button.padding", {"sticky": "nswe", "children": [
                ("Button.label", {"sticky": "nswe"}),
            ]}),
        ]}),
    ])
    style.configure("TButton", padding=pad, anchor="center", foreground=FG, width=-6)
    style.map(
        "TButton",
        foreground=[("disabled", DISABLED_FG)],
        background=[("disabled", BG), ("pressed", BG), ("active", BG)],
    )

    default, states = _button_images(
        ACCENT, ACCENT, ACCENT_HOVER, ACCENT_PRESSED, ACCENT_DISABLED, ACCENT_DISABLED, ACCENT_PRESSED
    )
    _image_element(style, "Round.Accent.field", default, *states, border=(px(6),) * 4, sticky="nswe")
    style.layout("Accent.TButton", [
        ("Round.Accent.field", {"sticky": "nswe", "children": [
            ("Button.padding", {"sticky": "nswe", "children": [
                ("Button.label", {"sticky": "nswe"}),
            ]}),
        ]}),
    ])
    style.configure("Accent.TButton", padding=pad, anchor="center", foreground="#ffffff", width=-6)
    style.map(
        "Accent.TButton",
        foreground=[("disabled", "#ffffff")],
        background=[("disabled", BG), ("pressed", BG), ("active", BG)],
    )


def _chevron(direction: str, color: str, width: int, height: int, reach: float) -> tk.PhotoImage:
    return photo(
        ("chevron", direction, color, width, height),
        lambda: render.chevron(width, height, direction, color, max(1.5, 1.6 * _scale), reach),
    )


def _style_inputs(style: ttk.Style) -> None:
    border = (px(6),) * 4
    field = _box(SURFACE, BORDER)
    states = [
        (("disabled",), _box(DISABLED_FILL, BORDER)),
        (("focus",), _box(SURFACE, ACCENT, border_px=max(2, round(_scale * 1.5)))),
    ]
    for name in ("Round.Entry.field", "Round.Combobox.field", "Round.Spinbox.field"):
        _image_element(style, name, field, *states, border=border, sticky="nswe")

    style.layout("TEntry", [
        ("Round.Entry.field", {"sticky": "nswe", "children": [
            ("Entry.padding", {"sticky": "nswe", "children": [
                ("Entry.textarea", {"sticky": "nswe"}),
            ]}),
        ]}),
    ])
    common = dict(
        fieldbackground=SURFACE, foreground=FG,
        selectbackground=SELECTION, selectforeground=FG, insertcolor=FG,
    )
    style.configure("TEntry", padding=(px(8), px(FIELD_PAD_TOP), px(8), px(FIELD_PAD_BOTTOM)), **common)
    style.map("TEntry", fieldbackground=[("disabled", DISABLED_FILL)], foreground=[("disabled", DISABLED_FG)])

    arrow_w, arrow_h = px(26), px(20)
    reach = px(4.5)
    _image_element(
        style, "Round.Combobox.arrow",
        _chevron("down", FG_MUTED, arrow_w, arrow_h, reach),
        (("disabled",), _chevron("down", DISABLED_FG, arrow_w, arrow_h, reach)),
        sticky="",
    )
    style.layout("TCombobox", [
        ("Round.Combobox.field", {"sticky": "nswe", "children": [
            ("Round.Combobox.arrow", {"side": "right", "sticky": "ns"}),
            ("Combobox.padding", {"expand": "1", "sticky": "nswe", "children": [
                ("Combobox.textarea", {"sticky": "nswe"}),
            ]}),
        ]}),
    ])
    style.configure("TCombobox", padding=(px(8), px(FIELD_PAD_TOP), px(8), px(FIELD_PAD_BOTTOM)), arrowsize=arrow_w, **common)
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", SURFACE), ("disabled", DISABLED_FILL)],
        foreground=[("disabled", DISABLED_FG)],
        selectbackground=[("readonly", SURFACE)],
        selectforeground=[("readonly", FG)],
    )

    spin_w, spin_h = px(22), px(10)
    spin_reach = px(3.5)
    for direction in ("up", "down"):
        _image_element(
            style, f"Round.Spinbox.{direction}arrow",
            _chevron(direction, FG_MUTED, spin_w, spin_h, spin_reach),
            (("disabled",), _chevron(direction, DISABLED_FG, spin_w, spin_h, spin_reach)),
            sticky="",
        )
    style.layout("TSpinbox", [
        ("Round.Spinbox.field", {"side": "top", "sticky": "we", "children": [
            ("null", {"side": "right", "sticky": "", "children": [
                ("Round.Spinbox.uparrow", {"side": "top", "sticky": "e"}),
                ("Round.Spinbox.downarrow", {"side": "bottom", "sticky": "e"}),
            ]}),
            ("Spinbox.padding", {"sticky": "nswe", "children": [
                ("Spinbox.textarea", {"sticky": "nswe"}),
            ]}),
        ]}),
    ])
    style.configure("TSpinbox", padding=(px(8), px(FIELD_PAD_TOP), px(8), px(FIELD_PAD_BOTTOM)), **common)


def _style_radiobutton(style: ttk.Style) -> None:
    size = px(14)
    margin = px(6)

    def indicator(color: str, selected: bool) -> tk.PhotoImage:
        return photo(
            ("radio", color, selected, size, margin),
            lambda: render.radio(size, color, selected, _scale, margin),
        )

    _image_element(
        style, "Round.Radiobutton.indicator",
        indicator(RADIO_OFF, False),
        (("disabled",), indicator(DISABLED_FG, False)),
        (("selected",), indicator(RADIO_ON, True)),
        sticky="",
    )
    style.layout("TRadiobutton", [
        ("Radiobutton.padding", {"sticky": "nswe", "children": [
            ("Round.Radiobutton.indicator", {"side": "left", "sticky": ""}),
            ("Radiobutton.label", {"side": "left", "sticky": "nswe"}),
        ]}),
    ])
    style.configure("TRadiobutton", padding=(0, px(2)), foreground=FG)
    style.map("TRadiobutton", foreground=[("disabled", DISABLED_FG)])


def _style_scrollbar(style: ttk.Style) -> None:
    r = px(4)
    size = 2 * r + px(40)
    thumb = lambda color: photo(("thumb", color, r), lambda: render.rounded_rect(size, size, r, color))
    _image_element(
        style, "Round.Scrollbar.thumb",
        thumb("#cfd4db"),
        (("pressed",), thumb("#9aa3af")),
        (("active",), thumb("#b3bac4")),
        border=(r,) * 4, sticky="nswe", width=2 * r + 2,
    )
    style.layout("Vertical.TScrollbar", [
        ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Round.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
        ]}),
    ])
    style.configure("Vertical.TScrollbar", troughcolor=SURFACE, background=SURFACE, bordercolor=SURFACE,
                    relief="flat", borderwidth=0)


def _style_card(style: ttk.Style) -> None:
    """A white rounded panel with a thin border, for lists."""
    _image_element(
        style, "Round.Card.field", _box(SURFACE, BORDER, radius=9, center=px(160)),
        # No minimum size: a frame whose own layout demands more room than its
        # children do makes Tk's geometry manager and the frame keep
        # overriding each other's size request forever.
        border=(px(9),) * 4, sticky="nswe", width=0, height=0,
    )
    style.layout("Card.TFrame", [("Round.Card.field", {"sticky": "nswe"})])
