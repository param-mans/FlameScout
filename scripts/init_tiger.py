"""Create Tiger Data hypertables + continuous aggregate. Needs TIGER_DATABASE_URL in .env.

    python scripts/init_tiger.py
"""
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
url = os.getenv("TIGER_DATABASE_URL")
if not url:
    sys.exit("TIGER_DATABASE_URL is not set")
sql = (ROOT / "services" / "api" / "tiger_schema.sql").read_text()
with psycopg.connect(url, autocommit=True, connect_timeout=10) as conn:
    for stmt in [s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]:
        conn.execute(stmt)
print("tiger schema applied")
