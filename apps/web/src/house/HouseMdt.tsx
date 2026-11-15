// MDT screen for a confirmed house that is in the viewer catalog with a stored plan (useConfirmedHouse state "house"),
// with or without the brief. Top: the address once and the 4 summary cards. Left: size-up (listing + plan, nothing
// invented) and up to 5 considerations. Center + right: one viewer iframe in layout=house (3D full height; the 2D plan
// and Surroundings stacked on the right). No inspector, legend, layers menu, version or live chip on this screen.
import { BuildingViewer } from "../components/viewer/BuildingViewer";
import type { Brief } from "../types/schemas";
import { houseBrief, NA } from "./houseBrief";
import { peopleSummary } from "./people";
import type { ConfirmedHouse } from "./liveFeed";
import { useHouseData } from "./useHouseData";

const HAZARDS = ["Gas", "Fireplace", "Garage", "Solar panels"];   // listing mentions shown on this screen (no pool)

function Readout({ label, value, sub, tone = "text-ink" }: { label: string; value: string; sub?: React.ReactNode; tone?: string }) {
  return (
    <div className="bg-panel px-3 py-2 text-left">
      <div className="text-[15px] font-semibold uppercase tracking-wider text-dim">{label}</div>
      <div className={`num text-[44px] font-bold leading-none ${tone}`}>{value}</div>
      <div className="mt-1 h-5 text-[15px] text-dim">{sub}</div>
    </div>
  );
}

/** "9660 Fairdale Lane, Houston, Houston" -> "9660 Fairdale Lane, Houston" */
const dedupe = (a: string) => [...new Set(a.split(",").map((x) => x.trim()).filter(Boolean))].join(", ");

export function HouseMdt({ house, brief }: { house: Extract<ConfirmedHouse, { state: "house" }>; brief: Brief | null }) {
  const { sample, doc, incident, onPlanIndex, onIncidentState } = useHouseData(house.sampleId);
  const hb = sample ? houseBrief(sample, doc, incident.overlay) : null;
  const address = hb?.extras.header.address ?? dedupe(house.address);

  // size-up: listing first, plan where the listing has nothing; missing data stays "Not available"
  const plan = doc?.plan ?? null, L = sample?.listing ?? null;
  const floors = plan?.floors ?? [];
  const beds = floors.flatMap((f) => f.rooms).filter((r) => r.type === "Bedroom").length;
  const exits = (floors[0]?.openings ?? []).filter((o) => o.type === "doorway" && o.exterior === true).length;
  const stairs = plan?.stairs?.length ?? 0;
  const rows: [string, string][] = [
    ["Floors", plan ? String(floors.length) : doc?.listing_record?.stories != null ? String(doc.listing_record.stories) : NA],
    ["Year built", L?.yearBuilt != null ? String(L.yearBuilt) : NA],
    ["Area (sq ft)", L?.sqft ? Math.round(L.sqft).toLocaleString("en-US") : NA],
    ["Bedrooms", L?.beds != null ? String(L.beds) : plan ? String(beds) : NA],
    ["Exterior doors", plan ? String(exits) : NA],
    ["Stairs", plan ? (stairs ? String(stairs) : "Not detected") : NA],
  ];
  // only what this house has: a garage needs a garage on the plan (the listing may mention a neighbour's or a carport)
  const hasRoom = (t: string) => floors.some((f) => f.rooms.some((r) => r.type === t));
  const hazards = (hb?.extras.hazards ?? []).map((h) => h.label).filter((h) => HAZARDS.includes(h) && (h !== "Garage" || !plan || hasRoom("Garage")));

  // considerations: the brief's (by rank), then the incident overlay's; titles only, 5 at most
  const cons = [
    ...[...(brief?.considerations ?? [])].sort((a, b) => a.rank - b.rank).map((c) => ({ id: `b-${c.id}`, text: c.title })),
    ...(hb?.extras.considerations ?? []).filter((c) => c.source === "incident").map((c) => ({ id: `i-${c.id}`, text: c.text })),
  ].slice(0, 5);

  // summary cards: same as the brief screen, without version or scan text; before the brief, "—"
  const stairMarkers = brief?.markers.filter((m) => m.kind === "stair").length ?? 0;
  const stairwells = brief && stairMarkers ? stairMarkers : plan ? stairs : null;
  const people = peopleSummary(incident.overlay, plan);   // PEOPLE card: validated overlay only; without it, a dash
  const hazardCount = brief ? brief.hazards.length : hb ? hazards.length : null;

  return (
    <div className="flex h-full flex-col" data-confirmed-building={house.buildingId} data-house-mdt>
      <header className="flex h-11 shrink-0 items-center border-b border-line bg-panel px-3 text-[20px] font-bold uppercase">{address}</header>

      <div className="grid shrink-0 grid-cols-3 gap-px border-b border-line bg-line">
        <Readout label="Stairwells identified" value={stairwells == null ? "—" : String(stairwells)} sub={stairwells != null && stairwells < 2 ? "verify on arrival" : undefined} />
        <Readout label="Hazard flags" value={hazardCount == null ? "—" : String(hazardCount)} sub={brief ? "from tenants (inferred)" : hb ? "from listing" : undefined} />
        <div className="min-w-0 bg-panel px-3 py-2 text-left" data-people-card>
          <div className="text-[15px] font-semibold uppercase tracking-wider text-dim">People</div>
          <div className="num text-[44px] font-bold leading-none text-ink" data-people-total>{people ? String(people.total) : "—"}</div>
          <div className="mt-1 h-5 truncate whitespace-nowrap text-[15px] leading-5 text-ink" data-people-rooms>{people?.rooms}</div>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[260px_minmax(0,1fr)] gap-px bg-line max-[1100px]:grid-cols-[236px_minmax(0,1fr)]">
        <aside className="min-h-0 overflow-y-auto bg-panel">
          <div className="border-b border-line bg-bg px-3 py-1.5 text-[15px] font-semibold uppercase tracking-wider text-dim">Size-up</div>
          {rows.map(([k, v]) => (
            <div key={k} data-sizeup-row className="flex items-baseline justify-between gap-2 border-b border-line px-3 py-2">
              <span data-k className="text-[18px] text-dim">{k}</span>
              <span data-v className={`num text-right text-[24px] font-bold ${v === NA ? "text-faint" : "text-ink"}`}>{v}</span>
            </div>
          ))}
          {hazards.length > 0 && (
            <div data-hazards className="border-b border-line px-3 py-2 text-[20px]">
              <span className="text-dim">Hazards </span><span className="font-semibold text-reported">{hazards.join(" · ")}</span>
            </div>
          )}
          {cons.length > 0 && (
            <>
              <div className="border-b border-line bg-bg px-3 py-1.5 text-[15px] font-semibold uppercase tracking-wider text-dim">Considerations</div>
              {cons.map((c) => (
                <div key={c.id} className="border-b border-line px-3 py-2 text-[17px] leading-snug text-ink"><span className="line-clamp-3">{c.text}</span></div>
              ))}
            </>
          )}
        </aside>
        <section className="relative min-h-0 bg-bg">
          <BuildingViewer layout="house" sampleId={house.sampleId} onIncidentState={onIncidentState} onPlanIndex={onPlanIndex} />
        </section>
      </div>
    </div>
  );
}
