#!/usr/bin/env python3
"""Mock Potok API для демо без живого тенанта. См. SKILL.md / README.

Запуск: python3 scripts/mock_server.py [порт, по умолчанию 8765]
Затем: POTOK_BASE_URL=http://localhost:8765 POTOK_API_TOKEN=demo python3 scripts/talent_pool.py reserve
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load(name):
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


JOBS = _load("jobs.json")
APPLICANTS = _load("applicants.json")
AJS_JOINS = _load("ajs_joins.json")
FINALISTS = _load("finalists.json")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, body):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/jobs.json":
            return self._json({"data": JOBS, "page": 1, "pages": 1, "per_page": len(JOBS)})

        if path == "/applicants.json":
            return self._json({"data": APPLICANTS, "page": 1, "pages": 1, "per_page": len(APPLICANTS)})

        if path == "/finalists.json":
            return self._json({"objects": FINALISTS, "has_next_page": False, "page_next_cursor": None})

        m = re.match(r"^/jobs/(\d+)/ajs_joins\.json$", path)
        if m:
            joins = AJS_JOINS.get(m.group(1), [])
            return self._json({"objects": joins, "has_next_page": False, "page_next_cursor": None})

        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"Mock Potok API на http://localhost:{port} (фикстуры: {FIXTURES})")
    HTTPServer(("localhost", port), Handler).serve_forever()
