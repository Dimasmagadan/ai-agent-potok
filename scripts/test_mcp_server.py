#!/usr/bin/env python3
"""Тесты mcp_server.py: протокол JSON-RPC поверх функций-обработчиков (без реального stdio/сети).

Запуск: python3 scripts/test_mcp_server.py
"""
import unittest
from unittest.mock import patch

import job_seeker as js
import mcp_server as srv
import talent_pool as tp


class ProtocolTests(unittest.TestCase):
    def test_initialize(self):
        resp = srv.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(resp["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(resp["result"]["serverInfo"]["name"], "potok-talent-pool")

    def test_notifications_initialized_has_no_reply(self):
        resp = srv.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertIsNone(resp)

    def test_tools_list_has_five_tools(self):
        resp = srv.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertEqual(names, {"potok_reserve", "potok_search", "potok_dedup", "potok_reopen", "potok_jobs_match"})
        for tool in resp["result"]["tools"]:
            self.assertFalse(tool["inputSchema"].get("additionalProperties", True))

    def test_reopen_schema_describes_required_salary_context(self):
        resp = srv.handle_message({"jsonrpc": "2.0", "id": 11, "method": "tools/list"})
        tools = resp["result"]["tools"]
        reopen = next(tool for tool in tools if tool["name"] == "potok_reopen")
        request = reopen["inputSchema"]["properties"]["request"]
        self.assertIn("target_job_id", request["properties"])
        self.assertIn("previous_criteria", request["properties"])
        self.assertIn("salary_to", reopen["inputSchema"]["$defs"]["criteria"]["properties"])
        self.assertIn("Не передавай changes", reopen["description"])

    def test_jobs_match_schema_describes_profile_terms_and_filters(self):
        resp = srv.handle_message({"jsonrpc": "2.0", "id": 12, "method": "tools/list"})
        tools = resp["result"]["tools"]
        jobs_match = next(tool for tool in tools if tool["name"] == "potok_jobs_match")
        profile = jobs_match["inputSchema"]["properties"]["profile"]
        self.assertIn("terms", profile["properties"])
        self.assertIn("filters", profile["properties"])
        self.assertEqual(profile["properties"]["terms"]["items"]["required"], ["term", "kind"])
        self.assertIn("salary_from", profile["properties"]["filters"]["properties"])

    def test_unknown_method_returns_dash_32601(self):
        resp = srv.handle_message({"jsonrpc": "2.0", "id": 3, "method": "does_not_exist"})
        self.assertEqual(resp["error"]["code"], -32601)


class ToolCallTests(unittest.TestCase):
    def test_reserve_call_matches_cli_function(self):
        with patch.object(tp, "build_reserve_pool", return_value=[{"id": 1}]):
            resp = srv.handle_message({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "potok_reserve", "arguments": {}}})
        self.assertFalse(resp["result"]["isError"])
        self.assertIn('"id": 1', resp["result"]["content"][0]["text"])

    def test_exception_inside_tool_becomes_is_error_without_crash(self):
        with patch.object(tp, "build_reserve_pool", side_effect=RuntimeError("boom")):
            resp = srv.handle_message({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "potok_reserve", "arguments": {}}})
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("boom", resp["result"]["content"][0]["text"])
        # сервер не падает: следующий вызов обрабатывается как обычно
        resp2 = srv.handle_message({"jsonrpc": "2.0", "id": 6, "method": "tools/list"})
        self.assertIn("result", resp2)

    def test_unknown_tool_name_is_error(self):
        resp = srv.handle_message({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "nope", "arguments": {}}})
        self.assertTrue(resp["result"]["isError"])

    def test_reopen_validation_error_is_error_true(self):
        resp = srv.handle_message({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "potok_reopen", "arguments": {"request": {}}}})
        self.assertTrue(resp["result"]["isError"])

    def test_jobs_match_invalid_profile_is_error_true_without_hitting_network(self):
        with patch.object(js, "fetch_jobs_constructor", side_effect=AssertionError("should not be called")):
            resp = srv.handle_message(
                {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "potok_jobs_match", "arguments": {"profile": {"terms": "not-a-list"}}}}
            )
        self.assertTrue(resp["result"]["isError"])

    def test_jobs_match_valid_profile_calls_match_jobs_and_returns_gaps(self):
        job = {"id": 1, "title": "Python developer", "key_skills": []}
        with patch.object(js, "fetch_jobs_constructor", return_value=[job]), patch.object(js, "match_jobs", return_value={"jobs": [{"id": 1}]}) as match_jobs, patch.object(js, "compute_gaps", return_value=([], ["salary"])):
            resp = srv.handle_message(
                {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "potok_jobs_match", "arguments": {"profile": {"terms": []}}}}
            )
        self.assertFalse(resp["result"]["isError"])
        self.assertTrue(match_jobs.called)
        self.assertIn('"unknown_fields": [', resp["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
