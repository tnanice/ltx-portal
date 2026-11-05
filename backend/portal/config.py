import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    data: Path = field(default_factory=lambda: Path(os.getenv('DATA_DIR', '/data')))
    admin_user: str = field(default_factory=lambda: os.getenv('ADMIN_USERNAME', 'admin'))
    admin_password: str = field(default_factory=lambda: os.getenv('ADMIN_PASSWORD', ''))
    source: str = field(default_factory=lambda: os.getenv('CATALOG_URL', 'https://ltx.libray.workers.dev/api/items'))
    public_url: str = field(default_factory=lambda: os.getenv('PUBLIC_URL', '').rstrip('/'))
    secure_cookie: bool = field(default_factory=lambda: os.getenv('COOKIE_SECURE', 'false').lower() == 'true')
    allowed_hosts: list[str] = field(default_factory=lambda: os.getenv('ALLOWED_HOSTS', '*').split(','))
    static: Path = field(default_factory=lambda: Path(os.getenv('STATIC_DIR', '/app/static')))
    background: bool = True
    session_ttl: int = 7 * 86400
    ticket_ttl: int = 12 * 3600
