import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class Settings:
    window_width: int = 480
    window_height: int = 620
    last_session_id: Optional[int] = None
    font_face: Optional[str] = None
    font_size: Optional[int] = None
    terminal_cols: Optional[int] = None
    terminal_rows: Optional[int] = None
    identity_key_dir: Optional[str] = "~/.ssh"


class SettingsManager:
    def __init__(self, path: Path) -> None:
        self._path = path
        self.settings = self._load()

    def _load(self) -> Settings:
        if not self._path.exists():
            return Settings()
        try:
            with self._path.open(encoding="utf-8") as f:
                data = json.load(f)
            valid = {k: v for k, v in data.items() if k in Settings.__dataclass_fields__}
            return Settings(**valid)
        except Exception:
            return Settings()

    def save(self) -> None:
        with self._path.open("w", encoding="utf-8") as f:
            json.dump(asdict(self.settings), f, indent=2)
