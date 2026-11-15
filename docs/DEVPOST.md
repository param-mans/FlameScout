# Devpost submission text (copy into the fields)

Fill the `<...>` placeholders before submitting. Everything else is drawn from what is in the repo and what we measured.

## Project name
FlameScout

## Tagline
Live fire-dispatch calls become a building-specific size-up on the crew's tablet before the engine arrives.

## Main track
Healthcare (public safety and emergency response). One main track only.

## Special tracks to select
CoStar (Inside and Out) · Amazon (Practical AI) · LangChain (deepagents) · ChatOverflow · Peri (Forward Deployed Challenge)
Also the MLH or sponsor categories for **Gemini**, **Tiger Data**, **MongoDB Atlas** and **Vultr**, if Devpost lists them.
We do not enter Photon, Nessie, Arlequin, Physical AI or Hardware: this build has no integration for them.

## Inspiration
On arrival, a company officer spends minutes finding entries, stairs, occupancy and hazards, and what callers said is scattered across several calls and often disagrees. We talked to Dartmouth Safety and Security (Officer Bigelow, Nov 14) about how a fire call is handed to Hanover Fire: the location and nature of the call, then an on-scene assessment before action. Size-up aligns the crew, but every minute spent gathering information at the curb is a minute not spent on rescue and suppression. The 2016 Morton Hall fire showed how much a building's design can matter. We wanted most of that information ready before the crew arrives, so the on-scene size-up confirms and refines it.

## What it does
- Listens to dispatch calls (scripted, typed, or a recorded call-in) and fuses every caller into one incident record. Contradictions (for example "third floor" vs "fourth floor") are flagged and never auto-resolved.
- Locates the building through CoStar search and lets the dispatcher confirm it; clear matches can auto-confirm.
- Pulls a dossier from three layers for the same address: CoStar (use, size, tenants), Matterport (rooms, doors, stairs from the interior scan) and Vexcel (aerial views read by Gemini vision; distances computed in code).
- Writes a brief with up to five advisory "Consider ..." items, each with evidence, a confidence and a status, or marked *unverified*. A building with no scan says "No interior scan on file" and lists its gaps.
- Plots where callers report people, smoke and fire on a 3D and 2D plan, and computes entry-to-target routes with a deterministic planner. If no route exists, it says so.
- Updates live: each new call produces a new brief version with a "changed since" line. Crew feedback ("wrong", "outdated", "confirmed") is remembered per building.

## How we built it
- **deepagents (LangChain):** one orchestrator per incident on its own checkpointed thread, with `write_todos` as the dispatcher's progress strip, parallel subagents (CoStar, Matterport, Vexcel), an incident mapper, a strategy subagent and a critic on a different model provider. It pauses for a human at location confirmation and at low-confidence briefs. Skills, a filesystem workspace and long-term building memory are used. Traced in LangSmith.
- **Gemini:** fact extraction from each caller line (quote-verified, structured output), the agents, aerial image reading and call-in transcription. xAI is the failover provider and the critic.
- **CoStar + Matterport + Vexcel:** the three layers, combined in one brief. Hazard flags come from CoStar tenants, interior routes from Matterport, exposures and roof from Vexcel.
- **Tiger Data:** hypertables for call segments and pipeline events, a continuous aggregate, the `time_to_brief` view and the agents' checkpoints. The time-to-brief figures in the top bar read from it.
- **MongoDB Atlas:** the incident workspace the agents and the UI share, plus the prefetch cache and building memory.
- **Vultr:** Docker Compose (API, house-data service, web/Caddy) with automatic HTTPS.
- **FastAPI + React/Vite + Three.js** for the backend, the dispatcher console and MDT, and the 3D viewer.

## Design principles
Advisory only. Evidence-linked. Deterministic first (distances, counts, contradictions and routes are code, never a model). Abstain instead of guess. Simulation only: no real emergency line is ever contacted.

## Measured results (only what we measured)
- Fast pipeline, first brief on our Vultr deployment: median about 8 s over 4 runs (`/api/metrics/stages`).
- Full agent pipeline, first brief: median about 63 s, p95 about 122 s over 9 recorded runs (Tiger Data `time_to_brief` view). 13 other recorded runs ended in a facts-only fallback brief when the agent run failed (the cause was not recorded; many were early in development). A failed run never blocks the demo: the dispatcher still gets the facts-only brief.
- <add your own numbers from the final rehearsal here>

## Challenges
Model rate limits on the free tier (fixed with a failover provider and a run deadline that publishes the best partial brief); matching what callers say ("over the kitchen") to rooms in a scan without letting a model invent geometry (a deterministic place resolver and planner); keeping every claim tied to evidence.

## What we learned
Models are good at reading messy callers and images and bad at geometry, so we let code own the geometry and kept the model's output to ids and wording we can check.

## What's next
Live CAD/MDT integration, hydrant and water-supply data, a structured crew-feedback review loop, multilingual calls, and offline cache for district buildings.

## Built with
Python, FastAPI, LangChain deepagents, LangSmith, Gemini, xAI, CoStar, Matterport, Vexcel, Tiger Data (TimescaleDB), MongoDB Atlas, Vultr, Docker, Caddy, React, Vite, Tailwind, Three.js.

## Links
- Repo: https://github.com/param-mans/FlameScout
- Live demo: https://flamescout.health (if the venue network blocks `.health`, use a phone hotspot)
- Video: <paste link>
- Discord usernames: <all teammates>

## Peri Forward Deployed Challenge: required evidence
- Business consulted: Dartmouth Safety and Security (and, secondhand, Hanover Fire Department).
- Person and role: Officer Bigelow, Dartmouth Safety and Security.
- Date and mode: Nov 14, 2026, 13:04, by phone. Not recorded (the office's policy does not allow a formal interview); personal permission to paraphrase.
- Evidence: our notes, quoted in the repo README under "Client consultation". Attach a photo of the call notes or a screenshot here: <attach>.
