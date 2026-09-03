#!/usr/bin/env python3
"""Поток: режим соискателя — подбор открытых вакансий под свободное описание кандидата.

Только открытый Career API (без токена), см. SDD-C08-DELIVERY-EXTENSIONS.md §6.
HTTP-обвязка и матчер термов переиспользуются из talent_pool.py (импорт, не копия).
Никогда не отправляет отклик и не передаёт текст кандидата наружу — только GET.
"""
import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser

import talent_pool as tp

OPEN_BASE_URL = os.environ.get("POTOK_OPEN_BASE_URL", "").rstrip("/")
CONSTRUCTOR_ID = os.environ.get("POTOK_CONSTRUCTOR_ID", "")


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks = []

    def handle_data(self, data):
        self.chunks.append(data)


def _strip_html(html_text):
    parser = _TextExtractor()
    parser.feed(html_text or "")
    return re.sub(r"\s+", " ", " ".join(parser.chunks)).strip()


def _parse_open_job(raw, open_base_url):
    dept = raw.get("department")
    city = raw.get("city")
    salary = raw.get("salary") or {}
    description = raw.get("description")
    return {
        "id": raw.get("id"),
        "title": raw.get("name"),
        "department": dept.get("name") if isinstance(dept, dict) else dept,
        "city": city.get("name") if isinstance(city, dict) else city,
        "salary_from": salary.get("from"),
        "salary_to": salary.get("to"),
        "currency": salary.get("currency"),
        "schedule": raw.get("schedule_type"),
        "description": _strip_html(description) if description else None,
        "apply_url": f"{open_base_url}/jobs/{raw.get('id')}" if open_base_url and raw.get("id") is not None else None,
    }


def fetch_jobs_constructor(open_base_url, constructor_id):
    raw = tp._request(f"/constructor/{constructor_id}", base=open_base_url)
    jobs_raw = raw.get("jobs") if isinstance(raw, dict) else raw
    return [_parse_open_job(j, open_base_url) for j in (jobs_raw or [])]


def fetch_jobs_v3_fallback(open_base_url):
    """Авторизованный fallback, если тенант не отдаёт JSON конструктора (SDD C08 §6.5).

    Поле публикации на карьерном сайте не задокументировано в docs/02-jobs.md;
    используется best-effort ключ 'career_site_published', отсутствие которого
    трактуется как «опубликована» (не отфильтровывать вслепую).
    """
    raw = tp._request("/jobs.json", {"by_scope": "all"})
    jobs = []
    for j in raw.get("data", []):
        if j.get("career_site_published") is False:
            continue
        jobs.append(
            {
                "id": j.get("id"),
                "title": j.get("name"),
                "department": (j.get("company_department") or {}).get("name"),
                "city": j.get("city"),
                "salary_from": j.get("salary_from"),
                "salary_to": j.get("salary_to"),
                "currency": j.get("currency_type"),
                "schedule": j.get("schedule_type"),
                "description": None,
                "apply_url": f"{open_base_url}/jobs/{j.get('id')}" if open_base_url and j.get("id") is not None else None,
            }
        )
    return jobs


def _apply_filters(job, filters):
    unknown = []
    passed = True
    if filters.get("city"):
        if job.get("city") is None:
            unknown.append("city")
        elif job["city"].casefold() != filters["city"].casefold():
            passed = False
    if filters.get("schedule"):
        if job.get("schedule") is None:
            unknown.append("schedule")
        elif job["schedule"] != filters["schedule"]:
            passed = False
    if filters.get("salary_from") is not None:
        if job.get("salary_to") is None:
            unknown.append("salary")
        elif job["salary_to"] < filters["salary_from"]:
            passed = False
    return passed, unknown


def _score_job(job, terms):
    total = 0
    evidence = []
    for t in terms:
        term, kind = t["term"], t["kind"]
        kind_mult = 2 if kind == "original" else 1
        for field, field_weight in (("title", 3), ("department", 2), ("description", 1)):
            text = job.get(field) or ""
            if tp._term_matches(term, set(tp._tokens(text))):
                total += field_weight * kind_mult
                evidence.append({"source": field, "term": term, "kind": kind, "quote": tp._find_quote(text, term)})
                break
    return total, evidence


def match_jobs(jobs, profile, top=10):
    terms = profile.get("terms") or []
    filters = profile.get("filters") or {}
    matched, filtered_out = [], 0
    for job in jobs:
        passed, unknown = _apply_filters(job, filters)
        if not passed:
            filtered_out += 1
            continue
        score, evidence = _score_job(job, terms)
        if score <= 0:
            continue
        matched.append(
            {
                "id": job["id"],
                "title": job.get("title"),
                "apply_url": job.get("apply_url"),
                "score": score,
                "filter_unknown": unknown,
                "evidence": evidence,
            }
        )
    matched.sort(key=lambda j: j["id"])
    matched.sort(key=lambda j: -j["score"])
    matched_count = len(matched)
    return {
        "summary": {"jobs_total": len(jobs), "matched": matched_count, "filtered_out": filtered_out},
        "jobs": matched[:top],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("jobs-list", help="список открытых вакансий с карьерного сайта")
    p_list.add_argument("--fallback-v3", action="store_true", help="авторизованный GET /api/v3/jobs вместо конструктора (см. SDD §6.5)")

    p_match = sub.add_parser("jobs-match", help="подбор вакансий под профиль соискателя")
    p_match.add_argument("profile_json", help='PROFILE_JSON: {"terms":[...], "filters": {...}} (см. SDD §6.2)')
    p_match.add_argument("--jobs-file", default=None, help="JSON-файл со списком вакансий (вывод jobs-list); иначе запрашивается заново")
    p_match.add_argument("--top", type=int, default=10)
    p_match.add_argument("--fallback-v3", action="store_true")

    args = parser.parse_args()

    if args.cmd == "jobs-list":
        if args.fallback_v3:
            if not tp.BASE_URL or not tp.TOKEN:
                sys.exit("POTOK_BASE_URL / POTOK_API_TOKEN не заданы (нужны для --fallback-v3)")
            jobs = fetch_jobs_v3_fallback(OPEN_BASE_URL)
            print(json.dumps({"source": "v3_fallback", "jobs": jobs}, ensure_ascii=False, indent=2))
        else:
            if not OPEN_BASE_URL or not CONSTRUCTOR_ID:
                sys.exit("POTOK_OPEN_BASE_URL / POTOK_CONSTRUCTOR_ID не заданы (см. .env)")
            jobs = fetch_jobs_constructor(OPEN_BASE_URL, CONSTRUCTOR_ID)
            print(json.dumps({"source": "constructor", "jobs": jobs}, ensure_ascii=False, indent=2))
    elif args.cmd == "jobs-match":
        profile = json.loads(args.profile_json)
        if args.jobs_file:
            with open(args.jobs_file, encoding="utf-8") as f:
                jobs = json.load(f)
            if isinstance(jobs, dict):
                jobs = jobs.get("jobs", [])
        elif args.fallback_v3:
            if not tp.BASE_URL or not tp.TOKEN:
                sys.exit("POTOK_BASE_URL / POTOK_API_TOKEN не заданы (нужны для --fallback-v3)")
            jobs = fetch_jobs_v3_fallback(OPEN_BASE_URL)
        else:
            if not OPEN_BASE_URL or not CONSTRUCTOR_ID:
                sys.exit("POTOK_OPEN_BASE_URL / POTOK_CONSTRUCTOR_ID не заданы (см. .env)")
            jobs = fetch_jobs_constructor(OPEN_BASE_URL, CONSTRUCTOR_ID)
        print(json.dumps(match_jobs(jobs, profile, args.top), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
