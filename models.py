from dataclasses import dataclass
from typing import Optional


@dataclass
class Session:
    name: str
    hostname: str
    port: int
    auth_method: str
    id: Optional[int] = None
    folder: Optional[str] = None
    username: Optional[str] = None
    identity_file: Optional[str] = None
    proxy_jump: Optional[str] = None
    sort_order: Optional[int] = None
    background_color: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
