"""Bridge service entry for nssm (avoids fragile `python -c` command lines)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.main import create_bridge_app  # noqa: E402
import uvicorn  # noqa: E402

port = int(os.environ.get("BRIDGE_PORT", "8790"))
if not os.environ.get("BRIDGE_PSK"):
    raise SystemExit("BRIDGE_PSK is not set")

uvicorn.run(create_bridge_app(), host="127.0.0.1", port=port, reload=False)
