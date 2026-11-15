# 2:00 demo script (judging presentation and backup video)

In-person demos are required, 2 minutes at most, even with a video. Rehearse it twice, once on the live site and once locally. If the venue network blocks `flamescout.health`, use a phone hotspot (the site is up; some filters block `.health`).

**Before you start:** open the deployed site (or `npm run dev` plus the API), confirm the top bar says **● LIVE**, and have a second tab ready with the LangSmith trace and one with the Tiger time-to-brief view. Pick a scripted call on a scanned house (for example `S6_FAIRDALE`, or a `SHOW_*` call) and the commercial example `S2` as the backup. Note which one rendered best in rehearsal and use that.

| Time | Beat | What is on screen | What to say |
|---|---|---|---|
| 0:00 | Problem | Title or the Dispatcher view | "Crews lose minutes at the curb finding stairs and entries, and callers contradict each other. We talked to Dartmouth Safety and Security: the handoff is the location and nature of the call, then an on-scene assessment. FlameScout puts the building in the crew's hands first." |
| 0:15 | The call | Dispatcher view. Press **Run call**. Transcript fills; extracted facts show under each caller line | "Two callers, streaming in. Each caller line shows what the model extracted, with the quote it relied on." |
| 0:30 | Contradiction | The contradiction banner (for example floor 3 vs 4) | "Callers disagree. We flag it and never auto-resolve. The dispatcher picks." Press **Use** on one. |
| 0:40 | Confirm | The Confirm location card, then the plan strip | "The dispatcher confirms the building from CoStar. The orchestrator plans, three subagents pull CoStar, Matterport and Vexcel in parallel, and a critic checks the brief." |
| 0:55 | MDT brief | MDT view: top bar (address, incident type, scan age, CoStar date), size-up, considerations | "The officer sees the address and nature of the call first, then building detail. Every card says how old its data is and how sure we are." |
| 1:10 | Spatial | 3D and 2D viewer: people, smoke or fire, the planned route | "Reports are placed on the scan, and the route is computed by a planner in code, not by the model. If it can't compute one, it says so." |
| 1:25 | Evidence + feedback | Click a consideration, then a fact. Show the evidence (source, date, aerial crop). Press **Mark wrong** or **Confirm** | "Every claim opens its source. Crew feedback is stored per building and applied to the next brief." |
| 1:35 | No-scan state | Open a building with no scan (for example the `S4B`/`S9` no-scan building) | "Without a scan we show gaps, not guesses." |
| 1:42 | Stack and numbers | Top-bar metrics chip or the Tiger view | "deepagents orchestrator traced in LangSmith, Gemini for extraction, vision and transcription, Tiger Data for timings and checkpoints, Atlas for the shared workspace, deployed on Vultr. First brief: about 8 s on the fast path, about a minute through the full agent path." |
| 1:55 | Close | The README or the title | "Advisory only, simulation only. The crew decides; we get them there with the picture." |

## Fallbacks (decide before judging)
- **Live API slow or rate-limited:** switch to the recorded video, or press **Run call** on a short `SHOW_*` call (three segments) instead of S2.
- **Viewer shows "VIEWER UNAVAILABLE":** reload once; check the Matterport and Vexcel keys on the server. Do not demo with `?mock=1` unless you say it is mock data.
- **Venue Wi-Fi blocks the site:** hotspot.

## What to avoid claiming
- Do not mention Photon or an iMessage or camera link: it is not in this build.
- Do not quote a time-to-brief number you did not measure. Quote the fast path with its sample size, or the measured agent-path median.
- Routes and distances are estimates; say "estimate".
