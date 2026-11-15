// "PEOPLE" card of the house screen (HouseMdt): total people reported in the validated incident overlay and, on one line,
// where they are. A contradicted group (one person or party reported in several places) counts ONCE, with its candidate
// rooms and a "?": "Office 1 · Bedroom 2 · Bathroom ? 1". Floor-level reports: "Floor 2 ? 1". Nothing is invented.
import type { HouseDoc, Overlay } from "./houseBrief";

type Occ = NonNullable<Overlay["occupants"]>[number];
const count = (o: Occ) => o.count ?? o.count_range?.[1] ?? 1;
const nice = (s: string) => (s === s.toUpperCase() ? s.charAt(0) + s.slice(1).toLowerCase() : s);

export function peopleSummary(overlay: Overlay | null, plan: HouseDoc["plan"]): { total: number; rooms: string } | null {
  const occ = overlay?.occupants ?? [];
  if (!overlay) return null;
  const floors = plan?.floors ?? [];
  const roomName = (id?: string) => {
    for (const f of floors) { const r = f.rooms.find((x) => x.id === id); if (r) return nice(r.label || r.type || "Room"); }
    return "Room";
  };
  const place = (o: Occ) => {
    const l = o.location;
    if (!l || l.kind === "unknown") return null;
    if (l.kind === "floor") {
      const i = l.floor_id ? floors.findIndex((f) => f.id === l.floor_id) : -1;
      return `Floor ${i >= 0 ? i + 1 : l.floor_reported ?? "?"}`;
    }
    return roomName(l.kind === "room" ? l.room_id : l.anchor?.room_id);
  };
  const parts: { name: string; n: number; unsure: boolean }[] = [];
  const add = (name: string, n: number, unsure: boolean) => {
    const p = parts.find((x) => x.name === name && x.unsure === unsure);
    if (p) p.n += n; else parts.push({ name, n, unsure });
  };
  const groups = new Set<string>();
  let total = 0;
  for (const o of occ) {
    if (o.group_id) {
      if (groups.has(o.group_id)) continue;
      groups.add(o.group_id);
      const members = occ.filter((x) => x.group_id === o.group_id), n = Math.max(...members.map(count));
      const names = [...new Set(members.map((m) => place(m) ?? "?"))];
      total += n; add(names.join("/"), n, true);
      continue;
    }
    const n = count(o), where = place(o);
    total += n;
    add(where ?? "?", n, !where || o.location?.kind === "floor" || o.status === "contradicted");
  }
  return { total, rooms: parts.map((p) => (p.name === "?" ? `? ${p.n}` : `${p.name}${p.unsure ? " ?" : ""} ${p.n}`)).join(" · ") };
}
