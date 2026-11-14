"""FlameScout FastAPI backend. Run: uvicorn services.api.main:app --reload"""
import sys
from pathlib import Path

# flamescout_schemas lives in packages/schemas (same trick as scripts/, pytest pythonpath).
_SCHEMAS = str(Path(__file__).resolve().parents[2] / "packages" / "schemas")
if _SCHEMAS not in sys.path:
    sys.path.insert(0, _SCHEMAS)
