import os
from pathlib import Path
import sys
import traceback


if Path(sys.argv[0]).name == "_sdk.py":
    try:
        import claude_agent_sdk
        from sdk_scenarios import query

        claude_agent_sdk.query = query
    except Exception:
        # a broken fixture must never fall through to a live query
        traceback.print_exc()
        os._exit(70)
