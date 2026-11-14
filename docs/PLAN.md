# **FlameScout — Product Requirements Document**

|  |  |
| ----- | ----- |
| **Special tracks targeted** | CoStar "Inside and Out", Amazon Practical AI, LangChain deepagents, Peri (conditional), Photon, ChatOverflow, \+ MLH: Gemini, Tiger Data, Vultr (+ cheap extras) |

> **How to use this document.** It is written so a human or an AI coding assistant can build from it. Anything marked **ASSUMPTION** or **TBD** must be verified at the smoke test (Section 17, Phase 0\) — do not invent API fields; build against the adapter interfaces in Section 7 and fall back to fixtures.

---

## **1\. Summary**

**One-liner:** FlameScout turns live fire-dispatch calls into a building-specific size-up on the crew's MDT (mobile data terminal) before the engine arrives.

**Problem.** On arrival, company officers burn minutes finding entries, stairs, occupancy, and hazards. Pre-incident plans cover few buildings and go stale. Information about the incident is scattered across several callers.

**Solution.** A pipeline that:

1. **Listens** to dispatch calls and fuses multiple callers into one evolving incident record, flagging contradictions.  
2. **Locates** the building (dispatcher confirms the address).  
3. **Pulls a dossier** from CoStar (use, size, stories, year built, tenants), Matterport (layout, stairs, exits, measurements, scan date), and Vexcel (roof, adjacent buildings, approach).  
4. **Synthesizes** a size-up card, access/egress map, hazard flags, and ranked *considerations*, each with rationale, confidence, and linked evidence.  
5. **Delivers** it on a glanceable MDT interface that updates as new call information arrives.

**Design principles (non-negotiable)**

* **Advisory only.** The incident commander decides. Wording is "consider…", never orders.  
* **Evidence-linked and dated.** Every claim carries a source, timestamp, and confidence.  
* **Deterministic checks first.** Computable things (distances, heights, counts) are computed, not guessed by an LLM.  
* **Abstain over guess.** Missing or unverifiable → shown as "unverified", never filled in.  
* **Simulation only.** Never connect to real 911 lines or call emergency numbers.

## **2\. Goals, non-goals, success metrics**

### **Goals**

* **G1** One end-to-end, convincing vertical slice on 3 scanned hero buildings plus 1 no-scan building.  
* **G2** Show real use of at least two (target: all three) CoStar-toolkit layers where the combination yields something neither gives alone.  
* **G3** Genuinely agentic: planning, subagents, tool use, verification loop, action (L2 callback if time).  
* **G4** Measured results we can state honestly in the pitch.  
* **G5** A clean, glanceable UI that reads in seconds.

### **Non-goals (this build)**

* Tactical commands or code-compliance determinations.  
* EMS and law-enforcement modules (roadmap only).  
* Fully autonomous call-taking (L3 is a vision slide only).  
* Building interiors without Matterport scans (fallback shows gaps instead).

### **Success metrics (targets are hypotheses; report only measured numbers)**

| Metric | Definition | Target |
| ----- | ----- | ----- |
| Time-to-brief | Confirmed address → first brief rendered | ≤ 10 s with cached dossier; ≤ 45 s cold |
| Incident update latency | Transcript segment → incident record updated in UI | ≤ 3 s (P50) |
| Fact accuracy | Precision/recall of brief facts vs. manually annotated ground truth | Report; aim ≥ 85% precision |
| Unsupported-claim rate | Claims in brief without evidence | 0 |
| Contradiction detection | Seeded contradictions surfaced | 100% on test scripts |
| Correct abstention | No-scan building shows gaps, not fabricated interior data | 100% |

## **3\. Users and scenarios**

| Persona | Need | Moment |
| ----- | ----- | ----- |
| **Company officer** (primary) | Know entries, stairs, occupancy, hazards before arriving | Reviewing MDT en route |
| **Dispatcher** | Capture the right facts fast, confirm location | During the live call |
| **Chief / incident commander** | Shared picture, data freshness, confidence | En route and on scene |

**Primary scenario.** Call 1 reports smoke from a mid-rise commercial building. The dispatcher confirms the address. FlameScout builds the incident record, pulls the dossier, and the officer's MDT shows the size-up card, stair/exit markers in the scan, hazard flags from tenants, adjacent exposures, and ranked considerations. Call 2 arrives with a different floor; the system flags the contradiction and updates the brief.

## **4\. Scope by phase**

| ID | Feature | MVP | v1 | v2 | Final |
| ----- | ----- | ----- | ----- | ----- | ----- |
| F1 | Scripted dispatch calls with 2 callers \+ 1 contradiction | ● |  |  |  |
| F2 | Real-time transcription \+ speaker split | ● |  |  |  |
| F3 | Incident fusion \+ contradiction flags | ● |  |  |  |
| F4 | Location resolution \+ dispatcher confirmation | ● |  |  |  |
| F5 | Dossier (CoStar \+ Matterport; Vexcel exterior) | ● |  |  |  |
| F6 | Brief \+ egress map in MDT-style UI, with confidence and scan date | ● |  |  |  |
| F7 | No-scan fallback state | ● |  |  |  |
| F8 | L1 dispatcher copilot (suggested questions) | ● |  |  |  |
| F9 | Progressive live updates (brief versions) | ◐ | ● |  |  |
| F10 | Occupancy-based hazard rules (expanded) | ◐ | ● |  |  |
| F11 | Hydrant / water-supply data |  | ● |  |  |
| F12 | Crew correction loop (feedback memory) | ◐ | ● |  |  |
| F13 | Offline cache \+ precomputed district dossiers |  | ● |  |  |
| F14 | Multilingual transcripts |  | ● |  |  |
| F15 | Role-based access \+ audit log | ◐ | ● |  |  |
| F16 | L2 building callback (property contact) | ◐ | ● |  |  |
| F17 | CAD / MDT integration |  |  | ● |  |
| F18 | Live-incident layer (wind, weather, unit status) |  |  | ● |  |
| F19 | Drill mode |  |  | ● |  |
| F20 | After-action calibration |  |  | ● |  |
| F21 | Portfolio triage \+ staleness watch |  |  | ● |  |
| F22 | Phone / drone scan updates |  |  | ● |  |
| F23 | Regional mutual-aid sharing |  |  |  | ● |
| F24 | Predictive building-risk scoring |  |  |  | ● |
| F25 | Opt-in live building signals (alarm panels) |  |  |  | ● |
| F26 | Heads-up / AR guidance |  |  |  | ● |
| F27 | L3 overflow call-taker (policy-approved) |  |  |  | ● |
| F28 | EMS and law-enforcement modules |  |  |  | ● |

● \= in scope · ◐ \= lite version / stretch inside the MVP window

**MVP priorities.** P0 \= F1–F8. P1 \= F9, F12 (lite), F15 (lite). P2 \= F10 (lite), F16 stretch.

## **5\. Functional requirements (MVP)**

### **FR-1 Call ingestion and transcription (F1, F2) — P0**

* Input: one audio stream per call (pre-recorded or TTS-generated scripts for the demo; live mic optional).  
* Streaming speech-to-text with speaker separation (call-taker vs. caller).  
* Emits `transcript.segment` events with `call_id`, `speaker`, `text`, `t_start`, `t_end`.  
* **Acceptance:** two simultaneous or sequential calls produce correctly labeled segments in the UI within 3 s of speech. A text-feed fallback (replay JSON transcripts) exists and is selectable with a flag.

### **FR-2 Incident fusion (F3) — P0**

* A fusion agent consumes segments and maintains one `IncidentRecord` (Section 7).  
* Extracted fields: address/building name, floor, occupants reported/trapped, smoke/fire visible location, hazards mentioned (gas, chemicals), alarm sounding, access notes.  
* Contradictions: same key, different values from different calls → `Contradiction` listing both values with transcript spans. **Never auto-resolve.**  
* Generates `open_questions` for missing high-value fields.  
* **Acceptance:** on scenario S2 the contradiction is flagged with both spans; on S1 the record reaches "ready to brief" status.

### **FR-3 Location resolution and confirmation (F4) — P0**

* Resolve address/name → ranked building candidates (geocoder \+ CoStar property search).  
* UI shows candidates; **brief generation requires dispatcher confirmation** (one tap).  
* **Acceptance:** S3 (wrong name) shows ≥ 2 candidates; no brief is produced before confirmation; changing the confirmation regenerates the dossier and brief.

### **FR-4 Dossier assembly (F5) — P0**

* Adapters (Section 7.4) fetch:  
  * **CoStar:** property use, location, matter port id  
  * **Matterport:** model ID, floor plans, stairs/exits/measurements via annotations or vision extraction, scan capture date.  
  * **Vexcel:** aerial imagery for roof, adjacent buildings, approach/street width.  
* Gemini vision runs on floor plans / scan frames / aerial crops to detect and label stairs, exits, elevators, roof access, and adjacent structures. Every detection → `Evidence` \+ `Fact` with confidence.  
* Cache dossiers (hero buildings are prefetched).  
* **Acceptance:** dossier JSON validates against schema; every fact has ≥ 1 evidence item or `status: unverified`.

### **FR-5 Deterministic rules engine — P0**

* Implements rules in Section 9\. Pure Python, unit-tested, no LLM calls.  
* **Acceptance:** each rule has tests (happy path \+ missing data → "unknown").

### **FR-6 Brief synthesis (F6) — P0**

* Strategy agent \+ critic produce a `Brief` (Section 7.3): size-up facts, access/egress markers, hazard flags, exposures, ≤ 5 ranked considerations, gaps, freshness banner, disclaimer.  
* Critic verifies: every claim has evidence or an explicit `unverified` tag; no consideration contradicts a rule output; language is advisory. Failures trigger up to 2 regeneration attempts, then safe fallback (facts only, no considerations).  
* **Acceptance:** unsupported-claim rate \= 0 on all scenarios.

### **FR-7 Evidence and provenance — P0**

* Evidence drawer: clicking any claim shows the source (scan frame, aerial crop, CoStar record, transcript span), capture date, and confidence.  
* **Acceptance:** 100% of rendered claims are clickable to evidence or labeled "unverified".

### **FR-8 MDT UI (F6) — P0**

* Specified in Section 10\. Glanceable, dark, high contrast, ≤ 3 taps to any detail.

### **FR-9 Live updates (F9) — P1**

* New facts create new brief versions (`version` increments); UI highlights what changed ("+ Floor changed 3→4, reported by Call 2").  
* **Acceptance:** S2 shows a visible diff between brief v1 and v2.

### **FR-10 No-scan fallback (F7) — P0**

* If Matterport scan missing: brief built from CoStar \+ Vexcel; interior sections show "No interior scan on file" with a gaps list. **No interior facts are invented.**  
* **Acceptance:** S4 renders a valid brief with gaps and confidence capped appropriately.

### **FR-11 L1 dispatcher copilot (F8) — P0**

* Silent listener suggests next questions based on missing/uncertain fields (e.g., "Is anyone trapped? Which floor?").  
* Dispatcher can dismiss or mark asked. Optional voice prompt via Gemini Live.  
* **Acceptance:** suggestions appear within 3 s of a relevant gap and disappear when the field is filled.

### **FR-12 L2 building callback (F16) — P2 stretch**

* Outbound call (Twilio) to a **consenting teammate** playing the property contact; voice agent asks occupancy, hazards, key-box location; answers merge into the incident record as `reported` facts with the call as evidence.  
* **Acceptance:** one successful end-to-end demo call; falls back to a recorded clip if telephony fails.

### **FR-13 Persistence and metrics — P0**

* Tiger Data (Postgres/TimescaleDB): call segments, facts, brief versions, and pipeline timings as time series; continuous aggregate or view for time-to-brief.  
* **Acceptance:** a metrics panel/query shows time-to-brief per run from the database.

### **FR-14 Audit and access (lite) — P1**

* Every brief view and every feedback action is logged (who, when, what). Simple role flag (dispatcher / officer / admin) via demo toggle.

### **FR-15 Crew feedback (lite) — P1**

* Officer can mark a fact "correct / wrong / outdated" with optional note → stored and shown in later briefs for the same building.

## **6\. System architecture**

```
 Call audio (scripted)           ┌─────────────────────────────────────────────┐
        │                        │                FastAPI backend (Vultr)      │
        ▼                        │                                             │
 Streaming STT + speaker split ─►│  Fusion agent ──► IncidentRecord ──┐        │
 (one stream per call)           │                                    ▼        │
                                 │  Locate (geocode + CoStar search) ─► confirm│
                                 │                                    │        │
                                 │  Dossier subagents  ◄──────────────┘        │
                                 │   ├─ CoStar adapter                         │
                                 │   ├─ Matterport adapter + Gemini vision     │
                                 │   └─ Vexcel adapter + Gemini vision         │
                                 │            │                                │
                                 │            ▼                                │
                                 │  Rules engine ─► Strategy agent ─► Critic   │
                                 │            │                       │        │
                                 │            └────────► Brief vN ◄───┘        │
                                 │                                             │
                                 │  Tiger Data (events, facts, versions, metrics)
                                 └──────────────┬──────────────────────────────┘
                                                │ WebSocket + REST
                                                ▼
                         React MDT UI + Dispatcher console (map, Matterport viewer)
```

**Orchestration:** LangChain **deepagents** (planner \+ subagents \+ scratch filesystem) with LangSmith tracing. Use LangChain's model interface so Gemini (vision/primary) and a second model (critic) can be swapped.

**Deployment:** single FastAPI service \+ Next.js app on Vultr (public IP for Twilio webhooks); managed Tiger Data instance.

## **7\. Data model and interfaces**

All schemas live in `packages/schemas` as Pydantic models; generate JSON Schema \+ TypeScript types for the frontend.

### **7.1 Core primitives**

```py
from pydantic import BaseModel, Field
from typing import Literal, Optional, Any
from datetime import datetime

FactStatus = Literal["verified", "inferred", "reported", "unverified", "contradicted"]

class Evidence(BaseModel):
    id: str
    kind: Literal["scan_frame", "scan_floorplan", "aerial_crop",
                  "costar_record", "transcript_span", "rule_output"]
    source: Literal["matterport", "vexcel", "costar", "call", "rules"]
    uri: Optional[str] = None
    captured_at: Optional[datetime] = None
    locator: Optional[dict] = None   # e.g. {"sweep_id": "...", "bbox": [x,y,w,h]} or {"call_id": "...", "start": 12.3, "end": 18.0}

class Fact(BaseModel):
    key: str                      # e.g. "building.stories", "incident.floor"
    value: Any
    status: FactStatus
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = []
    as_of: Optional[datetime] = None   # when underlying data was captured
    updated_at: datetime
```

**Status semantics**

* `verified` \= from a record or deterministic computation on records.  
* `inferred` \= from vision/model/heuristic.  
* `reported` \= from a caller.  
* `unverified` \= shown but not supported.  
* `contradicted` \= conflicting sources.

### **7.2 Incident record**

```py
class CallSegment(BaseModel):
    call_id: str
    speaker: Literal["call_taker", "caller"]
    text: str
    t_start: float
    t_end: float

class ContradictionValue(BaseModel):
    call_id: str
    value: Any
    span: dict            # {"start": float, "end": float}

class Contradiction(BaseModel):
    key: str
    values: list[ContradictionValue]
    resolved_by_dispatcher: bool = False

class IncidentRecord(BaseModel):
    incident_id: str
    status: Literal["listening", "locating", "confirmed", "briefing", "updated"]
    call_ids: list[str]
    facts: dict[str, Fact]               # location.address, incident.floor, people.trapped, hazards.reported, smoke.visible, alarm.sounding...
    contradictions: list[Contradiction] = []
    open_questions: list[str] = []
    confirmed_building_id: Optional[str] = None
```

### **7.3 Dossier and brief**

```py
class Marker(BaseModel):
    id: str
    kind: Literal["entry", "stair", "exit", "elevator", "key_box",
                  "roof_access", "fdc", "standpipe", "other"]
    label: str
    floor: Optional[int] = None
    scan_position: Optional[dict] = None   # {"x":..,"y":..,"z":..} in model space
    map_position: Optional[dict] = None    # {"lat":..,"lon":..}
    status: FactStatus
    confidence: float
    evidence_ids: list[str]

class BuildingDossier(BaseModel):
    building_id: str
    address: str
    coordinates: dict
    facts: dict[str, Fact]               # building.use, building.stories, building.year_built, building.area_sqft, ...
    tenants: list[Fact]
    markers: list[Marker]
    exposures: list[dict]                # adjacent structures with distance + evidence
    evidence: dict[str, Evidence]
    scan_available: bool
    scan_captured_at: Optional[datetime] = None
    matterport_model_id: Optional[str] = None
    generated_at: datetime

class Consideration(BaseModel):
    id: str
    rank: int
    category: Literal["access", "life_safety", "exposure", "hazard", "roof", "staging", "water_supply"]
    title: str                           # advisory phrasing: "Consider …"
    rationale: str
    confidence: float
    evidence_ids: list[str]

class Brief(BaseModel):
    brief_id: str
    incident_id: str
    version: int
    generated_at: datetime
    location_confirmed: bool
    freshness: dict                      # {"scan_age_days": int|None, "costar_as_of": str|None}
    size_up: dict[str, Fact]
    markers: list[Marker]
    hazards: list[Fact]
    exposures: list[dict]
    considerations: list[Consideration]  # max 5
    changes_since_last: list[str] = []
    gaps: list[str]                      # explicit unknowns
    disclaimer: str                      # "Advisory. Verify on arrival."
```

### **7.4 Data provider adapters (build against these; swap in fixtures)**

```py
class CoStarProvider(Protocol):
    def search_property(self, query: str) -> list[dict]: ...
    def get_property(self, property_id: str) -> dict: ...

class MatterportProvider(Protocol):
    def find_model(self, property: dict) -> Optional[dict]: ...        # model id, capture date
    def get_floorplans(self, model_id: str) -> list[dict]: ...
    def get_frames(self, model_id: str, floor: Optional[int]) -> list[dict]: ...

class VexcelProvider(Protocol):
    def get_aerial(self, lat: float, lon: float, radius_m: int) -> list[dict]: ...
```

Each real adapter lives next to a `Fixture*Provider` that reads `data/fixtures/<building_id>/…`. A `DATA_MODE=live|fixture` flag switches them. **Fixtures for the hero buildings must exist by the end of Phase 1** so the demo can never depend on live API uptime.

## **8\. Agent design (deepagents)**

| Agent | Responsibility | Tools | Output |
| ----- | ----- | ----- | ----- |
| **Orchestrator** | Plans per incident; delegates; tracks todo | all subagents | Brief versions |
| **Fusion** | Merge call segments into incident facts; detect contradictions; list open questions | `get_segments`, `upsert_fact`, `flag_contradiction` | `IncidentRecord` |
| **Locator** | Candidate buildings from address/name | `geocode`, `costar_search` | Ranked candidates |
| **Dossier** (parallel subagents) | Fetch and extract from CoStar / Matterport / Vexcel | provider adapters, Gemini vision | `BuildingDossier` |
| **Strategy** | Draft ranked considerations from dossier \+ incident \+ rule outputs | `run_rules`, `get_dossier`, `get_incident` | Draft `Brief` |
| **Critic** | Verify evidence links, rule consistency, advisory wording | `check_evidence`, `run_rules` | Pass / revise (≤ 2 loops) |
| **Copilot (L1)** | Suggest next questions from gaps | `get_incident` | Suggestions |

**Rules for all agents**

* Structured output only (Pydantic); reject and retry on schema violation.  
* Never state a fact without an evidence ID; otherwise emit `status: unverified`.  
* Geometry and arithmetic come from the rules engine or measured annotations, not from model guesses. Vision extracts 2D detections; spatial math is done in code.  
* Budget: set max tool calls and wall-clock per run; on timeout return the best partial brief marked "partial".  
* Trace every run in LangSmith; store run IDs with brief versions in Tiger Data.  
* Model routing: Gemini (Flash-class) for transcript fusion and copilot speed; Gemini (Pro-class) for vision on floor plans/scans/aerials; a different model family for the critic. **TBD:** confirm current model IDs in AI Studio.

## **9\. Rules engine specification**

All thresholds are **configurable placeholders in `config/rules.yaml`**, not operational standards. This is decision support, not a code-compliance check.

| ID | Rule | Inputs | Output |
| ----- | ----- | ----- | ----- |
| R-01 | Height estimate | stories × assumed floor height (default 3.5 m) | `building.height_est` (inferred, labeled estimate) |
| R-02 | Apparatus reach check | R-01 \+ department apparatus config | `reachable: yes/no/unknown` \+ margin; `unknown` if no config |
| R-03 | Exposure classification | distance to adjacent structures from aerial/footprint | Band: close / moderate / low (configurable cutoffs) |
| R-04 | Occupancy hazard map | CoStar use \+ tenant categories | Hazard flags (see table below), status `inferred` |
| R-05 | Egress inventory | markers per floor from scan | Counts; flag "only N stairwell(s) identified — verify on arrival" |
| R-06 | Data staleness | scan capture date, CoStar as-of date | Banner: fresh / aging / stale (configurable ages) |
| R-07 | Occupant-load estimate (P2) | floor area × use factor | Labeled estimate |
| R-08 | Contradiction detection | facts across calls | `Contradiction` records; dispatcher resolves |
| R-09 | Confidence aggregation | evidence confidences \+ status | Fact confidence (min-based; caller-reported capped) |

**R-04 starter map (heuristic flags, editable):**

* Restaurant / commercial kitchen → cooking/grease fire potential  
* Auto repair / service → flammable liquids  
* E-bike / battery / electronics retail → lithium-ion batteries  
* Salon / nail → chemicals, aerosols  
* Warehouse / storage → high-piled or dense storage  
* Dry cleaner / lab → solvents  
* Gym / assembly / church → high occupant load  
* Residential-over-commercial → sleeping occupants above

## **10\. UI specification**

### **Surfaces**

1. **Dispatcher console:** live call list with transcript, incident record panel (fields with status chips), contradiction banners, location candidates \+ confirm button, copilot suggestions.  
2. **MDT view (primary):** what the officer sees en route.

### **MDT layout (landscape tablet, dark theme)**

* **Top bar:** address/building name · confirmed badge · incident type · data-freshness chip ("Scan: 14 mo · CoStar: Oct 2026") · version \+ "changed" indicator.  
* **Left column — Size-up card:** use, stories, year built, area, occupancy; reported occupants/floor; hazards chips (each with confidence).  
* **Center — Map \+ Floor viewer toggle:** map with building footprint, exposures, approach route; Matterport viewer with tagged entries/stairs/exits/roof access.  
* **Right — Considerations:** ≤ 5 ranked cards ("Consider …"), each with rationale, confidence badge, evidence button.  
* **Bottom — Gaps strip:** "Unknown: sprinklers, standpipe, key box".  
* **Evidence drawer:** slides over; shows source image/record, date, confidence, "mark wrong/outdated".

### **Principles**

* Glanceable in ≤ 5 s; large type, high contrast, color \+ icon \+ text (never color alone).  
* Max 3 taps to any detail; no scrolling on the primary view at tablet size.  
* Distinct visual states for `verified / inferred / reported / unverified / contradicted`.  
* Empty states: "Listening…", "Awaiting address confirmation", "No interior scan on file".  
* Error states never block the demo: fixture mode toggle visible in admin menu.

### **Frontend stack**

React/Next.js, MapLibre or Mapbox for maps, Matterport viewer via the SDK/embed (**TBD:** keys and embed rules from the toolkit), WebSocket client for live events.

## **11\. API contracts**

### **REST**

| Method | Path | Purpose |
| ----- | ----- | ----- |
| POST | `/api/incidents` | Create incident |
| POST | `/api/calls/simulate` | Start a scripted call `{incident_id, script_id}` or upload audio |
| GET | `/api/incidents/{id}` | Incident record |
| POST | `/api/incidents/{id}/confirm-location` | `{building_id}` |
| GET | `/api/buildings/{id}/dossier` | Cached/fresh dossier |
| GET | `/api/incidents/{id}/briefs` | Brief versions |
| POST | `/api/incidents/{id}/feedback` | `{fact_key, verdict, note}` |
| GET | `/api/metrics/time-to-brief` | From Tiger Data |
| POST | `/api/copilot/suggestion/{id}/dismiss` | Dismiss suggestion |
| POST | `/api/l2/call` | (stretch) Start building callback |

### **WebSocket `/ws/incidents/{id}` events**

`transcript.segment` · `incident.updated` · `contradiction.raised` · `location.candidates` · `dossier.progress` · `brief.updated` · `copilot.suggestion` · `metric.tick`

Every event carries `incident_id`, `ts`, and a `trace_id` linking to LangSmith.

## **12\. Voice agent ladder**

| Level | Description | In build? | Constraints |
| ----- | ----- | ----- | ----- |
| **L1 Dispatcher copilot** | Silent; suggests questions; optional voice prompt | **Yes (P0)** | Human always in control |
| **L2 Building callback** | Calls property contact to confirm occupancy, hazards, key box | Stretch (P2) | Consenting teammate only in demo; label as simulation |
| **L3 Overflow call-taker** | Autonomous intake when operators saturated, with escalation | No — vision slide | Requires policy approval, human escalation path |

Telephony: Twilio programmable voice with media streams; Gemini Live (or equivalent) for the agent voice. **Fallback:** pre-recorded clip.

## **13\. Non-functional requirements**

* **Latency:** see Section 2 metrics; measure and log every stage (STT, fusion, dossier, strategy, critic, render).  
* **Reliability:** `DATA_MODE=fixture` makes the demo independent of external APIs; hero dossiers prefetched; scripted transcripts available as text feed.  
* **Privacy/security (demo scale):** no real personal data; synthetic calls; role flag \+ audit log; interior scan access limited to confirmed incidents; secrets only in env vars; no keys in the repo.  
* **Observability:** LangSmith traces \+ structured logs; a debug panel (admin) showing agent steps and rule outputs.  
* **Accessibility:** WCAG-minded contrast, text alternatives for icons.  
* **Cost:** cache aggressively; one vision pass per floor plan/aerial per building.

## **14\. Tech stack and prize alignment**

| Layer | Choice | Prize | What to show judges |
| ----- | ----- | ----- | ----- |
| Agents | LangChain deepagents \+ LangSmith | LangChain | Trace with planning, parallel subagents, critic loop |
| Models | Gemini (vision \+ Live) | Gemini | Vision calls on floor plans/aerials; voice copilot |
| Data | CoStar \+ Matterport \+ Vexcel via toolkit | CoStar | Brief where each claim shows which layer produced it |
| Speech | Streaming STT with diarization (AWS Transcribe preferred; Whisper fallback) | Amazon (optional) | Live two-caller transcript |
| DB | Tiger Data (Postgres/TimescaleDB) | Tiger Data | Hypertable \+ continuous aggregate dashboard of time-to-brief |
| Hosting | Vultr | Vultr | Public deployed URL |
| Telephony | Twilio | — | L2 demo call |
| Dev tooling | Cursor \+ Grok Bot | SpaceXSI raffle | Used during build; claim code on MLH |
| Extras | GoDaddy domain, DEV post | Domain, \$100 | Domain live; post published |
| Optional | MongoDB Atlas | MongoDB | **Only** if one teammate owns it; otherwise skip |
| Skip | Solana, hardware tracks, Arlequin | — | — |

**Rules for prize integrations**

* Each integration must be load-bearing and demonstrable; no logo-stuffing.  
* **Amazon:** include README with use case, target user, architecture, and how to run; working prototype; pitch.  
* **Peri (conditional):** interview a property manager (the person L2 would call) Saturday; capture business name, person's name/role, and a quote or screenshot. Ask Peri on Discord whether a fire company also counts.  
* MLH prizes are not in the HackPrinceton packet: confirm how they're judged (Devpost opt-in? demo?).

## **15\. Repo structure and conventions**

```
flamescout/
├── README.md                 # use case, target user, architecture, run instructions
├── PRD.md
├── .env.example
├── apps/web/                 # Next.js: dispatcher console + MDT view
├── services/
│   ├── api/                  # FastAPI: routes, ws, db, config
│   └── agents/
│       ├── orchestrator.py
│       ├── subagents/        # fusion.py locator.py dossier.py strategy.py critic.py copilot.py
│       └── tools/            # costar.py matterport.py vexcel.py geocode.py rules.py
├── packages/schemas/         # Pydantic models → JSON Schema + TS types
├── config/rules.yaml         # thresholds, hazard map, apparatus config
├── data/fixtures/            # per-building dossiers, call scripts, audio
├── evals/                    # ground_truth/*.json, run_evals.py, report.md
└── docs/                     # architecture.md, demo-script.md
```

**Environment variables (`.env.example`)** — names are placeholders until confirmed: `GEMINI_API_KEY`, `COSTAR_API_KEY`, `MATTERPORT_API_KEY`, `VEXCEL_API_KEY`, `TIGER_DATABASE_URL`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `LANGSMITH_API_KEY`, `MAP_TOKEN`, `DATA_MODE`

**Conventions (for humans and AI coding assistants)**

* Python 3.11+, full type hints, Pydantic for every cross-module payload.  
* Small functions; the rules engine has unit tests before wiring into agents.  
* Never invent third-party API fields: stub behind the adapter, log what's missing, and use fixtures.  
* Every fact creation site must set `status`, `confidence`, `evidence_ids`.  
* Log timings per stage into Tiger Data.  
* No secrets in git; `.env` is ignored.  
* Commit small and often; the repo must be public and created within the hackathon window.

## **16\. Test and evaluation plan**

### **Scenarios (synthetic scripts in `data/fixtures/calls/`)**

| ID | Scenario | Tests |
| ----- | ----- | ----- |
| **S1** | Clean single caller reports smoke from an upper floor at a scanned building | Happy path, time-to-brief |
| **S2** | Two callers: Call 1 says floor 3; Call 2 says floor 4 and mentions a rear stairwell | Contradiction flag, brief v1→v2 diff |
| **S3** | Caller gives a business name that matches two buildings | Candidate list, confirmation gate |
| **S4** | Building with no scan | Fallback brief, gaps, abstention |

### **Ground truth**

For each hero building, annotate 20–30 facts by manual review of the scan/aerial/record (stairs count and locations, exits, stories, use, tenants, roof access, adjacent structures). Store in `evals/ground_truth/<building_id>.json`.

### **Metrics reported**

Time-to-brief (P50/P95), fact precision/recall, unsupported-claim rate, contradiction detection, abstention correctness. Run via `evals/run_evals.py`; log runs to LangSmith and Tiger Data. **Only quote numbers we measured.**

### **Dry-run checklist (before judging)**

* Full demo in `DATA_MODE=live` and again in `fixture`.  
* Phone hotspot fallback tested (venue Wi-Fi may block webhooks).  
* Backup video recorded.  
* Headphones/speaker tested for noisy rooms; transcripts visible on screen.

## **17\. Build plan (relative to the 8:00 AM Sunday deadline \= D)**

> The 36-hour clock started Friday 8 PM. Schedule backward from D and scale to the time remaining. If you have **under \~16 hours**, apply the cut list in 17.3 immediately.

### **17.1 Phases**

| Phase | Window (before D) | Goal | Exit criteria |
| ----- | ----- | ----- | ----- |
| **0 Smoke test** | first 90 min | Confirm toolkit access; choose hero buildings | 3 scanned \+ 1 unscanned building chosen; sample CoStar/Matterport/Vexcel responses saved as fixtures; fallback decision made |
| **1 Walking skeleton** | next \~3 h | End-to-end with fixture data | Scripted transcript → incident → (fixture) dossier → brief → UI updating live; deployed to Vultr |
| **2 Real data \+ agents** | next \~6 h | Live dossiers, vision extraction, rules, critic | FR-4, FR-5, FR-6, FR-7 pass on S1 |
| **3 Voice \+ evals** | next \~4 h | Streaming STT, L1 copilot, eval harness, metrics | S1–S4 pass; metrics panel from Tiger Data |
| **4 Polish \+ stretch** | next \~3 h | UI polish, feedback loop, L2 stretch | Demo smooth in both modes |
| **Freeze** | D − 6 h | No new features | Bug fixes only |
| **5 Package** | D − 6 h → D − 0.5 h | Video, README, Devpost, DEV draft, rehearsal, sleep shifts | Submitted by **7:30 AM** |

Career fair is Saturday 11 AM (JRR 1st-floor atrium) — use it for feedback from CoStar, Amazon, and LangChain if you can still make it.

### **17.2 Workstreams and tasks**

Assumes four people (A Agents, B Data, C Frontend, D Voice/Infra/Demo). Merge roles for smaller teams.

| ID | Task | Owner | Est | Depends | Definition of done |
| ----- | ----- | ----- | ----- | ----- | ----- |
| T1 | Toolkit smoke test; save sample responses | B | 1.5 h | — | Fixtures committed for hero buildings |
| T2 | Pydantic schemas \+ generated TS types | A | 1 h | — | `packages/schemas` builds |
| T3 | FastAPI skeleton, WS events, config | D | 1.5 h | T2 | `/health`, WS echo, deployed to Vultr |
| T4 | Tiger Data schema (hypertables) \+ timing logger | D | 1.5 h | T3 | Rows written per stage |
| T5 | Provider adapters \+ fixture providers \+ `DATA_MODE` | B | 2 h | T1, T2 | Both modes return valid dossiers |
| T6 | MDT UI shell (layout, empty states) | C | 3 h | T2 | Renders from mock `Brief` |
| T7 | Dispatcher console (transcript, incident, confirm) | C | 3 h | T6 | S3 confirmation works |
| T8 | Fusion agent \+ contradiction logic | A | 3 h | T2 | S1/S2 pass |
| T9 | Locator (geocode \+ CoStar search) | B | 1.5 h | T5 | Ranked candidates |
| T10 | Dossier subagents \+ Gemini vision extraction | A/B | 4 h | T5 | Markers with evidence on hero buildings |
| T11 | Rules engine \+ tests \+ `rules.yaml` | B | 3 h | T2 | R-01…R-09 tests green |
| T12 | Strategy agent \+ critic loop | A | 3 h | T10, T11 | Unsupported-claim rate 0 |
| T13 | Matterport viewer with tags | C | 3 h | T10 | Stair/exit markers clickable |
| T14 | Evidence drawer \+ provenance UI | C | 2 h | T12 | Every claim clickable |
| T15 | No-scan fallback path | A/C | 1.5 h | T12 | S4 passes |
| T16 | Streaming STT \+ diarization | D | 3 h | T3 | Two-caller transcript live |
| T17 | L1 copilot logic \+ UI | A/C | 2 h | T8 | Suggestions appear/disappear |
| T18 | Eval harness \+ ground truth annotation | A/D | 3 h | T12 | `run_evals.py` outputs report |
| T19 | Metrics panel from Tiger Data | D/C | 1.5 h | T4 | Time-to-brief chart |
| T20 | Brief versioning \+ diff UI | A/C | 2 h | T12 | S2 shows v1→v2 |
| T21 | LangSmith tracing wired everywhere | A | 1 h | T8 | Trace links in UI |
| T22 | Feedback loop \+ audit log (lite) | B/D | 2 h | T14 | Corrections persist |
| T23 | L2 callback prototype (stretch) | D | 3 h | T16 | One demo call |
| T24 | README, architecture doc, Devpost text, DEV draft | D | 2 h | — | Published |
| T25 | Demo script, backup video, rehearsals | D \+ all | 2 h | Freeze | 2:00 run-through, two modes |

### **17.3 Cut list (apply in order if behind)**

1. L2 callback (T23)  
2. MongoDB (never started unless clean)  
3. Vexcel vision (keep basic aerial display)  
4. Gemini Live voice (keep text suggestions)  
5. Feedback loop and audit lite (T22)  
6. Live STT (use text transcript feed)

**Never cut:** fusion, dossier with evidence, brief UI with provenance, fixtures, eval numbers.

## **18\. Demo script (2:00)**

| Time | Beat |
| ----- | ----- |
| 0:00–0:15 | Problem: minutes lost at arrival; scattered caller info |
| 0:15–0:50 | Play S2: Call 1 and Call 2 stream in; incident record fills; contradiction flagged; dispatcher confirms address |
| 0:50–1:20 | MDT: brief appears (note time-to-brief); stair/exit markers in the scan; hazards from tenants; considerations ranked |
| 1:20–1:40 | Click a claim → evidence drawer; show brief v1→v2 diff; switch to S4 no-scan state showing gaps |
| 1:40–2:00 | Measured results (time-to-brief, fact accuracy), voice ladder (L1 now, L2/L3 roadmap), close |

**Backup:** recorded video; `fixture` mode; transcripts on screen.

## **19\. Risks and mitigations**

| Risk | Impact | Mitigation |
| ----- | ----- | ----- |
| Toolkit lacks expected fields (stories, tenants, contacts) | Brief thinner | Smoke test first; show gaps honestly; use available fields only |
| Few buildings have scans | Demo limited | Pick hero buildings with scans; fallback state is a feature |
| Vision misreads stairs/exits | Wrong markers | Confidence \+ evidence \+ "verify" language; measured accuracy |
| Hallucinated advice | Credibility | Critic loop, rule consistency, advisory wording, abstain |
| Venue Wi-Fi blocks webhooks/streams | Voice demo fails | Phone hotspot; recorded fallback; text feed |
| Time overrun | Missing core | Cut list; fixtures early; freeze at D − 6 h |
| Privacy objections to interior scans | Judge pushback | Role-based access, audit log, access only for confirmed incidents; synthetic data |
| Prize sprawl | Diluted build | Core four first; extras only if load-bearing |

## **20\. Open questions (resolve in Phase 0\)**

1. Which CoStar fields are exposed (stories, year built, tenants, construction, contacts)? Via SDK, MCP, or OpenAPI?  
2. How are Matterport scans accessed (embed only, or floor-plan/mesh/annotation data)? Capture dates available?  
3. Does Vexcel imagery come with metadata (capture date, resolution) and in what coverage?  
4. Which LA/Houston buildings have all three layers?  
5. How are MLH prize categories judged and opted into?  
6. Does a fire company (non-business) qualify for Peri's track?

## **21\. Submission checklist**

* \[ \] Public GitHub repo **created between Nov 13 and Nov 15**, with README (use case, target user, architecture, run steps)  
* \[ \] Devpost: **one** HackPrinceton track (Business & Enterprise) \+ special tracks selected (CoStar, Amazon, LangChain, \+ MLH categories as confirmed)  
* \[ \] Discord usernames of all teammates on the submission  
* \[ \] Optional ≤ 2-minute demo video uploaded and linked  
* \[ \] Peri evidence (business name, person name/role, screenshot or quote) if entering  
* \[ \] Deployed URL (Vultr) and domain (if claimed)  
* \[ \] LangSmith trace link or screenshots  
* \[ \] Final submission by **7:30 AM**; deadline 8:00 AM is hard  
* \[ \] In-person demo prepared (required even with a video)

**22\. Architecture at a glance**

One **Incident Commander agent** (`create_deep_agent`) runs per incident, on its own checkpointed thread. It doesn’t do the work itself. It plans with `write_todos`, delegates to specialist subagents through the `task` tool, keeps all working state in an incident filesystem, and pauses for dispatcher approval at defined gates. A long-term store holds building knowledge and department skills that persist across incidents.

| Deep agent feature | How FlameScout uses it |
| :---- | :---- |
| **Planning** (`write_todos`) | Each incident gets a live checklist (fuse callers → confirm location → pull dossier → synthesize → verify → publish). The todo list is shown to the dispatcher as a progress strip, so they always see what’s done and what’s pending. |
| **Filesystem** (`ls`, `read_file`, `write_file`, `edit_file`, `grep`, `glob`) | The incident workspace is the contract between agents and the UI. The MDT renders straight from these files. |
| **Subagents** (`task`) | Ten specialists (fusion, L1 copilot, locator, CoStar, Matterport, Vexcel, egress engine, hazard engine, brief writer, verifier) in isolated context windows; dossier pulls run in parallel. |
| **Code execution** (sandbox `execute`) | All deterministic checks: egress distances, occupancy rules, contradiction scoring, map rendering, evals. |
| **Human-in-the-loop**(`interrupt_on`) | Location confirmation, outbound calls, publishing low-confidence claims, memory and skill edits. |
| **Skills** (`SKILL.md`, loaded on demand) | Department size-up format, occupancy hazard rules, hazmat tenant flags, question protocols, Matterport interpretation. |
| **Long-term memory**(`CompositeBackend` routing `/memories/` to a store) | Per-building notes that grow with every incident, plus department SOPs in `AGENTS.md`. |
| **Context management**(summarization \+ large-result eviction) | Transcript streams compacted as they grow; big CoStar/Matterport payloads written to files instead of flooding context. |

### **Skills library (progressive disclosure)**

The agent sees only skill names and descriptions until one is relevant. That keeps context lean during the time-critical window.

* `size-up-format/`: the department’s card template and ordering.  
* `occupancy-hazards/`: occupancy classes mapped to hazard flags, with a rules script.  
* `hazmat-tenants/`: tenant-type heuristics (labs, pool supply, battery storage).  
* `dispatcher-questions/`: L1 question protocol by call type.  
* `matterport-reading/`: how to derive stairs and exits from scan data, and how to treat stale scans.  
* `no-scan-fallback/`: what to say and show when no Matterport exists.

**23\. Normalize plans**  
Data from Costar includes 3D plans, 2D plans, and pictures of exteriors, and not all in the same style. Need to normalize to 2D plans where possible.

---

## **Appendix A — Call script templates (synthetic)**

Use placeholders for the hero building until chosen: `<HERO_A>`, `<HERO_B>`.

**S2 (two callers, contradiction)**

* *Call 1* — Call-taker: "911, what's the address of your emergency?" Caller: "There's smoke coming out of the building at `<HERO_A address>`, I think the third floor." Call-taker: "Is anyone inside?" Caller: "I saw people at the windows."  
* *Call 2* — Caller: "I'm across the street from `<HERO_A name>`. Fire's on the fourth floor, flames at the corner window. Someone's shouting from the back stairs."  
* *Expected:* contradiction on `incident.floor` (3 vs 4), `people.reported` merged, open question about stairwell smoke.

**S3 (ambiguous name)** — Caller gives a business name that exists at two addresses; dispatcher must choose.

**S4 (no scan)** — Standard call to an unscanned building; expect gaps list and capped confidence.

## **Appendix B — Copilot question bank (triggered by missing fields)**

* Is anyone trapped or unaccounted for? Which floor and where?  
* Where is the smoke or fire visible — which side/floor?  
* Is the fire alarm sounding? Is the building sprinklered (if known)?  
* Any hazardous materials, gas, or chemicals mentioned?  
* How many people were inside? Any mobility-impaired occupants?  
* Which entrance is accessible? Is there a key box?

## **Appendix C — Glossary**

* **MDT:** mobile data terminal in the apparatus.  
* **CAD:** computer-aided dispatch.  
* **Size-up:** the officer's initial assessment of the incident on arrival.  
* **Pre-incident plan:** a building-specific response plan prepared in advance.  
* **Exposure:** adjacent structures or property threatened by the fire.  
* **FDC:** fire department connection (for sprinkler/standpipe systems).

