import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from models import Session

if getattr(sys, "frozen", False):
    # PyInstaller's --onefile mode extracts bundled data files to a
    # temporary directory at runtime (sys._MEIPASS), not next to the actual
    # exe — Path(sys.executable).parent would silently fail to find bundled
    # assets like the icon once packaged.
    _BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    _BASE_DIR = Path(__file__).resolve().parent
_ICON_PATH = _BASE_DIR / "assets" / "icon.png"


def find_ssh() -> Optional[str]:
    return shutil.which("ssh")


DEFAULT_IDENTITY_MARKER = "*"
_KEY_AUTH_METHODS = ("default", "identity", "fido2")


def parse_identity_files(identity_file: Optional[str]) -> list[str]:
    """All identity files registered on this session, in display order,
    with any leading selection marker stripped. Session editor UI use only
    — ssh itself only ever gets the one file get_selected_identity picks."""
    if not identity_file:
        return []
    lines = [line.strip() for line in identity_file.splitlines() if line.strip()]
    return [
        line[len(DEFAULT_IDENTITY_MARKER):] if line.startswith(DEFAULT_IDENTITY_MARKER) else line
        for line in lines
    ]


def get_selected_identity(identity_file: Optional[str]) -> Optional[str]:
    """The one registered identity actually used to connect: whichever
    entry is marked with a leading '*', or the first one if none is
    marked. Several keys can stay registered on a session for quick
    switching (e.g. multiple YubiKeys used from different locations)
    without ssh ever attempting more than the one currently selected —
    trying several in sequence turned out to be unreliable on Windows
    (a wrong-device attempt can trigger a spurious PIN prompt and burn
    through the server's LoginGraceTime before reaching the right one)."""
    if not identity_file:
        return None
    lines = [line.strip() for line in identity_file.splitlines() if line.strip()]
    for line in lines:
        if line.startswith(DEFAULT_IDENTITY_MARKER):
            return line[len(DEFAULT_IDENTITY_MARKER):]
    return lines[0] if lines else None


def resolve_identity_file(identity_file: str, identity_key_dir: Optional[str]) -> str:
    """Resolve one stored identity file entry to an actual path.

    A bare filename (e.g. "id_ed25519_sk_gan-staging") is resolved against
    the configured key directory, so sessions stay portable — only the one
    directory setting needs to change between machines, not every session.
    A value that already has directory components (legacy sessions saved
    before this existed, or a deliberate override) is used as-is.
    """
    p = Path(identity_file)
    if p.is_absolute() or len(p.parts) > 1:
        return str(p.expanduser())
    if identity_key_dir:
        return str(Path(identity_key_dir).expanduser() / identity_file)
    return identity_file


def build_ssh_args(session: Session, identity_key_dir: Optional[str] = None) -> list[str]:
    args: list[str] = ["ssh"]

    if session.auth_method == "password":
        args += ["-o", "PubkeyAuthentication=no"]
    elif session.auth_method in ("default", "identity", "fido2"):
        # "default" means a single chosen key file rather than ssh's own
        # implicit resolution (agent/~/.ssh/id_*) — but if one somehow
        # isn't set (a pre-existing session from before this had a picker),
        # fall through to that implicit behavior rather than adding
        # IdentitiesOnly=yes with no -i, which would make ssh try nothing.
        selected = get_selected_identity(session.identity_file)
        if selected:
            args += ["-o", "IdentitiesOnly=yes", "-i", resolve_identity_file(selected, identity_key_dir)]

    if session.proxy_jump:
        args += ["-J", session.proxy_jump]

    if session.port != 22:
        args += ["-p", str(session.port)]

    target = session.hostname
    if session.username:
        target = f"{session.username}@{target}"

    args.append(target)
    return args


def test_connection(
    session: Session, identity_key_dir: Optional[str] = None, timeout: int = 10
) -> str:
    """Attempt a non-interactive SSH connection with no visible terminal.

    Returns a human-readable success message. Raises RuntimeError with an
    informative message on failure.
    """
    if session.auth_method in _KEY_AUTH_METHODS:
        selected = get_selected_identity(session.identity_file)
        if selected:
            key_path = resolve_identity_file(selected, identity_key_dir)
            if not os.path.isfile(key_path):
                raise RuntimeError(f"Key file not found:\n{key_path}")

    ssh_args = build_ssh_args(session, identity_key_dir)
    args = [
        ssh_args[0],
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"ConnectTimeout={timeout}",
        *ssh_args[1:],
        "exit",
    ]

    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    # ConnectTimeout only bounds the initial network connection. FIDO2 needs
    # extra time on top of that for a person to notice the key blinking,
    # touch it, and possibly retry after a failed fingerprint match — so its
    # overall watchdog budget is larger, not just its network timeout. Only
    # ever one identity is attempted (see get_selected_identity), so this
    # doesn't need to scale with how many are registered.
    if session.auth_method == "fido2":
        overall_timeout = timeout + 25
    else:
        overall_timeout = timeout + 5

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=overall_timeout, **kwargs,
        )
    except subprocess.TimeoutExpired:
        if session.auth_method == "fido2":
            raise RuntimeError(
                f"No response from {session.hostname} within {overall_timeout}s while "
                "waiting on FIDO2 authentication.\nMake sure the security key is "
                "plugged in and touch it promptly once it starts blinking."
            )
        raise RuntimeError(f"Connection to {session.hostname} timed out after {timeout}s.")
    except FileNotFoundError:
        raise RuntimeError("ssh was not found in PATH.")

    if result.returncode == 0:
        return f"Successfully connected to {session.hostname}."

    stderr = (result.stderr or "").strip() or f"ssh exited with code {result.returncode}."

    if "permission denied" in stderr.lower():
        if session.auth_method in ("default", "identity"):
            raise RuntimeError(
                f"{stderr}\n\n"
                "Either the server doesn't accept this key, or the key has a "
                "passphrase and isn't loaded in an SSH agent (this test can't "
                "prompt for it, though Connect would)."
            )
        if session.auth_method == "fido2":
            raise RuntimeError(
                f"{stderr}\n\n"
                "This usually means the security key wasn't plugged in, the "
                "touch/fingerprint wasn't confirmed in time, or the fingerprint "
                "didn't match. Reconnect the key and try again, or use Connect "
                "to retry interactively."
            )
        # password: BatchMode can't answer an interactive prompt, so a denial
        # here only means this headless test can't verify it — unless the
        # server's own list of accepted methods doesn't include a password
        # method at all, in which case Connect can't succeed either.
        offered = re.search(r"permission denied \(([^)]*)\)", stderr, re.IGNORECASE)
        if offered:
            methods = {m.strip() for m in offered.group(1).split(",")}
            if not methods & {"password", "keyboard-interactive"}:
                raise RuntimeError(
                    f"{stderr}\n\n"
                    "This server doesn't accept password login "
                    f"(it only offers: {', '.join(sorted(methods))}). "
                    "Change this session's authentication method."
                )
        return (
            f"Reached {session.hostname}, but authentication requires "
            "interactive input (e.g. a password) that this automated test "
            "can't supply.\nConnectivity and the server configuration look "
            "correct — use Connect to complete a real login."
        )

    raise RuntimeError(stderr)


def launch_session(
    session: Session,
    identity_key_dir: Optional[str] = None,
    font_face: Optional[str] = None,
    font_size: Optional[int] = None,
    terminal_cols: Optional[int] = None,
    terminal_rows: Optional[int] = None,
) -> None:
    ssh_args = build_ssh_args(session, identity_key_dir)
    color = session.background_color

    if sys.platform == "win32":
        _launch_windows(
            session.id, session.name, ssh_args, color, font_face, font_size,
            terminal_cols, terminal_rows,
        )
    elif sys.platform == "darwin":
        _launch_macos(
            session.name, ssh_args, color, font_face, font_size, terminal_cols, terminal_rows
        )
    else:
        _launch_linux(
            session.name, ssh_args, color, font_face, font_size, terminal_cols, terminal_rows
        )


# ------------------------------------------------------------------
# Windows
# ------------------------------------------------------------------

def _launch_windows(
    session_id: Optional[int],
    title: Optional[str],
    ssh_args: list[str],
    color: Optional[str],
    font_face: Optional[str],
    font_size: Optional[int],
    terminal_cols: Optional[int],
    terminal_rows: Optional[int],
) -> None:
    # wt has no CLI mechanism for ad-hoc per-tab font, size, or background —
    # every variant of --font-face/--font-size/--initialCols/--initialRows
    # was tested and confirmed to abort the launch outright, on new-tab and
    # even on a brand-new window. terminal_cols/terminal_rows are
    # consequently unsupported on Windows, the same way konsole has no
    # size/font args.
    #
    # Background color used to be injected via an OSC 11 escape sequence
    # written to the tab's own console. That leaked across tabs: opening a
    # second tab in the same reused "polyssh" window would reset the first
    # tab's background back to black, because both tabs resolved to the same
    # underlying wt profile and OSC 11 mutated that shared, live profile
    # state rather than a value scoped to one pane. Font and background are
    # now both set via a dedicated hidden wt profile *per session*, so
    # simultaneously open tabs never share a profile and can't interfere
    # with each other. This stays a Windows-only mechanism confined to this
    # function.
    cmd = ["wt", "-w", "polyssh", "new-tab"]

    if title:
        # Without --suppressApplicationTitle, the remote shell's own prompt
        # setup (most Linux .bashrc/.zshrc defaults emit an OSC title
        # sequence) overwrites this the moment the session becomes
        # interactive — confirmed directly: identical launch with vs.
        # without the flag showed the title surviving only with it present.
        cmd += ["--title", title, "--suppressApplicationTitle"]

    if session_id is not None:
        profile_guid = _ensure_session_profile(session_id, font_face, font_size, color)
        if profile_guid:
            cmd += ["-p", profile_guid]

    ps_cmd = "& " + " ".join(_ps1_quote(a) for a in ssh_args)

    # wt splits its command line on unescaped ';' even inside quoted
    # arguments, which mangles the -Command string. Escape any literal ';'
    # so wt passes it through intact instead of treating it as a separator
    # between wt subcommands.
    ps_cmd = ps_cmd.replace(";", "\\;")

    cmd += ["--", "powershell", "-Command", ps_cmd]

    try:
        subprocess.Popen(cmd)
    except FileNotFoundError:
        raise RuntimeError(
            "Windows Terminal (wt) was not found.\n"
            "Install Windows Terminal from the Microsoft Store."
        )


def _ps1_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


_POLYSSH_WT_NAMESPACE = uuid.UUID("d2f6a6d2-8f0a-4c1e-9a9b-2f6a6d2d2f6a")


def _session_profile_guid(session_id: int) -> str:
    return "{" + str(uuid.uuid5(_POLYSSH_WT_NAMESPACE, f"polyssh-session-{session_id}")) + "}"


def _wt_settings_path() -> Optional[Path]:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    for candidate in (
        Path(local) / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe"
        / "LocalState" / "settings.json",
        Path(local) / "Packages" / "Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe"
        / "LocalState" / "settings.json",
        Path(local) / "Microsoft" / "Windows Terminal" / "settings.json",
    ):
        if candidate.exists():
            return candidate
    return None


def _ensure_session_profile(
    session_id: int,
    font_face: Optional[str],
    font_size: Optional[int],
    background_color: Optional[str],
) -> Optional[str]:
    """Create/update a hidden wt profile dedicated to this one session and
    set its font/background/icon there, so overrides never touch the
    user's own profiles and never bleed into another session's tab.
    Returns the profile guid to pass to `wt -p`, or None if there's
    nothing to customize or wt's settings.json can't be found/updated."""
    has_icon = _ICON_PATH.exists()
    if not (font_face or font_size or background_color or has_icon):
        return None

    path = _wt_settings_path()
    if path is None:
        return None

    guid = _session_profile_guid(session_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        profile_list = data.setdefault("profiles", {}).setdefault("list", [])

        profile = next((p for p in profile_list if p.get("guid") == guid), None)
        if profile is None:
            profile = {
                "guid": guid,
                "name": f"PolySSH session {session_id}",
                "hidden": True,
            }
            profile_list.append(profile)

        font = profile.setdefault("font", {})
        if font_face:
            font["face"] = font_face
        else:
            font.pop("face", None)
        if font_size:
            font["size"] = font_size
        else:
            font.pop("size", None)
        if not font:
            profile.pop("font", None)

        if background_color:
            profile["background"] = background_color
        else:
            profile.pop("background", None)

        if has_icon:
            profile["icon"] = str(_ICON_PATH)
        else:
            profile.pop("icon", None)

        path.write_text(json.dumps(data, indent=4), encoding="utf-8")
        return guid
    except Exception:
        return None


def remove_session_profile(session_id: int) -> None:
    """Best-effort cleanup of a session's dedicated wt profile once the
    session itself is deleted, so settings.json doesn't accumulate stale
    hidden profiles forever. Silently does nothing on non-Windows platforms
    or if wt's settings.json can't be found."""
    path = _wt_settings_path()
    if path is None:
        return

    guid = _session_profile_guid(session_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        profile_list = data.get("profiles", {}).get("list", [])
        filtered = [p for p in profile_list if p.get("guid") != guid]
        if len(filtered) != len(profile_list):
            data["profiles"]["list"] = filtered
            path.write_text(json.dumps(data, indent=4), encoding="utf-8")
    except Exception:
        pass


# ------------------------------------------------------------------
# macOS
# ------------------------------------------------------------------
#
# NOTE: implemented from documented Terminal.app AppleScript behavior —
# there is no Mac available in this environment to actually run it against.
# Everything below is reasoned through carefully but genuinely unverified;
# please confirm it works on real hardware and report back anything wrong.

def _launch_macos(
    title: Optional[str],
    ssh_args: list[str],
    color: Optional[str],
    font_face: Optional[str],
    font_size: Optional[int],
    terminal_cols: Optional[int],
    terminal_rows: Optional[int],
) -> None:
    # Terminal.app's `do script "command"` with no target reliably opens a
    # fresh window and returns a reference to the new tab. The alternative —
    # opening a new tab in an existing window, to consolidate sessions the
    # way the Windows Terminal integration does — requires driving System
    # Events with a synthetic Cmd+T keystroke, which needs the user to grant
    # Accessibility permissions to Terminal/osascript in System Settings on
    # first run. That's a failure mode with no way to test around it here,
    # so this deliberately opens one window per connection instead: it needs
    # no special permissions and is far more likely to just work.
    import shlex

    cmd = " ".join(shlex.quote(a) for a in ssh_args)

    script_lines = [
        'tell application "Terminal"',
        "activate",
        f'set newTab to do script "{_applescript_escape(cmd)}"',
    ]
    if title:
        script_lines.append(f'set custom title of newTab to "{_applescript_escape(title)}"')
    if color:
        r, g, b = _hex_to_applescript_rgb(color)
        script_lines.append(f"set background color of newTab to {{{r}, {g}, {b}}}")
    if font_face:
        script_lines.append(f'set font name of newTab to "{_applescript_escape(font_face)}"')
    if font_size:
        script_lines.append(f"set font size of newTab to {font_size}")
    if terminal_cols:
        script_lines.append(f"set number of columns of newTab to {terminal_cols}")
    if terminal_rows:
        script_lines.append(f"set number of rows of newTab to {terminal_rows}")
    script_lines.append("end tell")

    try:
        subprocess.Popen(["osascript", "-e", "\n".join(script_lines)])
    except FileNotFoundError:
        raise RuntimeError("osascript was not found. This launch path requires macOS.")


def _applescript_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _hex_to_applescript_rgb(hex_color: str) -> tuple[int, int, int]:
    """AppleScript color specifications use 16-bit-per-channel values
    (0-65535), not the 0-255 used by hex color strings; 257 = 65535/255 is
    the exact scaling factor between the two ranges."""
    h = hex_color.lstrip("#")
    r = int(h[0:2], 16) * 257
    g = int(h[2:4], 16) * 257
    b = int(h[4:6], 16) * 257
    return r, g, b


# ------------------------------------------------------------------
# Linux
# ------------------------------------------------------------------

def _launch_linux(
    title: Optional[str],
    ssh_args: list[str],
    color: Optional[str],
    font_face: Optional[str],
    font_size: Optional[int],
    terminal_cols: Optional[int],
    terminal_rows: Optional[int],
) -> None:
    for terminal in ("gnome-terminal", "konsole", "xterm"):
        if not shutil.which(terminal):
            continue

        if terminal == "xterm":
            extra: list[str] = []
            if title:
                extra += ["-title", title]
            if color:
                extra += ["-bg", color]
            if font_face:
                extra += ["-fa", font_face]
            if font_size:
                extra += ["-fs", str(font_size)]
            if terminal_cols and terminal_rows:
                extra += ["-geometry", f"{terminal_cols}x{terminal_rows}"]
            subprocess.Popen(["xterm", *extra, "-e", *ssh_args])

        elif terminal == "gnome-terminal":
            extra = []
            if title:
                extra += [f"--title={title}"]
            if terminal_cols and terminal_rows:
                extra += [f"--geometry={terminal_cols}x{terminal_rows}"]
            if color:
                script = _write_sh_launcher(ssh_args, color)
                subprocess.Popen(["gnome-terminal", *extra, "--", "sh", script])
            else:
                subprocess.Popen(["gnome-terminal", *extra, "--", *ssh_args])

        else:  # konsole — no reliable CLI size or font args
            sep = ["-e"]
            if color:
                script = _write_sh_launcher(ssh_args, color)
                subprocess.Popen([terminal, *sep, "sh", script])
            else:
                subprocess.Popen([terminal, *sep, *ssh_args])

        return

    raise RuntimeError(
        "No terminal emulator found.\n"
        "Please install GNOME Terminal, Konsole, or XTerm."
    )


def _write_sh_launcher(ssh_args: list[str], color: str) -> str:
    """Temporary shell script that sets the terminal background via OSC 11."""
    import shlex
    cmd = " ".join(shlex.quote(a) for a in ssh_args)
    # Delete itself after the ssh session ends, rather than leaving one
    # stray temp script behind per colored connection forever.
    script = f"#!/bin/sh\nprintf '\\033]11;{color}\\007'\n{cmd}\nrm -f -- \"$0\"\n"

    fd, path = tempfile.mkstemp(suffix=".sh", prefix="polyssh_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(script)
    os.chmod(path, 0o700)
    return path
