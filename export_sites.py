"""Export the saved session list to formats other SFTP/SSH clients can
import, so switching auth methods (or just trying another client) doesn't
mean re-entering every host by hand.

Field accuracy notes:
  - WinSCP's ini keys (HostName/PortNumber/UserName/FSProtocol/PublicKeyFile)
    and FSProtocol=1 for SFTP are confirmed directly against WinSCP's own
    SessionData.h TFSProtocol enum and real example ini output.
  - FileZilla's numeric Protocol/Logontype codes used here (1=SFTP,
    5=key file) are the values consistently reported across community
    examples, but are not independently confirmed against FileZilla's
    current source — that enum has reportedly shifted across versions in
    the past. Worth a one-time test import before relying on it.
  - Proxy jump hosts have no verified equivalent field in either format and
    are not exported; sessions using one will import as a direct connection.
  - WinSCP historically expects PuTTY-format (.ppk) private keys; modern
    versions (5.15+) accept OpenSSH-format keys directly, which is what
    gets written here as-is.
  - Both formats' site entries hold exactly one key file. Sessions with
    multiple registered identities (e.g. several YubiKeys) export whichever
    one is currently selected in PolySSH, not just whichever is listed
    first — those are no longer necessarily the same thing.
"""

import xml.etree.ElementTree as ET
import xml.dom.minidom
from typing import Optional

from models import Session
from ssh import get_selected_identity, resolve_identity_file

_IDENTITY_AUTH_METHODS = ("keyfile",)


def _selected_identity_path(session: Session, identity_key_dir: Optional[str]) -> Optional[str]:
    selected = get_selected_identity(session.identity_file)
    if not selected:
        return None
    return resolve_identity_file(selected, identity_key_dir)


def _winscp_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\\", "%5C")


def _winscp_section_name(session: Session) -> str:
    segments = session.folder.split("/") if session.folder else []
    segments.append(session.name)
    return "\\".join(_winscp_escape(seg) for seg in segments)


def export_winscp(sessions: list[Session], identity_key_dir: Optional[str]) -> str:
    lines: list[str] = []
    for s in sessions:
        lines.append(f"[Sessions\\{_winscp_section_name(s)}]")
        lines.append(f"HostName={s.hostname}")
        lines.append(f"PortNumber={s.port}")
        if s.username:
            lines.append(f"UserName={s.username}")
        lines.append("FSProtocol=1")
        key_path = _selected_identity_path(s, identity_key_dir) if s.auth_method in _IDENTITY_AUTH_METHODS else None
        if key_path:
            lines.append(f"PublicKeyFile={_winscp_escape(key_path)}")
        lines.append("")
    return "\n".join(lines)


def export_filezilla(sessions: list[Session], identity_key_dir: Optional[str]) -> str:
    root = ET.Element("FileZilla3")
    servers = ET.SubElement(root, "Servers")

    for s in sessions:
        server = ET.SubElement(servers, "Server")
        ET.SubElement(server, "Host").text = s.hostname
        ET.SubElement(server, "Port").text = str(s.port)
        ET.SubElement(server, "Protocol").text = "1"  # SFTP
        ET.SubElement(server, "Type").text = "0"
        if s.username:
            ET.SubElement(server, "User").text = s.username

        key_path = _selected_identity_path(s, identity_key_dir) if s.auth_method in _IDENTITY_AUTH_METHODS else None
        if key_path:
            ET.SubElement(server, "Logontype").text = "5"  # key file
            ET.SubElement(server, "Keyfile").text = key_path
        else:
            ET.SubElement(server, "Logontype").text = "1"  # normal, prompts for password

        name = f"{s.folder}/{s.name}" if s.folder else s.name
        ET.SubElement(server, "Name").text = name

    raw = ET.tostring(root, encoding="unicode")
    return xml.dom.minidom.parseString(raw).toprettyxml(indent="\t")


def export_generic_uri(sessions: list[Session]) -> str:
    lines: list[str] = []
    for s in sessions:
        target = f"{s.username}@{s.hostname}" if s.username else s.hostname
        uri = f"sftp://{target}" if s.port == 22 else f"sftp://{target}:{s.port}"
        lines.append(uri)
    return "\n".join(lines) + "\n"
