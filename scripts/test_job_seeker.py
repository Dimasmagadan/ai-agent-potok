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
            "key_skills": ["Django"],
        }
        job = js._parse_open_job(raw, "http://localhost:8765/open")
        self.assertEqual(job["title"], "Python Backend Developer")
        self.assertEqual(job["department"], "Разработка")
        self.assertEqual(job["city"], "Москва")
        self.assertEqual(job["salary_to"], 350000)
        self.assertEqual(job["description"], "Django")
        self.assertEqual(job["key_skills"], ["Django"])
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

    def test_v3_fallback_strips_html_description_and_reads_key_skills(self):
        rows = [{"id": 1, "name": "One", "description": "<p>Django</p>", "key_skills": ["Django", "PostgreSQL"]}]
        with patch.object(js.tp, "_paginate_page", return_value=iter(rows)):
            jobs = js.fetch_jobs_v3_fallback("https://careers.example")
        self.assertEqual(jobs[0]["description"], "Django")
        self.assertEqual(jobs[0]["key_skills"], ["Django", "PostgreSQL"])

    def test_v3_fallback_city_is_always_unresolved(self):
        rows = [{"id": 1, "name": "One", "city": "1"}]
        with patch.object(js.tp, "_paginate_page", return_value=iter(rows)):
            jobs = js.fetch_jobs_v3_fallback("https://careers.example")
        self.assertIsNone(jobs[0]["city"])

    def test_v3_fallback_published_only_default_filters_unpublished(self):
        rows = [{"id": 1, "name": "Published"}, {"id": 2, "name": "Unpublished", "career_site_published": False}]
        with patch.object(js.tp, "_paginate_page", return_value=iter(rows)):
            jobs = js.fetch_jobs_v3_fallback("https://careers.example")
        self.assertEqual([j["id"] for j in jobs], [1])

    def test_v3_fallback_published_only_false_keeps_unpublished(self):
        rows = [{"id": 1, "name": "Published"}, {"id": 2, "name": "Unpublished", "career_site_published": False}]
        with patch.object(js.tp, "_paginate_page", return_value=iter(rows)):
            jobs = js.fetch_jobs_v3_fallback("https://careers.example", published_only=False)
        self.assertEqual([j["id"] for j in jobs], [1, 2])

    def test_v3_fallback_excludes_private_by_default(self):
        rows = [{"id": 1, "name": "Open"}, {"id": 2, "name": "Confidential", "private": True}]
        with patch.object(js.tp, "_paginate_page", return_value=iter(rows)):
            jobs = js.fetch_jobs_v3_fallback("https://careers.example", published_only=False)
        self.assertEqual([j["id"] for j in jobs], [1])

    def test_v3_fallback_include_private_flag_keeps_confidential_jobs(self):
        rows = [{"id": 1, "name": "Open"}, {"id": 2, "name": "Confidential", "private": True}]
        with patch.object(js.tp, "_paginate_page", return_value=iter(rows)):
            jobs = js.fetch_jobs_v3_fallback("https://careers.example", published_only=False, include_private=True)
        self.assertEqual([j["id"] for j in jobs], [1, 2])


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

    def test_key_skill_only_term_scores(self):
        job = dict(self.JOB, key_skills=["PostgreSQL"])
        score, evidence = js._score_job(job, [{"term": "postgresql", "kind": "original"}])
        self.assertEqual(score, 2)
        self.assertEqual(evidence[0]["source"], "key_skills")

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

    def test_filter_mismatch_is_kept_for_gap_reporting_when_requested(self):
        profile = {"terms": [{"term": "python", "kind": "original"}], "filters": {"city": "Питер", "schedule": "remote", "salary_from": 400000}}
        result = js.match_jobs(self.JOBS, profile, include_filter_mismatches=True)
        self.assertEqual([job["id"] for job in result["jobs"]], [3])
        self.assertEqual([job["id"] for job in result["near_matches"]], [1])
        self.assertEqual(result["summary"]["matched"], 1)
        self.assertEqual(result["summary"]["near_matches"], 1)

    def test_near_match_does_not_displace_compatible_job_at_top_boundary(self):
        jobs = [{"id": 1, "title": "Python Python", "city": "X"}, {"id": 2, "title": "Python", "city": "Y"}]
        profile = {"terms": [{"term": "python", "kind": "original"}], "filters": {"city": "Y"}}
        result = js.match_jobs(jobs, profile, top=1, include_filter_mismatches=True)
        self.assertEqual([job["id"] for job in result["jobs"]], [2])
        self.assertEqual(result["near_matches"], [])

    def test_internal_v3_resolution_keeps_unpublished_jobs(self):
        args = type("Args", (), {"jobs_file": None, "fallback_v3": True, "internal": True, "include_private": False})()
        with patch.object(js.tp, "BASE_URL", "https://api.example"), patch.object(js.tp, "TOKEN", "token"), patch.object(
            js, "fetch_jobs_v3_fallback", return_value=[]
        ) as fetch:
            js._resolve_jobs(args)
        self.assertFalse(fetch.call_args.kwargs["published_only"])


class ComputeGapsTests(unittest.TestCase):
    JOB = {
        "id": 9,
        "title": "Python Backend Developer",
        "city": "Москва",
        "schedule": "remote",
        "salary_to": 320000,
        "key_skills": ["Django", "PostgreSQL"],
    }

    def test_salary_gap_when_profile_asks_more_than_max(self):
        profile = {"terms": [], "filters": {"salary_from": 400000}}
        gaps, _ = js.compute_gaps(self.JOB, profile)
        gap = next(g for g in gaps if g["field"] == "salary")
        self.assertEqual(gap["job_value"], 320000)
        self.assertEqual(gap["profile_value"], 400000)

    def test_no_salary_gap_when_profile_asks_less_than_max(self):
        profile = {"terms": [], "filters": {"salary_from": 200000}}
        gaps, _ = js.compute_gaps(self.JOB, profile)
        self.assertFalse(any(g["field"] == "salary" for g in gaps))

    def test_salary_unknown_when_profile_silent(self):
        gaps, unknown = js.compute_gaps(self.JOB, {"terms": [], "filters": {}})
        self.assertIn("salary", unknown)
        self.assertFalse(any(g["field"] == "salary" for g in gaps))

    def test_salary_unknown_when_job_has_no_salary_to(self):
        job = dict(self.JOB, salary_to=None)
        gaps, unknown = js.compute_gaps(job, {"terms": [], "filters": {"salary_from": 100000}})
        self.assertIn("salary", unknown)
        self.assertFalse(any(g["field"] == "salary" for g in gaps))

    def test_city_gap_on_mismatch(self):
        gaps, _ = js.compute_gaps(self.JOB, {"terms": [], "filters": {"city": "Питер"}})
        self.assertTrue(any(g["field"] == "city" for g in gaps))

    def test_city_unknown_when_job_city_unresolved(self):
        job = dict(self.JOB, city=None)
        gaps, unknown = js.compute_gaps(job, {"terms": [], "filters": {"city": "Москва"}})
        self.assertIn("city", unknown)
        self.assertFalse(any(g["field"] == "city" for g in gaps))

    def test_terms_gap_lists_missing_key_skills_and_title_tokens(self):
        profile = {"terms": [{"term": "python", "kind": "original"}], "filters": {}}
        gaps, _ = js.compute_gaps(self.JOB, profile)
        gap = next(g for g in gaps if g["field"] == "terms")
        self.assertIn("django", [m.casefold() for m in gap["missing"]])

    def test_empty_gaps_when_profile_matches_everything(self):
        profile = {
            "terms": [{"term": "python", "kind": "original"}, {"term": "backend", "kind": "original"}, {"term": "developer", "kind": "original"}, {"term": "django", "kind": "original"}, {"term": "postgresql", "kind": "original"}],
            "filters": {"city": "Москва", "schedule": "remote", "salary_from": 200000},
        }
        gaps, unknown = js.compute_gaps(self.JOB, profile)
        self.assertEqual(gaps, [])
        self.assertEqual(unknown, [])


class ProfileValidationTests(unittest.TestCase):
    def test_valid_profile(self):
        self.assertTrue(js.validate_profile({"terms": [{"term": "python", "kind": "original"}], "filters": {"city": "Москва"}}))
        self.assertTrue(js.validate_profile({"terms": [], "filters": {}}))
        self.assertFalse(js.validate_profile({"terms": [], "filters": {}}, require_terms=True))

    def test_rejects_invalid_term_and_filter_types(self):
        self.assertFalse(js.validate_profile({"terms": "python", "filters": {}}))
        self.assertFalse(js.validate_profile({"terms": [{"term": "python", "kind": "original"}], "filters": {"city": 1}}))
        self.assertFalse(js.validate_profile({"terms": [{"term": "", "kind": "original"}]}))
        self.assertFalse(js.validate_profile({"terms": [{"term": "python", "kind": "original"}], "filters": {"salary_from": -1}}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
