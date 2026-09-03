#!/usr/bin/env python3
"""Тесты ядра talent_pool.py: нормализация телефона, дедуп, поиск, резерв.

Запуск: python3 scripts/test_talent_pool.py (stdlib unittest, без зависимостей).
"""
import unittest
from io import BytesIO
from email.message import Message
from urllib.error import HTTPError, URLError
from unittest.mock import MagicMock, patch

import talent_pool as tp


class NormalizePhoneTests(unittest.TestCase):
    def test_plus7_formatted_and_plain_collide(self):
        self.assertEqual(tp._normalize_phone("+7 (916) 123-45-01"), "9161234501")
        self.assertEqual(tp._normalize_phone("79161234501"), "9161234501")

    def test_leading_eight_collides_with_plus7(self):
        self.assertEqual(tp._normalize_phone("8 916 123 45 01"), "9161234501")

    def test_international_untouched(self):
        self.assertEqual(tp._normalize_phone("+12025550123"), "12025550123")

    def test_empty(self):
        self.assertEqual(tp._normalize_phone(""), "")
        self.assertEqual(tp._normalize_phone(None), "")


class FindDuplicatesTests(unittest.TestCase):
    def test_finds_pairs_across_formats(self):
        applicants = [
            {"id": 1, "email": "a@x.com", "phones": ["+7 (916) 123-45-01"]},
            {"id": 2, "email": "b@x.com", "phones": ["8 916 123 45 01"]},
            {"id": 3, "email": "Shared@X.com", "phones": []},
            {"id": 4, "email": "shared@x.com", "phones": []},
            {"id": 5, "email": "solo@x.com", "phones": ["70000000001"]},
        ]
        groups = {(g["match"], g["value"]): sorted(g["applicant_ids"]) for g in tp.find_duplicates(applicants)}
        self.assertEqual(groups[("phone", "9161234501")], [1, 2])
        self.assertEqual(groups[("email", "shared@x.com")], [3, 4])

    def test_no_false_positive_within_one_card(self):
        self.assertEqual(tp.find_duplicates([{"id": 1, "email": "a@x.com", "phones": ["999", "999"]}]), [])


class SearchReserveTests(unittest.TestCase):
    RESERVE = [
        {"id": 1, "name": "JS Dev", "title": "JavaScript developer", "tags": ["react"]},
        {"id": 2, "name": "Py Dev", "title": "Python developer", "tags": ["django", "postgresql"]},
        {"id": 3, "name": "Lead", "title": "Team Lead Java", "tags": []},
    ]

    def test_no_substring_false_positive_java_vs_javascript(self):
        res = tp.search_reserve(self.RESERVE, [{"term": "java", "kind": "original"}])
        self.assertEqual([r["applicant_id"] for r in res], [3])

    def test_multiword_term_requires_all_tokens(self):
        res = tp.search_reserve(self.RESERVE, [{"term": "team lead", "kind": "original"}])
        self.assertEqual([r["applicant_id"] for r in res], [3])

    def test_symbol_term(self):
        res = tp.search_reserve(
            [{"id": 9, "title": "C++ developer", "tags": []}],
            [{"term": "c++", "kind": "original"}],
        )
        self.assertEqual([r["applicant_id"] for r in res], [9])

    def test_evidence_splits_original_and_synonym(self):
        res = tp.search_reserve(self.RESERVE, [{"term": "django", "kind": "original"}, {"term": "python", "kind": "synonym"}])
        row = next(r for r in res if r["applicant_id"] == 2)
        self.assertEqual(row["matched_original"], ["django"])
        self.assertEqual(row["matched_synonym"], ["python"])

    def test_ranking_by_score_and_top_n(self):
        res = tp.search_reserve(self.RESERVE, [{"term": "developer", "kind": "original"}, {"term": "django", "kind": "synonym"}])
        self.assertEqual([r["applicant_id"] for r in res], [2, 1])
        self.assertEqual([r["score"] for r in res], [2, 1])
        self.assertEqual(len(tp.search_reserve(self.RESERVE, [], top_n=0)), 0)


class ReservePoolTests(unittest.TestCase):
    def _build_with_mocks(self):
        jobs = [{"id": 101}]
        joins = [{"applicant_id": 1, "active": True}, {"applicant_id": 5, "active": False}]
        finalists = [
            {"applicant_id": 2, "state": "hired"},
            {"applicant_id": 3, "state": "cancel_hire"},
        ]
        applicants = [{"id": i} for i in range(1, 6)]

        def cursor(path, params=None, warnings=None):
            return iter(joins if "ajs_joins" in path else finalists)

        with patch.object(tp, "all_jobs", return_value=jobs), patch.object(tp, "_paginate_cursor", side_effect=cursor), patch.object(
            tp, "all_applicants", return_value=applicants
        ):
            return tp.build_reserve_pool()

    def test_active_excluded_hired_excluded_cancel_hire_kept(self):
        reserve = self._build_with_mocks()
        self.assertEqual(sorted(a["id"] for a in reserve), [3, 4, 5])


class HttpTests(unittest.TestCase):
    def test_page_pagination_collects_all_pages(self):
        pages = [
            {"data": [{"id": 1}], "pages": 2},
            {"data": [{"id": 2}], "pages": 2},
        ]
        calls = []

        def request(path, params):
            calls.append(params.copy())
            return pages.pop(0)

        with patch.object(tp, "_request", side_effect=request):
            self.assertEqual(list(tp._paginate_page("/applicants.json")), [{"id": 1}, {"id": 2}])
        self.assertEqual([call["page"] for call in calls], [1, 2])

    def test_rate_limit_retries_using_retry_after(self):
        headers = Message()
        headers["Retry-After"] = "0"
        rate_limited = HTTPError("https://example.test", 429, "Too Many Requests", headers, BytesIO())
        response = MagicMock()
        response.read.return_value = b'{"data": []}'
        response.__enter__.return_value = response
        with patch.object(tp, "BASE_URL", "https://example.test"), patch.object(tp, "urlopen", side_effect=[rate_limited, response]), patch.object(tp.time, "sleep") as sleep:
            self.assertEqual(tp._request("/applicants.json"), {"data": []})
        sleep.assert_called_once_with(0.0)

    def test_network_error_raises_fetch_error(self):
        with patch.object(tp, "BASE_URL", "https://example.test"), patch.object(tp, "urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(tp.FetchError, "ошибка сети"):
                tp._request("/applicants.json")

    def test_exhausted_rate_limit_raises_fetch_error(self):
        headers = Message()
        headers["Retry-After"] = "0"
        errors = [HTTPError("https://example.test", 429, "Too Many Requests", headers, BytesIO()) for _ in range(4)]
        with patch.object(tp, "BASE_URL", "https://example.test"), patch.object(tp, "urlopen", side_effect=errors), patch.object(tp.time, "sleep") as sleep:
            with self.assertRaisesRegex(tp.FetchError, "HTTP 429"):
                tp._request("/applicants.json")
        self.assertEqual(sleep.call_count, 3)

    def test_failed_later_page_returns_collected_partial_data(self):
        warnings = []
        with patch.object(tp, "_request", side_effect=[
            {"data": [{"id": 1}], "pages": 2},
            tp.FetchError("/applicants.json: HTTP 429"),
        ]):
            self.assertEqual(list(tp._paginate_page("/applicants.json", warnings=warnings)), [{"id": 1}])
        self.assertEqual(warnings, ["/applicants.json: HTTP 429"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
