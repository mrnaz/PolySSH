import bisect
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from tkinter import ttk
from typing import Callable, Optional

from models import Session
from ui import render, theme


@dataclass
class _Row:
    kind: str  # "folder" | "session"
    key: tuple
    depth: int
    text: str
    ancestors: tuple[str, ...]
    session: Optional[Session] = None
    y0: int = 0
    y1: int = 0


def _luminance(color: str) -> float:
    r, g, b = render.hex_rgb(color)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


class SessionList(ttk.Frame):
    """Folders and sessions as roomy rows: a dot in the session's own colour,
    its name with user@host beneath, and a badge for its speed-dial number.
    Drawn on a canvas so rows can look like a modern list rather than a
    tree widget; selection, hover, folder collapsing and keyboard
    navigation are all handled here."""

    def __init__(
        self,
        parent: tk.Misc,
        on_select: Callable[[Optional[Session]], None],
        on_activate: Callable[[Session], None],
    ) -> None:
        super().__init__(parent, style="Card.TFrame", padding=theme.px(3))
        self._on_select = on_select
        self._on_activate = on_activate

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._canvas = tk.Canvas(
            self, background=theme.SURFACE, highlightthickness=0, borderwidth=0,
            width=1, height=1, takefocus=1, yscrollincrement=theme.px(16),
        )
        self._scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._on_yscroll)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        self._scrollbar.grid(row=0, column=1, sticky="ns")

        family = theme.FONT_FAMILY
        self._name_font = tkfont.Font(family=family, size=10)
        self._sub_font = tkfont.Font(family=family, size=9)
        self._folder_font = tkfont.Font(family=family, size=10, weight="bold")
        self._badge_font = tkfont.Font(family=family, size=9, weight="bold")

        self._all_rows: list[_Row] = []
        self._rows: list[_Row] = []
        self._row_tops: list[int] = []
        self._collapsed: set[str] = set()
        self._expand_all = False
        self._selected_key: Optional[tuple] = None
        self._hover_index: Optional[int] = None
        self._width = 0
        self._total_height = 0
        self._hl_cache: dict[tuple, tk.PhotoImage] = {}
        self._hl_items: dict[str, int] = {}

        c = self._canvas
        c.bind("<Configure>", self._on_configure)
        c.bind("<Button-1>", self._on_click)
        c.bind("<Double-Button-1>", self._on_double_click)
        c.bind("<Motion>", self._on_motion)
        c.bind("<Leave>", self._on_leave)
        c.bind("<MouseWheel>", self._on_wheel)
        c.bind("<Button-4>", self._on_wheel)
        c.bind("<Button-5>", self._on_wheel)
        c.bind("<Up>", lambda _e: self._move(-1))
        c.bind("<Down>", lambda _e: self._move(1))
        c.bind("<Home>", lambda _e: self._move(-10**9))
        c.bind("<End>", lambda _e: self._move(10**9))
        c.bind("<Left>", lambda _e: self._collapse_or_parent())
        c.bind("<Right>", lambda _e: self._expand_selected())
        c.bind("<space>", lambda _e: self.activate_selected())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def focus_set(self) -> None:
        self._canvas.focus_set()

    def selected_session(self) -> Optional[Session]:
        row = self._row_for_key(self._selected_key)
        return row.session if row else None

    def set_sessions(self, sessions: list[Session], expand_all: bool = False) -> None:
        self._expand_all = expand_all
        self._build_rows(sessions)
        if self._row_for_key(self._selected_key, in_all=True) is None:
            self._selected_key = None
        self._render()
        if self._index_of(self._selected_key) is None:
            self._selected_key = None
        self._on_select(self.selected_session())

    def select_session(self, session_id: int) -> None:
        key = ("s", session_id)
        row = self._row_for_key(key, in_all=True)
        if row is None:
            return
        self._collapsed.difference_update(row.ancestors)
        self._render()
        self._select(key)
        self._see(key)

    def activate_selected(self) -> None:
        row = self._row_for_key(self._selected_key)
        if row is None:
            return
        if row.kind == "folder":
            self._toggle(row.key[1])
        elif row.session is not None:
            self._on_activate(row.session)

    def move_selection(self, delta: int) -> None:
        self._move(delta)

    @property
    def visible_keys(self) -> list[tuple]:
        return [row.key for row in self._rows]

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------

    def _build_rows(self, sessions: list[Session]) -> None:
        rows: list[_Row] = []
        seen_folders: set[str] = set()
        for session in sessions:
            parts = session.folder.split("/") if session.folder else []
            for depth in range(len(parts)):
                path = "/".join(parts[: depth + 1])
                if path not in seen_folders:
                    seen_folders.add(path)
                    ancestors = tuple("/".join(parts[:d + 1]) for d in range(depth))
                    rows.append(_Row("folder", ("f", path), depth, parts[depth], ancestors))
            ancestors = tuple("/".join(parts[:d + 1]) for d in range(len(parts)))
            rows.append(_Row("session", ("s", session.id), len(parts), session.name, ancestors, session))
        self._all_rows = rows

    def _visible_rows(self) -> list[_Row]:
        if self._expand_all:
            return list(self._all_rows)
        return [r for r in self._all_rows if not any(a in self._collapsed for a in r.ancestors)]

    def _row_for_key(self, key: Optional[tuple], in_all: bool = False) -> Optional[_Row]:
        if key is None:
            return None
        for row in (self._all_rows if in_all else self._rows):
            if row.key == key:
                return row
        return None

    def _index_of(self, key: Optional[tuple]) -> Optional[int]:
        for i, row in enumerate(self._rows):
            if row.key == key:
                return i
        return None

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _dot(self, color: Optional[str], diameter: int) -> tk.PhotoImage:
        valid = color if color and len(color) == 7 and color.startswith("#") else None
        key = ("dot", valid, diameter)
        if valid is None:
            return theme.photo(key, lambda: render.ring(diameter, "#c3c9d1", max(1.5, 1.5 * theme._scale)))
        border = theme.BUTTON_BORDER if _luminance(valid) > 0.85 else valid
        bw = max(1, round(theme._scale))
        return theme.photo(key, lambda: render.rounded_rect(diameter, diameter, diameter / 2, valid, border, bw))

    def _badge(self, width: int, height: int) -> tk.PhotoImage:
        return theme.photo(
            ("badge", width, height),
            lambda: render.rounded_rect(width, height, height / 2, theme.BADGE),
        )

    def _chevron(self, direction: str, size: int) -> tk.PhotoImage:
        return theme.photo(
            ("list-chevron", direction, size),
            lambda: render.chevron(size, size, direction, theme.FG_MUTED, max(1.5, 1.7 * theme._scale), size * 0.26),
        )

    def _elide(self, text: str, font: tkfont.Font, max_px: int) -> str:
        if max_px <= 0:
            return ""
        if font.measure(text) <= max_px:
            return text
        while len(text) > 1 and font.measure(text + "…") > max_px:
            text = text[:-1]
        return text + "…"

    def _render(self) -> None:
        c = self._canvas
        self._rows = self._visible_rows()
        width = c.winfo_width()
        if width < 40:
            return  # not laid out yet; <Configure> will call back
        self._width = width
        self._hl_cache.clear()
        c.delete("all")
        self._hl_items = {
            "sel": c.create_image(0, 0, anchor="nw", state="hidden", tags=("hl",)),
            "hover": c.create_image(0, 0, anchor="nw", state="hidden", tags=("hl",)),
        }

        if not self._rows:
            c.create_text(
                width / 2, theme.px(40), text="No sessions", fill=theme.FG_MUTED, font=self._name_font,
            )
            self._row_tops = []
            self._total_height = theme.px(80)
            c.configure(scrollregion=(0, 0, width, self._total_height))
            return

        pad_x = theme.px(6)
        text_pad = theme.px(10)
        indent = theme.px(16)
        name_lh = self._name_font.metrics("linespace")
        folder_lh = self._folder_font.metrics("linespace")
        session_h = name_lh + theme.px(10)
        folder_h = folder_lh + theme.px(8)
        dot_d = theme.px(9)
        chev = theme.px(13)
        name_ascent = self._name_font.metrics("ascent")
        name_descent = self._name_font.metrics("descent")
        sub_descent = self._sub_font.metrics("descent")

        y = theme.px(4)
        for i, row in enumerate(self._rows):
            if row.kind == "folder" and i > 0:
                y += theme.px(3)
            height = folder_h if row.kind == "folder" else session_h
            row.y0, row.y1 = y, y + height
            mid = y + height / 2
            x = pad_x + text_pad + row.depth * indent
            right = width - pad_x - text_pad

            if row.kind == "folder":
                collapsed = (not self._expand_all) and row.key[1] in self._collapsed
                c.create_image(x, mid, anchor="w", image=self._chevron("right" if collapsed else "down", chev))
                c.create_text(
                    x + chev + theme.px(4), mid, anchor="w", font=self._folder_font, fill=theme.FG,
                    text=self._elide(row.text, self._folder_font, right - x - chev),
                )
            else:
                session = row.session
                assert session is not None
                c.create_image(x, mid, anchor="w", image=self._dot(session.background_color, dot_d))
                text_x = x + dot_d + theme.px(10)
                limit = right
                if session.speed_dial:
                    bw, bh = theme.px(20), theme.px(18)
                    c.create_image(right - bw, mid, anchor="w", image=self._badge(bw, bh))
                    c.create_text(
                        right - bw / 2, mid, text=str(session.speed_dial), font=self._badge_font,
                        fill=theme.FG_MUTED,
                    )
                    limit = right - bw - theme.px(8)

                # One line: the name, then user@host in muted text. The name
                # keeps priority (up to ~65% of the width) if both can't fit.
                avail = limit - text_x
                gap = theme.px(10)
                name_w = self._name_font.measure(session.name)
                subtitle = self._subtitle(session)
                sub_w = self._sub_font.measure(subtitle)
                if name_w + gap + sub_w <= avail:
                    name_max = name_w
                else:
                    name_max = min(name_w, int(avail * 0.65))
                name_text = self._elide(session.name, self._name_font, name_max)
                baseline = mid + (name_ascent - name_descent) / 2
                c.create_text(
                    text_x, baseline + name_descent, anchor="sw", font=self._name_font, fill=theme.FG,
                    text=name_text,
                )
                sub_x = text_x + self._name_font.measure(name_text) + gap
                if limit - sub_x >= theme.px(48):
                    c.create_text(
                        sub_x, baseline + sub_descent, anchor="sw", font=self._sub_font, fill=theme.FG_MUTED,
                        text=self._elide(subtitle, self._sub_font, limit - sub_x),
                    )
            y += height

        self._row_tops = [r.y0 for r in self._rows]
        self._total_height = y + theme.px(4)
        c.configure(scrollregion=(0, 0, width, self._total_height))
        self._place_highlights()

    @staticmethod
    def _subtitle(session: Session) -> str:
        target = f"{session.username}@{session.hostname}" if session.username else session.hostname
        return target if session.port == 22 else f"{target}:{session.port}"

    def _highlight_image(self, kind: str, width: int, height: int) -> tk.PhotoImage:
        key = (kind, width, height)
        image = self._hl_cache.get(key)
        if image is None:
            color = theme.SELECTION if kind == "sel" else theme.HOVER
            png = render.rounded_rect(width, height, theme.px(6), color)
            image = tk.PhotoImage(master=self, data=render.to_tk_data(png))
            self._hl_cache[key] = image
        return image

    def _place_highlights(self) -> None:
        c = self._canvas
        pad_x = theme.px(6)
        targets = {
            "sel": self._index_of(self._selected_key),
            "hover": self._hover_index if self._hover_index != self._index_of(self._selected_key) else None,
        }
        for kind, index in targets.items():
            item = self._hl_items.get(kind)
            if item is None:
                continue
            if index is None or index >= len(self._rows):
                c.itemconfigure(item, state="hidden")
                continue
            row = self._rows[index]
            image = self._highlight_image(kind, self._width - 2 * pad_x, row.y1 - row.y0)
            c.itemconfigure(item, image=image, state="normal")
            c.coords(item, pad_x, row.y0)
        c.tag_lower("hl")

    # ------------------------------------------------------------------
    # Selection and navigation
    # ------------------------------------------------------------------

    def _select(self, key: Optional[tuple]) -> None:
        if key == self._selected_key:
            return
        self._selected_key = key
        self._place_highlights()
        self._on_select(self.selected_session())

    def _toggle(self, path: str) -> None:
        moved = False
        if path in self._collapsed:
            self._collapsed.discard(path)
        else:
            self._collapsed.add(path)
            row = self._row_for_key(self._selected_key, in_all=True)
            if row is not None and path in row.ancestors:
                self._selected_key = ("f", path)
                moved = True
        self._render()
        if moved:
            self._on_select(self.selected_session())

    def _see(self, key: tuple) -> None:
        index = self._index_of(key)
        if index is None or not self._total_height:
            return
        row = self._rows[index]
        c = self._canvas
        top, view = c.canvasy(0), c.winfo_height()
        if row.y0 < top:
            c.yview_moveto(max(0.0, (row.y0 - theme.px(6)) / self._total_height))
        elif row.y1 > top + view:
            c.yview_moveto((row.y1 + theme.px(6) - view) / self._total_height)

    def _move(self, delta: int) -> None:
        if not self._rows:
            return
        current = self._index_of(self._selected_key)
        if current is None:
            target = 0 if delta > 0 else len(self._rows) - 1
        else:
            target = max(0, min(len(self._rows) - 1, current + delta))
        key = self._rows[target].key
        self._select(key)
        self._see(key)

    def _collapse_or_parent(self) -> None:
        row = self._row_for_key(self._selected_key)
        if row is None:
            return
        if row.kind == "folder" and row.key[1] not in self._collapsed and not self._expand_all:
            self._toggle(row.key[1])
        elif row.ancestors:
            parent = ("f", row.ancestors[-1])
            self._select(parent)
            self._see(parent)

    def _expand_selected(self) -> None:
        row = self._row_for_key(self._selected_key)
        if row and row.kind == "folder" and row.key[1] in self._collapsed:
            self._toggle(row.key[1])

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _row_at(self, canvas_y: float) -> Optional[int]:
        if not self._rows:
            return None
        index = bisect.bisect_right(self._row_tops, canvas_y) - 1
        if index < 0:
            return None
        return index if canvas_y < self._rows[index].y1 else None

    def _on_configure(self, event: tk.Event) -> None:
        if event.width != self._width:
            self._render()

    def _on_click(self, event: tk.Event) -> None:
        self._canvas.focus_set()
        index = self._row_at(self._canvas.canvasy(event.y))
        if index is None:
            return
        row = self._rows[index]
        self._select(row.key)
        if row.kind == "folder":
            self._toggle(row.key[1])

    def _on_double_click(self, event: tk.Event) -> None:
        index = self._row_at(self._canvas.canvasy(event.y))
        if index is None:
            return
        row = self._rows[index]
        if row.kind == "session" and row.session is not None:
            self._on_activate(row.session)

    def _on_motion(self, event: tk.Event) -> None:
        index = self._row_at(self._canvas.canvasy(event.y))
        if index != self._hover_index:
            self._hover_index = index
            self._place_highlights()

    def _on_leave(self, _event: tk.Event) -> None:
        if self._hover_index is not None:
            self._hover_index = None
            self._place_highlights()

    def _on_wheel(self, event: tk.Event) -> None:
        if event.num == 4:
            steps = -1.0
        elif event.num == 5:
            steps = 1.0
        elif abs(event.delta) >= 120:
            steps = -event.delta / 120
        else:
            steps = -float(event.delta)
        lines = round(steps * 3) or (1 if steps > 0 else -1)
        self._canvas.yview_scroll(lines, "units")
        self._hover_index = None
        self._place_highlights()

    def _on_yscroll(self, first: str, last: str) -> None:
        """Auto-hide the scrollbar when everything already fits."""
        self._scrollbar.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self._scrollbar.grid_remove()
        else:
            self._scrollbar.grid()
