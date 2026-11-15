"""Text the caller a link over iMessage with Photon Stable (spectrum-ts). SIMULATION: consenting demo phones only.

spectrum-ts is TypeScript only, so the sending happens in the messenger service (services/messenger/server.mjs,
its own container in docker-compose.yml); this module asks it: POST {MESSENGER_URL}/send {to, text}.
Photon Stable can start a conversation (a DM to any iMessage number), so the link goes out at once.

Env: PHOTON_PROJECT_ID + PHOTON_PROJECT_SECRET (PHOTON_API_KEY is accepted as the secret), read by the messenger;
MESSENGER_URL (default http://localhost:8090; http://messenger:8090 in Docker). Not configured = the dispatcher
gets the link to share by hand.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("flamescout.messaging")


@dataclass
class Result:
    state: str                     # sent | failed | not_configured | none (no number)
    detail: str
    line: Optional[str] = None     # the Photon line it was sent from, when the messenger knows it


def configured() -> bool:
    return bool(os.getenv("PHOTON_PROJECT_ID", "").strip()
                and (os.getenv("PHOTON_PROJECT_SECRET", "").strip() or os.getenv("PHOTON_API_KEY", "").strip()))


def messenger_url() -> str:
    return os.getenv("MESSENGER_URL", "").strip().rstrip("/") or "http://localhost:8090"


def normalize(phone: str) -> Optional[str]:
    """'(609) 555-0123' -> '+16095550123' (10 digits = North America); None if it isn't a phone number."""
    digits = re.sub(r"\D", "", phone or "")
    if phone.strip().startswith("+") and 8 <= len(digits) <= 15:
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


def masked(phone: str) -> str:
    return f"…{phone[-4:]}" if len(phone) >= 4 else "…"


def send_link(to: str, text: str) -> Result:
    """Blocking (a few seconds). Never raises: a failed text must not break the dispatcher's flow."""
    if not configured():
        return Result("not_configured", "iMessage is not configured (PHOTON_* in .env): share the link by hand.")
    import httpx

    try:
        r = httpx.post(f"{messenger_url()}/send", json={"to": to, "text": text}, timeout=30)
        body = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("messenger unreachable at %s: %r", messenger_url(), e)
        return Result("failed", f"iMessage failed: messenger not reachable ({e.__class__.__name__})")
    if not body.get("ok"):
        log.warning("iMessage to %s failed: %s", masked(to), body.get("error"))
        return Result("failed", f"iMessage failed: {body.get('error') or f'HTTP {r.status_code}'}")
    log.info("camera link sent by iMessage to %s", masked(to))
    return Result("sent", f"Sent by iMessage to {masked(to)}", body.get("line"))
