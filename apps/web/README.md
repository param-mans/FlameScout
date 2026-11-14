# FlameScout web (MDT + dispatcher console)

    npm install
    npm run dev      # http://localhost:5173, proxies /api and /ws to :8000
    npm run build

Data: the app uses the FastAPI backend when `/health` answers (badge "LIVE" in the top bar) and falls back to
the mock S2 data in `src/mock/s2.ts` when it does not (badge "MOCK DATA", "Retry API" button). `?mock=1`
forces mock data. Start the API from the repo root: `python -m uvicorn services.api.main:app --port 8000`.

Live flow: pick a scripted call in the top bar and press **Run call** (creates an incident, opens
`/ws/incidents/{id}`, replays the script) -> the Dispatcher view fills in -> **Confirm location** -> the MDT
shows the orchestrator's plan, then the brief. The incident id is kept in `?incident=` and localStorage.

- `src/api/client.ts` REST helpers, `src/api/useLiveIncident.ts` state + WebSocket (reconnects).
- A brief held for low-confidence considerations shows a "held for review" bar in the Dispatcher view.
- Transcript segments are not replayed on reload (the API has no endpoint for them); facts, candidates, the
  latest brief and copilot questions are re-fetched.

Types in `src/types/schemas.ts` mirror `packages/schemas` (update by hand when a Pydantic model changes).
