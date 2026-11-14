"""Gemini vision: label stairs / exits / roof access / adjacent structures in one image.

Vision only produces 2D labels + confidence. It never estimates distances,
heights or counts - those are computed in rules.py from coordinates/records.
One pass per image; results are cached on disk by image reference.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
from pathlib import Path
from typing import Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ..llm import gemini_configured, structured

log = logging.getLogger("flamescout.vision")

Context = Literal["scan_floorplan", "scan_frame", "aerial_crop"]

_PROMPTS: dict[str, str] = {
    "scan_floorplan": "This is a building floor plan. Label every stairwell, exit door, entry, "
                      "elevator, standpipe, fire department connection (FDC) and key box you can see.",
    "scan_frame": "This is an interior photo from a 3D building scan. Label any stairwell, exit "
                  "door, elevator, standpipe, FDC or key box that is clearly visible.",
    "aerial_crop": "This is an aerial image centred on a target building. Label roof access "
                   "points (hatches, bulkheads) on the target and adjacent structures around it.",
}


class Detection(BaseModel):
    kind: Literal["entry", "stair", "exit", "elevator", "key_box", "roof_access", "fdc",
                  "standpipe", "adjacent_structure", "other"]
    label: str = Field(description="Short label, e.g. 'Stair B (north)'")
    floor: Optional[int] = Field(default=None, description="Floor number only if printed on the image")
    bbox: Optional[list[float]] = Field(default=None, description="[x, y, w, h] normalised 0-1")
    confidence: float = Field(ge=0, le=1)


class DetectionResult(BaseModel):
    detections: list[Detection] = []


def _image_block(ref: str) -> Optional[dict]:
    if ref.startswith(("http://", "https://")):
        return {"type": "image", "url": ref}
    p = Path(ref)
    if p.is_file():
        mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
        return {"type": "image", "base64": base64.b64encode(p.read_bytes()).decode(), "mime_type": mime}
    return None   # fixture:// or unknown -> nothing to look at


def detect(image_ref: Optional[str], context: Context, cache_dir: Path) -> Optional[list[dict]]:
    """Returns detections (source="vision"), [] if none, or None if vision could not run."""
    if not image_ref or not gemini_configured("vision"):
        return None
    block = _image_block(image_ref)
    if block is None:
        return None
    key = hashlib.sha1(f"{context}:{image_ref}".encode()).hexdigest()[:16]
    cache = cache_dir / f"{key}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    msgs = [SystemMessage("Label only what is clearly visible. If unsure, omit it or give low "
                          "confidence. Never guess positions you cannot see."),
            HumanMessage(content=[{"type": "text", "text": _PROMPTS[context]}, block])]
    res = structured("vision", DetectionResult, msgs)
    if res is None:
        return None
    out = [{**d.model_dump(), "source": "vision"} for d in res.detections]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out))
    return out
