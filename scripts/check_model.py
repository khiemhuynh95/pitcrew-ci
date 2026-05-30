"""`make check-model` — the #1 first-run trap (HANDOFF §2).

Confirms the LM Studio endpoint is reachable AND the configured model is loaded.
Cross-platform (runs under `uv run`; no curl/shell assumptions).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

api_base = os.environ.get("LMSTUDIO_API_BASE", "http://127.0.0.1:1234/v1").rstrip("/")
model = os.environ.get("LMSTUDIO_MODEL", "google/gemma-4-e4b")
url = f"{api_base}/models"

try:
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 (trusted local endpoint)
        payload = json.load(resp)
except (urllib.error.URLError, OSError) as e:
    print(f"FAIL: cannot reach LM Studio at {url}: {e}")
    print("Is LM Studio running and bound to 0.0.0.0:1234? Is LMSTUDIO_API_BASE correct?")
    sys.exit(1)

ids = [m.get("id") for m in payload.get("data", [])]
print(f"OK: {url} reachable. {len(ids)} model(s) loaded:")
for i in ids:
    mark = "  <- configured (LMSTUDIO_MODEL)" if i == model else ""
    print(f"  - {i}{mark}")

if model and model not in ids:
    print(f"\nFAIL: configured LMSTUDIO_MODEL='{model}' is not loaded in LM Studio.")
    sys.exit(1)

print("\ncheck-model passed.")
