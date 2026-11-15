"""Caller's phone camera as a sensor: one vision reading per frame -> one camera segment for fusion.

The device that starts a call (Call in, or a scripted / blank call in the web app) also streams its camera:
one JPEG every few seconds. For each frame:
- read_frame(jpeg): the vision model's only job, a FrameReading (structured output, closed vocabularies:
  the room the camera is in, smoke / flames, hazards, people). Never floors, sides or addresses: a single
  phone photo can't support them, and counts/geometry are computed in code, never by an LLM.
- observe(reading, state): deterministic, no LLM. Turns the reading into one plain sentence and the facts it
  states (each quote is a substring of that sentence, so the evidence is grounded by construction), and
  keeps only what is new for this camera since its last segment: a room change, a new hazard, smoke -> flames,
  people seen. Nothing new -> None, so a steady view doesn't flood the transcript.

Rooms use the plan index's room types ("living room", "staircase", ...) so resolve_place can match them.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .extract import ExtractedFact

log = logging.getLogger(__name__)

Room = Literal["kitchen", "living room", "dining room", "bedroom", "bathroom", "hallway", "staircase",
               "entrance", "garage", "basement", "attic", "office", "laundry room", "closet", "outside"]
Hazard = Literal["propane", "gas", "gasoline", "chemical", "battery", "oxygen", "electrical", "fireworks"]

ROOM_MIN_CONF = 0.6     # below this the room is left out (the model was unsure)

_SYSTEM = ("You look at one photo from the phone camera of a person calling a fire emergency line. Report only "
           "what is clearly visible in this photo. Never guess anything you cannot see; if unsure, leave it out.")
_PROMPT = ("Answer for this photo:\n"
           "- usable: false if the photo is too dark, blurred or covered to tell anything.\n"
           "- room: the type of room the camera is in, only if the room itself is visible (fixtures, furniture), "
           "'outside' if the camera is outdoors; null if you can't tell. A screen showing a scene: read the scene.\n"
           "- room_confidence: 0-1.\n"
           "- fire: 'flames' if flames are visible, else 'smoke' if smoke is visible, else 'none'.\n"
           "- hazards: things that make a fire more dangerous that are clearly visible: propane or gas cylinders "
           "(propane / gas), gasoline cans, chemicals, lithium batteries or e-bikes (battery), oxygen tanks, "
           "exposed or arcing wiring or power lines (electrical), fireworks. Empty if none.\n"
           "- people_visible: true only if a person other than the photographer is visible.\n"
           "- summary: one short plain sentence of what the photo shows.")


class FrameReading(BaseModel):
    usable: bool = Field(description="False if the photo is too dark, blurred or covered to read")
    room: Optional[Room] = Field(default=None, description="Room type the camera is in; null if unclear")
    room_confidence: float = Field(default=0, ge=0, le=1)
    fire: Literal["none", "smoke", "flames"] = "none"
    hazards: list[Hazard] = Field(default_factory=list)
    people_visible: bool = False
    summary: str = Field(default="", description="One short plain sentence of what the photo shows")


def read_frame(jpeg: bytes) -> Optional[FrameReading]:
    """One vision call (LLM_PROVIDER's vision model, then the fallback). None when no model is configured or
    every attempt failed."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from .llm import structured

    block = {"type": "image", "base64": base64.b64encode(jpeg).decode(), "mime_type": "image/jpeg"}
    return structured("vision", FrameReading,
                      [SystemMessage(_SYSTEM), HumanMessage(content=[{"type": "text", "text": _PROMPT}, block])],
                      retries=1)


_FIRE_RANK = {"none": 0, "smoke": 1, "flames": 2}


@dataclass
class CameraState:
    """What this camera has already put in the transcript (observe() only reports changes)."""
    room: Optional[str] = None
    fire: str = "none"
    hazards: set[str] = field(default_factory=set)
    people: bool = False


def observe(reading: FrameReading, state: CameraState) -> Optional[tuple[str, list[ExtractedFact]]]:
    """(sentence, facts) for what is new in this reading, or None. Updates `state`."""
    if not reading.usable:
        return None
    parts: list[tuple[str, ExtractedFact]] = []

    room = reading.room if reading.room_confidence >= ROOM_MIN_CONF else None
    if room and room != state.room:
        phrase = "Camera is outside the building" if room == "outside" else f"Camera is in the {room}"
        parts.append((phrase, ExtractedFact(key="caller.room", value=room, quote=phrase)))
        state.room = room

    if _FIRE_RANK[reading.fire] > _FIRE_RANK[state.fire]:
        phrase = f"{reading.fire.capitalize()} visible"
        parts.append((phrase, ExtractedFact(key="incident.visible", value=reading.fire, quote=phrase)))
        state.fire = reading.fire

    for hz in reading.hazards:
        if hz in state.hazards:
            continue
        phrase = f"{hz.capitalize()} hazard visible"
        parts.append((phrase, ExtractedFact(key="hazards.reported", value=hz, quote=phrase)))
        state.hazards.add(hz)

    if reading.people_visible and not state.people and room != "outside":
        phrase = "Person visible inside"
        parts.append((phrase, ExtractedFact(key="people.reported", value=True, quote=phrase)))
        state.people = True

    if not parts:
        return None
    sentence = " ".join(f"{p}." for p, _ in parts)   # each phrase is its fact's quote, verbatim
    if summary := reading.summary.strip():
        sentence += f" ({summary.rstrip('.')}.)"
    return sentence, [f for _, f in parts]
