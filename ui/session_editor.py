import tkinter as tk
from pathlib import Path
from tkinter import ttk, colorchooser, filedialog, messagebox
from typing import Callable, Optional

from models import Session
from ssh import DEFAULT_IDENTITY_MARKER, HostKeyChangedError, find_ssh, forget_host_key, test_connection
from ui import center_on_parent, render, section_header, theme


_AUTH_METHOD_LABELS = {
    "password": "Password",
    "local": "Local Identity",
    "keyfile": "Key File / FIDO2",
}
_AUTH_METHOD_VALUES = {label: value for value, label in _AUTH_METHOD_LABELS.items()}

# "Key File / FIDO2" keeps a list so several keys (e.g. multiple registered
# YubiKeys, or a mix of ordinary and security-key files) can stay registered
# on one session for quick switching — but only the one marked as default is
# ever actually used to connect; ssh no longer tries the others in sequence
# (unreliable on Windows: a wrong-device attempt can trigger a spurious PIN
# prompt and burn through the server's LoginGraceTime before reaching the
# right one).

_PAD = {"padx": 6, "pady": 3}


class SessionEditor(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        session: Optional[Session] = None,
        on_delete: Optional[Callable[[], None]] = None,
        identity_key_dir: Optional[str] = None,
        speed_dial_owners: Optional[dict[int, str]] = None,
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
        self._speed_dial_owners = speed_dial_owners or {}
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
        self._auth_var = tk.StringVar(value=_AUTH_METHOD_LABELS["local"])
        self._auth_combo = ttk.Combobox(
            self,
            textvariable=self._auth_var,
            values=list(_AUTH_METHOD_LABELS.values()),
            state="readonly",
            width=20,
        )
        self._auth_combo.grid(row=r, column=1, columnspan=2, sticky="w", **_PAD)
        self._auth_var.trace_add("write", lambda *_: self._update_identity_state())

        # Key list — only shown for the "Key File / FIDO2" method. Several keys
        # can stay registered for quick switching, but only one — marked with a
        # radio button — is ever actually used to connect. List order is purely
        # cosmetic/organizational and never changes on its own.
        r += 1
        self._identity_label = ttk.Label(self, text="Key Files:")
        self._identity_label.grid(row=r, column=0, sticky="ne", **_PAD)
        self._identity_row = r

        self._identity_files: list[str] = []
        self._identity_selected: Optional[int] = None  # row selected for Remove
        self._identity_default: Optional[int] = None  # row used to connect; None = index 0

        self._identity_multi_frame = ttk.Frame(self)
        self._identity_multi_frame.columnconfigure(0, weight=1)

        self._identity_rows = ttk.Frame(self._identity_multi_frame, style="Card.TFrame", padding=theme.px(3))
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
        self._color_swatch = tk.Label(color_frame, bg=theme.BG, bd=0, highlightthickness=0)
        self._color_swatch.pack(side="left", padx=(0, 8))
        ttk.Button(color_frame, text="Choose…", command=self._pick_color).pack(side="left", padx=(0, 6))
        ttk.Button(color_frame, text="Clear",   command=self._clear_color).pack(side="left")
        self._refresh_swatch()

        # Speed dial: a number from 1-9 that Ctrl+<number> (or --dial) connects
        # straight to this session. Numbers already held by another session
        # are labelled so taking one over is a visible choice.
        r += 1
        ttk.Label(self, text="Speed Dial:").grid(row=r, column=0, sticky="e", **_PAD)
        self._speed_dial_labels: dict[str, Optional[int]] = {"None": None}
        for number in range(1, 10):
            owner = self._speed_dial_owners.get(number)
            label = f"{number}  (replaces {owner})" if owner else str(number)
            self._speed_dial_labels[label] = number
        self._speed_dial_var = tk.StringVar(value="None")
        ttk.Combobox(
            self,
            textvariable=self._speed_dial_var,
            values=list(self._speed_dial_labels),
            state="readonly",
            width=28,
        ).grid(row=r, column=1, columnspan=2, sticky="w", **_PAD)

        # Buttons
        r += 1
        btn_frame = ttk.Frame(self, padding=(6, 6))
        btn_frame.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        if self._on_delete:
            ttk.Button(btn_frame, text="Delete", command=self._delete).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Forget Host Key…", command=self._forget_host_key).pack(side="left", padx=4)

        ttk.Button(btn_frame, text="Cancel", command=self._cancel).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Save", command=self._save, style="Accent.TButton").pack(side="right", padx=4)
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
        self._rebuild_identity_rows()
        self._vars["proxy_jump"].set(s.proxy_jump or "")
        self._auth_var.set(_AUTH_METHOD_LABELS.get(s.auth_method, _AUTH_METHOD_LABELS["local"]))
        if s.background_color:
            self._bg_color = s.background_color
            self._refresh_swatch()
        for label, number in self._speed_dial_labels.items():
            if number == s.speed_dial:
                self._speed_dial_var.set(label)

    def _update_identity_state(self) -> None:
        auth_method = _AUTH_METHOD_VALUES.get(self._auth_var.get())

        if auth_method != "keyfile":
            self._identity_label.grid_remove()
            self._identity_multi_frame.grid_remove()
        else:
            self._identity_label.grid()
            self._identity_multi_frame.grid(
                row=self._identity_row, column=1, columnspan=2, sticky="ew", **_PAD
            )
            self._rebuild_identity_rows()

    def _refresh_swatch(self) -> None:
        w, h, r = theme.px(44), theme.px(26), theme.px(6)
        color = self._bg_color if self._bg_color and len(self._bg_color) == 7 else None
        bw = max(1, round(theme._scale))
        image = theme.photo(
            ("swatch", color, w, h),
            lambda: render.rounded_rect(w, h, r, color or theme.SURFACE, theme.BUTTON_BORDER, bw),
        )
        self._color_swatch.configure(image=image)

    def _pick_color(self) -> None:
        result = colorchooser.askcolor(color=self._bg_color or "#1e1e1e", parent=self)
        if result[1]:
            self._bg_color = result[1]
            self._refresh_swatch()

    def _clear_color(self) -> None:
        self._bg_color = None
        self._refresh_swatch()

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
        size = theme.px(14)
        color = theme.RADIO_ON if selected else theme.RADIO_OFF
        image = theme.photo(
            ("editor-radio", color, selected, size),
            lambda: render.radio(size, color, selected, theme._scale),
        )
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
            if not is_default:
                radio.configure(cursor="hand2")
                radio.bind("<Button-1>", lambda _e, i=index: self._make_default_identity(i))

            label = tk.Label(row, text=filename, anchor="w", bg=row_bg, fg=theme.FG)
            label.grid(row=0, column=1, sticky="ew", padx=4, pady=2)
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

        self._identity_remove_btn.configure(
            state="normal" if self._identity_selected is not None else "disabled"
        )

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
        if auth_method == "keyfile":
            if not self._identity_files:
                messagebox.showwarning(
                    "Validation",
                    "Add at least one key file for this authentication method.",
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
            speed_dial=self._speed_dial_labels[self._speed_dial_var.get()],
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
        except HostKeyChangedError as e:
            self._test_btn.configure(state="normal", text="Test Connection")
            if messagebox.askyesno("Host Key Changed", f"{e}\n\nForget the saved host key?", parent=self):
                self._forget_host_key(confirm=False)
        except RuntimeError as e:
            messagebox.showerror("Test Failed", str(e), parent=self)
        finally:
            self._test_btn.configure(state="normal", text="Test Connection")

    def _forget_host_key(self, confirm: bool = True) -> None:
        hostname = self._vars["hostname"].get().strip()
        if not hostname:
            messagebox.showwarning("Validation", "Hostname is required.", parent=self)
            return
        try:
            port = int(self._vars["port"].get().strip() or "22")
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            messagebox.showwarning("Validation", "Port must be a number between 1 and 65535.", parent=self)
            return

        if confirm and not messagebox.askyesno(
            "Forget Host Key", f"Remove the saved host key for {hostname}?", parent=self
        ):
            return
        try:
            removed = forget_host_key(Session(name="", hostname=hostname, port=port, auth_method="local"))
        except RuntimeError as e:
            messagebox.showerror("Forget Host Key", str(e), parent=self)
            return
        if removed:
            messagebox.showinfo("Forget Host Key", "Removed:\n" + "\n".join(removed), parent=self)
        else:
            messagebox.showinfo("Forget Host Key", f"No saved host key found for {hostname}.", parent=self)
