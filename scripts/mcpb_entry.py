#!/usr/bin/env python3
"""Bootstrap MCP Bundle: starts fixture API only in demo mode."""
import os
import sys
import threading
from http.server import HTTPServer


def _demo_mode():
    return os.environ.get("POTOK_DEMO_MODE", "true").strip().lower() == "true"


def _start_demo_server():
    # Import after the mode check so real mode never loads the fixture server.
    from mock_server import Handler

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    os.environ.update(
        {
            "POTOK_BASE_URL": base_url,
            "POTOK_API_TOKEN": "demo",
            "POTOK_API_V2_BASE_URL": base_url,
            "POTOK_OPEN_BASE_URL": f"{base_url}/open",
            "POTOK_CONSTRUCTOR_ID": "1",
        }
    )


def main():
    if _demo_mode():
        _start_demo_server()
    elif not os.environ.get("POTOK_BASE_URL") or not os.environ.get("POTOK_API_TOKEN"):
        print("Real mode requires POTOK_BASE_URL and POTOK_API_TOKEN.", file=sys.stderr)
        return 1

    # These modules read Potok settings when imported, so import only after setup.
    import mcp_server

    mcp_server.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
