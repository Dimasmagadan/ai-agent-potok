#!/usr/bin/env python3
"""Поток API: кадровый резерв, дедупликация, поиск, CV-индексация, reopen. См. SKILL.md.

Auth: POTOK_API_TOKEN, POTOK_BASE_URL из окружения (см. .env.example).
"""
import argparse
import io
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

BASE_URL = os.environ.get("POTOK_BASE_URL", "").rstrip("/")
TOKEN = os.environ.get("POTOK_API_TOKEN", "")

HIRED_EXCLUDE_STATES = {"cancel_hire", "hire_canceled"}


class FetchError(RuntimeError):
    """An API page could not be retrieved after retrying."""


def _retry_delay(error, attempt):
    retry_after = error.headers.get("Retry-After") if error.headers else None
    if retry_after:
        try:
            return min(float(retry_after), 30)
        except ValueError:
            pass
    return 2**attempt


def _request(path, params=None, base=None, authenticated=True):
    url = f"{base if base is not None else BASE_URL}{path}"
    if params:
        url += "?" + urlencode(params, doseq=True)
    headers = {"Authorization": f"Bearer {TOKEN}"} if authenticated else {}
    req = Request(url, headers=headers)
    for attempt in range(4):
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            if e.code == 429 and attempt < 3:
                delay = _retry_delay(e, attempt)
                e.close()
                time.sleep(delay)
                continue
            status = e.code
            e.close()
            raise FetchError(f"{path}: HTTP {status}") from e
        except (URLError, TimeoutError) as e:
            raise FetchError(f"{path}: ошибка сети ({e})") from e
    raise FetchError(f"{path}: превышено число повторов после 429")


def _paginate_page(path, params=None, warnings=None):
    params = dict(params or {}, page=1, per_page=100)
    while True:
        try:
            body = _request(path, params)
        except FetchError as e:
            if warnings is None:
                raise
            warnings.append(str(e))
            return
        yield from body["data"]
        if params["page"] >= body["pages"]:
            return
        params["page"] += 1


def _paginate_cursor(path, params=None, warnings=None):
    params = dict(params or {}, page_size=100)
    while True:
        try:
            body = _request(path, params)
        except FetchError as e:
            if warnings is None:
                raise
            warnings.append(str(e))
            return
        yield from body["objects"]
        if not body.get("has_next_page"):
            return
        params["page_cursor"] = body["page_next_cursor"]


def all_jobs(warnings=None):
    return list(_paginate_page("/jobs.json", {"by_scope": "all"}, warnings))


def all_applicants(warnings=None):
    return list(_paginate_page("/applicants.json", warnings=warnings))


def build_active_ids(jobs=None, warnings=None):
    ids = set()
    for job in jobs if jobs is not None else all_jobs(warnings):
        for join in _paginate_cursor(f"/jobs/{job['id']}/ajs_joins.json", warnings=warnings):
            if join.get("active"):
                ids.add(join["applicant_id"])
    return ids


def build_hired_ids(warnings=None):
    ids = set()
    for fin in _paginate_cursor("/finalists.json", warnings=warnings):
        if fin.get("state") not in HIRED_EXCLUDE_STATES:
            ids.add(fin["applicant_id"])
    return ids


def build_reserve_pool(warnings=None):
    jobs = all_jobs(warnings)
    active_ids = build_active_ids(jobs, warnings)
    hired_ids = build_hired_ids(warnings)
    applicants = all_applicants(warnings)
    return [a for a in applicants if a["id"] not in active_ids and a["id"] not in hired_ids]


def _normalize_phone(phone):
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    return digits


def find_duplicates(applicants=None, warnings=None):
    applicants = applicants if applicants is not None else all_applicants(warnings)
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


# ---------------------------------------------------------------------------
# Поток A: полнотекстовый поиск по резюме (SDD C08 §5)
# ---------------------------------------------------------------------------

MAX_CV_BYTES = 10 * 1024 * 1024
MAX_DOCX_XML_BYTES = 10 * 1024 * 1024
CV_STATUSES = {"ok", "no_cv", "download_failed", "too_large", "extract_failed", "unsupported_format"}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _cv_record(applicant_id, source_url, status, fmt, text, mock_fallback=False):
    record = {
        "applicant_id": applicant_id,
        "source_url": source_url,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "format": fmt,
        "text": text if status == "ok" else "",
    }
    if mock_fallback:
        record["mock_fallback"] = True
    return record


def _load_cv_mock_fallback():
    """Load explicitly supplied mock CV text for offline demos only."""
    path = os.environ.get("CV_MOCK_FALLBACK_FILE")
    if not path:
        return {}
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {int(k): v for k, v in raw.items()} if isinstance(raw, dict) else {}


def _download_cv(url):
    parts = urlsplit(url)
    is_local = parts.hostname in ("localhost", "127.0.0.1")
    if parts.scheme != "https" and not (parts.scheme == "http" and is_local):
        return None, "download_failed"

    def _try(download_url, allow_redirects=True):
        req = Request(download_url)
        opener = urlopen if allow_redirects else build_opener(_NoRedirect()).open
        with opener(req, timeout=30) as resp:
            data = bytearray()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                data += chunk
                if len(data) > MAX_CV_BYTES:
                    return None, "too_large"
            return bytes(data), None

    try:
        return _try(url)
    except HTTPError as e:
        if e.code in (401, 403):
            e.close()
            try:
                parts = urlsplit(url)
                query = f"{parts.query}&" if parts.query else ""
                token_url = urlunsplit((
                    parts.scheme,
                    parts.netloc,
                    parts.path,
                    f"{query}{urlencode({'token': TOKEN})}",
                    parts.fragment,
                ))
                # Query credentials must never be forwarded to a redirect target.
                return _try(token_url, allow_redirects=False)
            except HTTPError as retry_error:
                retry_error.close()
                return None, "download_failed"
            except (URLError, TimeoutError):
                return None, "download_failed"
        e.close()
        return None, "download_failed"
    except (URLError, TimeoutError):
        return None, "download_failed"


def _extract_docx_text(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        info = z.getinfo("word/document.xml")
        if info.file_size > MAX_DOCX_XML_BYTES:
            raise ValueError("DOCX document.xml is too large")
        xml_bytes = z.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    lines = []
    for p in root.iter():
        if p.tag.endswith("}p"):
            lines.append("".join(t.text or "" for t in p.iter() if t.tag.endswith("}t")))
    return "\n".join(lines)


def _extract_txt(data):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("cp1251")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")


def _normalize_cv_text(text):
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)[:200000]


def _extract_cv_text(data, ext):
    if ext == ".docx":
        try:
            return _normalize_cv_text(_extract_docx_text(data)), "docx", None
        except (ValueError, zipfile.BadZipFile, KeyError, ET.ParseError):
            return "", "docx", "extract_failed"
    if ext == ".txt":
        return _normalize_cv_text(_extract_txt(data)), "txt", None
    if ext == ".pdf":
        try:
            from pdfminer.high_level import extract_text
        except ImportError:
            return "", "pdf", "unsupported_format:pdf_missing"
        try:
            return _normalize_cv_text(extract_text(io.BytesIO(data))), "pdf", None
        except Exception:
            return "", "pdf", "extract_failed"
    return "", (ext.lstrip(".") or "unknown"), "unsupported_format"


def cv_index_reserve(reserve, cache_dir=".cv_cache", limit=None):
    cache_path_dir = Path(cache_dir)
    cache_path_dir.mkdir(parents=True, exist_ok=True)
    stats = {"indexed": 0, "skipped_fresh": 0, "no_cv": 0, "failed": 0, "by_status": {}}
    pdf_missing = False
    items = reserve[:limit] if limit else reserve

    for a in items:
        aid = a["id"]
        cache_file = cache_path_dir / f"{aid}.json"
        resumes = a.get("resumes") or []
        cv = next((r for r in resumes if r.get("cv_original")), None)

        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = None
            if cached and cached.get("status") == "ok" and cv and cached.get("source_url") == cv["cv_original"]:
                stats["skipped_fresh"] += 1
                stats["by_status"]["ok"] = stats["by_status"].get("ok", 0) + 1
                continue

        if not cv:
            record = _cv_record(aid, None, "no_cv", None, "")
            cache_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            stats["no_cv"] += 1
            stats["by_status"]["no_cv"] = stats["by_status"].get("no_cv", 0) + 1
            continue

        url = cv["cv_original"]
        data, err = _download_cv(url)
        if err:
            mock_text = _load_cv_mock_fallback().get(aid)
            if mock_text is not None:
                print(
                    f"[cv-mock-fallback] applicant {aid}: реальное скачивание вернуло "
                    f"'{err}', использован CV_MOCK_FALLBACK_FILE вместо cv_original",
                    file=sys.stderr,
                )
                record = _cv_record(aid, url, "ok", "mock", mock_text, mock_fallback=True)
                cache_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                stats["indexed"] += 1
                stats["by_status"]["ok_mock_fallback"] = stats["by_status"].get("ok_mock_fallback", 0) + 1
                continue
            record = _cv_record(aid, url, err, None, "")
            cache_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            stats["failed"] += 1
            stats["by_status"][err] = stats["by_status"].get(err, 0) + 1
            continue

        ext = os.path.splitext(urlsplit(url).path)[1].lower()
        text, fmt, err = _extract_cv_text(data, ext)
        if err and err.startswith("unsupported_format"):
            if err == "unsupported_format:pdf_missing":
                pdf_missing = True
            err = "unsupported_format"
        if err:
            record = _cv_record(aid, url, err, fmt, "")
            stats["failed"] += 1
            stats["by_status"][err] = stats["by_status"].get(err, 0) + 1
        else:
            record = _cv_record(aid, url, "ok", fmt, text)
            stats["indexed"] += 1
            stats["by_status"]["ok"] = stats["by_status"].get("ok", 0) + 1
        cache_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    if pdf_missing:
        print("PDF пропущены: установите pdfminer.six", file=sys.stderr)
    return stats


def _load_cv_cache(cache_dir):
    cache = {}
    for f in Path(cache_dir).glob("*.json"):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("status") == "ok":
            cache[rec["applicant_id"]] = rec["text"]
    return cache


def _find_quote(text, term, max_len=120):
    tokens = _tokens(term)
    if not tokens:
        return None
    match = None
    for tok in tokens:
        m = re.search(r"\b" + re.escape(tok) + r"\b", text, re.IGNORECASE)
        if m:
            match = m
            break
    if not match:
        return None
    start, end = match.start(), match.end()
    half = max_len // 2
    lo, hi = max(0, start - half), min(len(text), end + half)
    snippet = text[lo:hi]
    if lo > 0 and " " in snippet:
        snippet = snippet.split(" ", 1)[-1]
    if hi < len(text) and " " in snippet:
        snippet = snippet.rsplit(" ", 1)[0]
    return snippet[:max_len].strip()


def search_reserve(reserve, terms, top_n=10, cv_cache_dir=None):
    """terms: [{"term": str, "kind": "original"|"synonym"}, ...]"""
    cv_cache = _load_cv_cache(cv_cache_dir) if cv_cache_dir else {}
    with_cv = without_cv = 0
    results = []
    for a in reserve:
        haystack_tokens = set(_tokens(" ".join([a.get("title") or ""] + (a.get("tags") or []))))
        matched = [t for t in terms if _term_matches(t["term"], haystack_tokens)]

        evidence = []
        cv_bonus_keys = set()
        if cv_cache_dir:
            cv_text = cv_cache.get(a["id"])
            if cv_text is not None:
                with_cv += 1
            else:
                without_cv += 1
            if cv_text:
                cv_tokens = set(_tokens(cv_text))
                matched_keys = {t["term"].casefold() for t in matched}
                for t in terms:
                    key = t["term"].casefold()
                    if key in matched_keys or key in cv_bonus_keys:
                        continue
                    if _term_matches(t["term"], cv_tokens):
                        quote = _find_quote(cv_text, t["term"])
                        evidence.append({"source": "cv", "term": t["term"], "kind": t["kind"], "quote": quote})
                        cv_bonus_keys.add(key)

        if matched or cv_bonus_keys:
            result = {
                "applicant_id": a["id"],
                "name": a.get("name"),
                "title": a.get("title"),
                "score": len(matched) + len(cv_bonus_keys),
                "matched_original": [t["term"] for t in matched if t["kind"] == "original"],
                "matched_synonym": [t["term"] for t in matched if t["kind"] == "synonym"],
            }
            if cv_cache_dir:
                result["evidence"] = evidence
            results.append(result)

    results.sort(key=lambda r: r["score"], reverse=True)
    results = results[:top_n]
    if cv_cache_dir:
        return {"results": results, "summary": {"cv_coverage": {"with_cv_text": with_cv, "without": without_cv}}}
    return results


# ---------------------------------------------------------------------------
# reopen: пересмотр прошлых кандидатов (SDD C07)
# ---------------------------------------------------------------------------

EXPERIENCE_BUCKET_MIN_YEARS = {"noExperience": 0, "between1And3": 1, "between3And6": 3, "moreThan6": 6}
_CATEGORY_FIELD = {"schedule": "schedule_type", "location": "city", "salary": "salary_to", "experience_minimum": "experience_minimum_years"}
CONTEXT_CATEGORIES = ("salary", "location", "schedule", "experience_minimum", "profile")
_TERM_SPLIT_RE = re.compile(r"[^\w#+]+", re.UNICODE)


class ReopenValidationError(Exception):
    def __init__(self, code, message, scope="request"):
        super().__init__(message)
        self.code = code
        self.message = message
        self.scope = scope


def _warn(code, message, scope, applicant_id=None):
    w = {"code": code, "message": message, "scope": scope}
    if applicant_id is not None:
        w["applicant_id"] = applicant_id
    return w


def _v2_base_url():
    explicit = os.environ.get("POTOK_API_V2_BASE_URL", "").rstrip("/")
    if explicit:
        return explicit
    if BASE_URL.endswith("/api/v3"):
        return BASE_URL[: -len("/api/v3")] + "/api/v2"
    raise ReopenValidationError(
        "VALIDATION_ERROR", "POTOK_API_V2_BASE_URL не задан, а POTOK_BASE_URL не оканчивается на /api/v3"
    )


def _normalize_term_key(term):
    return " ".join(_TERM_SPLIT_RE.split((term or "").casefold())).strip()


def _normalize_term_list(raw):
    seen = {}
    for t in raw or []:
        key = _normalize_term_key(t)
        if key and key not in seen:
            seen[key] = t
    return list(seen.values()), set(seen.keys())


def _normalize_criteria(raw):
    raw = raw or {}
    out = {}
    if raw.get("salary_to") is not None:
        out["salary_to"] = float(raw["salary_to"])
    if raw.get("currency_type"):
        out["currency_type"] = raw["currency_type"]
    if raw.get("schedule_type"):
        out["schedule_type"] = raw["schedule_type"]
    if raw.get("experience_minimum_years") is not None:
        out["experience_minimum_years"] = int(raw["experience_minimum_years"])
    elif raw.get("experience_type") in EXPERIENCE_BUCKET_MIN_YEARS:
        out["experience_minimum_years"] = EXPERIENCE_BUCKET_MIN_YEARS[raw["experience_type"]]
    if raw.get("city") is not None:
        out["city"] = str(raw["city"])
    if raw.get("role_terms") is not None:
        disp, keys = _normalize_term_list(raw.get("role_terms"))
        out["role_terms"], out["_role_keys"] = disp, keys
    if raw.get("profile_terms_any") is not None:
        disp, keys = _normalize_term_list(raw.get("profile_terms_any"))
        out["profile_terms_any"], out["_profile_keys"] = disp, keys
    return out


def _criteria_from_job(job):
    if not job:
        return {}
    raw = {}
    for k in ("salary_to", "currency_type", "schedule_type", "experience_minimum_years", "experience_type", "city"):
        if job.get(k) is not None:
            raw[k] = job[k]
    return raw


def _merge_current_criteria(explicit_raw, job):
    merged = dict(_criteria_from_job(job))
    for k, v in (explicit_raw or {}).items():
        if v is not None and v != [] and v != "":
            merged[k] = v
    return merged


def _prepare_criteria(request, target_job, source_job):
    current_raw = _merge_current_criteria(request.get("current_criteria"), target_job)
    current = _normalize_criteria(current_raw)

    previous_raw = request.get("previous_criteria")
    if previous_raw:
        previous = _normalize_criteria(previous_raw)
    elif request.get("source_represents_previous_criteria"):
        previous = _normalize_criteria(_criteria_from_job(source_job))
    else:
        raise ReopenValidationError(
            "VALIDATION_ERROR",
            "Нет прежних условий: передайте previous_criteria или подтвердите "
            "source_represents_previous_criteria с отличной от целевой референсной вакансией",
        )

    added_disp, added_keys = [], set()
    if current.get("_profile_keys") is not None:
        prev_keys = previous.get("_profile_keys") or set()
        for term in current.get("profile_terms_any") or []:
            key = _normalize_term_key(term)
            if key and key not in prev_keys and key not in added_keys:
                added_keys.add(key)
                added_disp.append(term)

    criteria = {"previous": previous, "current": current, "added_profile_terms_display": added_disp, "added_profile_terms_keys": added_keys}
    if not _has_supported_diff(criteria):
        raise ReopenValidationError(
            "VALIDATION_ERROR", "После нормализации нет ни одного поддерживаемого сравнимого изменения условий"
        )
    return criteria


def _has_supported_diff(criteria):
    prev, curr = criteria["previous"], criteria["current"]
    if prev.get("salary_to") is not None and curr.get("salary_to") is not None and curr["salary_to"] > prev["salary_to"]:
        return True
    if (
        prev.get("experience_minimum_years") is not None
        and curr.get("experience_minimum_years") is not None
        and curr["experience_minimum_years"] < prev["experience_minimum_years"]
    ):
        return True
    if prev.get("city") is not None and curr.get("city") is not None and prev["city"] != curr["city"]:
        return True
    if criteria["added_profile_terms_keys"] and prev.get("_role_keys") is not None and curr.get("_role_keys") is not None and prev["_role_keys"] == curr["_role_keys"]:
        return True
    if prev.get("schedule_type") is not None and curr.get("schedule_type") is not None and prev["schedule_type"] != curr["schedule_type"]:
        return True
    return False


def _detect_directional_changes(criteria):
    prev, curr = criteria["previous"], criteria["current"]
    changes = {}
    if prev.get("salary_to") is not None and curr.get("salary_to") is not None:
        changes["salary"] = curr["salary_to"] > prev["salary_to"]
    if prev.get("experience_minimum_years") is not None and curr.get("experience_minimum_years") is not None:
        changes["experience_minimum"] = curr["experience_minimum_years"] < prev["experience_minimum_years"]
    if prev.get("schedule_type") is not None and curr.get("schedule_type") is not None:
        changes["schedule"] = prev["schedule_type"] != curr["schedule_type"]
    if prev.get("city") is not None and curr.get("city") is not None:
        changes["location"] = prev["city"] != curr["city"]
    role_unchanged = prev.get("_role_keys") is not None and curr.get("_role_keys") is not None and prev["_role_keys"] == curr["_role_keys"]
    changes["profile"] = bool(criteria["added_profile_terms_keys"]) and role_unchanged
    return changes


def _validate_request(request):
    target_job_id = request.get("target_job_id")
    source_job_id = request.get("source_job_id")
    use_target_as_source = bool(request.get("use_target_as_source"))
    represents_prev = bool(request.get("source_represents_previous_criteria"))
    has_explicit_previous = bool(request.get("previous_criteria"))

    if not target_job_id and not request.get("target_job_description"):
        raise ReopenValidationError("VALIDATION_ERROR", "Нужен target_job_id или target_job_description")

    if use_target_as_source and source_job_id and source_job_id != target_job_id:
        raise ReopenValidationError("VALIDATION_ERROR", "use_target_as_source=true конфликтует с отличным source_job_id")
    if use_target_as_source:
        source_job_id = target_job_id
    if not source_job_id:
        raise ReopenValidationError("VALIDATION_ERROR", "Нужен source_job_id либо use_target_as_source=true")

    if has_explicit_previous and represents_prev:
        raise ReopenValidationError(
            "VALIDATION_ERROR", "source_represents_previous_criteria запрещён при явно заданных previous_criteria"
        )
    if source_job_id == target_job_id and not has_explicit_previous:
        raise ReopenValidationError(
            "VALIDATION_ERROR", "source_job_id == target_job_id требует явных previous_criteria"
        )
    if not has_explicit_previous and not represents_prev:
        raise ReopenValidationError(
            "VALIDATION_ERROR", "Нет источника прежних условий: previous_criteria или source_represents_previous_criteria"
        )

    currency_confirmed = False
    applicant_currency = request.get("applicant_salary_currency")
    if applicant_currency:
        prev_cur = (request.get("previous_criteria") or {}).get("currency_type")
        curr_cur = (request.get("current_criteria") or {}).get("currency_type")
        currency_confirmed = bool(prev_cur) and prev_cur == curr_cur == applicant_currency

    return {"target_job_id": target_job_id, "source_job_id": source_job_id, "currency_confirmed_hint": currency_confirmed}


def _fetch_cursor_strict(path, required_fields, params=None, base=None):
    items, params = [], dict(params or {}, page_size=100)
    while True:
        try:
            body = _request(path, params, base=base)
        except FetchError:
            return items, False
        if not isinstance(body, dict) or not isinstance(body.get("objects"), list) or "has_next_page" not in body:
            return items, False
        for it in body["objects"]:
            if not isinstance(it, dict):
                return items, False
            for f in required_fields:
                if f == "active":
                    if not isinstance(it.get("active"), bool):
                        return items, False
                elif it.get(f) is None:
                    return items, False
        items.extend(body["objects"])
        if not body["has_next_page"]:
            return items, True
        cursor = body.get("page_next_cursor")
        if cursor is None:
            return items, False
        params["page_cursor"] = cursor


def _fetch_cursor_lenient(path, required_fields, params=None, base=None):
    items, complete = [], True
    params = dict(params or {}, page_size=100)
    while True:
        try:
            body = _request(path, params, base=base)
        except FetchError:
            return items, False
        if not isinstance(body, dict) or not isinstance(body.get("objects"), list) or "has_next_page" not in body:
            return items, False
        for it in body["objects"]:
            ok = isinstance(it, dict) and all(
                (isinstance(it.get("active"), bool) if f == "active" else it.get(f) is not None) for f in required_fields
            )
            if ok:
                items.append(it)
            else:
                complete = False
        if not body["has_next_page"]:
            return items, complete
        cursor = body.get("page_next_cursor")
        if cursor is None:
            return items, False
        params["page_cursor"] = cursor


def _fetch_job(job_id):
    try:
        job = _request(f"/jobs/{job_id}.json")
        return (job, True) if isinstance(job, dict) else (None, False)
    except FetchError:
        return None, False


def _fetch_applicant(applicant_id):
    try:
        applicant = _request(f"/applicants/{applicant_id}.json")
        return (applicant, True) if isinstance(applicant, dict) else (None, False)
    except FetchError:
        return None, False


def _load_declination_reasons():
    try:
        raw = _request("/declination_reasons.json", base=_v2_base_url())
    except (FetchError, ReopenValidationError):
        return None, False
    if not isinstance(raw, list):
        return None, False
    return {r["id"]: r["name"] for r in raw if isinstance(r, dict) and "id" in r and "name" in r}, True


def _fetch_comments_for(applicant_id, source_job_id):
    """Отдаёт (comments, decline) для applicant_id/source_job_id.

    Реальный тенант (проверено на песочнице 2026-09-03) не возвращает
    declination_reason_id/declined_at на ajs_join, вопреки документации API —
    причина и дата лежат в properties события Event::Decline. Извлекаем их
    здесь же, без лишнего запроса: события уже читаются для комментариев.
    """
    params = {"applicant_id": applicant_id, "page": 1, "per_page": 50}
    comments = []
    decline = None
    try:
        base = _v2_base_url()
    except ReopenValidationError:
        return None, None
    while True:
        try:
            body = _request("/events.json", params, base=base)
        except FetchError:
            return None, None
        if not isinstance(body, dict) or not isinstance(body.get("data"), list) or not isinstance(body.get("pages"), int):
            return None, None
        if body["pages"] < params["page"]:
            return None, None
        for ev in body["data"]:
            if not isinstance(ev, dict):
                return None, None
            if ev.get("type") == "Event::Comment" and ev.get("job_id") == source_job_id:
                if not all(k in ev for k in ("id", "body", "created_at")):
                    return None, None
                comments.append(ev)
            elif ev.get("type") == "Event::Decline" and ev.get("job_id") == source_job_id:
                props = ev.get("properties")
                reason_id = props.get("declination_reason_id") if isinstance(props, dict) else None
                if isinstance(reason_id, int):
                    decline = {"declination_reason_id": reason_id, "declined_at": ev.get("created_at")}
        if params["page"] >= body["pages"]:
            return comments, decline
        params["page"] += 1


def _context_phrase_in_text(term, text):
    term_tokens = _tokens(term)
    if not term_tokens:
        return False
    text_tokens = _tokens(text)
    n = len(term_tokens)
    return any(text_tokens[i : i + n] == term_tokens for i in range(len(text_tokens) - n + 1))


def _signal_salary(criteria, applicant, currency_confirmed, request):
    prev, curr = criteria["previous"].get("salary_to"), criteria["current"].get("salary_to")
    prev_cur, curr_cur = criteria["previous"].get("currency_type"), criteria["current"].get("currency_type")
    salary = applicant.get("salary")
    if prev is None or curr is None or salary is None or not currency_confirmed:
        return None
    if not (prev_cur and prev_cur == curr_cur):
        return None
    if not (prev < salary <= curr):
        return None
    return {
        "type": "salary_unlocked",
        "weight": 3,
        "confidence": "high",
        "evidence": [
            {"source": "applicant", "field": "salary", "value": salary},
            {"source": "criteria", "field": "previous.salary_to", "value": prev},
            {"source": "criteria", "field": "current.salary_to", "value": curr},
            {"source": "criteria", "field": "currency_type", "value": curr_cur},
            {"source": "request", "field": "applicant_salary_currency", "value": request.get("applicant_salary_currency")},
        ],
    }


def _signal_location(criteria, applicant):
    prev, curr = criteria["previous"].get("city"), criteria["current"].get("city")
    city = (applicant.get("city") or {}).get("id")
    if prev is None or curr is None or city is None or prev == curr:
        return None
    if city != prev and city == curr:
        return {
            "type": "location_unlocked",
            "weight": 2,
            "confidence": "medium",
            "evidence": [
                {"source": "applicant", "field": "city.id", "value": city},
                {"source": "criteria", "field": "previous.city", "value": prev},
                {"source": "criteria", "field": "current.city", "value": curr},
            ],
        }
    return None


def _signal_new_terms(criteria, applicant):
    role_disp = criteria["current"].get("role_terms")
    if not role_disp:
        return None
    added_disp = criteria["added_profile_terms_display"]
    if not added_disp:
        return None
    haystack = set(_tokens(" ".join([applicant.get("title") or ""] + (applicant.get("tags") or []))))
    if not all(_term_matches(t, haystack) for t in role_disp):
        return None
    if not any(_term_matches(t, haystack) for t in added_disp):
        return None
    old_alts = criteria["previous"].get("profile_terms_any") or []
    if any(_term_matches(t, haystack) for t in old_alts):
        return None
    return {
        "type": "new_terms_match",
        "weight": 2,
        "confidence": "medium",
        "evidence": [
            {"source": "applicant", "field": "tags", "value": applicant.get("tags") or []},
            {"source": "criteria", "field": "role_terms", "value": role_disp},
            {"source": "criteria", "field": "added_profile_terms", "value": added_disp},
            {"source": "criteria", "field": "profile_terms_any_old", "value": old_alts},
        ],
    }


def _category_evidence(criteria, category):
    field = _CATEGORY_FIELD.get(category)
    ev = []
    if field:
        ev.append({"source": "criteria", "field": f"previous.{field}", "value": criteria["previous"].get(field)})
        ev.append({"source": "criteria", "field": f"current.{field}", "value": criteria["current"].get(field)})
    if category == "profile":
        ev.append({"source": "criteria", "field": "added_profile_terms", "value": criteria["added_profile_terms_display"]})
    return ev


def _signal_decline_reason(criteria, applicant, mapping, reasons_dict, changes):
    reason_id = applicant.get("declination_reason_id")
    if reason_id is None or not reasons_dict or reason_id not in reasons_dict or not mapping:
        return None
    matched = []
    for cat in CONTEXT_CATEGORIES:
        cfg = mapping.get(cat)
        if not cfg or not changes.get(cat):
            continue
        if cat in ("salary", "experience_minimum", "profile"):
            ids = [c if isinstance(c, int) else c.get("reason_id") for c in cfg]
            if reason_id in ids and (cat != "profile" or _signal_new_terms(criteria, applicant)):
                matched.append(cat)
        else:
            field = _CATEGORY_FIELD[cat]
            for c in cfg:
                if (
                    isinstance(c, dict)
                    and c.get("reason_id") == reason_id
                    and c.get("from") == criteria["previous"].get(field)
                    and c.get("to") == criteria["current"].get(field)
                ):
                    matched.append(cat)
                    break
    if not matched:
        return None
    evidence = [
        {"source": "declination_reason", "field": "id", "value": reason_id},
        {"source": "declination_reason", "field": "name", "value": reasons_dict[reason_id]},
    ]
    for cat in ("salary", "location", "schedule", "experience_minimum", "profile"):
        if cat in matched:
            evidence.extend(_category_evidence(criteria, cat))
    return {"type": "decline_reason_matches_change", "weight": 3, "confidence": "high", "evidence": evidence}


def _is_controlled_context_term(criteria, applicant, category, term):
    key = _normalize_term_key(term)
    if not key:
        return False
    if category == "profile":
        return key in criteria["added_profile_terms_keys"]
    if key in {"salary", "experience", "city", "location", "schedule", "graph", "график", "зарплата", "опыт", "город"}:
        return False
    values = [criteria["previous"].get(_CATEGORY_FIELD[category]), criteria["current"].get(_CATEGORY_FIELD[category])]
    if category == "salary":
        values.append(applicant.get("salary"))
    normalized_values = {_normalize_term_key(str(value)) for value in values if value is not None}
    if key in normalized_values:
        return True
    if category == "schedule" and criteria["current"].get("schedule_type") == "remote":
        return key in {"удаленно", "удалённо"}
    return False


def _signal_context(criteria, applicant, context_terms, comments, changes):
    if not context_terms:
        return None
    evidence = []
    for cat in CONTEXT_CATEGORIES:
        terms = context_terms.get(cat)
        if not terms or not changes.get(cat):
            continue
        for term in terms:
            if not _is_controlled_context_term(criteria, applicant, cat, term):
                continue
            for tag in applicant.get("tags") or []:
                if _normalize_term_key(term) == _normalize_term_key(tag):
                    evidence.append({"source": "tag", "field": cat, "value": tag})
            for c in comments or []:
                body = c.get("body") or ""
                if _context_phrase_in_text(term, body):
                    evidence.append(
                        {"source": "event", "field": cat, "event_id": c.get("id"), "quote": _find_quote(body, term) or body[:120], "created_at": c.get("created_at")}
                    )
    if not evidence:
        return None
    return {"type": "context_mentions_change", "weight": 2, "confidence": "medium", "evidence": evidence}


_SIGNAL_WEIGHTS = {"salary_unlocked": 3, "location_unlocked": 2, "decline_reason_matches_change": 3, "new_terms_match": 2, "context_mentions_change": 2}


def _score_candidate(signals, criteria, applicant):
    base = sum(_SIGNAL_WEIGHTS[t] for t in {s["type"] for s in signals})
    bonus = 0
    role_terms = criteria["current"].get("role_terms")
    if signals and role_terms:
        haystack = set(_tokens(" ".join([applicant.get("title") or ""] + (applicant.get("tags") or []))))
        if all(_term_matches(t, haystack) for t in role_terms):
            bonus = 1
    return base + bonus


def _blocked_result(warnings):
    return {
        "status": "blocked",
        "summary": {
            "candidate_ids_observed": 0,
            "cards_attempted": 0,
            "cards_loaded": 0,
            "excluded_hired": 0,
            "excluded_active_on_target": 0,
            "lower_bound_fields": [],
        },
        "candidates": [],
        "warnings": warnings,
        "completeness": {
            "source_joins": False,
            "finalists": False,
            "target_joins": None,
            "jobs": {"target": None, "source": None},
            "cards": {"attempted": 0, "loaded": 0},
            "reasons": None,
            "events": {"complete": False, "failed_applicant_ids": []},
            "ranking_complete": False,
        },
    }


def _validate_mapping(mapping):
    if mapping is None:
        return
    if not isinstance(mapping, dict):
        raise ReopenValidationError("VALIDATION_ERROR", "declination_reason_mapping должен быть объектом")
    for cat, cfg in mapping.items():
        if cat not in CONTEXT_CATEGORIES:
            continue
        if not isinstance(cfg, list):
            raise ReopenValidationError("VALIDATION_ERROR", f"declination_reason_mapping.{cat} должен быть списком")
        for item in cfg:
            if isinstance(item, bool) or (
                not isinstance(item, int)
                and not (isinstance(item, dict) and isinstance(item.get("reason_id"), int) and not isinstance(item.get("reason_id"), bool))
            ):
                raise ReopenValidationError("VALIDATION_ERROR", f"declination_reason_mapping.{cat} содержит некорректный элемент")


def run_reopen(request, mapping=None, top=20):
    warnings = []
    try:
        _validate_mapping(mapping)
        prep = _validate_request(request)
        target_job_id, source_job_id = prep["target_job_id"], prep["source_job_id"]

        target_job, jobs_target_ok = (None, True)
        if target_job_id:
            target_job, jobs_target_ok = _fetch_job(target_job_id)
            if not jobs_target_ok:
                raise ReopenValidationError("JOB_UNAVAILABLE", f"Не удалось получить целевую вакансию {target_job_id}")

        source_job, jobs_source_ok = (None, True)
        if request.get("source_represents_previous_criteria"):
            source_job, jobs_source_ok = _fetch_job(source_job_id)
            if not jobs_source_ok:
                raise ReopenValidationError("JOB_UNAVAILABLE", f"Не удалось получить референсную вакансию {source_job_id}")

        criteria = _prepare_criteria(request, target_job, source_job)
        applicant_currency = request.get("applicant_salary_currency")
        currency_confirmed = bool(
            applicant_currency
            and criteria["previous"].get("currency_type")
            and criteria["previous"]["currency_type"] == criteria["current"].get("currency_type") == applicant_currency
        )
    except ReopenValidationError as e:
        result = _blocked_result([_warn(e.code, e.message, e.scope)])
        return result, 2

    same_job = source_job_id == target_job_id

    finalists_raw, finalists_ok = _fetch_cursor_strict("/finalists.json", ["applicant_id", "state"])
    if not finalists_ok:
        return _blocked_result([_warn("FINALISTS_INCOMPLETE", "Неполный список финалистов — исключение нанятых небезопасно", "global")]), 3
    hired_ids = {f["applicant_id"] for f in finalists_raw if f.get("state") not in HIRED_EXCLUDE_STATES}

    source_raw, source_complete = _fetch_cursor_lenient(f"/jobs/{source_job_id}/ajs_joins.json", ["applicant_id", "active"])

    if same_job:
        if not source_complete:
            return _blocked_result([_warn("SOURCE_JOINS_PARTIAL", "Референсная и целевая вакансии совпадают — неполный ответ небезопасен", "global")]), 3
        target_raw, target_joins_ok = source_raw, True
    elif target_job_id:
        target_raw, target_joins_ok = _fetch_cursor_strict(f"/jobs/{target_job_id}/ajs_joins.json", ["applicant_id", "active"])
        if not target_joins_ok:
            return _blocked_result([_warn("TARGET_JOINS_INCOMPLETE", "Неполный список активных на целевой вакансии — исключение активных небезопасно", "global")]), 3
    else:
        target_raw, target_joins_ok = [], None

    target_active_ids = {j["applicant_id"] for j in target_raw if j.get("active")} if target_job_id else set()

    if not source_complete:
        warnings.append(_warn("SOURCE_JOINS_PARTIAL", "Неполный список прошлых кандидатов референсной вакансии — показано найденное подмножество", "global"))

    inactive_by_applicant = {}
    for j in source_raw:
        if not j.get("active") and j["applicant_id"] not in inactive_by_applicant:
            inactive_by_applicant[j["applicant_id"]] = j
    candidate_ids_observed = len(inactive_by_applicant)

    excluded_hired = excluded_active = 0
    survivors = []
    for aid, j in inactive_by_applicant.items():
        if aid in hired_ids:
            excluded_hired += 1
            continue
        if aid in target_active_ids:
            excluded_active += 1
            continue
        survivors.append((aid, j))

    cards_attempted = len(survivors)
    cards_loaded = 0
    reasons_dict, reasons_ok = _load_declination_reasons()
    if not reasons_ok:
        warnings.append(_warn("REASONS_UNAVAILABLE", "Причины отказа недоступны через API текущего тенанта", "global"))

    changes = _detect_directional_changes(criteria)
    context_terms = request.get("context_terms")
    failed_events = []

    ranked = []
    for aid, join in survivors:
        applicant, ok = _fetch_applicant(aid)
        if not ok:
            warnings.append(_warn("CARD_UNAVAILABLE", f"Не удалось получить карточку кандидата {aid}", "candidate", aid))
            continue
        cards_loaded += 1
        applicant = dict(applicant)

        comments, decline = _fetch_comments_for(aid, source_job_id)
        if comments is None:
            failed_events.append(aid)
            comments = []
        applicant["declination_reason_id"] = join.get("declination_reason_id")
        if applicant["declination_reason_id"] is None and decline:
            applicant["declination_reason_id"] = decline["declination_reason_id"]
        declined_at = join.get("declined_at") or (decline["declined_at"] if decline else None)

        signals = []
        for fn in (
            lambda: _signal_salary(criteria, applicant, currency_confirmed, request),
            lambda: _signal_location(criteria, applicant),
            lambda: _signal_new_terms(criteria, applicant),
            lambda: _signal_decline_reason(criteria, applicant, mapping, reasons_dict, changes),
            lambda: _signal_context(criteria, applicant, context_terms, comments, changes),
        ):
            s = fn()
            if s:
                signals.append(s)
        if not signals:
            continue

        score = _score_candidate(signals, criteria, applicant)
        confidence = "high" if any(s["confidence"] == "high" for s in signals) else "medium"
        name = applicant.get("name") or " ".join(filter(None, [applicant.get("first_name"), applicant.get("last_name")])) or None
        url_template = request.get("applicant_url_template")
        url = url_template.replace("{id}", str(aid)) if url_template else None
        ranked.append(
            {
                "applicant_id": aid,
                "name": name,
                "url": url,
                "source_job_id": source_job_id,
                "declined_at": declined_at,
                "confirmed_declination_reason": (
                    {"id": applicant["declination_reason_id"], "name": reasons_dict[applicant["declination_reason_id"]]}
                    if applicant.get("declination_reason_id") is not None and reasons_dict and applicant["declination_reason_id"] in reasons_dict
                    else None
                ),
                "score": score,
                "confidence": confidence,
                "signals": [{k: v for k, v in s.items() if k != "confidence"} for s in signals],
            }
        )

    ranked = [c for c in ranked if c["score"] > 0]
    ranked.sort(key=lambda c: c["applicant_id"])
    with_date = [c for c in ranked if c["declined_at"] is not None]
    without_date = [c for c in ranked if c["declined_at"] is None]
    with_date.sort(key=lambda c: c["declined_at"], reverse=True)
    ranked = with_date + without_date
    ranked.sort(key=lambda c: -c["score"])
    ranked = ranked[:top]

    ranking_complete = source_complete and not failed_events and reasons_ok
    if failed_events:
        warnings.append(_warn("EVENTS_INCOMPLETE", "Не удалось получить события части кандидатов", "global"))

    status = "ok" if ranking_complete else "partial"
    lower_bound_fields = [] if source_complete else ["candidate_ids_observed", "excluded_hired", "excluded_active_on_target"]

    result = {
        "status": status,
        "summary": {
            "candidate_ids_observed": candidate_ids_observed,
            "cards_attempted": cards_attempted,
            "cards_loaded": cards_loaded,
            "excluded_hired": excluded_hired,
            "excluded_active_on_target": excluded_active,
            "lower_bound_fields": lower_bound_fields,
        },
        "candidates": ranked,
        "warnings": warnings,
        "completeness": {
            "source_joins": source_complete,
            "finalists": True,
            "target_joins": target_joins_ok,
            "jobs": {"target": jobs_target_ok if target_job_id else None, "source": jobs_source_ok},
            "cards": {"attempted": cards_attempted, "loaded": cards_loaded},
            # ponytail: не различаем "тенант не отдаёт declination_reason_id" от "запрос причин не удался";
            # оба случая описываются как reasons=false, апгрейд — проверить поле у известного отказа отдельно.
            "reasons": reasons_ok,
            "events": {"complete": not failed_events, "failed_applicant_ids": failed_events},
            "ranking_complete": ranking_complete,
        },
    }
    return result, 0


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
    p_search.add_argument("--cv-cache-dir", default=None, help="каталог CV-кэша (см. cv-index) для полнотекстового поиска")

    p_cvindex = sub.add_parser("cv-index", help="скачать и извлечь текст резюме кандидатов резерва в локальный кэш")
    p_cvindex.add_argument("--reserve-file", required=True, help="JSON-файл с резервом (вывод команды reserve)")
    p_cvindex.add_argument("--cache-dir", default=os.environ.get("CV_CACHE_DIR", ".cv_cache"))
    p_cvindex.add_argument("--limit", type=int, default=None)

    p_reopen = sub.add_parser("reopen", help="пересмотр прошлых кандидатов при изменении условий вакансии (SDD C07)")
    p_reopen.add_argument("request_json", help="REQUEST_JSON (см. SDD-C07-REOPEN-CANDIDATES.md §4)")
    p_reopen.add_argument("--top", type=int, default=20)
    p_reopen.add_argument("--source-job-id", type=int, default=None)
    p_reopen.add_argument("--target-job-id", type=int, default=None)
    p_reopen.add_argument("--declination-reasons-file", default=None)

    args = parser.parse_args()

    if not BASE_URL or not TOKEN:
        sys.exit("POTOK_BASE_URL / POTOK_API_TOKEN не заданы (см. .env)")

    warnings = []
    if args.cmd == "reserve":
        print(json.dumps(build_reserve_pool(warnings), ensure_ascii=False, indent=2))
    elif args.cmd == "dedup":
        print(json.dumps(find_duplicates(warnings=warnings), ensure_ascii=False, indent=2))
    elif args.cmd == "search":
        terms = json.loads(args.terms_json)
        if args.reserve_file:
            with open(args.reserve_file, encoding="utf-8") as f:
                reserve = json.load(f)
        else:
            reserve = build_reserve_pool(warnings)
        print(json.dumps(search_reserve(reserve, terms, args.top, cv_cache_dir=args.cv_cache_dir), ensure_ascii=False, indent=2))
    elif args.cmd == "cv-index":
        with open(args.reserve_file, encoding="utf-8") as f:
            reserve = json.load(f)
        print(json.dumps(cv_index_reserve(reserve, cache_dir=args.cache_dir, limit=args.limit), ensure_ascii=False, indent=2))
    elif args.cmd == "reopen":
        request = json.loads(args.request_json)
        if args.source_job_id is not None:
            if request.get("source_job_id") not in (None, args.source_job_id):
                sys.exit("Конфликт source_job_id между JSON и --source-job-id")
            request.setdefault("source_job_id", args.source_job_id)
        if args.target_job_id is not None:
            if request.get("target_job_id") not in (None, args.target_job_id):
                sys.exit("Конфликт target_job_id между JSON и --target-job-id")
            request.setdefault("target_job_id", args.target_job_id)
        mapping = request.get("declination_reason_mapping")
        if args.declination_reasons_file:
            if mapping is not None:
                sys.exit("Одновременная передача declination_reason_mapping и --declination-reasons-file запрещена")
            with open(args.declination_reasons_file, encoding="utf-8") as f:
                mapping = json.load(f)
        result, exit_code = run_reopen(request, mapping=mapping, top=args.top)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(exit_code)

    for warning in warnings:
        print(f"ПРЕДУПРЕЖДЕНИЕ: частичный результат, {warning}", file=sys.stderr)


if __name__ == "__main__":
    main()
