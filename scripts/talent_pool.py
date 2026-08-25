#!/usr/bin/env python3
"""Поток API: кадровый резерв, дедупликация, поиск. См. SKILL.md.

Auth: POTOK_API_TOKEN, POTOK_BASE_URL из окружения (см. .env.example).
"""
import argparse
import json
import os
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = os.environ.get("POTOK_BASE_URL", "").rstrip("/")
TOKEN = os.environ.get("POTOK_API_TOKEN", "")

HIRED_EXCLUDE_STATES = {"cancel_hire", "hire_canceled"}


def _retry_delay(error, attempt):
    retry_after = error.headers.get("Retry-After") if error.headers else None
    if retry_after:
        try:
            return min(float(retry_after), 30)
        except ValueError:
            pass
    return 2**attempt


def _request(path, params=None):
    url = f"{BASE_URL}{path}"
    if params:
        url += "?" + urlencode(params, doseq=True)
    req = Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    for attempt in range(4):
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(_retry_delay(e, attempt))
                continue
            raise
    raise RuntimeError("превышено число повторов после 429")


def _paginate_page(path, params=None):
    params = dict(params or {}, page=1, per_page=100)
    while True:
        body = _request(path, params)
        yield from body["data"]
        if params["page"] >= body["pages"]:
            return
        params["page"] += 1


def _paginate_cursor(path, params=None):
    params = dict(params or {}, page_size=100)
    while True:
        body = _request(path, params)
        yield from body["objects"]
        if not body.get("has_next_page"):
            return
        params["page_cursor"] = body["page_next_cursor"]


def all_jobs():
    return list(_paginate_page("/jobs.json", {"by_scope": "all"}))


def all_applicants():
    return list(_paginate_page("/applicants.json"))


def build_active_ids(jobs=None):
    ids = set()
    for job in jobs if jobs is not None else all_jobs():
        for join in _paginate_cursor(f"/jobs/{job['id']}/ajs_joins.json"):
            if join.get("active"):
                ids.add(join["applicant_id"])
    return ids


def build_hired_ids():
    ids = set()
    for fin in _paginate_cursor("/finalists.json"):
        if fin.get("state") not in HIRED_EXCLUDE_STATES:
            ids.add(fin["applicant_id"])
    return ids


def build_reserve_pool():
    jobs = all_jobs()
    active_ids = build_active_ids(jobs)
    hired_ids = build_hired_ids()
    applicants = all_applicants()
    return [a for a in applicants if a["id"] not in active_ids and a["id"] not in hired_ids]


def _normalize_phone(phone):
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    return digits


def find_duplicates(applicants=None):
    applicants = applicants if applicants is not None else all_applicants()
    by_phone, by_email = {}, {}
    for a in applicants:
        for p in a.get("phones") or []:
            norm = _normalize_phone(p)
            if norm:
                by_phone.setdefault(norm, set()).add(a["id"])
        email = (a.get("email") or "").strip().lower()
        if email:
            by_email.setdefault(email, set()).add(a["id"])
    groups = []
    for phone, ids in by_phone.items():
        if len(ids) > 1:
            groups.append({"match": "phone", "value": phone, "applicant_ids": sorted(ids)})
    for email, ids in by_email.items():
        if len(ids) > 1:
            groups.append({"match": "email", "value": email, "applicant_ids": sorted(ids)})
    return groups


TOKEN_RE = re.compile(r"[\w#+]+")


def _tokens(text):
    return TOKEN_RE.findall((text or "").lower())


def _term_matches(term, haystack_tokens):
    term_tokens = _tokens(term)
    return bool(term_tokens) and all(t in haystack_tokens for t in term_tokens)


def search_reserve(reserve, terms, top_n=10):
    """terms: [{"term": str, "kind": "original"|"synonym"}, ...]"""
    results = []
    for a in reserve:
        haystack_tokens = set(_tokens(" ".join([a.get("title") or ""] + (a.get("tags") or []))))
        matched = [t for t in terms if _term_matches(t["term"], haystack_tokens)]
        if matched:
            results.append(
                {
                    "applicant_id": a["id"],
                    "name": a.get("name"),
                    "title": a.get("title"),
                    "score": len(matched),
                    "matched_original": [t["term"] for t in matched if t["kind"] == "original"],
                    "matched_synonym": [t["term"] for t in matched if t["kind"] == "synonym"],
                }
            )
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top_n]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("reserve", help="построить кадровый резерв (JSON-массив кандидатов)")
    sub.add_parser("dedup", help="найти дубли кандидатов по телефону/email")

    p_search = sub.add_parser("search", help="поиск по резерву")
    p_search.add_argument(
        "terms_json", help='JSON: [{"term": "python", "kind": "original"}, {"term": "django", "kind": "synonym"}]'
    )
    p_search.add_argument("--reserve-file", help="JSON-файл с резервом (вывод команды reserve); иначе строится заново")
    p_search.add_argument("--top", type=int, default=10)

    args = parser.parse_args()

    if not BASE_URL or not TOKEN:
        sys.exit("POTOK_BASE_URL / POTOK_API_TOKEN не заданы (см. .env)")

    if args.cmd == "reserve":
        print(json.dumps(build_reserve_pool(), ensure_ascii=False, indent=2))
    elif args.cmd == "dedup":
        print(json.dumps(find_duplicates(), ensure_ascii=False, indent=2))
    elif args.cmd == "search":
        terms = json.loads(args.terms_json)
        if args.reserve_file:
            with open(args.reserve_file, encoding="utf-8") as f:
                reserve = json.load(f)
        else:
            reserve = build_reserve_pool()
        print(json.dumps(search_reserve(reserve, terms, args.top), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
