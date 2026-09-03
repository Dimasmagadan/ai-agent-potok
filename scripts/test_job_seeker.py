#!/usr/bin/env python3
"""Тесты job_seeker.py: фильтры, скоринг, очистка HTML — без сетевых вызовов.

Запуск: python3 scripts/test_job_seeker.py
"""
import unittest
from unittest.mock import patch

import job_seeker as js


class StripHtmlTests(unittest.TestCase):
    def test_removes_tags_and_collapses_whitespace(self):
        html = "<p>Опыт с <b>Django</b>  и\n<i>FastAPI</i></p>"
        self.assertEqual(js._strip_html(html), "Опыт с Django и FastAPI")

    def test_empty_input(self):
        self.assertEqual(js._strip_html(None), "")
        self.assertEqual(js._strip_html(""), "")


class ParseOpenJobTests(unittest.TestCase):
    def test_nested_fields_and_apply_url(self):
        raw = {
            "id": 42,
            "name": "Python Backend Developer",
            "department": {"name": "Разработка"},
            "city": {"name": "Москва"},
            "salary": {"from": 280000, "to": 350000, "currency": "RUR"},
            "schedule_type": "remote",
            "description": "<p>Django</p>",
        }
        job = js._parse_open_job(raw, "http://localhost:8765/open")
        self.assertEqual(job["title"], "Python Backend Developer")
        self.assertEqual(job["department"], "Разработка")
        self.assertEqual(job["city"], "Москва")
        self.assertEqual(job["salary_to"], 350000)
        self.assertEqual(job["description"], "Django")
        self.assertEqual(job["apply_url"], "http://localhost:8765/open/jobs/42")

    def test_missing_fields_are_null_not_empty(self):
        job = js._parse_open_job({"id": 1, "name": "X"}, "http://x")
        self.assertIsNone(job["city"])
        self.assertIsNone(job["salary_from"])
        self.assertIsNone(job["description"])

    def test_constructor_fetch_is_unauthenticated(self):
        with patch.object(js.tp, "_request", return_value=[]) as request:
            js.fetch_jobs_constructor("https://careers.example", "1")
        self.assertFalse(request.call_args.kwargs["authenticated"])

    def test_v3_fallback_paginates(self):
        rows = [{"id": 1, "name": "One"}, {"id": 2, "name": "Two"}]
        with patch.object(js.tp, "_paginate_page", return_value=iter(rows)):
            jobs = js.fetch_jobs_v3_fallback("https://careers.example")
        self.assertEqual([job["id"] for job in jobs], [1, 2])


class FilterTests(unittest.TestCase):
    def test_city_equality_after_casefold(self):
        job = {"city": "Москва"}
        self.assertEqual(js._apply_filters(job, {"city": "москва"}), (True, []))
        self.assertEqual(js._apply_filters(job, {"city": "Питер"}), (False, []))

    def test_missing_city_is_not_dropped_but_flagged_unknown(self):
        job = {"city": None}
        passed, unknown = js._apply_filters(job, {"city": "Москва"})
        self.assertTrue(passed)
        self.assertEqual(unknown, ["city"])

    def test_salary_to_null_passes_and_flags_unknown(self):
        job = {"salary_to": None}
        passed, unknown = js._apply_filters(job, {"salary_from": 300000})
        self.assertTrue(passed)
        self.assertEqual(unknown, ["salary"])

    def test_salary_to_below_expectation_is_dropped(self):
        job = {"salary_to": 250000}
        passed, _ = js._apply_filters(job, {"salary_from": 300000})
        self.assertFalse(passed)

    def test_schedule_exact_mismatch_dropped(self):
        job = {"schedule": "fullDay"}
        passed, _ = js._apply_filters(job, {"schedule": "remote"})
        self.assertFalse(passed)


class ScoreJobTests(unittest.TestCase):
    JOB = {"title": "Python Backend Developer", "department": "Разработка", "description": "Опыт с Django и FastAPI"}

    def test_title_weighs_more_than_description(self):
        score, evidence = js._score_job(self.JOB, [{"term": "python", "kind": "original"}])
        self.assertEqual(score, 6)  # title(3) * original(2)
        self.assertEqual(evidence[0]["source"], "title")

    def test_synonym_halves_weight(self):
        score, _ = js._score_job(self.JOB, [{"term": "python", "kind": "synonym"}])
        self.assertEqual(score, 3)  # title(3) * synonym(1)

    def test_description_only_term(self):
        score, evidence = js._score_job(self.JOB, [{"term": "fastapi", "kind": "original"}])
        self.assertEqual(score, 2)  # description(1) * original(2)
        self.assertEqual(evidence[0]["source"], "description")

    def test_no_match_scores_zero_and_no_evidence(self):
        score, evidence = js._score_job(self.JOB, [{"term": "golang", "kind": "original"}])
        self.assertEqual(score, 0)
        self.assertEqual(evidence, [])


class MatchJobsTests(unittest.TestCase):
    JOBS = [
        {"id": 1, "title": "Python Backend Developer", "department": None, "description": "Django, удалённая работа", "city": "Москва", "schedule": "remote", "salary_to": 350000, "apply_url": "u1"},
        {"id": 2, "title": "Frontend Developer", "department": None, "description": "React", "city": "СПб", "schedule": "fullDay", "salary_to": 220000, "apply_url": "u2"},
        {"id": 3, "title": "Data Analyst", "department": None, "description": "SQL, Python", "city": None, "schedule": "remote", "salary_to": None, "apply_url": "u3"},
    ]

    def test_python_first_frontend_filtered_by_schedule(self):
        profile = {"terms": [{"term": "python", "kind": "original"}, {"term": "django", "kind": "original"}], "filters": {"schedule": "remote"}}
        result = js.match_jobs(self.JOBS, profile)
        self.assertEqual(result["jobs"][0]["id"], 1)
        self.assertEqual(result["summary"]["filtered_out"], 1)
        self.assertNotIn(2, [j["id"] for j in result["jobs"]])

    def test_zero_score_jobs_excluded(self):
        profile = {"terms": [{"term": "golang", "kind": "original"}], "filters": {}}
        result = js.match_jobs(self.JOBS, profile)
        self.assertEqual(result["jobs"], [])


class ProfileValidationTests(unittest.TestCase):
    def test_valid_profile(self):
        self.assertTrue(js.validate_profile({"terms": [{"term": "python", "kind": "original"}], "filters": {"city": "Москва"}}))

    def test_rejects_invalid_term_and_filter_types(self):
        self.assertFalse(js.validate_profile({"terms": "python", "filters": {}}))
        self.assertFalse(js.validate_profile({"terms": [{"term": "python", "kind": "original"}], "filters": {"city": 1}}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
