"""Force a timeout on the primary model and check the call fails over to LLM_FALLBACK_PROVIDER.

    python scripts/check_failover.py

Builds the orchestrator's real models and middleware (services.agents.orchestrator._Models), makes the
primary model (LLM_PROVIDER, normally Gemini) raise a timeout on every call, and asks one question
through a one-node agent. Passes when the backup provider answers. One real API call, on the backup.
"""
import logging
import sys
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packages" / "schemas")]

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from langchain.agents import create_agent  # noqa: E402

from services.agents.llm import fallback_provider, model_name, provider  # noqa: E402
from services.agents.orchestrator import _Models  # noqa: E402
from services.agents.stalls import StallCounter  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    primary, backup = provider(), fallback_provider("fast")
    if not backup:
        print("FAIL: no failover configured. Set LLM_FALLBACK_PROVIDER (openai or xai) and its API key and "
              "fast model ID in .env.")
        return 1
    counter = StallCounter()
    m = _Models(counter)
    agent = create_agent(model=m.main, middleware=m.middleware("failover-check", m.main_backup))
    print(f"Primary {primary}:{model_name('fast', primary)} forced to time out; "
          f"backup {backup}:{model_name('fast', backup)}")
    t0 = time.perf_counter()
    with mock.patch.object(type(m.main), "_generate", side_effect=TimeoutError(f"forced {primary} timeout")):
        out = agent.invoke({"messages": [{"role": "user", "content": "Reply with the single word OK."}]})
    reply = out["messages"][-1]
    answered_by = (reply.response_metadata or {}).get("model_name") or (reply.response_metadata or {}).get("model")
    ok = counter.failovers == 1 and bool(reply.text.strip())
    print(f"{'PASS' if ok else 'FAIL'}: failovers={counter.failovers}, answered in {time.perf_counter() - t0:.1f} s "
          f"by {answered_by or backup}: {reply.text.strip()[:60]!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
