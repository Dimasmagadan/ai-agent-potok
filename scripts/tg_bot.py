#!/usr/bin/env python3
"""Telegram-бот соискателя: long polling, извлечение профиля через Anthropic API, jobs-match.

См. SDD-C08-DELIVERY-EXTENSIONS.md §9. Stdlib-only (urllib/json/time). Бот stateless:
история между сообщениями не хранится ни в памяти, ни на диске. Текст сообщений
пользователей не логируется — только chat_id, timestamp, статус обработки.
"""
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import job_seeker as js

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
API_BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

MAX_TEXT_LEN = 1000
RATE_LIMIT_SECONDS = 5
JOBS_CACHE_TTL = 600

START_MESSAGE = (
    "Привет! Я показываю открытые вакансии компании под ваше описание себя. "
    "Опишите свободным текстом, кого/что вы ищете (роль, навыки, город, формат работы) — "
    "и я подберу подходящие вакансии со ссылками. Показываю только опубликованные вакансии "
    "и не отправляю отклик за вас: ссылка ведёт на страницу вакансии, откликаетесь вы сами."
)

PROFILE_SYSTEM_PROMPT = """Ты извлекаешь профиль соискателя из свободного текста для поиска вакансий.
Верни СТРОГО один JSON-объект без пояснений в формате:
{"terms": [{"term": "...", "kind": "original"|"synonym"}, ...], "filters": {"city": "...", "schedule": "...", "salary_from": 0}}
"terms" — роль, навыки, ключевые слова из текста кандидата (kind=original) плюс контекстные
синонимы (kind=synonym), например "питон"->"python", "джун"->"junior". "filters" содержит только
явно упомянутые поля; неизвестное поле не включай вовсе. Никаких полей кроме terms и filters."""


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


def extract_profile(user_text, api_key):
    """Возвращает (profile_dict|None, refusal_code|None). refusal_code: "refusal" | "parse_failed"."""
    messages = [{"role": "user", "content": user_text}]
    resp = call_llm_messages(messages, PROFILE_SYSTEM_PROMPT, api_key)
    if resp.get("stop_reason") == "refusal":
        return None, "refusal"
    try:
        profile = extract_json_object(_text_of(resp))
        if js.validate_profile(profile):
            return profile, None
    except ValueError:
        pass

    messages.append({"role": "assistant", "content": _text_of(resp)})
    messages.append({"role": "user", "content": "Ответ не распарсился как JSON. Верни строго один JSON-объект без пояснений."})
    resp2 = call_llm_messages(messages, PROFILE_SYSTEM_PROMPT, api_key)
    if resp2.get("stop_reason") == "refusal":
        return None, "refusal"
    try:
        profile = extract_json_object(_text_of(resp2))
        if js.validate_profile(profile):
            return profile, None
    except ValueError:
        pass
    return None, "parse_failed"


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


def get_updates(offset):
    url = f"{API_BASE}/getUpdates?" + urlencode({"timeout": 50, "offset": offset})
    with urlopen(url, timeout=60) as resp:
        return json.loads(resp.read()).get("result", [])


def send_message(chat_id, text):
    data = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}).encode("utf-8")
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
        cache["jobs"] = js.fetch_jobs_constructor(js.OPEN_BASE_URL, js.CONSTRUCTOR_ID)
        cache["fetched_at"] = now
    return cache["jobs"]


def handle_update(chat_id, text, last_request_ts, jobs_cache):
    now = time.time()

    if text.strip() == "/start":
        send_message(chat_id, START_MESSAGE)
        _log(chat_id, "start")
        return

    if len(text) > MAX_TEXT_LEN:
        send_message(chat_id, f"Слишком длинное сообщение ({len(text)} символов). Опишите короче, до {MAX_TEXT_LEN} символов.")
        _log(chat_id, "too_long")
        return

    if is_rate_limited(last_request_ts, chat_id, now):
        send_message(chat_id, "Подождите пару секунд между запросами.")
        _log(chat_id, "rate_limited")
        return
    last_request_ts[chat_id] = now

    try:
        profile, refusal = extract_profile(text, ANTHROPIC_API_KEY)
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

    try:
        jobs = get_cached_jobs(jobs_cache, now)
    except tp_fetch_errors():
        send_message(chat_id, "Список вакансий сейчас недоступен, попробуйте позже.")
        _log(chat_id, "jobs_unavailable")
        return

    result = js.match_jobs(jobs, profile)
    jobs_by_id = {j["id"]: j for j in jobs}
    send_message(chat_id, format_jobs_plain(result, jobs_by_id))
    _log(chat_id, "matched")


def tp_fetch_errors():
    import talent_pool as tp

    return (tp.FetchError, HTTPError, URLError, TimeoutError)


def run():
    if not TELEGRAM_TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN не задан")
    if not ANTHROPIC_API_KEY:
        sys.exit("ANTHROPIC_API_KEY не задан")

    last_update_id = 0
    last_request_ts = {}
    jobs_cache = {"jobs": None, "fetched_at": 0}

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
            handle_update(msg["chat"]["id"], msg["text"], last_request_ts, jobs_cache)


if __name__ == "__main__":
    run()
