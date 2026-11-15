# Pitch briefs: what to say to each track's judges

For the team. One section per track we present: what the judges score, our 30-second pitch, the points to hit mapped to their criteria, what to show, the questions they are likely to ask with honest answers, and what not to say. Read sections 0 to 2 first: they apply to every judge.

Rules from the packet: judging is Sunday 9:30 AM to 2:30 PM, **in person, 2 minutes**, even with a video. Special-track judges walk around during the first round, so each of us needs the 30-second version of every track ready. Submit by 7:30 AM (hard deadline 8:00 AM).

---

## 0. The 2-minute spine (every judge)

1. **Problem (15 s).** Crews lose minutes at the curb finding entries, stairs and hazards, and callers contradict each other. We spoke to Dartmouth Safety and Security: the handoff is the location and nature of the call, then an on-scene assessment before action.
2. **Live call (30 s).** Two callers stream in. Each caller line shows what the model extracted, with the quote. A contradiction (floor 3 vs 4) is flagged, never auto-resolved.
3. **Confirm and plan (15 s).** The dispatcher confirms the building. An orchestrator plans, three subagents pull CoStar, Matterport and Vexcel in parallel, and a critic checks the brief.
4. **MDT (30 s).** The officer sees the address and incident type first, then building detail. Each card shows its source, date and confidence. Reports are placed on the 3D and 2D plan; a planner in code draws the route.
5. **Trust (15 s).** Click a claim: source, date, aerial crop. Press **Mark wrong / Confirm**: crew feedback is remembered for that building.
6. **Close (15 s).** Advisory only, simulation only. The crew decides; we get them there with the picture.

**Pick the demo scenario in rehearsal and stick to it.** Use a short call on a scanned house (`S6_FAIRDALE` or a `SHOW_*` call). `S2` is a synthetic commercial example: its building (Wilshire Tower) uses fixture data, so say that if asked.

---

## 1. Numbers we can quote (measured; re-run before judging)

| Fact | Value | How to re-check |
|---|---|---|
| Repo created | Nov 13, 2026 (inside the window), about 140 commits, public | `git log --reverse` |
| CoStar data | 3,339 Houston and LA properties from the kit, normalized into a local index | `data/costar/properties.jsonl` |
| Live layers | Matterport (live plan generation), Vexcel (oblique views, vision reading), CoStar index | `/api/house-viewer/...` returns 200 |
| Caller-line processing | median about 0.36 s per line (97 measured lines, local); PRD target 3 s | `/api/metrics/stages` → `fusion` |
| First brief, **fast path** | about 8 s median on the Vultr deployment (4 runs); 3.9 s on one local run | `/api/metrics/stages` → `fast_path` |
| First brief, **full agent path** | median 63 s, p95 122 s over 11 recorded briefs (Tiger Data) | `/api/metrics/time-to-brief` |
| Failed agent runs | 13 recorded runs ended in a facts-only fallback brief (cause not recorded) | same endpoint, `n_fallback` |
| Tiger Data | 25 incidents, 411 call segments, 96 pipeline events, 13 briefs; 2 hypertables, 1 continuous aggregate, the `time_to_brief` view, agent checkpoints | count queries as of 4:37 AM |
| MongoDB Atlas | 318 incident workspace documents | `workspace_files` collection |
| Deployment | 4 containers (api, house-data, messenger, web/Caddy) on Vultr, HTTPS on `flamescout.health` | `docker compose ps` on the server |
| Tests | 493 passed, 52 skipped (live and data-dependent) | `pytest -q` |
| LangSmith project | `flamescout` | LangSmith account |

Quote the fast-path number **with its sample size**, and the agent-path numbers as "about a minute". Never give a single number without saying which path it is.

---

## 2. Questions every judge may ask (honest answers)

- **Is the data real?** The callers are synthetic. The building data is real for the Houston houses: Matterport plans come live from the Matterport API, Vexcel imagery comes from the Vexcel API, and CoStar records come from the hackathon kit export. The S2 commercial example uses fixture data.
- **Does it ever make things up?** We designed against it. Every fact, marker and consideration has a status and evidence ids; unsupported ones are marked *unverified*. Distances, counts, contradictions and routes are computed in code. The model only extracts facts from a line, reads images, drafts wording and critiques; it never outputs coordinates or route geometry. A building with no scan shows "No interior scan on file".
- **What happens when a model call fails?** 12 s client timeout and one retry; a second provider (xAI) takes over once; each run has a deadline (about 90 s for v1) after which the best partial brief is published. The dispatcher still gets a facts-only brief. This happened: 13 recorded runs fell back.
- **Why is the full agent path slow?** It does a plan, three parallel data pulls, drafting and a critic. For speed there is a fast path (one structured call, about 8 s) that falls back to the agent path if its checks fail twice.
- **Did a fire department use it?** No. We consulted Dartmouth Safety and Security (Officer Bigelow, Nov 14, by phone, not recorded, paraphrased with permission). What we know about Hanover Fire is secondhand.
- **Is it safe to use?** It is advisory ("Consider ...") and simulation only. It never calls an emergency number. Command stays with the incident commander.
- **What would you do next?** CAD/MDT integration, hydrant and water-supply data, a structured crew-review loop, multilingual calls, and an offline cache for district buildings.

**Do not say:** "Photon 2.0" (we use Photon Stable), "lives saved", an unmeasured speedup, "production ready", or that the iMessage link is an agent.

---

## 3. Best Overall / Healthcare main track (HackPrinceton)

**Criteria:** Creativity, Utility, Charity, Avidity. You enter one main track only; ours is Healthcare (public safety and emergency response). Best Overall is automatic and does not depend on the track.

**30 seconds:** "On a fire call, the first minutes go to finding stairs and entries. FlameScout fuses what callers say, pulls the building's interior, aerial view and records, and puts a brief and a computed route on the crew's tablet before they arrive. Advisory only; the crew decides."

**Points to hit**
- **Creativity (the aha moment):** callers' words ("over the kitchen", "the back stairs") are placed on a real building scan, and the contradiction between callers is surfaced, not hidden.
- **Utility:** it works end to end today: call, fused record, confirmation, brief, routes, versions, feedback. Show it, don't describe it.
- **Charity:** public safety. Cite the consultation and the Morton Hall record (four-alarm fire, Oct 1, 2016; reporting that the design challenged firefighters and that fourth-floor visibility was near zero). Do **not** claim outcomes.
- **Avidity:** the team story: the Dartmouth call on Nov 14, the on-scene-assessment handoff that shaped the design, and what you'd do next.

**Likely questions:** "Why Healthcare?" Emergency response delivery and public safety; EMS is a natural extension. "Who is it for?" The company officer en route and the dispatcher. "Is this just a chatbot?" No: it plans, delegates and acts, and a human gates the key steps.

---

## 4. CoStar: Inside and Out

**Criteria:** 30% use of the data, 30% agentic depth, 25% usefulness, 15% demo and polish. They want at least two of the three layers combined, an agent that reasons and acts (not chat over a record), a real person's problem, and spatial or visual understanding.

**30 seconds:** "Three layers on one address: CoStar says what the building is and who's in it, Matterport has the inside, Vexcel has the view from above. Our agent combines them into a brief and a route the crew can use before arrival."

**Points (by criterion)**
- **Use of the data (30%):** state which layer gives what.
  - **CoStar:** use, size, stories, year built, sprinklered flag where present, tenants. Tenant types drive hazard flags (for example a commercial kitchen or lithium batteries).
  - **Matterport:** rooms, doors, stairs and floors from the read-only API; these drive the evacuation graph, the 3D and 2D plan and the routes.
  - **Vexcel:** oblique views from four directions with capture dates; Gemini labels exposures, roof and entries once, and **distances are then computed in code**.
  - **The combination:** the brief checker requires at least one cross-layer consideration. In our run, a consideration about a commercial kitchen and lithium batteries came from tenant data, and one about roof access above a stair came from aerial plus scan evidence.
- **Agentic depth (30%):** an orchestrator per incident plans with `write_todos`, runs the three layer subagents in parallel, maps reports onto the plan, drafts and critiques, and pauses for the dispatcher at location confirmation.
- **Usefulness (25%):** the officer's glanceable MDT: address and nature of the call first, data age shown, gaps listed. Cite the consultation.
- **Demo (15%):** do the live call, click a claim to its evidence (aerial crop and capture date), show the route.

**Sponsor-specific questions**
- *Which CoStar fields?* The normalized kit record: name, use, stories, year built, area, sprinklered and tenants where present. We used the kit export (3,339 properties), not a live CoStar API.
- *How do you deal with plans in different formats (3D, 2D, photos)?* Interiors come from Matterport's API as structured floors, rooms and openings, which we render as 2D and 3D from the same data; exteriors are Vexcel images read by a vision model. We didn't try to convert arbitrary customer plan images.
- *Where do stairs and doors come from?* Matterport floors, rooms and openings; stairs are paired between floors in the viewer's plan code. Room types come from a classifier and can be wrong, so we say "estimate".
- *Which Vexcel services?* Coverage lookups and extract for oblique and straight-down imagery, with the key in a header, never in a URL.
- *Beyond fire?* The same dossier would serve EMS, insurers or appraisers. That is roadmap, not built.
- *What isn't covered?* Interiors exist only for buildings with a Matterport scan; others show gaps. The S2 commercial example is fixture data.

**Don't say:** that all three layers are live for every building; that CoStar is a live API connection.

---

## 5. Best use of deepagents (LangChain)

**What they look for:** a deepagent for any use case; real use of the framework's features.

**30 seconds:** "One persistent deep agent per incident, with a visible plan, parallel subagents, a filesystem the UI renders from, human approval gates, memory that carries across incidents, and a critic on a different model."

**Points**
- **Planning:** `write_todos` is shown to the dispatcher as a progress strip; the plan changes with the situation (no scan, life-safety contradiction).
- **Subagents:** costar, matterport and vexcel (delegated in one turn, in parallel), incident_mapper, strategy and critic, each in an isolated context.
- **Filesystem:** the incident workspace (brief, critique, overlay, notes) is the contract between agents and the UI. The model may only write `notes/*.md`; JSON artifacts come from tools.
- **Human in the loop:** `interrupt_on` pauses at `confirm_building` and at `publish_brief` when a consideration has low confidence.
- **Skills and memory:** two skills (`occupancy-hazards`, `size-up-format`) load on demand; per-building crew memory is mounted read-only at `/memories/`.
- **Durability:** a checkpointer on Tiger Data (PostgresSaver) so incidents survive a restart; the workspace on MongoDB Atlas.
- **Reliability engineering:** timeouts and retries on every model call, provider failover, a different-provider critic, and a run deadline that publishes the best partial brief.
- **Observability:** LangSmith tracing on (project `flamescout`). **Have a trace open to show.**

**Likely questions**
- *Why deepagents rather than plain LangGraph?* We needed planning, subagent isolation, a file workspace and approval interrupts out of the box, and the incident lifecycle maps to them.
- *How do you keep it grounded?* Tools write the artifacts, outputs are schema-validated with retries, and the critic and deterministic checks reject unsupported claims and non-advisory wording.
- *Why is part of the pipeline outside the agent?* Fusion runs per caller line as plain code (median about 0.36 s) because the agent loop is too slow for live transcripts. Deterministic geometry stays in code on purpose.
- *Code execution sandbox?* We didn't use `execute`; deterministic checks are ordinary tools.
- *What failed?* Model rate limits and stalls. Now handled with failover and a deadline; some earlier runs ended as facts-only fallbacks.

---

## 6. Best Practical AI Innovation (Amazon)

**Criteria (25% each):** Creativity, Usefulness, Technical implementation, User experience. **Must include:** working prototype, clear use case and target user, basic documentation, presentation.

**30 seconds:** "AI that does a real job for a real person: it turns scattered 911 calls into a building brief the crew can act on, updates as new calls arrive, and learns from the crew's corrections."

**Points**
- **Agentic behaviour:** reasons across callers, plans, acts (pulls data, drafts, critiques), re-plans as facts change.
- **Real-world impact:** target users are the company officer, the dispatcher and the incident commander. Cite the consultation and what it changed in the design.
- **Adaptive intelligence:** the crew's **Mark wrong / outdated / Confirm** feedback is stored per building and applied to later briefs.
- **User-friendly:** a glanceable MDT (address and nature of call first, three zones, data age, gaps). There is an iPad layout (`docs/IPAD.md`).
- **Technical quality:** 493 tests pass; failover and deadlines; deployed over HTTPS; documented in the README.
- **Presentation:** the 2-minute spine in section 0.

**Likely questions**
- *Why not AWS services (Bedrock, Transcribe)?* We built on Gemini for extraction, vision and audio in one stack. The model layer is provider-switchable (Gemini, OpenAI, xAI via environment settings). We didn't integrate AWS.
- *How do you handle privacy?* Synthetic callers; the brief only exists after the dispatcher confirms the location; an audit log records brief views and feedback; secrets stay in `.env`.
- *How would a department adopt it?* A pilot with a campus safety office or fire department, with CAD integration as the next step. We haven't built CAD integration.

---

## 7. Forward Deployed Challenge (Peri)

**Criteria:** real-world need (does the project address a problem the business actually has), usability (could they adopt it immediately), execution (how complete and functional end to end). **Evidence required:** business name, the person's name and role, and one supporting item.

**30 seconds:** "We called Dartmouth Safety and Security. They respond to every campus fire alarm and hand command to Hanover Fire on arrival with the location and nature of the call. We built the building brief that should be ready before that handoff, and we kept the handoff intact."

**Points**
- **Who and when:** Officer Bigelow, Dartmouth Safety and Security, Nov 14, 2026, 13:04, by phone. Not recorded (the office's policy doesn't allow a formal interview); he gave personal permission to paraphrase. Hanover Fire's role is **secondhand**; say so.
- **What he said:** Safety and Security responds to every campus fire alarm, supports evacuation, building access and escorting responders; command passes to Hanover Fire; Hanover assesses on scene before acting; he took part in the 2016 Morton Hall response.
- **How it shaped the design:** the brief opens with the address and incident type (the handoff items); the wording is advisory because command sits with Hanover; every brief shows the age of its interior scan and the CoStar date because buildings change over years.
- **Evidence:** the README's consultation section and your call notes (attach a photo or screenshot in Devpost).

**Likely questions**
- *What did they push back on?* **Fill this in from your call.** If nothing, say what you asked and what you changed because of it.
- *Could they use it tomorrow?* Not yet: the brief needs the building to have a scan and a call feed; CAD integration doesn't exist. Say what a pilot would need.
- *Did you show it to them?* **Answer honestly.** If not, say the next step is a walkthrough with Hanover Fire.

**Don't say:** that Hanover Fire reviewed or endorsed it, or that the call was recorded.

---

## 8. ChatOverflow Agent Leaderboard

**What they look for:** agents that contribute the most useful insights; no extra submission.

**Points**
- Our coding agent (Claude Code) was connected to ChatOverflow through its CLI hook while we built, and it posted field notes from real findings: a Vite proxy that returns `index.html` for `/health`, a metrics query that silently fell back because a table never existed, a Windows encoding crash in a data build, a Vexcel 401 diagnosis.
- Notes contain no keys or project secrets.

**Don't say:** anything about your rank, or counts, that you haven't just checked on the live leaderboard.

---

## 9. Gemini (MLH, if listed)

**30 seconds:** "Gemini is load-bearing in four places: extracting facts from each caller line, reading aerial images, transcribing a recorded call, and driving the agents."

**Points:** structured output with schema retries; quote verification (an extracted fact must carry the caller's exact words); vision reads each aerial image once, with boxes, and geometry then goes to code; call-in audio is transcribed in one pass into caller sentences (audio always uses Gemini because the other providers here take no audio); a fast pipeline gets a brief from one structured call with thinking disabled.

**Likely questions:** *Model choice?* A fast model for extraction and agents and a vision-capable one for images, set by environment variables. *Rate limits?* We hit the free-tier cap; fixed with a failover provider and a run deadline. *Gemini Live voice?* Not built; text and recorded audio only.

---

## 10. Tiger Data (MLH, if listed)

**30 seconds:** "Tiger Data is our system of record for the incident's time series: every call segment and pipeline stage is a row in a hypertable, and the time-to-brief number on screen comes from a view over it."

**Points:** two hypertables (`call_segments`, `pipeline_events`); a continuous aggregate (`stage_seconds_hourly`); the `time_to_brief` view feeding `/api/metrics/time-to-brief` and the top-bar chip; the LangGraph PostgresSaver so agent threads and incidents survive a restart; current counts: 25 incidents, 411 segments, 96 pipeline events, 13 briefs.

**Likely questions:** *Why Timescale?* Segments and stage timings are append-only time series, and we wanted hourly rollups and percentile views. *What do the figures say?* Median 63 s and p95 122 s for agent-built briefs over 11 runs, with 13 fallback runs excluded; fast path about 8 s. *Anything not working?* We found and fixed a metrics query that pointed at a table the schema never created (it silently fell back to local files); it now reads the real view.

---

## 11. MongoDB Atlas (MLH, if listed)

**30 seconds:** "The agents' filesystem is a MongoDB collection: the incident's brief, critique, overlay and notes are documents the UI renders from."

**Points:** a LangGraph store backed by Atlas mounted as `/workspace/` for the deep agent; one document per file with the parsed JSON queryable (for example by brief version); the prefetch cache and per-building crew memory use it too; 318 documents now.

**Likely questions:** *Why Mongo for this?* The artifacts are JSON documents with different shapes per step, and the deepagents store backend maps onto document stores. *Is it optional?* Yes: with no URI it falls back to local files, which is what tests use.

---

## 12. Vultr (MLH, if listed)

**30 seconds:** "It runs on a Vultr instance: four containers behind Caddy with automatic HTTPS on flamescout.health, deployed with one script."

**Points:** Docker Compose (api, house-data service, messenger, web); Caddy gets certificates; one `deploy.sh` pulls and rebuilds; the firewall allows 22, 80 and 443; managed databases (Tiger, Atlas) stay separate. Show `docker compose ps`.

**Likely questions:** *Scaling?* One instance for the demo; the databases are managed and the stateless services could be replicated. *Secrets?* In `.env` on the server only, not in the image or repo. *Why isn't the site opening here?* Some networks (this venue's, possibly) filter the `.health` domain; use a phone hotspot.

---

## 13. Tracks we are not entering

- **Photon 2.0:** our iMessage caller-camera link uses Photon Stable (spectrum-ts), not Photon 2.0, and sends a link rather than running a conversational agent. If a judge asks, say that exactly.
- **Nessie, Arlequin (forensics, social media), Physical AI, Hardware:** not built.

---

## 14. Last checklist

- [ ] Devpost: Healthcare as the single main track; special tracks selected (see `docs/DEVPOST.md`); Discord usernames; video link; live link; repo link; Peri evidence attached.
- [ ] A phone hotspot ready; the live site opened once from it.
- [ ] Demo scenario chosen and rehearsed twice; fallback short call and the video ready.
- [ ] LangSmith trace and Tiger time-to-brief view open in tabs.
- [ ] Re-run the section 1 checks and update any number that changed.
- [ ] Everyone can give the 30-second version of every track in this document.
