// Human labels for fact keys (dotted keys like "incident.floor" never reach the screen).
const FACT_LABELS: Record<string, string> = {
  "building.use": "Use",
  "building.stories": "Stories",
  "building.year_built": "Year built",
  "building.area_sqft": "Area sq ft",
  "building.height_est": "Height",
  "incident.floor": "Reported floor",
  "location.address": "Address",
  "people.reported": "Occupants",
  "smoke.visible": "Smoke visible",
  "alarm.sounding": "Alarm sounding",
};

/** Label for a fact key; unknown keys fall back to their last segment, e.g. "fire.flames_visible" -> "Flames visible". */
export function factLabel(key: string): string {
  if (FACT_LABELS[key]) return FACT_LABELS[key];
  const last = key.split(".").pop() ?? key;
  const words = last.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
