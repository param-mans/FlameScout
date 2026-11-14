"""Incident fusion: deterministic merge of caller facts into one IncidentRecord.

Rules (PRD FR-2, R-08, R-09):
- Only caller speech produces facts; every fact is status "reported",
  confidence capped at REPORTED_CAP, with a transcript_span evidence item.
- Same key, same value  -> add evidence, small confidence bump (corroboration).
- Same key, different value, DIFFERENT call -> Contradiction (never auto-resolved),
  fact status becomes "contradicted".
- Same key, different value, SAME call -> treated as the caller correcting themselves.
- List keys (hazards, access notes) are unioned, never contradicted.
- open_questions = Appendix B questions for high-value keys still missing.
"""
from __future__ import annotations

from typing import Iterable, Optional

from flamescout_schemas import (CallSegment, Contradiction, ContradictionValue, Evidence,
                                Fact, IncidentRecord, now)

from .extract import ExtractedFact, Extractor, KeywordExtractor

REPORTED_CAP = 0.6
BASE_CONF = 0.5
CORROBORATION_BUMP = 0.05

# incident.visible is a list: smoke then flames is escalation, not a contradiction.
LIST_KEYS = {"hazards.reported", "access.notes", "incident.visible"}

# key -> copilot question (Appendix B). Order = priority.
QUESTIONS: dict[str, str] = {
    "location.address": "What is the exact address or nearest cross street?",
    "people.trapped": "Is anyone trapped or unaccounted for? Which floor and where?",
    "incident.floor": "Which floor is the fire or smoke on?",
    "incident.visible": "Where is the smoke or fire visible - which side and floor?",
    "hazards.reported": "Any gas, chemicals, or other hazardous materials mentioned?",
    "alarm.sounding": "Is the fire alarm sounding?",
    "access.notes": "Which entrance is accessible? Is there a key box?",
}


def _norm(v):
    return v.strip().lower() if isinstance(v, str) else v


class FusionEngine:
    def __init__(self, incident_id: str, extractor: Optional[Extractor] = None):
        self.record = IncidentRecord(incident_id=incident_id)
        self.extractor = extractor or KeywordExtractor()
        self._ev_counter = 0
        self._last_call_for_key: dict[str, str] = {}
        self._span_for_key: dict[str, dict] = {}
        self._refresh()

    # ---------- public ----------
    def ingest(self, seg: CallSegment) -> list[str]:
        """Process one transcript segment. Returns human-readable change notes."""
        if seg.call_id not in self.record.call_ids:
            self.record.call_ids.append(seg.call_id)
        changes: list[str] = []
        if seg.speaker == "caller":
            for ef in self.extractor.extract(seg.text):
                note = self._merge(seg, ef)
                if note:
                    changes.append(note)
        self._refresh()
        return changes

    def ingest_all(self, segs: Iterable[CallSegment]) -> IncidentRecord:
        for s in segs:
            self.ingest(s)
        return self.record

    # ---------- internals ----------
    def _evidence(self, seg: CallSegment, ef: ExtractedFact) -> str:
        self._ev_counter += 1
        eid = f"ev_call_{self._ev_counter}"
        self.record.evidence[eid] = Evidence(
            id=eid, kind="transcript_span", source="call",
            locator={"call_id": seg.call_id, "start": seg.t_start, "end": seg.t_end,
                     "text": seg.text, "quote": ef.quote},
        )
        return eid

    def _merge(self, seg: CallSegment, ef: ExtractedFact) -> Optional[str]:
        facts = self.record.facts
        eid = self._evidence(seg, ef)
        span = {"start": seg.t_start, "end": seg.t_end, "text": seg.text}

        if ef.key in LIST_KEYS:
            f = facts.get(ef.key)
            if f is None:
                facts[ef.key] = Fact(key=ef.key, value=[ef.value], status="reported",
                                     confidence=BASE_CONF, evidence_ids=[eid])
                return f"+ {ef.key}: {ef.value} ({seg.call_id})"
            if _norm(ef.value) not in [_norm(x) for x in f.value]:
                f.value.append(ef.value)
                f.evidence_ids.append(eid)
                f.updated_at = now()
                return f"+ {ef.key}: {ef.value} ({seg.call_id})"
            f.evidence_ids.append(eid)
            return None

        f = facts.get(ef.key)
        if f is None:
            facts[ef.key] = Fact(key=ef.key, value=ef.value, status="reported",
                                 confidence=BASE_CONF, evidence_ids=[eid])
            self._last_call_for_key[ef.key] = seg.call_id
            self._span_for_key[ef.key] = span
            return f"+ {ef.key}: {ef.value} ({seg.call_id})"

        if _norm(f.value) == _norm(ef.value):
            f.evidence_ids.append(eid)
            if f.status != "contradicted":
                f.confidence = min(REPORTED_CAP, f.confidence + CORROBORATION_BUMP)
            f.updated_at = now()
            return None

        prev_call = self._last_call_for_key.get(ef.key)
        if prev_call == seg.call_id and f.status != "contradicted":
            old = f.value
            f.value, f.evidence_ids, f.updated_at = ef.value, [eid], now()
            self._span_for_key[ef.key] = span
            return f"~ {ef.key}: {old} -> {ef.value} (caller corrected, {seg.call_id})"

        # Different calls disagree -> contradiction. Never auto-resolve.
        c = next((c for c in self.record.contradictions if c.key == ef.key), None)
        if c is None:
            c = Contradiction(key=ef.key, values=[
                ContradictionValue(call_id=prev_call or "?", value=f.value,
                                   span=self._span_for_key.get(ef.key, {}))])
            self.record.contradictions.append(c)
        if all(_norm(v.value) != _norm(ef.value) for v in c.values):
            c.values.append(ContradictionValue(call_id=seg.call_id, value=ef.value, span=span))
        old = f.value
        f.status = "contradicted"
        f.evidence_ids.append(eid)
        f.updated_at = now()
        return f"! {ef.key}: {old} vs {ef.value} (contradiction, {seg.call_id})"

    def resolve(self, key: str, value) -> None:
        """Dispatcher picks the correct value for a contradiction."""
        for c in self.record.contradictions:
            if c.key == key:
                c.resolved_by_dispatcher = True
        f = self.record.facts[key]
        f.value, f.status, f.updated_at = value, "reported", now()
        self._refresh()

    def _refresh(self) -> None:
        r = self.record
        r.open_questions = [q for k, q in QUESTIONS.items() if k not in r.facts]
        has_location = "location.address" in r.facts or "location.name" in r.facts
        r.ready_to_brief = has_location
        if r.status == "listening" and has_location:
            r.status = "locating"
