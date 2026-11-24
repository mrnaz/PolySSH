import logging
import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox
from typing import Optional

from database import Database
from models import Session
from settings import SettingsManager
from ssh import launch_session_with_settings, remove_session_profile
from ui import theme
from ui.session_editor import SessionEditor
from ui.session_list import SessionList

if getattr(sys, "frozen", False):
    # PyInstaller's --onefile mode extracts bundled data files to a
    # temporary directory at runtime (sys._MEIPASS); __file__ inside a
    # frozen bundle isn't a reliable filesystem path to build from.
    _ASSETS_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "assets"
else:
    _ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_ICON_ICO = _ASSETS_DIR / "icon.ico"
_ICON_PNG = _ASSETS_DIR / "icon.png"


class MainWindow:
    def __init__(
        self,
        db: Database,
        settings_mgr: SettingsManager,
        logger: logging.Logger,
    ) -> None:
        self._db = db
        self._settings_mgr = settings_mgr
        self._settings = settings_mgr.settings
        self._logger = logger

        self._selected_session: Optional[Session] = None
        self._all_sessions: list[Session] = []

        self._root = tk.Tk()
        self._root.title("PolySSH")
        self._root.minsize(320, 400)
        theme.apply(self._root)
        self._root.configure(background=theme.BG)
        # iconphoto alone reliably sets the title bar icon but Windows'
        # taskbar falls back to python.exe's icon unless a real .ico is set
        # via iconbitmap, which Windows handles as a native icon resource.
        if sys.platform == "win32" and _ICON_ICO.exists():
            self._root.iconbitmap(default=str(_ICON_ICO))
        elif _ICON_PNG.exists():
            self._root.iconphoto(True, tk.PhotoImage(file=str(_ICON_PNG)))

        self._build_ui()
        self._apply_geometry()
        self._bind_keys()
        self._load_sessions()
        self._restore_selection()
        self._search_entry.focus_set()

    def run(self) -> None:
        self._root.mainloop()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(1, weight=1)
        margin = theme.px(14)

        # Search field, with a placeholder that disappears once you type
        search_frame = ttk.Frame(self._root)
        search_frame.grid(row=0, column=0, sticky="ew", padx=margin, pady=(margin, theme.px(8)))
        search_frame.columnconfigure(0, weight=1)

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_change)
        self._search_entry = ttk.Entry(search_frame, textvariable=self._search_var)
        self._search_entry.grid(row=0, column=0, sticky="ew")
        self._search_hint = tk.Label(
            search_frame, text="Search sessions", fg=theme.DISABLED_FG, bg=theme.SURFACE, font=theme.FONT,
        )
        self._search_hint.place(in_=self._search_entry, x=theme.px(10), rely=0.5, anchor="w")
        self._search_hint.bind("<Button-1>", lambda _e: self._search_entry.focus_set())

        # Session list
        self._list = SessionList(self._root, self._on_list_select, self._connect)
        self._list.grid(row=1, column=0, sticky="nsew", padx=margin)

        # Button bar: everyday actions left, the primary action right
        self._btn_frame = ttk.Frame(self._root)
        self._btn_frame.grid(row=2, column=0, sticky="ew", padx=margin, pady=(theme.px(10), margin))
        gap = theme.px(8)
        for text, command in (("Add", self._cmd_add), ("Edit", self._cmd_edit), ("Settings", self._cmd_settings)):
            ttk.Button(self._btn_frame, text=text, command=command).pack(side="left", padx=(0, gap))
        ttk.Button(self._btn_frame, text="Connect", command=self._cmd_connect, style="Accent.TButton").pack(side="right")
        ttk.Button(self._btn_frame, text="Exit", command=self._on_close).pack(side="right", padx=(0, gap))

    def _apply_geometry(self) -> None:
        s = self._settings
        # The button bar's natural width depends on font and display scaling,
        # so a fixed default (or a width saved at another scale) can clip the
        # labels; never open narrower than the buttons need.
        self._root.update_idletasks()
        min_w = max(320, self._btn_frame.winfo_reqwidth() + 2 * theme.px(14))
        self._root.minsize(min_w, 400)
        width = max(s.window_width, min_w)
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - s.window_height) // 2)
        self._root.geometry(f"{width}x{s.window_height}+{x}+{y}")

    def _bind_keys(self) -> None:
        self._root.bind("<Return>",    lambda _: self._list.activate_selected())
        self._root.bind("<Control-n>", lambda _: self._cmd_add())
        self._root.bind("<Control-e>", lambda _: self._cmd_edit())
        self._root.bind("<Control-f>", lambda _: self._search_entry.focus_set())
        self._root.bind("<Escape>",    lambda _: self._clear_search())
        for number in range(1, 10):
            self._root.bind(f"<Control-Key-{number}>", lambda _e, n=number: self._cmd_dial(n))
        # Arrow keys steer the list even while typing in the search field, so
        # "type to filter, Down, Enter" works without touching the mouse.
        self._search_entry.bind("<Down>", lambda _e: (self._list.move_selection(1), "break")[1])
        self._search_entry.bind("<Up>", lambda _e: (self._list.move_selection(-1), "break")[1])
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Session loading
    # ------------------------------------------------------------------

    def _load_sessions(self, query: str = "") -> None:
        try:
            if query:
                self._all_sessions = self._db.search_sessions(query)
            else:
                self._all_sessions = self._db.all_sessions()
        except Exception as e:
            self._logger.error("Failed to load sessions: %s", e)
            messagebox.showerror("Database Error", f"Failed to load sessions:\n{e}")
            self._all_sessions = []

        self._list.set_sessions(self._all_sessions, expand_all=bool(query))

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_search_change(self, *_: object) -> None:
        if self._search_var.get():
            self._search_hint.place_forget()
        else:
            self._search_hint.place(in_=self._search_entry, x=theme.px(10), rely=0.5, anchor="w")
        self._load_sessions(self._search_var.get().strip())

    def _clear_search(self) -> None:
        self._search_var.set("")
        self._search_entry.focus_set()

    def _on_list_select(self, session: Optional[Session]) -> None:
        self._selected_session = session

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _speed_dial_owners(self, exclude_id: Optional[int] = None) -> dict[int, str]:
        try:
            sessions = self._db.all_sessions()
        except Exception:
            return {}
        return {
            s.speed_dial: s.name
            for s in sessions
            if s.speed_dial and s.id != exclude_id
        }

    def _cmd_add(self) -> None:
        editor = SessionEditor(
            self._root,
            identity_key_dir=self._settings.identity_key_dir,
            speed_dial_owners=self._speed_dial_owners(),
        )
        session = editor.show()
        if session:
            try:
                new_id = self._db.add_session(session)
                self._load_sessions(self._search_var.get().strip())
                self._list.select_session(new_id)
            except Exception as e:
                self._logger.error("Failed to add session: %s", e)
                messagebox.showerror("Error", f"Failed to save session:\n{e}")

    def _cmd_edit(self) -> None:
        if not self._selected_session:
            return
        editor = SessionEditor(
            self._root,
            self._selected_session,
            on_delete=self._do_delete,
            identity_key_dir=self._settings.identity_key_dir,
            speed_dial_owners=self._speed_dial_owners(self._selected_session.id),
        )
        session = editor.show()
        if session:
            try:
                self._db.update_session(session)
                self._load_sessions(self._search_var.get().strip())
            except Exception as e:
                self._logger.error("Failed to update session: %s", e)
                messagebox.showerror("Error", f"Failed to update session:\n{e}")

    def _do_delete(self) -> None:
        if not self._selected_session:
            return
        try:
            self._db.delete_session(self._selected_session.id)  # type: ignore[arg-type]
            remove_session_profile(self._selected_session.id)  # type: ignore[arg-type]
            self._selected_session = None
            self._load_sessions(self._search_var.get().strip())
        except Exception as e:
            self._logger.error("Failed to delete session: %s", e)
            messagebox.showerror("Error", f"Failed to delete session:\n{e}")

    def _cmd_connect(self) -> None:
        if self._selected_session:
            self._connect(self._selected_session)

    def _cmd_dial(self, number: int) -> None:
        try:
            session = self._db.get_by_speed_dial(number)
        except Exception as e:
            self._logger.error("Speed dial lookup failed: %s", e)
            return
        if session:
            self._connect(session)

    def _connect(self, session: Session) -> None:
        try:
            launch_session_with_settings(session, self._settings)
        except RuntimeError as e:
            messagebox.showerror("Connection Error", str(e), parent=self._root)
        except Exception as e:
            self._logger.error("Failed to launch session: %s", e)
            messagebox.showerror("Error", f"Failed to launch session:\n{e}")

    def _cmd_settings(self) -> None:
        from ui.settings_dialog import SettingsDialog
        SettingsDialog(self._root, self._settings_mgr, self._db).show()

    def _restore_selection(self) -> None:
        last_id = self._settings.last_session_id
        if last_id:
            self._list.select_session(last_id)

    def _on_close(self) -> None:
        geo = self._root.geometry()
        m = re.match(r"(\d+)x(\d+)", geo)
        if m:
            self._settings.window_width  = int(m.group(1))
            self._settings.window_height = int(m.group(2))

        if self._selected_session:
            self._settings.last_session_id = self._selected_session.id

        try:
            self._settings_mgr.save()
        except Exception as e:
            self._logger.error("Failed to save settings: %s", e)

        self._root.destroy()
