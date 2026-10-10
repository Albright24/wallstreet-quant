"""One provider/schema check. No broker, databases, or trading pipeline."""
from datetime import datetime, timezone
import json
import os
from .advisor import OpenAIAdvisor, provider_settings


def main():
    try:
        provider, endpoint, key = provider_settings()
        model = os.getenv("WQ_MODEL", "").strip()
        if not key.strip() or not model:
            raise ValueError("Missing provider API key or WQ_MODEL in this PowerShell session")
        print(f"Checking {provider} / {model}: one API request, no trades. Allow up to 120 seconds.", flush=True)
        event = {
            "source": "synthetic_connection_check", "id": "connection-check",
            "revision": "1", "symbol": "DEMO_EQUITY", "synthetic": True,
            "verified": False, "published_at": datetime.now(timezone.utc).isoformat(),
            "headline": "Synthetic test: no actionable company news supplied",
            "body": "This is an invented connectivity test. No investment evidence is provided.",
            "evidence": [],
        }
        decision = OpenAIAdvisor().decide(event, {"cash": "10000", "positions": {}})
        if decision["action"] not in ("skip", "hold"):
            raise ValueError("Connection responded, but the model did not abstain on an empty test event")
        print(json.dumps({"status": "passed", "provider": provider,
            "endpoint": endpoint, "requested_model": model,
            "structured_decision_valid": True, "trades_placed": 0,
            "action": decision["action"], "usage": decision.get("usage", {})}, indent=2))
        return 0
    except (ValueError, KeyError, TypeError):
        # Do not print provider response bodies or model text: neither is trusted
        # to omit credentials. Advisor errors themselves are fixed local messages.
        import sys
        exc = sys.exc_info()[1]
        safe = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else "Invalid model response"
        print(json.dumps({"status": "failed", "error": safe, "trades_placed": 0}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
