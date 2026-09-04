#!/usr/bin/env python3
"""Тесты tg_bot.py: извлечение JSON из ответа LLM, rate-limit. Без сетевых вызовов.

Цикл long polling не тестируется автоматически (см. SDD-C08-DELIVERY-EXTENSIONS.md §11).
Запуск: python3 scripts/test_tg_bot.py
"""
import unittest
from unittest.mock import patch

import job_seeker as js
import tg_bot as bot


class ExtractJsonObjectTests(unittest.TestCase):
    def test_valid_json(self):
        self.assertEqual(bot.extract_json_object('{"terms": []}'), {"terms": []})

    def test_json_with_surrounding_prose(self):
        text = 'Вот профиль:\n{"terms": [{"term": "python", "kind": "original"}], "filters": {}}\nНадеюсь, помогло!'
        result = bot.extract_json_object(text)
        self.assertEqual(result["terms"][0]["term"], "python")

    def test_invalid_json_raises_value_error(self):
        with self.assertRaises(ValueError):
            bot.extract_json_object("это не json вообще")

    def test_no_braces_raises_value_error(self):
        with self.assertRaises(ValueError):
            bot.extract_json_object("")

    def test_invalid_llm_profile_is_parse_failed(self):
        response = {"content": [{"type": "text", "text": '{"terms":"python","filters":{}}'}]}
        with patch.object(bot, "call_llm_messages", return_value=response):
            profile, target_job, error = bot.extract_profile("python", "key")
        self.assertIsNone(profile)
        self.assertIsNone(target_job)
        self.assertEqual(error, "parse_failed")

    def test_target_job_extracted_and_stripped_from_profile(self):
        text = '{"terms": [{"term": "python", "kind": "original"}], "filters": {}, "target_job": "вакансия 9"}'
        response = {"content": [{"type": "text", "text": text}]}
        with patch.object(bot, "call_llm_messages", return_value=response):
            profile, target_job, error = bot.extract_profile("чего не хватает для вакансии 9", "key")
        self.assertIsNone(error)
        self.assertEqual(target_job, "вакансия 9")
        self.assertNotIn("target_job", profile)
        self.assertTrue(js.validate_profile(profile))

    def test_missing_target_job_is_none(self):
        response = {"content": [{"type": "text", "text": '{"terms": [], "filters": {}}'}]}
        with patch.object(bot, "call_llm_messages", return_value=response):
            _, target_job, _ = bot.extract_profile("я питонист", "key")
        self.assertIsNone(target_job)


class MatchTargetJobTests(unittest.TestCase):
    JOBS = [{"id": 9, "title": "Python-разработчик (Backend)"}, {"id": 10, "title": "Frontend-разработчик (React)"}]

    def test_matches_by_id(self):
        job, similar = bot.match_target_job("9", self.JOBS)
        self.assertEqual(job["id"], 9)
        self.assertEqual(similar, [])

    def test_matches_embedded_id(self):
        job, similar = bot.match_target_job("вакансия 9", self.JOBS)
        self.assertEqual(job["id"], 9)
        self.assertEqual(similar, [])

    def test_matches_by_unique_title_substring(self):
        job, similar = bot.match_target_job("frontend", self.JOBS)
        self.assertEqual(job["id"], 10)

    def test_no_target_job_returns_none(self):
        job, similar = bot.match_target_job(None, self.JOBS)
        self.assertIsNone(job)
        self.assertEqual(similar, [])

    def test_ambiguous_substring_returns_candidates_not_a_match(self):
        jobs = [{"id": 1, "title": "Python Backend"}, {"id": 2, "title": "Python Data"}]
        job, similar = bot.match_target_job("python", jobs)
        self.assertIsNone(job)
        self.assertEqual({j["id"] for j in similar}, {1, 2})

    def test_no_match_returns_empty_candidates(self):
        job, similar = bot.match_target_job("golang", self.JOBS)
        self.assertIsNone(job)
        self.assertEqual(similar, [])


class FormatGapsPlainTests(unittest.TestCase):
    def test_empty_gaps_says_matches_all(self):
        result = {"job": {"id": 9, "title": "Python Dev"}, "gaps": [], "unknown_fields": []}
        text = bot.format_gaps_plain(result)
        self.assertIn("подходите по всем", text)

    def test_gaps_and_unknown_fields_listed(self):
        result = {
            "job": {"id": 9, "title": "Python Dev"},
            "gaps": [{"field": "salary", "job_value": 200000, "profile_value": 250000, "message": "вилка до 200000, ожидание от 250000"}],
            "unknown_fields": ["city"],
        }
        text = bot.format_gaps_plain(result)
        self.assertIn("вилка до 200000", text)
        self.assertIn("city", text)


class HandleUpdateInternalModeTests(unittest.TestCase):
    """Двухшаговый диалог и память профиля во внутреннем режиме (SDD-C09 §4 п.4)."""

    JOBS = [{"id": 9, "title": "Python-разработчик (Backend)", "salary_to": 300000, "city": None, "schedule": None, "key_skills": []}]

    def setUp(self):
        self.sent = []
        patch.object(bot, "MODE", "internal").start()
        patch.object(bot, "ALLOWED_USER_IDS", frozenset({1, 2})).start()
        patch.object(bot, "send_message", side_effect=lambda chat_id, text: self.sent.append(text)).start()
        patch.object(bot, "get_cached_jobs", return_value=self.JOBS).start()
        self.addCleanup(patch.stopall)

    def test_second_message_uses_stored_profile_for_gaps(self):
        last_profile = {}
        profile1 = {"terms": [{"term": "python", "kind": "original"}], "filters": {}}
        with patch.object(bot, "extract_profile", return_value=(profile1, None, None)):
            bot.handle_update(1, "я питон разработчик", {}, {"jobs": None, "fetched_at": 0}, last_profile)
        self.assertEqual(last_profile[1], profile1)

        with patch.object(bot, "extract_profile", return_value=({"terms": [], "filters": {}}, "9", None)):
            bot.handle_update(1, "чего не хватает для вакансии 9", {}, {"jobs": None, "fetched_at": 0}, last_profile)
        self.assertIn("Вакансия: Python-разработчик (Backend)", self.sent[-1])

    def test_target_question_uses_saved_profile_even_when_llm_extracts_terms(self):
        last_profile = {1: {"terms": [{"term": "python", "kind": "original"}, {"term": "django", "kind": "original"}], "filters": {}}}
        extracted = {"terms": [{"term": "python", "kind": "original"}], "filters": {}}
        with patch.object(bot, "extract_profile", return_value=(extracted, "9", None)):
            bot.handle_update(1, "чего не хватает для вакансии 9", {}, {"jobs": None, "fetched_at": 0}, last_profile)
        self.assertEqual(last_profile[1]["terms"], [{"term": "python", "kind": "original"}, {"term": "django", "kind": "original"}])

    def test_group_chat_is_rejected_before_profile_extraction(self):
        with patch.object(bot, "extract_profile") as extract:
            bot.handle_update(100, "я питон разработчик", {}, {"jobs": None, "fetched_at": 0}, {}, user_id=1, chat_type="group")
        extract.assert_not_called()
        self.assertIn("личном чате", self.sent[-1])

    def test_unauthorized_user_is_rejected_before_profile_extraction(self):
        with patch.object(bot, "extract_profile") as extract:
            bot.handle_update(3, "я питон разработчик", {}, {"jobs": None, "fetched_at": 0}, {}, user_id=3)
        extract.assert_not_called()
        self.assertIn("авторизованным", self.sent[-1])

    def test_target_job_without_prior_profile_asks_to_describe_first(self):
        last_profile = {}
        with patch.object(bot, "extract_profile", return_value=({"terms": [], "filters": {}}, "9", None)):
            bot.handle_update(1, "чего не хватает для вакансии 9", {}, {"jobs": None, "fetched_at": 0}, last_profile)
        self.assertIn("сначала опишите", self.sent[-1].casefold())

    def test_ambiguous_target_job_falls_back_to_match_with_note(self):
        jobs = [{"id": 1, "title": "Python Backend"}, {"id": 2, "title": "Python Data"}]
        last_profile = {}
        with patch.object(bot, "get_cached_jobs", return_value=jobs), patch.object(
            bot, "extract_profile", return_value=({"terms": [{"term": "python", "kind": "original"}], "filters": {}}, "python", None)
        ):
            bot.handle_update(1, "чего не хватает для python", {}, {"jobs": None, "fetched_at": 0}, last_profile)
        self.assertIn("Похожие по названию", self.sent[-1])


class RunStartupChecksTests(unittest.TestCase):
    def test_internal_mode_without_potok_token_exits_at_startup(self):
        with patch.object(bot, "TELEGRAM_TOKEN", "x"), patch.object(bot, "ANTHROPIC_API_KEY", "y"), patch.object(
            bot, "MODE", "internal"
        ), patch.object(bot.js.tp, "BASE_URL", ""), patch.object(bot.js.tp, "TOKEN", ""):
            with self.assertRaises(SystemExit):
                bot.run()

    def test_internal_mode_without_allowed_users_exits_at_startup(self):
        with patch.object(bot, "TELEGRAM_TOKEN", "x"), patch.object(bot, "ANTHROPIC_API_KEY", "y"), patch.object(
            bot, "MODE", "internal"
        ), patch.object(bot.js.tp, "BASE_URL", "https://api.example"), patch.object(bot.js.tp, "TOKEN", "token"), patch.object(
            bot, "ALLOWED_USER_IDS", frozenset()
        ):
            with self.assertRaises(SystemExit):
                bot.run()


class RateLimitTests(unittest.TestCase):
    def test_first_request_not_limited(self):
        self.assertFalse(bot.is_rate_limited({}, 1, now=100.0))

    def test_second_request_within_window_is_limited(self):
        ts = {1: 100.0}
        self.assertTrue(bot.is_rate_limited(ts, 1, now=102.0))

    def test_request_after_window_not_limited(self):
        ts = {1: 100.0}
        self.assertFalse(bot.is_rate_limited(ts, 1, now=106.0))

    def test_different_chat_ids_independent(self):
        ts = {1: 100.0}
        self.assertFalse(bot.is_rate_limited(ts, 2, now=100.5))


class FormatSalaryTests(unittest.TestCase):
    def test_range(self):
        self.assertEqual(bot._format_salary({"salary_from": 200000, "salary_to": 300000, "currency": "RUR"}), "200000–300000 RUR")

    def test_upper_bound_only(self):
        self.assertEqual(bot._format_salary({"salary_to": 300000, "currency": "RUR"}), "до 300000 RUR")

    def test_no_salary(self):
        self.assertEqual(bot._format_salary({}), "зарплата не указана")


if __name__ == "__main__":
    unittest.main(verbosity=2)
