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
    raw = tp._request(f"/constructor/{constructor_id}", base=open_base_url, authenticated=False)
    jobs_raw = raw.get("jobs") if isinstance(raw, dict) else raw
    return [_parse_open_job(j, open_base_url) for j in (jobs_raw or [])]


def fetch_jobs_v3_fallback(open_base_url, published_only=True, include_private=False):
    """Авторизованный fallback, если тенант не отдаёт JSON конструктора (SDD C08 §6.5).

    Поле публикации на карьерном сайте не задокументировано в docs/02-jobs.md;
    используется best-effort ключ 'career_site_published', отсутствие которого
    трактуется как «опубликована» (не отфильтровывать вслепую). `published_only`
    выключает этот фильтр для внутреннего режима (SDD-C09 §2 п.4) — поведение
    по умолчанию (C08) не меняется. `private: true` вакансии исключены, если
    не задан `include_private` (SDD-C09 §4 п.5).

    `city` в `/jobs.json` — сырой числовой ID компании, а не имя (SDD-C09 §2
    п.3). Discovery 2026-09-03 на реальном тенанте не нашёл эндпоинт, который
    резолвит эти ID в имена: `GET /api/v3/dictionaries/cities` использует
    другой формат ID (UUID); `GET /api/v3/business_units` документирует
    `city: {id, name}` в том же числовом формате, но на этом тенанте модуль
    штатного расписания не содержит данных для проверки — результат
    неподтверждён. Поэтому `city` здесь принудительно `None` (даёт
    `filter_unknown` в `_apply_filters`/`compute_gaps`, а не тихое
    несовпадение имени с числом) вместо того, чтобы отдавать непроверенный ID.
    """
    jobs = []
    for j in tp._paginate_page("/jobs.json", {"by_scope": "all"}):
        if published_only and j.get("career_site_published") is False:
            continue
        if j.get("private") and not include_private:
            continue
        description = j.get("description")
        jobs.append(
            {
                "id": j.get("id"),
                "title": j.get("name"),
                "department": (j.get("company_department") or {}).get("name"),
                "city": None,
                "salary_from": j.get("salary_from"),
                "salary_to": j.get("salary_to"),
                "currency": j.get("currency_type"),
                "schedule": j.get("schedule_type"),
                "description": _strip_html(description) if description else None,
                "key_skills": j.get("key_skills") or [],
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


def validate_profile(profile):
    if not isinstance(profile, dict) or set(profile) - {"terms", "filters"}:
        return False
    terms = profile.get("terms")
    filters = profile.get("filters", {})
    if not isinstance(terms, list) or not isinstance(filters, dict):
        return False
    if set(filters) - {"city", "schedule", "salary_from"}:
        return False
    for term in terms:
        if not isinstance(term, dict) or set(term) != {"term", "kind"}:
            return False
        if not isinstance(term["term"], str) or not term["term"].strip() or term["kind"] not in {"original", "synonym"}:
            return False
    if "city" in filters and (not isinstance(filters["city"], str) or not filters["city"].strip()):
        return False
    if "schedule" in filters and (not isinstance(filters["schedule"], str) or not filters["schedule"].strip()):
        return False
    if "salary_from" in filters and (isinstance(filters["salary_from"], bool) or not isinstance(filters["salary_from"], (int, float))):
        return False
    return True


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


def compute_gaps(job, profile):
    """Пробелы профиля относительно требований вакансии (SDD-C09 §3).

    Направление salary-gap совпадает с `_apply_filters`: gap только когда
    ожидание профиля (salary_from) выше максимума вилки вакансии (salary_to).
    Поле, не заданное ни у вакансии, ни у профиля — unknown, а не gap.
    """
    filters = profile.get("filters") or {}
    terms = profile.get("terms") or []
    gaps = []
    unknown_fields = []

    salary_to, salary_from = job.get("salary_to"), filters.get("salary_from")
    if salary_to is None or salary_from is None:
        unknown_fields.append("salary")
    elif salary_from > salary_to:
        gaps.append(
            {
                "field": "salary",
                "job_value": salary_to,
                "profile_value": salary_from,
                "message": f"вилка вакансии до {salary_to}, вы указали ожидание от {salary_from}",
            }
        )

    job_city, filter_city = job.get("city"), filters.get("city")
    if not job_city or not filter_city:
        unknown_fields.append("city")
    elif job_city.casefold() != filter_city.casefold():
        gaps.append(
            {
                "field": "city",
                "job_value": job_city,
                "profile_value": filter_city,
                "message": f"вакансия в городе {job_city}, вы указали {filter_city}",
            }
        )

    job_schedule, filter_schedule = job.get("schedule"), filters.get("schedule")
    if not job_schedule or not filter_schedule:
        unknown_fields.append("schedule")
    elif job_schedule != filter_schedule:
        gaps.append(
            {
                "field": "schedule",
                "job_value": job_schedule,
                "profile_value": filter_schedule,
                "message": f"формат вакансии {job_schedule}, вы указали {filter_schedule}",
            }
        )

    profile_haystack = set()
    for t in terms:
        profile_haystack |= set(tp._tokens(t["term"]))
    job_terms = list(job.get("key_skills") or []) + tp._tokens(job.get("title") or "")
    missing, seen = [], set()
    for jt in job_terms:
        key = jt.casefold() if isinstance(jt, str) else jt
        if not jt or key in seen:
            continue
        seen.add(key)
        if not tp._term_matches(jt, profile_haystack):
            missing.append(jt)
    if missing:
        gaps.append(
            {
                "field": "terms",
                "missing": missing,
                "message": f"в требованиях вакансии есть {', '.join(missing)}, в вашем профиле не найдено",
            }
        )

    return gaps, unknown_fields


def _resolve_jobs(args):
    if args.jobs_file:
        with open(args.jobs_file, encoding="utf-8") as f:
            jobs = json.load(f)
        return jobs.get("jobs", []) if isinstance(jobs, dict) else jobs
    if args.fallback_v3:
        if not tp.BASE_URL or not tp.TOKEN:
            sys.exit("POTOK_BASE_URL / POTOK_API_TOKEN не заданы (нужны для --fallback-v3)")
        return fetch_jobs_v3_fallback(OPEN_BASE_URL)
    if not OPEN_BASE_URL or not CONSTRUCTOR_ID:
        sys.exit("POTOK_OPEN_BASE_URL / POTOK_CONSTRUCTOR_ID не заданы (см. .env)")
    return fetch_jobs_constructor(OPEN_BASE_URL, CONSTRUCTOR_ID)


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

    p_gaps = sub.add_parser("jobs-gaps", help="отчёт о пробелах профиля для конкретной вакансии (см. SDD-C09 §3)")
    p_gaps.add_argument("profile_json", help='PROFILE_JSON: {"terms":[...], "filters": {...}} (см. SDD §6.2)')
    p_gaps.add_argument("--job-id", type=int, required=True)
    p_gaps.add_argument("--jobs-file", default=None)
    p_gaps.add_argument("--fallback-v3", action="store_true")

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
        if not validate_profile(profile):
            sys.exit("PROFILE_JSON имеет неверную структуру")
        jobs = _resolve_jobs(args)
        print(json.dumps(match_jobs(jobs, profile, args.top), ensure_ascii=False, indent=2))
    elif args.cmd == "jobs-gaps":
        profile = json.loads(args.profile_json)
        if not validate_profile(profile):
            sys.exit("PROFILE_JSON имеет неверную структуру")
        jobs = _resolve_jobs(args)
        job = next((j for j in jobs if j.get("id") == args.job_id), None)
        if job is None:
            print(json.dumps({"error": "job_not_found"}, ensure_ascii=False))
            return
        gaps, unknown_fields = compute_gaps(job, profile)
        print(
            json.dumps(
                {"job": {"id": job["id"], "title": job.get("title")}, "gaps": gaps, "unknown_fields": unknown_fields},
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
