// When the Dispatcher console shows the "Confirm location" section (live API only; mock data keeps it as before).
// The backend confirms a clear winner by itself (AUTO_CONFIRM_LOCATION, confirmed_by "auto"): then the section is
// hidden and a one-line status replaces it. It shows when the choice is ambiguous (same rule as the backend's
// auto_confirm_pick in services/api/service.py), when a clear winner stays unconfirmed for a few seconds (auto-confirm
// off or failed), or when the dispatcher confirmed it by hand.
import { useEffect, useState } from "react";
import { useLiveFeed } from "../house/liveFeed";
import type { BuildingCandidate, IncidentRecord, Progress } from "../types/schemas";
import { PlanList } from "./PlanList";
import { Panel } from "./ui";

const MIN_SCORE = 0.8, MIN_MARGIN = 0.2, GRACE_MS = 4000;

/** Mirrors auto_confirm_pick: true when the backend will wait for the dispatcher. */
export function ambiguous(cands: BuildingCandidate[]): boolean {
  if (cands.length <= 1) return false;
  const [best, second] = [...cands].sort((a, b) => b.score - a.score);
  return !(best.score >= MIN_SCORE && best.score - second.score >= MIN_MARGIN - 1e-9);
}

export function useShowConfirm(incident: IncidentRecord, candidates: BuildingCandidate[], confirmedId: string | null): boolean {
  const live = useLiveFeed().mode === "live";
  const by = incident.confirmed_by ?? null;
  const pending = live && !confirmedId && candidates.length > 0 && !ambiguous(candidates);
  const key = pending ? `${incident.incident_id}|${candidates.map((c) => c.building_id).join(",")}` : null;
  const [late, setLate] = useState<string | null>(null);   // key whose grace period ran out
  useEffect(() => {
    if (!key) return;
    const t = window.setTimeout(() => setLate(key), GRACE_MS);
    return () => window.clearTimeout(t);
  }, [key]);
  if (!live) return true;
  if (confirmedId) return by !== "auto";
  if (candidates.length === 0) return false;
  return ambiguous(candidates) || late === key;
}

/** Replaces the section when it is hidden: who confirmed what, and the orchestrator's plan. */
export function LocationStatus({ incident, candidates, confirmedId, progress, busy }: {
  incident: IncidentRecord; candidates: BuildingCandidate[]; confirmedId: string | null; progress?: Progress | null; busy?: string | null;
}) {
  const c = candidates.find((x) => x.building_id === confirmedId);
  return (
    <Panel title="Location" right={busy ?? (confirmedId ? "confirmed" : "locating")}>
      <div className="num px-3 py-2 text-[12px]" data-location-status>
        {confirmedId ? (
          <span className="text-verified">● {c ? c.name ?? c.address : confirmedId}{incident.confirmed_by === "auto" ? " · confirmed automatically" : ""}</span>
        ) : (
          <span className="text-dim">{candidates.length ? "Locating the building…" : "Awaiting address…"}</span>
        )}
      </div>
      <PlanList progress={progress ?? null} />
    </Panel>
  );
}
