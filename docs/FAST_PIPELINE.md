# FAST_PIPELINE: overlay and brief from one model call

`FAST_PIPELINE` (env, default `1`; `services/api/config.py` → `Settings.fast_pipeline`). Optional
`GEMINI_OVERLAY_MODEL` (`Settings.overlay_model`) picks the model; unset, the usual fast model
(`GEMINI_FAST_MODEL`) is used. Code: `services/agents/fast_path.py` (model call and checks) and
`services/api/service.py` (`FastState`, `_fast_*`: scheduling and events). Tests: `tests/test_fast_path.py`.

## When it runs

- Only for a building confirmed on the orchestrator pipeline (one of the orchestrator locator's candidates), and
  only when a model is available (an API key and a model id). Otherwise everything works as before.
- With `1`, no agent is started when candidates are found: nothing has to wait at the confirmation.
- Run 1 starts as soon as the location is confirmed (auto or dispatcher), if the transcript has a caller line.
- Every later change (a segment with new facts, a dispatcher resolving a contradiction) re-arms a **1 s
  debounce**; when it fires, one run reads the whole transcript so far.
- **At most 2 runs per call** (`call_id`): normally mid-call (at the confirmation) and at the end. Changes a
  call makes after its 2 runs start no run; they are in the transcript the next call's run reads. A dispatcher
  action has no call and no limit.
- Runs may overlap. Each one gets its version only when it is emitted, under the incident's lock; a run that
  finishes after a newer run was emitted is dropped (the last one wins).

## One run

1. In parallel, in code: the CoStar, Matterport and Vexcel layers (`dossier.pull_layer`, three threads, or a
   fresh prefetch cache; no interior scan → Vexcel again with the exterior entries), the plan index and the
   overlay `model_id`. These are file reads (a few ms), so they finish before the prompt is built.
2. **One structured-output call** (JSON schema, no tools, thinking off: `thinking_budget=0`) with the
   transcript (span ids), the facts fusion extracted, the plan index (per room: floor, type, label,
   `size_rank`/`size_of`, `next_to`, `over`, `under`, `at_stairs`), the listing facts and the scan / aerial
   digests with their evidence ids, crew memory, and the previous overlay's ids. It returns the reports
   (occupants, fire, hazards, contradictions, objectives) with each **place in the caller's words**
   (type, floor, over/under/next_to, size, at_stairs), plus 2–4 considerations with rationale and
   `evidence_ids`.
3. Code places every report with `resolve_place.py` (`overlay.resolve`): one matching room → that room;
   several → floor level and a dispatcher question (as the incident mapper does); none → floor or unknown.
   The model never gives a room id. Then `overlay.assemble` + `overlay.check` (`validate_overlay.py` and the
   mapper's rules: caller spans as evidence, never `verified`, contradictions from different calls, every room
   resolved) and `brief.check_considerations` (advisory, evidence that resolves, cross-layer, refs in the
   overlay, crew-wrong markers), plus 2–4 considerations and no order words (`critic.ORDER_WORDS`).
4. Anything wrong (or the call fails) → **one retry** with the same context, the errors and the previous answer.
   Wrong again → no output. If no version was emitted yet for this building, the **agent path takes the
   incident, unchanged** (`_confirm_job`: start + resume, then updates as before; audit action
   `fast_path_fallback`). If a version is already out, it stays on screen and the next change runs again.
   Nothing that fails a check is written or emitted.
5. Commit, under the incident's lock: version = last brief version + 1; the overlay is assembled again with
   that version (and `changes_since_last` against the previous one) and checked again; the brief is built in
   code (`brief.build_brief`: layers, facts, the considerations; caller spans in its evidence), low-confidence
   considerations are left out with a gap (nobody reviews them on this path), the gap "Considerations were
   not checked by the critic" is added, and it is published. Writes `overlay_vN.json`, `brief_vN.json`,
   `places_vN.json` and the copilot questions to the incident workspace, like the agent path.

## Events (same envelope `{type, incident_id, ts, trace_id, payload}`, same trace id)

Order on the fast path, per version: `dossier.progress` (`running`, fixed todos) … **`overlay.updated`**
`{version, overlay}` → **`brief.updated`** (same version, `pipeline: "fast"`, `status: "briefed"`) →
`incident.updated` (status `confirmed` for v1, then `updated`) → `metric.tick` (v1 only) → `dossier.progress`
(`done`).

The agent path (fallback, or `FAST_PIPELINE=0`) keeps its order: `brief.updated` → `overlay.updated`.
`GET /overlay/latest` and `GET /briefs/latest` read the same files on both paths.

## Measurement (real model, 2026-11-14, one run each, seconds after the last segment's `transcript.segment`)

| House | Before (agent path) | Final overlay | Final brief | Model calls | Retries | Fallback | Rooms |
|---|---|---|---|---|---|---|---|
| SHOW_FAIRDALE | 47.4 | 15.0 | 15.0 | 3 | 1 | no | fire, people correct |
| SHOW_FAIRHOPE | 53.5 | 11.7 | 11.7 | 2 | 0 | no | fire, people correct |
| SHOW_PLAYA | 44.7 | 8.3 | 8.3 | 3 | 1 (run 1) | no | fire, people correct |

Where the seconds go (final version): 1 s debounce, then the model call: 7–10.6 s per attempt
(6.4–6.8k input tokens, 1.4–2.6k output tokens although the JSON answer is ~0.5–0.9k: thinking is not fully
off with `thinking_budget=0`); a retry adds 3–5 s. Both retries were the model citing marker ids
(`m_exit_…`, `stairs-…`) as evidence ids. Layers + context ≈ 10 ms, checks < 2 ms, commit < 10 ms (up to
1.2 s when it waits for a segment's ingest, which holds the incident lock during LLM extraction). Not counted
above: each segment takes 4–5 s from the simulator to its `transcript.segment` (extraction).
Target (overlay ≈ 6 s, brief ≈ 10 s) not reached on the overlay; the brief now arrives with it.
