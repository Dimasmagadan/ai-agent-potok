#!/usr/bin/env python3
"""Тесты tg_bot.py: извлечение JSON из ответа LLM, rate-limit. Без сетевых вызовов.

Цикл long polling не тестируется автоматически (см. SDD-C08-DELIVERY-EXTENSIONS.md §11).
Запуск: python3 scripts/test_tg_bot.py
"""
import unittest

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
