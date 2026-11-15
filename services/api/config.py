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
    # orchestrator: the deep agent writes the briefs, rules pipeline as fallback. rules: rules only.
    pipeline: str = field(default_factory=lambda: os.getenv("PIPELINE", "orchestrator").lower())
    # 1 (default): confirm the location without the dispatcher when the locator is sure (one candidate, or
    # best score >= 0.8 and >= 0.2 ahead of the second); 0: always wait for POST /confirm-location.
    auto_confirm_location: bool = field(default_factory=lambda: os.getenv(
        "AUTO_CONFIRM_LOCATION", "1").strip().lower() not in ("0", "false", "no", "off", ""))
    cors_origins: list[str] = field(default_factory=lambda: [
        o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()])

    # Call in: a second call within this many seconds joins the same incident as c2.
    call_incident_window_s: float = field(
        default_factory=lambda: float(os.getenv("CALL_INCIDENT_WINDOW_S", "600")))

    def env(self) -> dict[str, str]:
        return dict(os.environ)
