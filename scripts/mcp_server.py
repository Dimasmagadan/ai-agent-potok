#!/usr/bin/env python3
"""MCP-сервер (stdio, JSON-RPC 2.0) поверх talent_pool.py / job_seeker.py.

Канал доставки для рекрутёра (Claude Desktop / Cursor / любой MCP-клиент),
см. SDD-C08-DELIVERY-EXTENSIONS.md §8. Только импорт функций, без subprocess.
Логи — исключительно в stderr; stdout содержит только JSON-RPC ответы.
"""
import json
import sys
import tempfile

import job_seeker as js
import talent_pool as tp

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "potok-talent-pool", "version": "1.1.0"}

TOOLS = [
    {
        "name": "potok_reserve",
        "description": "Построить кадровый резерв «Потока» (кандидаты без активной вакансии и не нанятые). "
        "Используй, когда рекрутёр спрашивает, кто есть в резерве/пуле кандидатов.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "potok_search",
        "description": "Поиск подходящих кандидатов в кадровом резерве по термам (роль, навыки, синонимы). "
        "Используй, когда рекрутёр описывает свободным текстом, кто нужен. При cv:true матчинг "
        "дополнительно идёт по полнотекстовому кэшу резюме кандидатов (сначала выполняется cv-index).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "terms": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"term": {"type": "string"}, "kind": {"type": "string", "enum": ["original", "synonym"]}},
                        "required": ["term", "kind"],
                        "additionalProperties": False,
                    },
                },
                "cv": {"type": "boolean"},
            },
            "required": ["terms"],
            "additionalProperties": False,
        },
    },
    {
        "name": "potok_dedup",
        "description": "Найти дубли кандидатов в базе «Потока» по точному совпадению телефона/email. "
        "Используй, когда рекрутёр просит показать дубли кандидатов.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "potok_reopen",
        "description": "Пересмотреть прошлых (неактивных) кандидатов вакансии при изменении условий найма "
        "(вилка, график, локация, минимальный опыт, профиль). Используй, когда рекрутёр изменил условия "
        "вакансии и просит пересмотреть прошлые отказы. До вызова нужны целевая вакансия, источник её "
        "прошлых кандидатов и прежние условия: если их нет в диалоге, задай один короткий уточняющий "
        "вопрос. Не передавай changes, salary, salary_from или salary_max: верх вилки задаётся только "
        "как current_criteria.salary_to и previous_criteria.salary_to. См. SDD-C07-REOPEN-CANDIDATES.md §4/§8.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "object",
                    "description": "Полный контекст изменения. Для повышения вилки укажите target_job_id, source_job_id, previous_criteria и current_criteria. "
                    "Если кандидатов нужно брать из той же вакансии, укажите use_target_as_source:true и явные previous_criteria.",
                    "properties": {
                        "target_job_id": {"type": "integer", "description": "ID целевой вакансии с новыми условиями."},
                        "target_job_description": {"type": "string", "description": "Явное описание целевой вакансии, только если её ID неизвестен."},
                        "source_job_id": {"type": "integer", "description": "ID вакансии, из которой взять прошлых кандидатов."},
                        "use_target_as_source": {"type": "boolean", "description": "true, если прошлых кандидатов нужно брать из target_job_id."},
                        "source_represents_previous_criteria": {"type": "boolean", "description": "true, только если прежние условия берутся из source_job_id, без previous_criteria."},
                        "previous_criteria": {"$ref": "#/$defs/criteria", "description": "Прежние условия. Для вилки: salary_to и currency_type."},
                        "current_criteria": {"$ref": "#/$defs/criteria", "description": "Новые условия. Для вилки: salary_to и currency_type."},
                        "applicant_salary_currency": {"type": "string", "description": "Валюта зарплатных ожиданий кандидатов; для рублей RUR. Обязательна для salary_unlocked."},
                        "context_terms": {"type": "object", "description": "Подтверждённые рекрутёром точные термины для поиска в тегах и комментариях."},
                        "declination_reason_mapping": {"type": "object", "description": "Контролируемое сопоставление категорий изменений с ID причин отказа."}
                    },
                    "additionalProperties": False
                },
                "top": {"type": "integer", "minimum": 1, "description": "Максимальное число кандидатов, по умолчанию 20."}
            },
            "required": ["request"],
            "additionalProperties": False,
            "$defs": {
                "criteria": {
                    "type": "object",
                    "properties": {
                        "salary_to": {"type": "number", "description": "Верхняя граница вилки."},
                        "currency_type": {"type": "string", "description": "Валюта вилки, например RUR."},
                        "schedule_type": {"type": "string"},
                        "experience_minimum_years": {"type": "number"},
                        "city": {"type": "string"},
                        "role_terms": {"type": "array", "items": {"type": "string"}},
                        "profile_terms_any": {"type": "array", "items": {"type": "string"}}
                    },
                    "additionalProperties": False
                }
            }
        },
    },
    {
        "name": "potok_jobs_match",
        "description": "Подобрать открытые вакансии компании под профиль соискателя (термы + фильтры) и "
        "показать пробелы по каждой подходящей вакансии. Используй, когда соискатель спрашивает, какие "
        "вакансии ему подходят и чего не хватает. Профиль обязан содержать terms; filters содержат только "
        "city, schedule и salary_from. Никогда не отправляет отклик — только ссылка apply_url.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "object",
                    "description": "Профиль кандидата: технологии и роль в terms, только явно названные ограничения в filters.",
                    "properties": {
                        "terms": {
                            "type": "array",
                            "description": "Роль, технологии и навыки. Каждый элемент обязан содержать term и kind.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "term": {"type": "string", "description": "Например: python, django, postgresql."},
                                    "kind": {"type": "string", "enum": ["original", "synonym"], "description": "original для слов пользователя, synonym для добавленного синонима."}
                                },
                                "required": ["term", "kind"],
                                "additionalProperties": False
                            }
                        },
                        "filters": {
                            "type": "object",
                            "properties": {
                                "city": {"type": "string", "description": "Например: Санкт-Петербург."},
                                "schedule": {"type": "string", "description": "Точное значение из вакансии, например remote."},
                                "salary_from": {"type": "number", "description": "Минимальные зарплатные ожидания кандидата."}
                            },
                            "additionalProperties": False
                        }
                    },
                    "required": ["terms"],
                    "additionalProperties": False
                },
                "top": {"type": "integer", "minimum": 1, "description": "Максимальное число вакансий, по умолчанию 10."}
            },
            "required": ["profile"],
            "additionalProperties": False,
        },
    },
]


def _tool_reserve(_args):
    warnings = []
    return tp.build_reserve_pool(warnings)


def _tool_search(args):
    terms = args["terms"]
    warnings = []
    reserve = tp.build_reserve_pool(warnings)
    if args.get("cv"):
        with tempfile.TemporaryDirectory() as cache_dir:
            tp.cv_index_reserve(reserve, cache_dir=cache_dir)
            return tp.search_reserve(reserve, terms, cv_cache_dir=cache_dir)
    return tp.search_reserve(reserve, terms)


def _tool_dedup(_args):
    return tp.find_duplicates()


def _tool_reopen(args):
    request = args["request"]
    mapping = request.get("declination_reason_mapping")
    result, exit_code = tp.run_reopen(request, mapping=mapping, top=args.get("top", 20))
    if exit_code == 2:
        # ошибка валидации входа -> isError: true, а не встроенный blocked-результат (SDD C08 §8.3)
        raise ValueError(result["warnings"][0]["message"])
    return result


def _tool_jobs_match(args):
    profile = args["profile"]
    if not js.validate_profile(profile):
        raise ValueError("Некорректный profile: см. описание схемы potok_jobs_match")
    jobs = js.fetch_jobs_constructor(js.OPEN_BASE_URL, js.CONSTRUCTOR_ID)
    result = js.match_jobs(jobs, profile, top=args.get("top", 10))
    jobs_by_id = {job["id"]: job for job in jobs}
    for matched in result["jobs"]:
        matched["gaps"], matched["unknown_fields"] = js.compute_gaps(jobs_by_id[matched["id"]], profile)
    return result


TOOL_HANDLERS = {
    "potok_reserve": _tool_reserve,
    "potok_search": _tool_search,
    "potok_dedup": _tool_dedup,
    "potok_reopen": _tool_reopen,
    "potok_jobs_match": _tool_jobs_match,
}


def _log(*parts):
    print(*parts, file=sys.stderr, flush=True)


def _reply(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle_message(msg):
    """Обработать одно распарсенное JSON-RPC сообщение. Возвращает dict-ответ или None (notification)."""
    method = msg.get("method")
    msg_id = msg.get("id")
    is_notification = "id" not in msg

    if method == "initialize":
        return _reply(msg_id, {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO})
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _reply(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return _reply(msg_id, {"content": [{"type": "text", "text": f"неизвестный инструмент: {name}"}], "isError": True})
        try:
            result = handler(arguments)
            text = json.dumps(result, ensure_ascii=False, indent=2)
            return _reply(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as e:  # инструмент не должен ронять сервер
            _log(f"tools/call {name} failed: {e}")
            return _reply(msg_id, {"content": [{"type": "text", "text": str(e)}], "isError": True})

    if is_notification:
        return None
    return _error(msg_id, -32601, "method not found")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            _log("invalid JSON on stdin, ignored")
            continue
        try:
            response = handle_message(msg)
        except Exception as e:
            _log(f"unhandled error: {e}")
            response = _error(msg.get("id"), -32601, "method not found")
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
