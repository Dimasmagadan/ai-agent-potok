#!/usr/bin/env python3
"""Тесты ядра talent_pool.py: нормализация телефона, дедуп, поиск, резерв.

Запуск: python3 scripts/test_talent_pool.py (stdlib unittest, без зависимостей).
"""
import json
import os
import tempfile
import unittest
from io import BytesIO
from email.message import Message
from pathlib import Path
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


class FindJobsByNameTests(unittest.TestCase):
    JOBS = [
        {"id": 101, "name": "Backend-разработчик (Java)"},
        {"id": 102, "name": "Frontend-разработчик (React)"},
        {"id": 201, "name": "Python Backend Developer (архивная версия)"},
        {"id": 202, "name": "Python Backend Developer"},
    ]

    def test_single_token_matches_only_overlapping_jobs(self):
        res = tp.find_jobs_by_name(self.JOBS, "разработчик")
        self.assertEqual({r["id"] for r in res}, {101, 102})

    def test_multiword_query_ranks_by_token_overlap(self):
        res = tp.find_jobs_by_name(self.JOBS, "python backend developer")
        self.assertEqual([r["id"] for r in res], [201, 202, 101])
        self.assertEqual([r["score"] for r in res], [3, 3, 1])

    def test_no_match_returns_empty_list(self):
        self.assertEqual(tp.find_jobs_by_name(self.JOBS, "devops"), [])

    def test_blank_query_raises(self):
        with self.assertRaises(ValueError):
            tp.find_jobs_by_name(self.JOBS, "   ")

    def test_top_limits_ranked_results(self):
        res = tp.find_jobs_by_name(self.JOBS, "python backend developer", top=2)
        self.assertEqual([r["id"] for r in res], [201, 202])

    def test_top_none_returns_all(self):
        res = tp.find_jobs_by_name(self.JOBS, "python backend developer", top=None)
        self.assertEqual([r["id"] for r in res], [201, 202, 101])

    def test_invalid_top_raises(self):
        for bad in (0, -1, True, "10"):
            with self.assertRaises(ValueError):
                tp.find_jobs_by_name(self.JOBS, "разработчик", top=bad)

    def test_job_without_id_is_skipped(self):
        jobs = self.JOBS + [{"name": "Разработчик без ID"}]
        res = tp.find_jobs_by_name(jobs, "разработчик")
        self.assertEqual({r["id"] for r in res}, {101, 102})


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
        with self.assertRaises(ValueError):
            tp.search_reserve(self.RESERVE, [{"term": "developer", "kind": "original"}], top_n=0)
        with self.assertRaises(ValueError):
            tp.search_reserve(self.RESERVE, [])


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


def _build_docx_bytes(lines):
    import io
    import zipfile

    body = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in lines)
    xml = f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


class DownloadCvTests(unittest.TestCase):
    def test_public_attempt_sends_no_authorization_header(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = [b"pdf-bytes", b""]
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["headers"] = dict(req.header_items())
            return response

        with patch.object(tp, "TOKEN", "secret-token"), patch.object(tp, "urlopen", fake_urlopen):
            data, err = tp._download_cv("https://example.test/cv/1.pdf")
        self.assertEqual((data, err), (b"pdf-bytes", None))
        self.assertNotIn("Authorization", captured["headers"])

    def test_401_retry_passes_token_as_query_parameter(self):
        headers = Message()
        unauthorized = HTTPError("https://example.test/cv/1.pdf", 401, "Unauthorized", headers, BytesIO())
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = [b"pdf-bytes", b""]
        captured = {}

        def fake_open(req, timeout=None):
            captured["headers"] = dict(req.header_items())
            captured["url"] = req.full_url
            return response

        calls = 0

        def fake_urlopen(req, timeout=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise unauthorized
            return fake_open(req, timeout)

        with patch.object(tp, "TOKEN", "secret-token"), patch.object(tp, "urlopen", fake_urlopen), patch.object(
            tp, "build_opener", return_value=MagicMock(open=fake_open)
        ):
            data, err = tp._download_cv("https://example.test/cv/1.pdf")

        self.assertEqual((data, err), (b"pdf-bytes", None))
        self.assertNotIn("Authorization", captured["headers"])
        self.assertEqual(captured["url"], "https://example.test/cv/1.pdf?token=secret-token")

    def test_401_retry_preserves_existing_query_parameters(self):
        headers = Message()
        unauthorized = HTTPError("https://example.test/cv/1.pdf", 401, "Unauthorized", headers, BytesIO())
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = [b"pdf-bytes", b""]
        captured = {}

        calls = 0

        def fake_urlopen(req, timeout=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise unauthorized
            captured["url"] = req.full_url
            return response

        with patch.object(tp, "TOKEN", "secret-token"), patch.object(tp, "urlopen", fake_urlopen), patch.object(
            tp, "build_opener", return_value=MagicMock(open=fake_urlopen)
        ):
            data, err = tp._download_cv("https://example.test/cv/1.pdf?download=1")
        self.assertEqual((data, err), (b"pdf-bytes", None))
        self.assertEqual(captured["url"], "https://example.test/cv/1.pdf?download=1&token=secret-token")

    def test_401_retry_refuses_redirects_with_query_token(self):
        headers = Message()
        unauthorized = HTTPError("https://example.test/cv/1.pdf", 401, "Unauthorized", headers, BytesIO())
        redirect = HTTPError("https://example.test/cv/1.pdf", 302, "Found", headers, BytesIO())
        opener = MagicMock(open=MagicMock(side_effect=redirect))
        with patch.object(tp, "TOKEN", "secret-token"), patch.object(tp, "urlopen", side_effect=unauthorized), patch.object(
            tp, "build_opener", return_value=opener
        ) as build_opener:
            data, err = tp._download_cv("https://example.test/cv/1.pdf")
        self.assertEqual((data, err), (None, "download_failed"))
        self.assertTrue(build_opener.called)
        self.assertIsInstance(build_opener.call_args.args[0], tp._NoRedirect)


class CvExtractionTests(unittest.TestCase):
    def test_docx_paragraphs_joined_by_newline(self):
        data = _build_docx_bytes(["Опыт: Python, Django", "FastAPI, PostgreSQL"])
        text = tp._extract_docx_text(data)
        self.assertEqual(text, "Опыт: Python, Django\nFastAPI, PostgreSQL")

    def test_txt_cp1251_fallback(self):
        raw = "Опыт: Python".encode("cp1251")
        self.assertEqual(tp._extract_txt(raw), "Опыт: Python")

    def test_txt_utf8_replace_on_double_failure(self):
        raw = b"\xff\xfe not valid in either utf-8 or cp1251 cleanly \x81"
        # should not raise regardless of decode path taken
        tp._extract_txt(raw)

    def test_normalize_cv_text_collapses_whitespace_and_drops_empty_lines(self):
        text = "Python   Django\n\n\tFastAPI  \n"
        self.assertEqual(tp._normalize_cv_text(text), "Python Django\nFastAPI")

    def test_normalize_cv_text_truncates_to_200000_chars(self):
        text = "a" * 300000
        self.assertEqual(len(tp._normalize_cv_text(text)), 200000)

    def test_pdf_without_pdfminer_is_unsupported(self):
        with patch.dict("sys.modules", {"pdfminer": None, "pdfminer.high_level": None}):
            text, fmt, err = tp._extract_cv_text(b"%PDF-1.4", ".pdf")
        self.assertEqual((text, err), ("", "unsupported_format:pdf_missing"))

    def test_unknown_extension_is_unsupported(self):
        text, fmt, err = tp._extract_cv_text(b"data", ".rtf")
        self.assertEqual(err, "unsupported_format")

    def test_docx_with_oversized_uncompressed_xml_is_rejected(self):
        with patch.object(tp, "MAX_DOCX_XML_BYTES", 1):
            text, fmt, err = tp._extract_cv_text(_build_docx_bytes(["Python"]), ".docx")
        self.assertEqual((text, fmt, err), ("", "docx", "extract_failed"))


class CvIndexTests(unittest.TestCase):
    def _reserve(self):
        return [
            {"id": 1, "resumes": [{"id": 1, "cv_original": "https://example.test/cv/1.docx"}]},
            {"id": 2, "resumes": []},
        ]

    def test_ok_and_no_cv_statuses(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(tp, "_download_cv", return_value=(_build_docx_bytes(["Python"]), None)):
                stats = tp.cv_index_reserve(self._reserve(), cache_dir=d)
            self.assertEqual(stats["indexed"], 1)
            self.assertEqual(stats["no_cv"], 1)
            self.assertEqual(stats["failed"], 0)
            cached = json.loads((Path(d) / "1.json").read_text(encoding="utf-8"))
            self.assertEqual(cached["status"], "ok")
            self.assertEqual(cached["text"], "Python")
            no_cv = json.loads((Path(d) / "2.json").read_text(encoding="utf-8"))
            self.assertEqual(no_cv["status"], "no_cv")
            self.assertEqual(no_cv["text"], "")

    def test_idempotent_skips_fresh_ok_cache(self):
        with tempfile.TemporaryDirectory() as d:
            download = MagicMock(return_value=(_build_docx_bytes(["Python"]), None))
            with patch.object(tp, "_download_cv", download):
                tp.cv_index_reserve(self._reserve(), cache_dir=d)
                stats2 = tp.cv_index_reserve(self._reserve(), cache_dir=d)
            self.assertEqual(download.call_count, 1)
            self.assertEqual(stats2["skipped_fresh"], 1)

    def test_download_failure_status_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(tp, "_download_cv", return_value=(None, "too_large")):
                stats = tp.cv_index_reserve(self._reserve(), cache_dir=d)
            self.assertEqual(stats["failed"], 1)
            self.assertEqual(stats["by_status"]["too_large"], 1)

    def test_mock_fallback_used_only_when_download_fails_and_applicant_listed(self):
        with tempfile.TemporaryDirectory() as d:
            fallback_file = Path(d) / "fallback.json"
            fallback_file.write_text(json.dumps({"1": "FastAPI резюме"}), encoding="utf-8")
            with patch.object(tp, "_download_cv", return_value=(None, "download_failed")), patch.dict(
                os.environ, {"CV_MOCK_FALLBACK_FILE": str(fallback_file)}
            ):
                stats = tp.cv_index_reserve(self._reserve(), cache_dir=d)
            self.assertEqual(stats["indexed"], 1)
            self.assertEqual(stats["by_status"]["ok_mock_fallback"], 1)
            cached = json.loads((Path(d) / "1.json").read_text(encoding="utf-8"))
            self.assertEqual(cached["status"], "ok")
            self.assertEqual(cached["text"], "FastAPI резюме")
            self.assertTrue(cached["mock_fallback"])

    def test_no_mock_fallback_file_keeps_real_failure(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(tp, "_download_cv", return_value=(None, "download_failed")), patch.dict(
                os.environ, {}, clear=False
            ):
                os.environ.pop("CV_MOCK_FALLBACK_FILE", None)
                stats = tp.cv_index_reserve(self._reserve(), cache_dir=d)
            self.assertEqual(stats["failed"], 1)
            self.assertNotIn("ok_mock_fallback", stats["by_status"])


class SearchReserveCvTests(unittest.TestCase):
    RESERVE = [{"id": 4, "name": "Dmitry", "title": "Python developer", "tags": ["django"]}]

    def test_without_cv_cache_dir_output_unchanged_shape(self):
        res = tp.search_reserve(self.RESERVE, [{"term": "python", "kind": "original"}])
        self.assertIsInstance(res, list)
        self.assertNotIn("evidence", res[0])

    def test_cv_only_term_adds_one_point_with_evidence_and_short_quote(self):
        with tempfile.TemporaryDirectory() as d:
            cache_file = Path(d) / "4.json"
            cache_file.write_text(
                json.dumps({"applicant_id": 4, "status": "ok", "text": "Опыт с FastAPI и Django на проде " * 3}),
                encoding="utf-8",
            )
            result = tp.search_reserve(self.RESERVE, [{"term": "python", "kind": "original"}, {"term": "fastapi", "kind": "original"}], cv_cache_dir=d)
        row = result["results"][0]
        self.assertEqual(row["score"], 2)  # python (title) + fastapi (cv-only)
        cv_evidence = [e for e in row["evidence"] if e["term"] == "fastapi"]
        self.assertEqual(len(cv_evidence), 1)
        self.assertEqual(cv_evidence[0]["source"], "cv")
        self.assertLessEqual(len(cv_evidence[0]["quote"]), 120)
        self.assertEqual(result["summary"]["cv_coverage"], {"with_cv_text": 1, "without": 0})

    def test_term_already_matched_in_title_not_double_counted_via_cv(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "4.json").write_text(json.dumps({"applicant_id": 4, "status": "ok", "text": "python everywhere"}), encoding="utf-8")
            result = tp.search_reserve(self.RESERVE, [{"term": "python", "kind": "original"}], cv_cache_dir=d)
        self.assertEqual(result["results"][0]["score"], 1)
        self.assertEqual(result["results"][0]["evidence"], [])


class ReopenValidationTests(unittest.TestCase):
    def test_requires_target(self):
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request({"source_job_id": 1})

    def test_description_target_requires_explicit_current_criteria(self):
        request = {"target_job_description": "Python developer", "source_job_id": 2, "previous_criteria": {"salary_to": 100}}
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request(request)
        tp._validate_request(dict(request, current_criteria={"salary_to": 200}))
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request(dict(request, target_job_id=1, current_criteria={"salary_to": 200}))
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request({"target_job_description": "Python", "use_target_as_source": True, "previous_criteria": {"salary_to": 100}, "current_criteria": {"salary_to": 200}})

    def test_criteria_rejects_fractional_experience_and_negative_salary(self):
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request({"target_job_id": 1, "source_job_id": 2, "previous_criteria": {"salary_to": -1}})
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request({"target_job_id": 1, "source_job_id": 2, "previous_criteria": {"experience_minimum_years": 2.5}})

    def test_criteria_null_is_unknown_but_invalid_experience_bucket_is_rejected(self):
        tp._validate_request({"target_job_id": 1, "source_job_id": 2, "previous_criteria": {"salary_to": None}})
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request({"target_job_id": 1, "source_job_id": 2, "previous_criteria": None})
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request({"target_job_id": 1, "source_job_id": 2, "previous_criteria": {}})
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request({"target_job_id": 1, "source_job_id": 2, "previous_criteria": {"experience_type": "bogus"}})

    def test_applicant_url_template_requires_exact_http_placeholder(self):
        base = {"target_job_id": 1, "source_job_id": 2, "previous_criteria": {"salary_to": 100}}
        tp._validate_request(dict(base, applicant_url_template="https://company.test/applicants/{id}"))
        for template in ("https://company.test/static", "ftp://company.test/{id}", "https://company.test/{id}/{id}", "https://company.test/{id}/{other}"):
            with self.assertRaises(tp.ReopenValidationError):
                tp._validate_request(dict(base, applicant_url_template=template))

    def test_same_source_and_target_requires_explicit_previous(self):
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request({"target_job_id": 1, "source_job_id": 1})
        # does not raise once previous_criteria is explicit
        tp._validate_request({"target_job_id": 1, "source_job_id": 1, "previous_criteria": {"salary_to": 100}})

    def test_use_target_as_source_forbids_explicit_source_job_id(self):
        for source_job_id in (1, 2):
            with self.assertRaises(tp.ReopenValidationError):
                tp._validate_request({"target_job_id": 1, "source_job_id": source_job_id, "use_target_as_source": True, "previous_criteria": {"salary_to": 100}})

    def test_explicit_previous_forbids_represents_flag(self):
        with self.assertRaises(tp.ReopenValidationError):
            tp._prepare_criteria(
                {"previous_criteria": {"salary_to": 1}, "source_represents_previous_criteria": True},
                target_job=None,
                source_job=None,
            )

    def test_mapping_none_and_empty_are_accepted(self):
        tp._validate_mapping(None)
        tp._validate_mapping({})

    def test_mapping_must_be_object(self):
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_mapping(["salary"])

    def test_mapping_category_must_be_list(self):
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_mapping({"salary": 5})

    def test_mapping_item_must_be_int_or_dict_with_int_reason_id(self):
        tp._validate_mapping({"salary": [5], "schedule": [{"reason_id": 6, "from": "fullDay", "to": "remote"}]})
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_mapping({"salary": ["not-a-reason"]})
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_mapping({"location": [{"reason_id": "not-an-int", "from": "1", "to": "2"}]})

    def test_unknown_mapping_and_malformed_context_are_rejected(self):
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_mapping({"unrelated": "anything"})
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_context_terms({"schedule": "remote"})

    def test_run_reopen_rejects_malformed_mapping_as_blocked_request(self):
        result, exit_code = tp.run_reopen({"target_job_id": 1, "source_job_id": 2}, mapping={"salary": ["bad"]})
        self.assertEqual(exit_code, 2)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["warnings"][0]["code"], "VALIDATION_ERROR")


class ReopenSchemaParityTests(unittest.TestCase):
    """Один тест на каждое правило `allOf` в inputSchema поток_reopen (mcp_server.py),
    чтобы runtime-валидация в _validate_request не разошлась со схемой молча."""

    def test_rule0_target_id_xor_description(self):
        base = {"source_job_id": 2, "previous_criteria": {"salary_to": 100}}
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request(dict(base))  # ни target_job_id, ни target_job_description
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request(dict(base, target_job_id=1, target_job_description="Python", current_criteria={"salary_to": 200}))  # оба
        tp._validate_request(dict(base, target_job_id=1))
        tp._validate_request(dict(base, target_job_description="Python", current_criteria={"salary_to": 200}))

    def test_rule1_source_id_xor_use_target_as_source(self):
        base = {"target_job_id": 1, "previous_criteria": {"salary_to": 100}}
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request(dict(base))  # ни source_job_id, ни use_target_as_source
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request(dict(base, source_job_id=1, use_target_as_source=True))  # оба
        tp._validate_request(dict(base, source_job_id=2))
        tp._validate_request(dict(base, use_target_as_source=True))

    def test_rule2_previous_criteria_xor_represents_flag(self):
        base = {"target_job_id": 1, "source_job_id": 2}
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request(dict(base))  # ни previous_criteria, ни флаг
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request(dict(base, previous_criteria={"salary_to": 100}, source_represents_previous_criteria=True))  # оба
        tp._validate_request(dict(base, previous_criteria={"salary_to": 100}))
        tp._validate_request(dict(base, source_represents_previous_criteria=True))

    def test_rule3_use_target_as_source_requires_target_job_id(self):
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request(
                {"target_job_description": "Python", "use_target_as_source": True, "current_criteria": {"salary_to": 200}, "previous_criteria": {"salary_to": 100}}
            )
        tp._validate_request({"target_job_id": 1, "use_target_as_source": True, "previous_criteria": {"salary_to": 100}})

    def test_rule4_missing_target_job_id_requires_current_criteria(self):
        with self.assertRaises(tp.ReopenValidationError):
            tp._validate_request({"target_job_description": "Python", "source_job_id": 2, "previous_criteria": {"salary_to": 100}})
        tp._validate_request({"target_job_description": "Python", "source_job_id": 2, "previous_criteria": {"salary_to": 100}, "current_criteria": {"salary_to": 200}})


class ReopenSignalTests(unittest.TestCase):
    def _criteria(self, prev_raw, curr_raw):
        prev = tp._normalize_criteria(prev_raw)
        curr = tp._normalize_criteria(curr_raw)
        added_disp, added_keys = [], set()
        for term in curr.get("profile_terms_any") or []:
            key = tp._normalize_term_key(term)
            if key and key not in (prev.get("_profile_keys") or set()):
                added_keys.add(key)
                added_disp.append(term)
        return {"previous": prev, "current": curr, "added_profile_terms_display": added_disp, "added_profile_terms_keys": added_keys}

    def test_salary_unlocked_requires_confirmed_currency(self):
        criteria = self._criteria({"salary_to": 280000, "currency_type": "RUR"}, {"salary_to": 350000, "currency_type": "RUR"})
        applicant = {"salary": 320000}
        self.assertIsNone(tp._signal_salary(criteria, applicant, currency_confirmed=False, request={}))
        signal = tp._signal_salary(criteria, applicant, currency_confirmed=True, request={"applicant_salary_currency": "RUR"})
        self.assertEqual(signal["type"], "salary_unlocked")

    def test_salary_unlocked_rejects_expectation_above_new_ceiling(self):
        criteria = self._criteria({"salary_to": 280000, "currency_type": "RUR"}, {"salary_to": 350000, "currency_type": "RUR"})
        applicant = {"salary": 400000}
        self.assertIsNone(tp._signal_salary(criteria, applicant, currency_confirmed=True, request={}))

    def test_location_unlocked_requires_city_move_from_old_to_new(self):
        criteria = self._criteria({"city": "1"}, {"city": "2"})
        self.assertIsNotNone(tp._signal_location(criteria, {"city": {"id": "2"}}))
        self.assertIsNone(tp._signal_location(criteria, {"city": {"id": "1"}}))
        self.assertIsNone(tp._signal_location(criteria, {"city": {"id": "3"}}))

    def test_new_terms_match_needs_added_alt_and_not_old_alt(self):
        criteria = self._criteria(
            {"role_terms": ["python", "backend"], "profile_terms_any": ["django"]},
            {"role_terms": ["python", "backend"], "profile_terms_any": ["django", "fastapi"]},
        )
        matched = tp._signal_new_terms(criteria, {"title": "Python developer", "tags": ["python", "backend", "fastapi"]})
        self.assertIsNotNone(matched)
        self.assertEqual(matched["evidence"][1]["field"], "role_terms")
        self.assertEqual(matched["evidence"][2]["value"], ["fastapi"])
        self.assertEqual(matched["evidence"][3]["value"], ["django"])
        # matching only the OLD alternative must not count as a consequence of the change
        only_old = tp._signal_new_terms(criteria, {"title": "Python developer", "tags": ["python", "backend", "django"]})
        self.assertIsNone(only_old)

    def test_decline_reason_needs_directionally_compatible_mapping(self):
        criteria = self._criteria({"experience_minimum_years": 3}, {"experience_minimum_years": 1})
        changes = tp._detect_directional_changes(criteria)
        reasons = {8: "Не хватает опыта"}
        applicant = {"declination_reason_id": 8}
        signal = tp._signal_decline_reason(criteria, applicant, {"experience_minimum": [8]}, reasons, changes)
        self.assertEqual(signal["type"], "decline_reason_matches_change")
        # wrong category mapping -> no signal
        self.assertIsNone(tp._signal_decline_reason(criteria, applicant, {"salary": [8]}, reasons, changes))
        # unresolved reason id -> no signal
        self.assertIsNone(tp._signal_decline_reason(criteria, applicant, {"experience_minimum": [8]}, None, changes))

    def test_context_generic_word_does_not_match_but_exact_phrase_does(self):
        criteria = self._criteria({"schedule_type": "fullDay"}, {"schedule_type": "remote"})
        changes = tp._detect_directional_changes(criteria)
        applicant = {"tags": []}
        comments = [{"id": 1, "body": "кандидат спрашивал про удалённо", "created_at": "2026-01-01T00:00:00Z"}]
        self.assertIsNone(tp._signal_context(criteria, applicant, {"schedule": ["опыт"]}, comments, changes))
        signal = tp._signal_context(criteria, applicant, {"schedule": ["удалённо"]}, comments, changes)
        self.assertEqual(signal["type"], "context_mentions_change")

    def test_profile_decline_reason_requires_added_profile_match(self):
        criteria = self._criteria(
            {"role_terms": ["python"], "profile_terms_any": ["django"]},
            {"role_terms": ["python"], "profile_terms_any": ["django", "fastapi"]},
        )
        changes = tp._detect_directional_changes(criteria)
        mapping, reasons = {"profile": [44]}, {44: "Нет FastAPI"}
        self.assertIsNone(tp._signal_decline_reason(criteria, {"declination_reason_id": 44, "tags": ["python"]}, mapping, reasons, changes))
        self.assertIsNotNone(tp._signal_decline_reason(criteria, {"declination_reason_id": 44, "tags": ["python", "fastapi"]}, mapping, reasons, changes))

    def test_job_criteria_preserve_exact_experience_minimum(self):
        criteria = tp._criteria_from_job({"experience_minimum_years": 2, "experience_type": "moreThan6"})
        self.assertEqual(tp._normalize_criteria(criteria)["experience_minimum_years"], 2)

    def test_score_bonus_only_with_signal_and_full_role_match(self):
        criteria = self._criteria({"salary_to": 100}, {"salary_to": 200, "role_terms": ["python"]})
        applicant = {"title": "Python developer", "tags": []}
        signals = [{"type": "salary_unlocked"}]
        self.assertEqual(tp._score_candidate(signals, criteria, applicant), 3 + 1)
        self.assertEqual(tp._score_candidate([], criteria, applicant), 0)


class ReopenIntegrationTests(unittest.TestCase):
    """run_reopen без сети: подменяем tp._request фикстурами, как в mock-сервере."""

    def setUp(self):
        patcher = patch.object(tp, "BASE_URL", "https://example.test/api/v3")
        patcher.start()
        self.addCleanup(patcher.stop)

    FINALISTS = [{"applicant_id": 99, "state": "hired"}]
    JOB_201 = {"id": 201, "salary_to": 280000, "currency_type": "RUR", "schedule_type": "fullDay", "city": "1"}
    JOB_202 = {"id": 202, "salary_to": 350000, "currency_type": "RUR", "schedule_type": "remote", "city": "2"}
    AJS_201 = [
        {"applicant_id": 21, "active": False, "declined_at": "2026-06-01T00:00:00Z", "declination_reason_id": None},
        {"applicant_id": 99, "active": False, "declined_at": "2026-06-02T00:00:00Z", "declination_reason_id": None},
        {"applicant_id": 28, "active": False, "declined_at": "2026-06-03T00:00:00Z", "declination_reason_id": None},
    ]
    AJS_202 = [{"applicant_id": 28, "active": True}]
    APPLICANTS = {
        21: {"id": 21, "name": "Мария", "salary": 320000, "city": {"id": "1"}, "tags": [], "title": ""},
        99: {"id": 99, "name": "Hired", "salary": 999999, "city": {"id": "1"}, "tags": [], "title": ""},
        28: {"id": 28, "name": "Active", "salary": 330000, "city": {"id": "1"}, "tags": [], "title": ""},
    }

    def _fake_request(self, path, params=None, base=None):
        if path == "/finalists.json":
            return {"objects": self.FINALISTS, "has_next_page": False, "page_next_cursor": None}
        if path == "/jobs/201/ajs_joins.json":
            return {"objects": self.AJS_201, "has_next_page": False, "page_next_cursor": None}
        if path == "/jobs/202/ajs_joins.json":
            return {"objects": self.AJS_202, "has_next_page": False, "page_next_cursor": None}
        if path == "/jobs/201.json":
            return self.JOB_201
        if path == "/jobs/202.json":
            return self.JOB_202
        if path.startswith("/applicants/"):
            aid = int(path.split("/")[2].split(".")[0])
            return self.APPLICANTS[aid]
        if path == "/declination_reasons.json":
            return []
        if path == "/events.json":
            return {"data": [], "page": 1, "pages": 1, "per_page": 50}
        raise AssertionError(f"unexpected path {path}")

    def _request(self):
        request = {
            "target_job_id": 202,
            "source_job_id": 201,
            "source_represents_previous_criteria": True,
            "applicant_salary_currency": "RUR",
        }
        return request

    def test_hired_and_active_on_target_excluded_salary_unlocked_survivor_ranked(self):
        with patch.object(tp, "_request", side_effect=self._fake_request):
            result, exit_code = tp.run_reopen(self._request())
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["summary"]["excluded_hired"], 1)
        self.assertEqual(result["summary"]["excluded_active_on_target"], 1)
        self.assertEqual([c["applicant_id"] for c in result["candidates"]], [21])
        self.assertEqual(result["candidates"][0]["signals"][0]["type"], "salary_unlocked")

    def test_finalists_malformed_item_blocks_output(self):
        def broken_request(path, params=None, base=None):
            if path == "/finalists.json":
                return {"objects": [{"applicant_id": 1}], "has_next_page": False, "page_next_cursor": None}  # missing "state"
            return self._fake_request(path, params, base)

        with patch.object(tp, "_request", side_effect=broken_request):
            result, exit_code = tp.run_reopen(self._request())
        self.assertEqual(exit_code, 3)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["candidates"], [])

    def test_source_joins_malformed_item_yields_partial_not_blocked(self):
        def partial_request(path, params=None, base=None):
            if path == "/jobs/201/ajs_joins.json":
                objects = self.AJS_201 + [{"applicant_id": 55}]  # missing "active" -> skipped, not fatal
                return {"objects": objects, "has_next_page": False, "page_next_cursor": None}
            return self._fake_request(path, params, base)

        with patch.object(tp, "_request", side_effect=partial_request):
            result, exit_code = tp.run_reopen(self._request())
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["completeness"]["ranking_complete"])
        self.assertTrue(any(w["code"] == "SOURCE_JOINS_PARTIAL" for w in result["warnings"]))

    def test_declination_reason_and_date_fallback_to_decline_event(self):
        # Реальный тенант (проверено на песочнице) не отдаёт declination_reason_id/
        # declined_at на ajs_join — только на Event::Decline. reopen должен подхватить
        # оба поля оттуда, если на ajs_join их нет.
        def request_without_join_fields(path, params=None, base=None):
            if path == "/jobs/201/ajs_joins.json":
                objects = [dict(a) for a in self.AJS_201]
                objects[0]["declination_reason_id"] = None
                objects[0]["declined_at"] = None
                return {"objects": objects, "has_next_page": False, "page_next_cursor": None}
            if path == "/declination_reasons.json":
                return [{"id": 34, "name": "Недостаток опыта"}]
            if path == "/events.json" and params and params.get("applicant_id") == 21:
                return {
                    "data": [
                        {
                            "type": "Event::Decline",
                            "job_id": 201,
                            "created_at": "2026-06-05T00:00:00Z",
                            "properties": {"declination_reason_id": 34},
                        }
                    ],
                    "page": 1,
                    "pages": 1,
                    "per_page": 50,
                }
            return self._fake_request(path, params, base)

        with patch.object(tp, "_request", side_effect=request_without_join_fields):
            result, exit_code = tp.run_reopen(self._request())
        self.assertEqual(exit_code, 0)
        candidate = next(c for c in result["candidates"] if c["applicant_id"] == 21)
        self.assertEqual(candidate["declined_at"], "2026-06-05T00:00:00Z")
        self.assertEqual(candidate["confirmed_declination_reason"], {"id": 34, "name": "Недостаток опыта"})

    def test_no_supported_diff_is_validation_error(self):
        with patch.object(tp, "_request", side_effect=self._fake_request):
            result, exit_code = tp.run_reopen(
                {"target_job_id": 202, "source_job_id": 201, "previous_criteria": {"salary_to": 350000, "currency_type": "RUR"}, "current_criteria": {"salary_to": 350000, "currency_type": "RUR"}}
            )
        self.assertEqual(exit_code, 2)
        self.assertEqual(result["warnings"][0]["code"], "VALIDATION_ERROR")


if __name__ == "__main__":
    unittest.main(verbosity=2)
