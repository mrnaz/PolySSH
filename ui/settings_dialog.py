import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Optional

from database import Database
from export_sites import export_filezilla, export_generic_uri, export_winscp
from settings import SettingsManager
from ui import center_on_parent, section_header, theme

_PAD = {"padx": 6, "pady": 4}

_EXPORT_FORMATS = {
    "winscp": ("WinSCP (.ini)", ".ini", [("WinSCP ini", "*.ini"), ("All files", "*.*")]),
    "filezilla": ("FileZilla (.xml)", ".xml", [("FileZilla XML", "*.xml"), ("All files", "*.*")]),
    "generic": ("Generic sftp:// list (.txt)", ".txt", [("Text file", "*.txt"), ("All files", "*.*")]),
}


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, settings_mgr: SettingsManager, db: Database) -> None:
        super().__init__(parent)
        self.title("Settings")
        self.resizable(False, False)
        self.configure(background=theme.BG)
        self.grab_set()
        self.transient(parent)

        self._settings_mgr = settings_mgr
        self._db = db
        self._build_ui()
        self._populate()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        center_on_parent(self)
        self.focus_set()

    def show(self) -> None:
        self.wait_window()

    def _build_ui(self) -> None:
        content = ttk.Frame(self, padding=theme.px(18))
        content.pack(fill="both", expand=True)
        content.columnconfigure(1, weight=1)

        row = section_header(content, 0, "Terminal Font", columnspan=2)
        ttk.Label(content, text="(Windows only)", foreground=theme.FG_MUTED).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4)
        )
        row += 1

        ttk.Label(content, text="Font Face:").grid(row=row, column=0, sticky="e", **_PAD)
        self._font_face_var = tk.StringVar()
        ttk.Entry(content, textvariable=self._font_face_var, width=28).grid(
            row=row, column=1, sticky="ew", **_PAD
        )
        row += 1

        ttk.Label(content, text="Font Size:").grid(row=row, column=0, sticky="e", **_PAD)
        self._font_size_var = tk.StringVar()
        ttk.Spinbox(
            content, textvariable=self._font_size_var,
            from_=6, to=72, increment=1, width=6,
        ).grid(row=row, column=1, sticky="w", **_PAD)
        row += 1

        row = section_header(content, row, "Terminal Size", columnspan=2)

        ttk.Label(content, text="Columns:").grid(row=row, column=0, sticky="e", **_PAD)
        self._cols_var = tk.StringVar()
        ttk.Spinbox(
            content, textvariable=self._cols_var,
            from_=40, to=500, increment=1, width=6,
        ).grid(row=row, column=1, sticky="w", **_PAD)
        row += 1

        ttk.Label(content, text="Rows:").grid(row=row, column=0, sticky="e", **_PAD)
        self._rows_var = tk.StringVar()
        ttk.Spinbox(
            content, textvariable=self._rows_var,
            from_=10, to=200, increment=1, width=6,
        ).grid(row=row, column=1, sticky="w", **_PAD)
        row += 1

        ttk.Label(content, text="(leave blank to use terminal defaults)", foreground=theme.FG_MUTED).grid(
            row=row, column=1, sticky="w", padx=6, pady=(0, 6)
        )
        row += 1

        row = section_header(content, row, "SSH Keys", columnspan=2)

        ttk.Label(content, text="Key Directory:").grid(row=row, column=0, sticky="e", **_PAD)
        key_dir_frame = ttk.Frame(content)
        key_dir_frame.grid(row=row, column=1, sticky="ew", **_PAD)
        key_dir_frame.columnconfigure(0, weight=1)
        self._key_dir_var = tk.StringVar()
        ttk.Entry(key_dir_frame, textvariable=self._key_dir_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(key_dir_frame, text="Browse…", command=self._browse_key_dir).grid(
            row=0, column=1, padx=(4, 0)
        )
        row += 1

        row = section_header(content, row, "Export Sites", columnspan=2)

        self._export_format_var = tk.StringVar(value="winscp")
        for key, (label, _ext, _filetypes) in _EXPORT_FORMATS.items():
            ttk.Radiobutton(
                content, text=label, value=key, variable=self._export_format_var
            ).grid(row=row, column=1, sticky="w", padx=6, pady=1)
            row += 1

        export_btn_frame = ttk.Frame(content)
        export_btn_frame.grid(row=row, column=1, sticky="w", padx=6, pady=(4, 6))
        ttk.Button(export_btn_frame, text="Export…", command=self._export_sites).pack(side="left")
        row += 1

        btn_frame = ttk.Frame(content, padding=(6, 4))
        btn_frame.grid(row=row, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(btn_frame, text="Save", command=self._save, style="Accent.TButton").pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=self._cancel).pack(side="left", padx=4)

        self.bind("<Return>", lambda _: self._save())
        self.bind("<Escape>", lambda _: self._cancel())

    def _populate(self) -> None:
        s = self._settings_mgr.settings
        self._font_face_var.set(s.font_face or "")
        self._font_size_var.set(str(s.font_size) if s.font_size else "")
        self._cols_var.set(str(s.terminal_cols) if s.terminal_cols else "")
        self._rows_var.set(str(s.terminal_rows) if s.terminal_rows else "")
        self._key_dir_var.set(s.identity_key_dir or "")

    def _browse_key_dir(self) -> None:
        import os
        initial = os.path.expanduser(self._key_dir_var.get().strip() or "~")
        path = filedialog.askdirectory(title="Select Key Directory", parent=self, initialdir=initial)
        if path:
            self._key_dir_var.set(path)

    def _export_sites(self) -> None:
        fmt = self._export_format_var.get()
        label, ext, filetypes = _EXPORT_FORMATS[fmt]

        path = filedialog.asksaveasfilename(
            title=f"Export Sites — {label}",
            parent=self,
            defaultextension=ext,
            filetypes=filetypes,
            initialfile=f"polyssh-sites{ext}",
        )
        if not path:
            return

        try:
            sessions = self._db.all_sessions()
            identity_key_dir = self._key_dir_var.get().strip() or None

            if fmt == "winscp":
                content = export_winscp(sessions, identity_key_dir)
            elif fmt == "filezilla":
                content = export_filezilla(sessions, identity_key_dir)
            else:
                content = export_generic_uri(sessions)

            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

            messagebox.showinfo(
                "Export Sites", f"Exported {len(sessions)} session(s) to:\n{path}", parent=self
            )
        except Exception as e:
            messagebox.showerror("Export Failed", f"Failed to export sites:\n{e}", parent=self)

    def _save(self) -> None:
        s = self._settings_mgr.settings
        s.font_face         = self._font_face_var.get().strip() or None
        s.font_size         = _parse_int(self._font_size_var.get(), 6,  72)
        s.terminal_cols     = _parse_int(self._cols_var.get(),     40, 500)
        s.terminal_rows     = _parse_int(self._rows_var.get(),     10, 200)
        s.identity_key_dir  = self._key_dir_var.get().strip() or None
        self._settings_mgr.save()
        self.destroy()

    def _cancel(self) -> None:
        self.destroy()


def _parse_int(value: str, lo: int, hi: int) -> Optional[int]:
    try:
        n = int(value.strip())
        return n if lo <= n <= hi else None
    except ValueError:
        return None
