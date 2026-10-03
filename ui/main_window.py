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
from ssh import launch_session, remove_session_profile
from ui import theme
from ui.session_editor import SessionEditor

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

    def run(self) -> None:
        self._root.mainloop()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(1, weight=1)

        # Search bar
        search_frame = ttk.Frame(self._root, padding=(4, 4, 4, 0))
        search_frame.grid(row=0, column=0, sticky="ew")
        search_frame.columnconfigure(1, weight=1)

        ttk.Label(search_frame, text="Search").grid(row=0, column=0, padx=(0, 6))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_change)
        self._search_entry = ttk.Entry(search_frame, textvariable=self._search_var)
        self._search_entry.grid(row=0, column=1, sticky="ew")

        # Session tree
        tree_frame = ttk.Frame(self._root, padding=9)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        self._tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
        self._tree.tag_configure("folder", font=theme.FONT_BOLD)
        self._tree_scrollbar = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self._tree.yview
        )
        self._tree.configure(yscrollcommand=self._on_tree_yscroll)
        self._tree.grid(row=0, column=0, sticky="nsew")
        self._tree_scrollbar.grid(row=0, column=1, sticky="ns")

        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self._tree.bind("<Double-1>", self._on_double_click)

        # Button bar
        btn_frame = ttk.Frame(self._root, padding=(4, 0, 4, 9))
        btn_frame.grid(row=2, column=0, sticky="ew")

        buttons = [
            ("Add",      self._cmd_add,     "TButton"),
            ("Edit",     self._cmd_edit,     "TButton"),
            ("Connect",  self._cmd_connect,  "TButton"),
            ("Settings", self._cmd_settings, "TButton"),
            ("Exit",     self._on_close,     "TButton"),
        ]
        for col in range(len(buttons)):
            btn_frame.columnconfigure(col, weight=1, uniform="btn")

        for col, (text, cmd, style_name) in enumerate(buttons):
            ttk.Button(btn_frame, text=text, command=cmd, style=style_name).grid(
                row=0, column=col, sticky="ew", padx=2
            )

    def _on_tree_yscroll(self, first: str, last: str) -> None:
        """Standard ttk idiom for an auto-hiding scrollbar: yscrollcommand
        fires with the visible fraction of content on every content or
        size change, so hiding it exactly when first<=0 and last>=1 (the
        whole tree already fits) keeps this correct across window resizes
        too, not just a fixed item count."""
        self._tree_scrollbar.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self._tree_scrollbar.grid_remove()
        else:
            self._tree_scrollbar.grid()

    def _apply_geometry(self) -> None:
        s = self._settings
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        x = max(0, (screen_w - s.window_width) // 2)
        y = max(0, (screen_h - s.window_height) // 2)
        self._root.geometry(f"{s.window_width}x{s.window_height}+{x}+{y}")

    def _bind_keys(self) -> None:
        self._root.bind("<Return>",    lambda _: self._cmd_connect())
        self._root.bind("<Control-n>", lambda _: self._cmd_add())
        self._root.bind("<Control-e>", lambda _: self._cmd_edit())
        self._root.bind("<Control-f>", lambda _: self._search_entry.focus_set())
        self._root.bind("<Escape>",    lambda _: self._clear_search())
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Session loading and tree building
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

        self._rebuild_tree()

    def _rebuild_tree(self) -> None:
        prev_id = self._selected_session.id if self._selected_session else None
        self._tree.delete(*self._tree.get_children())
        self._selected_session = None

        for session in self._all_sessions:
            parent_iid = ""
            if session.folder:
                parent_iid = self._ensure_folder_path(session.folder)
            iid = f"s:{session.id}"
            self._tree.insert(parent_iid, "end", iid=iid, text=session.name)

        self._expand_all()

        if prev_id:
            iid = f"s:{prev_id}"
            if self._tree.exists(iid):
                self._tree.selection_set(iid)
                self._tree.see(iid)
                self._selected_session = next(
                    (s for s in self._all_sessions if s.id == prev_id), None
                )

    def _ensure_folder_path(self, folder: str) -> str:
        parts = folder.split("/")
        for depth in range(len(parts)):
            path = "/".join(parts[: depth + 1])
            iid = f"f:{path}"
            if not self._tree.exists(iid):
                parent_path = "/".join(parts[:depth])
                parent_iid = f"f:{parent_path}" if parent_path else ""
                self._tree.insert(
                    parent_iid, "end", iid=iid, text=parts[depth], tags=("folder",)
                )
        return f"f:{folder}"

    def _expand_all(self) -> None:
        def _expand(iid: str) -> None:
            self._tree.item(iid, open=True)
            for child in self._tree.get_children(iid):
                _expand(child)

        for child in self._tree.get_children():
            _expand(child)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_search_change(self, *_: object) -> None:
        self._load_sessions(self._search_var.get().strip())

    def _clear_search(self) -> None:
        self._search_var.set("")
        self._search_entry.focus_set()

    def _on_tree_select(self, _event: object = None) -> None:
        sel = self._tree.selection()
        if not sel:
            self._selected_session = None
            return
        iid = sel[0]
        if iid.startswith("s:"):
            sid = int(iid[2:])
            self._selected_session = next(
                (s for s in self._all_sessions if s.id == sid), None
            )
        else:
            self._selected_session = None

    def _on_double_click(self, _event: object = None) -> None:
        if self._selected_session:
            self._cmd_connect()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _cmd_add(self) -> None:
        editor = SessionEditor(self._root, identity_key_dir=self._settings.identity_key_dir)
        session = editor.show()
        if session:
            try:
                self._db.add_session(session)
                self._load_sessions(self._search_var.get().strip())
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
        if not self._selected_session:
            return
        try:
            launch_session(
                self._selected_session,
                identity_key_dir=self._settings.identity_key_dir,
                font_face=self._settings.font_face,
                font_size=self._settings.font_size,
                terminal_cols=self._settings.terminal_cols,
                terminal_rows=self._settings.terminal_rows,
            )
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
        if not last_id:
            return
        iid = f"s:{last_id}"
        if self._tree.exists(iid):
            self._tree.selection_set(iid)
            self._tree.see(iid)
            self._selected_session = next(
                (s for s in self._all_sessions if s.id == last_id), None
            )

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
