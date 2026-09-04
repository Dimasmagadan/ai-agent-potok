#!/usr/bin/env python3
"""Telegram-бот соискателя/сотрудника: long polling, извлечение профиля через Anthropic API,
jobs-match / jobs-gaps.

Внешний режим (по умолчанию, `JOB_SEEKER_MODE=external`) — см. SDD-C08-DELIVERY-EXTENSIONS.md §9,
stateless, только опубликованные вакансии карьерного сайта. Внутренний режим
(`JOB_SEEKER_MODE=internal`) — см. SDD-C09-INTERNAL-MOBILITY.md §4: авторизованный источник
вакансий компании (включая неопубликованные, кроме `private`), второй интент «чего не хватает
для вакансии N» и минимальная in-memory память последнего профиля на chat_id (см. §4 п.4).
Stdlib-only (urllib/json/time). Текст сообщений пользователей не логируется — только chat_id,
timestamp, статус обработки.
"""
import json
import os
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import job_seeker as js

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
API_BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

MODE = os.environ.get("JOB_SEEKER_MODE", "external")
INCLUDE_PRIVATE = os.environ.get("JOB_SEEKER_INCLUDE_PRIVATE") == "1"
ALLOWED_USER_IDS = frozenset(
    int(user_id) for user_id in os.environ.get("JOB_SEEKER_ALLOWED_USER_IDS", "").split(",") if user_id.strip().isdigit()
)

MAX_TEXT_LEN = 1000
RATE_LIMIT_SECONDS = 5
JOBS_CACHE_TTL = 600

START_MESSAGE = (
    "Привет! Я показываю открытые вакансии компании под ваше описание себя. "
    "Опишите свободным текстом, кого/что вы ищете (роль, навыки, город, формат работы) — "
    "и я подберу подходящие вакансии со ссылками. Показываю только опубликованные вакансии "
    "и не отправляю отклик за вас: ссылка ведёт на страницу вакансии, откликаетесь вы сами."
)

START_MESSAGE_INTERNAL = (
    "Привет! Я бот компании для внутренней мобильности — только для сотрудников, ссылку на меня "
    "не пересылайте вовне. Опишите свободным текстом себя (роль, навыки, город, формат работы) — "
    "и я покажу подходящие вакансии компании, включая ещё не опубликованные на карьерном сайте. "
    "Можно спросить «чего мне не хватает для вакансии N» — сравню её требования с вашим профилем. "
    "Текст ваших сообщений отправляется в Anthropic API для извлечения профиля."
)

PROFILE_SYSTEM_PROMPT = """Ты извлекаешь профиль соискателя из свободного текста для поиска вакансий.
Верни СТРОГО один JSON-объект без пояснений в формате:
{"terms": [{"term": "...", "kind": "original"|"synonym"}, ...], "filters": {"city": "...", "schedule": "...", "salary_from": 0}, "target_job": "..."}
"terms" — роль, навыки, ключевые слова из текста кандидата (kind=original) плюс контекстные
синонимы (kind=synonym), например "питон"->"python", "джун"->"junior". "filters" содержит только
явно упомянутые поля; неизвестное поле не включай вовсе. "target_job" — опциональное поле: номер
или название вакансии, если человек спросил, чего ему не хватает для конкретной вакансии
(например "чего не хватает для вакансии 9" или "для позиции бэкендера"); не включай это поле,
если вакансия не упомянута. Никаких полей кроме terms, filters и target_job."""


def call_llm_messages(messages, system, api_key):
    body = {"model": "claude-opus-5", "max_tokens": 1024, "fallbacks": "default", "system": system, "messages": messages}
    req = Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "server-side-fallback-2026-07-01",
        },
        method="POST",
    )
    with urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def extract_json_object(text):
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in LLM response")
    return json.loads(text[start : end + 1])


def _text_of(resp):
    return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")


def _split_target_job(raw):
    """Отделяет опциональный target_job от структуры профиля {terms, filters} (SDD-C09 §4 п.3)."""
    target_job = raw.pop("target_job", None) if isinstance(raw, dict) else None
    if not isinstance(target_job, str) or not target_job.strip():
        target_job = None
    return raw, target_job


def extract_profile(user_text, api_key):
    """Возвращает (profile_dict|None, target_job|None, refusal_code|None).

    refusal_code: "refusal" | "parse_failed".
    """
    messages = [{"role": "user", "content": user_text}]
    resp = call_llm_messages(messages, PROFILE_SYSTEM_PROMPT, api_key)
    if resp.get("stop_reason") == "refusal":
        return None, None, "refusal"
    try:
        profile, target_job = _split_target_job(extract_json_object(_text_of(resp)))
        if js.validate_profile(profile):
            return profile, target_job, None
    except ValueError:
        pass

    messages.append({"role": "assistant", "content": _text_of(resp)})
    messages.append({"role": "user", "content": "Ответ не распарсился как JSON. Верни строго один JSON-объект без пояснений."})
    resp2 = call_llm_messages(messages, PROFILE_SYSTEM_PROMPT, api_key)
    if resp2.get("stop_reason") == "refusal":
        return None, None, "refusal"
    try:
        profile, target_job = _split_target_job(extract_json_object(_text_of(resp2)))
        if js.validate_profile(profile):
            return profile, target_job, None
    except ValueError:
        pass
    return None, None, "parse_failed"


def is_rate_limited(last_ts_map, chat_id, now):
    last = last_ts_map.get(chat_id)
    return last is not None and (now - last) < RATE_LIMIT_SECONDS


def _format_salary(job):
    lo, hi, cur = job.get("salary_from"), job.get("salary_to"), (job.get("currency") or "").strip()
    if lo and hi:
        return f"{lo}–{hi} {cur}".strip()
    if hi:
        return f"до {hi} {cur}".strip()
    if lo:
        return f"от {lo} {cur}".strip()
    return "зарплата не указана"


def format_jobs_plain(match_result, jobs_by_id):
    lines = []
    for j in match_result["jobs"]:
        job = jobs_by_id.get(j["id"], {})
        city = job.get("city") or "город не указан"
        lines.append(f"{j['title']} — {city} — {_format_salary(job)} — {j.get('apply_url')}")
    return "\n".join(lines) if lines else "Подходящих вакансий по вашему описанию не нашлось."


def match_target_job(target_job, jobs):
    """Сопоставляет target_job (свободный текст от LLM) со списком вакансий по id/подстроке title.

    Возвращает (job|None, similar_jobs). При однозначном совпадении job задан. При нескольких
    вариантах или отсутствии совпадений job is None, similar_jobs — кандидаты по названию
    (SDD-C09 §4 п.3).
    """
    if not target_job:
        return None, []
    query = target_job.strip().casefold()
    id_match = re.search(r"\b\d+\b", query)
    if id_match:
        job = next((j for j in jobs if str(j.get("id")) == id_match.group()), None)
        if job is not None:
            return job, []
    candidates = [j for j in jobs if query in (j.get("title") or "").casefold()]
    if len(candidates) == 1:
        return candidates[0], []
    return None, candidates


def format_gaps_plain(gaps_result):
    job = gaps_result["job"]
    lines = [f"Вакансия: {job.get('title')} (id={job.get('id')})"]
    if gaps_result["gaps"]:
        for g in gaps_result["gaps"]:
            lines.append(f"— {g['message']}")
    else:
        lines.append("Вы подходите по всем проверяемым критериям.")
    if gaps_result["unknown_fields"]:
        lines.append("Не удалось сравнить (нет данных): " + ", ".join(gaps_result["unknown_fields"]) + ".")
    return "\n".join(lines)


def get_updates(offset):
    url = f"{API_BASE}/getUpdates?" + urlencode({"timeout": 50, "offset": offset})
    with urlopen(url, timeout=60) as resp:
        return json.loads(resp.read()).get("result", [])


def send_message(chat_id, text):
    data = json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": True}).encode("utf-8")
    req = Request(f"{API_BASE}/sendMessage", data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=30):
            pass
    except (HTTPError, URLError, TimeoutError) as e:
        _log("(system)", f"send_failed:{e}")


def _log(chat_id, status):
    print(f"{time.time():.0f} chat_id={chat_id} status={status}", file=sys.stderr)


def get_cached_jobs(cache, now):
    if cache["jobs"] is None or now - cache["fetched_at"] > JOBS_CACHE_TTL:
        if MODE == "internal":
            cache["jobs"] = js.fetch_jobs_v3_fallback(js.OPEN_BASE_URL, published_only=False, include_private=INCLUDE_PRIVATE)
        else:
            cache["jobs"] = js.fetch_jobs_constructor(js.OPEN_BASE_URL, js.CONSTRUCTOR_ID)
        cache["fetched_at"] = now
    return cache["jobs"]


def handle_update(chat_id, text, last_request_ts, jobs_cache, last_profile, user_id=None, chat_type="private"):
    now = time.time()
    user_id = chat_id if user_id is None else user_id

    if MODE == "internal":
        if chat_type != "private" or user_id not in ALLOWED_USER_IDS:
            send_message(chat_id, "Этот бот доступен только авторизованным сотрудникам в личном чате.")
            _log(chat_id, "access_denied")
            return

    if text.strip() == "/start":
        send_message(chat_id, START_MESSAGE_INTERNAL if MODE == "internal" else START_MESSAGE)
        _log(chat_id, "start")
        return

    if len(text) > MAX_TEXT_LEN:
        send_message(chat_id, f"Слишком длинное сообщение ({len(text)} символов). Опишите короче, до {MAX_TEXT_LEN} символов.")
        _log(chat_id, "too_long")
        return

    if is_rate_limited(last_request_ts, user_id, now):
        send_message(chat_id, "Подождите пару секунд между запросами.")
        _log(chat_id, "rate_limited")
        return
    last_request_ts[user_id] = now

    try:
        profile, target_job, refusal = extract_profile(text, ANTHROPIC_API_KEY)
    except (HTTPError, URLError, TimeoutError):
        send_message(chat_id, "LLM сейчас недоступен, попробуйте позже.")
        _log(chat_id, "llm_unavailable")
        return

    if refusal == "refusal":
        send_message(chat_id, "Не могу обработать это сообщение, опишите, пожалуйста, желаемую работу.")
        _log(chat_id, "refusal")
        return
    if refusal == "parse_failed" or profile is None:
        send_message(chat_id, "Не получилось разобрать ответ, переформулируйте, пожалуйста, запрос.")
        _log(chat_id, "parse_failed")
        return

    if MODE == "internal":
        # Диалог двухшаговый (резюме → «чего не хватает для вакансии N»); без памяти
        # второе сообщение считало бы пробелом всё подряд (SDD-C09 §4 п.4).
        saved_profile = last_profile.get(user_id)
        if target_job and saved_profile is not None:
            profile = saved_profile
        elif not profile["terms"] and not profile.get("filters") and target_job:
            if saved_profile is None:
                send_message(chat_id, "Сначала опишите, пожалуйста, себя — роль, навыки, опыт — а потом спрашивайте про конкретную вакансию.")
                _log(chat_id, "no_profile_for_gaps")
                return
        else:
            last_profile[user_id] = profile

    try:
        jobs = get_cached_jobs(jobs_cache, now)
    except tp_fetch_errors():
        send_message(chat_id, "Список вакансий сейчас недоступен, попробуйте позже.")
        _log(chat_id, "jobs_unavailable")
        return

    similar = []
    if MODE == "internal" and target_job:
        matched_job, similar = match_target_job(target_job, jobs)
        if matched_job is not None:
            gaps, unknown_fields = js.compute_gaps(matched_job, profile)
            gaps_result = {"job": {"id": matched_job["id"], "title": matched_job.get("title")}, "gaps": gaps, "unknown_fields": unknown_fields}
            send_message(chat_id, format_gaps_plain(gaps_result))
            _log(chat_id, "gaps")
            return

    result = js.match_jobs(jobs, profile)
    jobs_by_id = {j["id"]: j for j in jobs}
    text_out = format_jobs_plain(result, jobs_by_id)
    if MODE == "internal" and target_job:
        if similar:
            titles = "; ".join(f"{j.get('title')} (id={j['id']})" for j in similar)
            text_out += f"\n\nНе удалось однозначно определить вакансию «{target_job}». Похожие по названию: {titles}."
        else:
            text_out += f"\n\nНе удалось найти вакансию «{target_job}»."
    send_message(chat_id, text_out)
    _log(chat_id, "matched")


def tp_fetch_errors():
    import talent_pool as tp

    return (tp.FetchError, HTTPError, URLError, TimeoutError)


def run():
    if not TELEGRAM_TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN не задан")
    if not ANTHROPIC_API_KEY:
        sys.exit("ANTHROPIC_API_KEY не задан")
    if MODE == "internal" and (not js.tp.BASE_URL or not js.tp.TOKEN):
        sys.exit("JOB_SEEKER_MODE=internal требует POTOK_BASE_URL и POTOK_API_TOKEN")
    if MODE == "internal" and not ALLOWED_USER_IDS:
        sys.exit("JOB_SEEKER_MODE=internal требует JOB_SEEKER_ALLOWED_USER_IDS")

    last_update_id = 0
    last_request_ts = {}
    jobs_cache = {"jobs": None, "fetched_at": 0}
    last_profile = {}

    while True:
        try:
            updates = get_updates(last_update_id + 1)
        except (HTTPError, URLError, TimeoutError) as e:
            _log("(system)", f"poll_failed:{e}")
            time.sleep(5)
            continue
        for upd in updates:
            last_update_id = max(last_update_id, upd["update_id"])
            msg = upd.get("message") or {}
            if "text" not in msg:
                continue
            handle_update(
                msg["chat"]["id"],
                msg["text"],
                last_request_ts,
                jobs_cache,
                last_profile,
                user_id=(msg.get("from") or {}).get("id"),
                chat_type=msg["chat"].get("type"),
            )


if __name__ == "__main__":
    run()
