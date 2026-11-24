# PolySSH

A lightweight SSH session manager for the desktop. Keep your servers organised in folders, then open each one in its own terminal tab with a click, with its own colour, font and window title.

PolySSH is a thin, friendly front end over the OpenSSH client you already have. It stores no passwords, bundles no SSH implementation and has no third-party Python dependencies.

Built and maintained by Accel IT.

## Features

- **Organised session list**: sessions grouped into folders, in a clean, flat UI.
- **One terminal per session**: each connection opens in its own tab, titled with the session name.
- **Per-session appearance**: background colour, and optionally font face, font size and terminal size, without touching your system-wide terminal settings (Windows Terminal profiles are managed for you).
- **Three ways to authenticate**: *Password* (prompted in the terminal), *Local Identity* (whatever `ssh` already uses on this machine: agent, `~/.ssh/config`, default keys, so nothing is stored in the session), or *Key File / FIDO2* (pick specific key files, including FIDO2 hardware security keys such as YubiKeys).
- **Several keys per session**: register every key you own, including a mix of ordinary and security-key files, and mark the default. PolySSH detects security keys automatically.
- **Jump hosts**: connect through a bastion with OpenSSH `ProxyJump`.
- **Speed dial**: give any session a number from 1 to 9, then press Ctrl+1 to Ctrl+9 to connect instantly. `PolySSH --dial 3` does the same from the command line without opening the window, so you can bind it to an OS-level hotkey or shortcut.
- **Test Connection**: headless check that reports success or the failure reason in a dialog, with no terminal window.
- **Export**: export your session list to WinSCP, FileZilla or plain `sftp://` URIs.

## Platform support

| Platform | Status |
|----------|--------|
| Windows 10/11 | Primary platform, fully tested. Uses Windows Terminal. |
| Linux | Implemented (xterm, gnome-terminal, konsole) but only code-reviewed so far. |
| macOS | Implemented (Terminal.app via AppleScript) but untested. |

Bug reports and fixes for Linux and macOS are especially welcome.

## Requirements

- Python 3.9 or newer (developed on 3.14), with Tkinter (included in the standard Python installers)
- An OpenSSH client (`ssh`) on your `PATH`
- Windows only: [Windows Terminal](https://aka.ms/terminal)

There is nothing to `pip install`.

## Running from source

```
git clone https://github.com/mrnaz/PolySSH.git
cd PolySSH
python PolySSH.py
```

## Building a standalone Windows executable

```
pip install pyinstaller
python -m PyInstaller --onefile --windowed --name PolySSH --icon assets/icon.ico --add-data "assets;assets" PolySSH.py
```

The result is `dist/PolySSH.exe`.

## Where your data lives

| What | Where |
|------|-------|
| Session list | `~/.polyssh/sessions.db` |
| Settings | `~/.polyssh/settings.json` |
| SSH keys | `~/.ssh` (configurable in Settings) |

PolySSH never copies, moves or stores your private keys, and never stores passwords. Password authentication is handled by `ssh` itself at connection time.

## Contributing

Issues and pull requests are welcome. The project deliberately avoids heavy dependencies; please keep it standard-library-only unless there is no reasonable alternative.

## License

PolySSH is free software, released under the [GNU General Public License v3.0](LICENSE) (or any later version at your option). You are free to use, study, share and modify it; derivative works must remain under the same license.

Copyright (C) 2025-2026 Accel IT Pty Ltd.
