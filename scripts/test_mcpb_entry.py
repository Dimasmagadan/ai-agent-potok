#!/usr/bin/env python3
"""Integration tests for the self-contained MCP Bundle entry point."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import mcp_server


ROOT = Path(__file__).parent.parent
ENTRY = ROOT / "scripts" / "mcpb_entry.py"
POTOK_ENV = [key for key in os.environ if key.startswith("POTOK_")]


class McpbEntryTests(unittest.TestCase):
    def test_demo_mode_serves_mcp_tools_without_external_settings(self):
        env = os.environ.copy()
        for key in POTOK_ENV:
            env.pop(key)
        env["POTOK_DEMO_MODE"] = "true"
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "potok_search", "arguments": {"terms": [{"term": "python", "kind": "original"}], "cv": True}},
            },
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "potok_dedup", "arguments": {}}},
        ]
        process = subprocess.run(
            [sys.executable, str(ENTRY)],
            input="".join(json.dumps(request, ensure_ascii=False) + "\n" for request in requests),
            text=True,
            capture_output=True,
            env=env,
            timeout=15,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(responses[0]["result"]["serverInfo"], mcp_server.SERVER_INFO)
        self.assertEqual(len(responses[1]["result"]["tools"]), 5)
        for response in responses[2:]:
            self.assertFalse(response["result"]["isError"])
            self.assertTrue(json.loads(response["result"]["content"][0]["text"]))

    def test_real_mode_requires_url_and_token(self):
        env = os.environ.copy()
        for key in POTOK_ENV:
            env.pop(key)
        env["POTOK_DEMO_MODE"] = "false"
        process = subprocess.run([sys.executable, str(ENTRY)], text=True, capture_output=True, env=env, timeout=5, check=False)
        self.assertEqual(process.returncode, 1)
        self.assertIn("POTOK_BASE_URL and POTOK_API_TOKEN", process.stderr)

    def test_manifest_version_matches_server(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], mcp_server.SERVER_INFO["version"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
