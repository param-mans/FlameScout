"""Export JSON Schema for the frontend. Then in apps/web:
    npx json-schema-to-typescript ../../packages/schemas/schema.json -o src/types/schemas.ts
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "schemas"))

import flamescout_schemas as s  # noqa: E402

models = [s.IncidentRecord, s.BuildingDossier, s.Brief, s.Progress, s.BuildingCandidate, s.LocationCandidate,
          s.CopilotSuggestion, s.CrewFeedback, s.Event, s.IncidentOverlay]
from pydantic.json_schema import models_json_schema  # noqa: E402

_, schema = models_json_schema([(m, "serialization") for m in models], title="FlameScout")
out = ROOT / "packages" / "schemas" / "schema.json"
out.write_text(json.dumps(schema, indent=2))
print(f"wrote {out.relative_to(ROOT)}")
