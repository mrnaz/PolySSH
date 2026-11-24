import shutil
import sys
import tkinter as tk
import tkinter.messagebox as mb
from pathlib import Path
from typing import Optional

# Resolve base directory whether running as a script or a PyInstaller bundle.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

# A --windowed/--noconsole PyInstaller build has no console, so sys.stdout
# and sys.stderr are None. Any code path that writes to them (a stray
# print, a library warning, even Tkinter's own default callback-exception
# reporter) then crashes with an unrelated AttributeError and no visible
# message at all, since there's no console to show it on either. Redirect
# both to real files before anything else runs, so the app can't die from
# this and any such output is still captured somewhere inspectable.
if getattr(sys, "frozen", False) and sys.stderr is None:
    _log_dir = BASE_DIR / "logs"
    _log_dir.mkdir(exist_ok=True)
    sys.stdout = open(_log_dir / "stdout.log", "a", encoding="utf-8")
    sys.stderr = open(_log_dir / "stderr.log", "a", encoding="utf-8")

sys.path.insert(0, str(BASE_DIR))

from database import Database
from logger import setup_logger
from settings import SettingsManager
from ssh import find_ssh, launch_session_with_settings
from ui.main_window import MainWindow

APP_DIR = Path.home() / ".polyssh"
DB_PATH = APP_DIR / "sessions.db"
SETTINGS_PATH = APP_DIR / "settings.json"


def _migrate_legacy_file(legacy_candidates: list[Path], new_path: Path) -> None:
    """sessions.db and settings.json have lived in two places before this:
    next to the app itself originally, then briefly in ~/.ssh alongside the
    keys they reference. Both were convenient but not quite right: ~/.ssh
    is OpenSSH's own directory by convention, not a general app-data
    folder, and mixing PolySSH's state into it risks tooling that treats
    ~/.ssh's contents as disposable/regenerable (key rotation scripts,
    dev-container provisioning) accidentally wiping curated session data
    along with keys. ~/.polyssh is PolySSH's own namespace that nothing
    else has any reason to touch. Identity keys themselves are unaffected
    and stay wherever identity_key_dir points (~/.ssh by default), since
    those are genuinely shared with other tools."""
    if new_path.exists():
        return
    for legacy_path in legacy_candidates:
        if legacy_path.exists():
            try:
                APP_DIR.mkdir(parents=True, exist_ok=True)
                shutil.move(str(legacy_path), str(new_path))
            except Exception:
                pass
            return


def _dial_argument(args: list[str]) -> Optional[str]:
    """The value given to --dial (or --dial=N), "" if it has none, and None
    if --dial wasn't passed at all."""
    for i, arg in enumerate(args):
        if arg == "--dial":
            return args[i + 1] if i + 1 < len(args) else ""
        if arg.startswith("--dial="):
            return arg[len("--dial="):]
    return None


def _run_dial(value: str, db: Database, settings, logger) -> int:
    """Headless speed dial: connect the session assigned to a number and
    exit, without ever showing the main window — so it's safe to bind to an
    OS hotkey or shortcut while the main window is also open."""
    root = tk.Tk()
    root.withdraw()
    try:
        try:
            number = int(value)
            if not 1 <= number <= 9:
                raise ValueError
        except ValueError:
            mb.showerror("Speed Dial", "--dial needs a number from 1 to 9.", parent=root)
            return 2

        session = db.get_by_speed_dial(number)
        if session is None:
            mb.showerror("Speed Dial", f"No session is assigned to speed dial {number}.", parent=root)
            return 1

        try:
            launch_session_with_settings(session, settings)
        except RuntimeError as e:
            mb.showerror("Connection Error", str(e), parent=root)
            return 1
        except Exception as e:
            logger.error("Failed to launch session: %s", e)
            mb.showerror("Error", f"Failed to launch session:\n{e}", parent=root)
            return 1
        return 0
    finally:
        root.destroy()


def main() -> None:
    if sys.platform == "win32":
        # Without a distinct AppUserModelID, Windows groups this process
        # under the generic "python.exe" host for taskbar purposes and uses
        # *its* icon for the taskbar button, regardless of what icon the
        # window itself sets (which is why the title bar can be correct
        # while the taskbar still shows Python's icon). Must be set before
        # any window is created.
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PolySSH.PolySSH")
        except Exception:
            pass

        # Without declaring DPI awareness, Windows treats the process as
        # "Unaware" and bitmap-stretches the entire rendered window to match
        # the display's scale factor — which is why thin 1px borders looked
        # blurry and thicker than 1px under fractional scaling (e.g. 150%)
        # instead of being crisply rendered at the true resolution. Must be
        # set before any window is created. Try Per-Monitor-V2 first (Windows
        # 10 1703+), falling back for older Windows versions.
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                try:
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass

    logger = setup_logger(BASE_DIR / "logs")

    if not find_ssh():
        mb.showerror(
            "SSH Not Found",
            "The ssh executable was not found in PATH.\n"
            "Please install OpenSSH and ensure it is on the system PATH.",
        )
        sys.exit(1)

    # On a machine that's never run PolySSH before, ~/.polyssh doesn't exist
    # yet and there's nothing to migrate — _migrate_legacy_file only creates
    # it as a side effect of actually moving a legacy file, so a genuinely
    # fresh install would otherwise hit Database(DB_PATH) trying to open a
    # file inside a directory that was never created. Ensure it exists
    # unconditionally first; Database's own CREATE TABLE IF NOT EXISTS and
    # SettingsManager's exists()-check both already handle a missing file
    # correctly on their own, so this is the one missing piece.
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error("Failed to create %s: %s", APP_DIR, e)
        mb.showerror("Startup Error", f"Failed to create {APP_DIR}:\n{e}")
        sys.exit(1)

    _migrate_legacy_file(
        [BASE_DIR / "sessions.db", Path.home() / ".ssh" / "sessions.db"], DB_PATH
    )
    try:
        db = Database(DB_PATH)
    except Exception as e:
        logger.error("Failed to open database: %s", e)
        mb.showerror("Database Error", f"Failed to open sessions database:\n{e}")
        sys.exit(1)

    _migrate_legacy_file(
        [BASE_DIR / "settings.json", Path.home() / ".ssh" / "settings.json"], SETTINGS_PATH
    )
    settings_mgr = SettingsManager(SETTINGS_PATH)

    dial = _dial_argument(sys.argv[1:])
    if dial is not None:
        sys.exit(_run_dial(dial, db, settings_mgr.settings, logger))

    window = MainWindow(db, settings_mgr, logger)
    window.run()


if __name__ == "__main__":
    main()
