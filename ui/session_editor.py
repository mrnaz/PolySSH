import base64
import math
import struct
import tkinter as tk
import zlib
from pathlib import Path
from tkinter import ttk, colorchooser, filedialog, messagebox
from typing import Callable, Optional

from models import Session
from ssh import DEFAULT_IDENTITY_MARKER, find_ssh, test_connection
from ui import center_on_parent, section_header, theme


_AUTH_METHOD_LABELS = {
    "password": "Password",
    "default": "OpenSSH Key File",
    "identity": "Local Identity",
    "fido2": "FIDO 2 Auth",
}
_AUTH_METHOD_VALUES = {label: value for value, label in _AUTH_METHOD_LABELS.items()}

# "default"/"OpenSSH Key File" is the common case: one specific key file,
# picked with a simple read-only field + Browse button. "identity"/"fido2"
# get a list instead, so several keys (e.g. multiple registered YubiKeys)
# can stay registered on one session for quick switching — but only the
# one marked as selected is ever actually used to connect; ssh no longer
# tries the others in sequence (unreliable on Windows: a wrong-device
# attempt can trigger a spurious PIN prompt and burn through the server's
# LoginGraceTime before reaching the right one).
_SINGLE_IDENTITY = {"default"}
_MULTI_IDENTITY = {"identity", "fido2"}
_IDENTITY_REQUIRED = _SINGLE_IDENTITY | _MULTI_IDENTITY

_PAD = {"padx": 6, "pady": 3}


def _radio_png(size: int, hex_color: str, selected: bool, scale: float) -> bytes:
    """Anti-aliased radio button as a transparent PNG. Tk's canvas can't
    anti-alias on Windows, so this is rasterised by hand (4x4 supersampling
    per pixel) at the real pixel size — crisp at any DPI, no dependencies."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    center = size / 2
    outer = size / 2 - 0.75 * scale
    inner = outer - 1.4 * scale
    dot = size * 0.2
    n = 4
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            hits = 0
            for sy in range(n):
                for sx in range(n):
                    d = math.hypot(x + (sx + 0.5) / n - center, y + (sy + 0.5) / n - center)
                    if inner <= d <= outer or (selected and d <= dot):
                        hits += 1
            rows += bytes((r, g, b, round(255 * hits / (n * n))))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows)))
        + chunk(b"IEND", b"")
    )


class SessionEditor(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        session: Optional[Session] = None,
        on_delete: Optional[Callable[[], None]] = None,
        identity_key_dir: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.title("Edit Session" if session else "New Session")
        self.resizable(False, False)
        self.configure(background=theme.BG)
        self.grab_set()
        self.transient(parent)

        self._session = session
        self._on_delete = on_delete
        self._identity_key_dir = identity_key_dir
        self._result: Optional[Session] = None

        self._build_ui()

        if session:
            self._populate(session)

        self._update_identity_state()
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        center_on_parent(self)
        self.focus_set()

    def show(self) -> Optional[Session]:
        self.wait_window()
        return self._result

    def _entry_row(self, row: int, label: str, key: str) -> int:
        ttk.Label(self, text=label + ":").grid(row=row, column=0, sticky="e", **_PAD)
        var = tk.StringVar()
        self._vars[key] = var
        ttk.Entry(self, textvariable=var, width=42).grid(
            row=row, column=1, columnspan=2, sticky="ew", **_PAD
        )
        return row + 1

    def _build_ui(self) -> None:
        self.columnconfigure(1, weight=1)

        self._vars: dict[str, tk.StringVar] = {}

        r = section_header(self, 0, "Connection Name")
        r = self._entry_row(r, "Name", "name")
        r = self._entry_row(r, "Folder", "folder")

        r = section_header(self, r, "Host Details")
        r = self._entry_row(r, "Hostname", "hostname")
        r = self._entry_row(r, "Port", "port")

        r = section_header(self, r, "Auth Details")
        r = self._entry_row(r, "Username", "username")

        # Auth method
        ttk.Label(self, text="Auth Method:").grid(row=r, column=0, sticky="e", **_PAD)
        self._auth_var = tk.StringVar(value=_AUTH_METHOD_LABELS["default"])
        self._auth_combo = ttk.Combobox(
            self,
            textvariable=self._auth_var,
            values=list(_AUTH_METHOD_LABELS.values()),
            state="readonly",
            width=20,
        )
        self._auth_combo.grid(row=r, column=1, columnspan=2, sticky="w", **_PAD)
        self._auth_var.trace_add("write", lambda *_: self._update_identity_state())

        # Identity file(s) — only one of the two control widgets below
        # (single-file vs. multi-file list) is gridded at a time, depending
        # on auth method.
        r += 1
        self._identity_label = ttk.Label(self, text="Identity File:")
        self._identity_label.grid(row=r, column=0, sticky="ne", **_PAD)
        self._identity_row = r

        # Single-file mode ("OpenSSH Key File"): read-only field + Browse.
        self._single_identity_var = tk.StringVar()
        self._single_identity_frame = ttk.Frame(self)
        self._single_identity_frame.columnconfigure(0, weight=1)
        self._single_identity_entry = ttk.Entry(
            self._single_identity_frame, textvariable=self._single_identity_var, state="readonly",
        )
        self._single_identity_entry.grid(row=0, column=0, sticky="ew")
        self._single_identity_browse_btn = ttk.Button(
            self._single_identity_frame, text="Browse…", command=self._browse_single_identity,
        )
        self._single_identity_browse_btn.grid(row=0, column=1, padx=(4, 0))

        # Multi-file mode ("Local Identity"/"FIDO 2 Auth"): several keys can
        # stay registered for quick switching, but only one — marked with a
        # radio button — is ever actually used to connect. List order is purely
        # cosmetic/organizational and never changes on its own.
        self._identity_files: list[str] = []
        self._identity_selected: Optional[int] = None  # row selected for Remove
        self._identity_default: Optional[int] = None  # row used to connect; None = index 0
        self._identity_enabled: bool = True
        self._radio_images: dict[bool, tk.PhotoImage] = {}

        self._identity_multi_frame = ttk.Frame(self)
        self._identity_multi_frame.columnconfigure(0, weight=1)

        self._identity_rows = ttk.Frame(self._identity_multi_frame, relief="solid", borderwidth=1)
        self._identity_rows.grid(row=0, column=0, sticky="ew")
        self._identity_rows.columnconfigure(0, weight=1)

        identity_btn_frame = ttk.Frame(self._identity_multi_frame)
        identity_btn_frame.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._identity_add_btn = ttk.Button(identity_btn_frame, text="Add…", command=self._add_identity)
        self._identity_add_btn.pack(side="left", padx=(0, 4))
        self._identity_remove_btn = ttk.Button(
            identity_btn_frame, text="Remove", command=self._remove_identity
        )
        self._identity_remove_btn.pack(side="left")

        r = section_header(self, r + 1, "Other")
        r = self._entry_row(r, "ProxyJump", "proxy_jump")

        # Background colour
        ttk.Label(self, text="Background:").grid(row=r, column=0, sticky="e", **_PAD)
        color_frame = ttk.Frame(self)
        color_frame.grid(row=r, column=1, columnspan=2, sticky="w", **_PAD)

        self._bg_color: Optional[str] = None
        self._color_swatch = tk.Label(
            color_frame, width=4, relief="solid", borderwidth=1,
            bg=theme.SURFACE, highlightbackground=theme.BORDER,
        )
        self._color_swatch.pack(side="left", padx=(0, 6))
        ttk.Button(color_frame, text="Choose…", command=self._pick_color).pack(side="left", padx=2)
        ttk.Button(color_frame, text="Clear",   command=self._clear_color).pack(side="left", padx=2)

        # Buttons
        r += 1
        btn_frame = ttk.Frame(self, padding=(6, 6))
        btn_frame.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        if self._on_delete:
            ttk.Button(btn_frame, text="Delete", command=self._delete).pack(side="left", padx=4)

        ttk.Button(btn_frame, text="Cancel", command=self._cancel).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Save", command=self._save).pack(side="right", padx=4)
        self._test_btn = ttk.Button(btn_frame, text="Test Connection", command=self._test)
        self._test_btn.pack(side="right", padx=4)

        self.bind("<Return>", lambda _: self._save())
        self.bind("<Escape>", lambda _: self._cancel())

        self._rebuild_identity_rows()

    def _populate(self, s: Session) -> None:
        self._vars["name"].set(s.name or "")
        self._vars["folder"].set(s.folder or "")
        self._vars["hostname"].set(s.hostname or "")
        self._vars["port"].set(str(s.port) if s.port else "22")
        self._vars["username"].set(s.username or "")
        identity_lines: list[str] = []
        default_index: Optional[int] = None
        if s.identity_file:
            for line in s.identity_file.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith(DEFAULT_IDENTITY_MARKER):
                    default_index = len(identity_lines)
                    line = line[len(DEFAULT_IDENTITY_MARKER):]
                identity_lines.append(line)
        self._identity_files = identity_lines
        self._identity_selected = None
        self._identity_default = default_index
        self._single_identity_var.set(identity_lines[0] if identity_lines else "")
        self._rebuild_identity_rows()
        self._vars["proxy_jump"].set(s.proxy_jump or "")
        self._auth_var.set(_AUTH_METHOD_LABELS.get(s.auth_method or "default", _AUTH_METHOD_LABELS["default"]))
        if s.background_color:
            self._bg_color = s.background_color
            self._color_swatch.configure(bg=s.background_color)

    def _update_identity_state(self) -> None:
        auth_method = _AUTH_METHOD_VALUES.get(self._auth_var.get())

        if auth_method in _SINGLE_IDENTITY:
            self._identity_multi_frame.grid_remove()
            self._single_identity_frame.grid(
                row=self._identity_row, column=1, columnspan=2, sticky="ew", **_PAD
            )
            self._single_identity_entry.configure(state="readonly")
            self._single_identity_browse_btn.configure(state="normal")
        else:
            self._single_identity_frame.grid_remove()
            self._identity_multi_frame.grid(
                row=self._identity_row, column=1, columnspan=2, sticky="ew", **_PAD
            )
            self._identity_enabled = auth_method in _MULTI_IDENTITY
            state = "normal" if self._identity_enabled else "disabled"
            self._identity_add_btn.configure(state=state)
            self._identity_remove_btn.configure(state=state)
            self._rebuild_identity_rows()

    def _pick_color(self) -> None:
        result = colorchooser.askcolor(color=self._bg_color or "#1e1e1e", parent=self)
        if result[1]:
            self._bg_color = result[1]
            self._color_swatch.configure(bg=self._bg_color)

    def _clear_color(self) -> None:
        self._bg_color = None
        self._color_swatch.configure(bg=theme.SURFACE)

    def _browse_single_identity(self) -> None:
        key_dir = Path(self._identity_key_dir).expanduser() if self._identity_key_dir else None
        path = filedialog.askopenfilename(
            title="Select Identity File",
            parent=self,
            initialdir=str(key_dir) if key_dir and key_dir.is_dir() else None,
            filetypes=[("All files", "*.*")],
        )
        if not path:
            return

        picked = Path(path)
        # If the file lives in the configured key directory, store just the
        # filename so the session stays portable; otherwise keep the full
        # path as an explicit override.
        if key_dir:
            try:
                picked.relative_to(key_dir)
                self._single_identity_var.set(picked.name)
                return
            except ValueError:
                pass
        self._single_identity_var.set(path)

    def _add_identity(self) -> None:
        key_dir = Path(self._identity_key_dir).expanduser() if self._identity_key_dir else None
        path = filedialog.askopenfilename(
            title="Select Identity File",
            parent=self,
            initialdir=str(key_dir) if key_dir and key_dir.is_dir() else None,
            filetypes=[("All files", "*.*")],
        )
        if not path:
            return

        picked = Path(path)
        # If the file lives in the configured key directory, store just the
        # filename so the session stays portable; otherwise keep the full
        # path as an explicit override.
        if key_dir:
            try:
                picked.relative_to(key_dir)
                self._identity_files.append(picked.name)
                self._identity_selected = len(self._identity_files) - 1
                self._rebuild_identity_rows()
                return
            except ValueError:
                pass
        self._identity_files.append(path)
        self._identity_selected = len(self._identity_files) - 1
        self._rebuild_identity_rows()

    def _remove_identity(self) -> None:
        if self._identity_selected is None:
            return
        removed = self._identity_selected
        del self._identity_files[removed]
        self._identity_selected = None

        if self._identity_default is not None:
            if removed == self._identity_default:
                self._identity_default = None  # falls back to (new) index 0
            elif removed < self._identity_default:
                self._identity_default -= 1

        self._rebuild_identity_rows()

    def _select_identity_row(self, index: int) -> None:
        self._identity_selected = index
        self._rebuild_identity_rows()

    def _make_default_identity(self, index: int) -> None:
        """Which entry ssh actually uses to connect — a marker, not a
        position, so the list itself never reorders. List order is purely
        the user's own organization."""
        self._identity_default = index
        self._rebuild_identity_rows()

    def _make_radio(self, parent: tk.Misc, bg: str, selected: bool) -> tk.Label:
        image = self._radio_images.get(selected)
        if image is None:
            scale = self.winfo_fpixels("1i") / 96
            color = theme.RADIO_ON if selected else theme.RADIO_OFF
            png = _radio_png(round(14 * scale), color, selected, scale)
            image = tk.PhotoImage(master=self, data=base64.b64encode(png).decode("ascii"))
            self._radio_images[selected] = image
        return tk.Label(parent, image=image, bg=bg, bd=0, highlightthickness=0)

    def _rebuild_identity_rows(self) -> None:
        for child in self._identity_rows.winfo_children():
            child.destroy()

        default_index = self._identity_default if self._identity_default is not None else 0

        for index, filename in enumerate(self._identity_files):
            selected = index == self._identity_selected
            row_bg = theme.SELECTION if selected else theme.SURFACE
            row = tk.Frame(self._identity_rows, bg=row_bg)
            row.grid(row=index, column=0, sticky="ew")
            row.columnconfigure(1, weight=1)

            is_default = index == default_index
            radio = self._make_radio(row, row_bg, is_default)
            radio.grid(row=0, column=0, padx=(6, 2), pady=2)
            if self._identity_enabled and not is_default:
                radio.configure(cursor="hand2")
                radio.bind("<Button-1>", lambda _e, i=index: self._make_default_identity(i))

            label = tk.Label(row, text=filename, anchor="w", bg=row_bg, fg=theme.FG)
            label.grid(row=0, column=1, sticky="ew", padx=4, pady=2)
            if self._identity_enabled:
                label.bind("<Button-1>", lambda _e, i=index: self._select_identity_row(i))

            if is_default:
                tk.Label(
                    row, text="Default", bg=row_bg, fg=theme.ACCENT,
                    font=(theme.FONT_FAMILY, 8),
                ).grid(row=0, column=2, padx=(4, 6), pady=2)

        if not self._identity_files:
            tk.Label(
                self._identity_rows, text="(none)", anchor="w",
                bg=theme.SURFACE, fg=theme.FG_MUTED,
            ).grid(row=0, column=0, sticky="ew", padx=4, pady=2)

    def _build_session(self) -> Optional[Session]:
        name = self._vars["name"].get().strip()
        hostname = self._vars["hostname"].get().strip()
        auth_method = _AUTH_METHOD_VALUES[self._auth_var.get()]

        if not name:
            messagebox.showwarning("Validation", "Name is required.", parent=self)
            return None
        if not hostname:
            messagebox.showwarning("Validation", "Hostname is required.", parent=self)
            return None

        port_str = self._vars["port"].get().strip() or "22"
        try:
            port = int(port_str)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            messagebox.showwarning("Validation", "Port must be a number between 1 and 65535.", parent=self)
            return None

        identity_file: Optional[str] = None
        if auth_method in _SINGLE_IDENTITY:
            value = self._single_identity_var.get().strip()
            if not value:
                messagebox.showwarning(
                    "Validation",
                    "An identity file is required for this authentication method.",
                    parent=self,
                )
                return None
            identity_file = value
        elif auth_method in _MULTI_IDENTITY:
            if not self._identity_files:
                messagebox.showwarning(
                    "Validation",
                    "At least one identity file is required for this authentication method.",
                    parent=self,
                )
                return None
            default_index = self._identity_default if self._identity_default is not None else 0
            identity_file = "\n".join(
                f"{DEFAULT_IDENTITY_MARKER}{f}" if i == default_index else f
                for i, f in enumerate(self._identity_files)
            )

        return Session(
            id=self._session.id if self._session else None,
            name=name,
            folder=self._vars["folder"].get().strip() or None,
            hostname=hostname,
            port=port,
            username=self._vars["username"].get().strip() or None,
            auth_method=auth_method,
            identity_file=identity_file,
            proxy_jump=self._vars["proxy_jump"].get().strip() or None,
            sort_order=self._session.sort_order if self._session else None,
            background_color=self._bg_color,
            created_at=self._session.created_at if self._session else None,
        )

    def _save(self) -> None:
        session = self._build_session()
        if session:
            self._result = session
            self.destroy()

    def _delete(self) -> None:
        name = self._session.name if self._session else ""
        if messagebox.askyesno("Delete Session", f"Delete '{name}'?", parent=self):
            if self._on_delete:
                self._on_delete()
            self.destroy()

    def _cancel(self) -> None:
        self.destroy()

    def _test(self) -> None:
        session = self._build_session()
        if not session:
            return

        if not find_ssh():
            messagebox.showerror(
                "SSH Not Found",
                "ssh was not found in PATH.\nPlease install OpenSSH.",
                parent=self,
            )
            return

        self._test_btn.configure(state="disabled", text="Testing…")
        self.update_idletasks()
        try:
            message = test_connection(session, identity_key_dir=self._identity_key_dir)
            messagebox.showinfo("Test Successful", message, parent=self)
        except RuntimeError as e:
            messagebox.showerror("Test Failed", str(e), parent=self)
        finally:
            self._test_btn.configure(state="normal", text="Test Connection")
