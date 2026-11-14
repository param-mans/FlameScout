"""Settings from env (.env is loaded once, never committed)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@dataclass
class Settings:
    data_mode: str = field(default_factory=lambda: os.getenv("DATA_MODE", "fixture").lower())
    workspace: Path = field(default_factory=lambda: Path(os.getenv("WORKSPACE_DIR", ROOT / "workspace")))
    fixtures: Path = field(default_factory=lambda: Path(os.getenv("FIXTURES_DIR", ROOT / "data" / "fixtures")))
    tiger_url: str | None = field(default_factory=lambda: os.getenv("TIGER_DATABASE_URL") or None)
    cors_origins: list[str] = field(default_factory=lambda: [
        o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()])

    def env(self) -> dict[str, str]:
        return dict(os.environ)
