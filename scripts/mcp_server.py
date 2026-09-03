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
SERVER_INFO = {"name": "potok-talent-pool", "version": "1.0.0"}

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
        "вакансии и просит пересмотреть прошлые отказы. См. SDD-C07-REOPEN-CANDIDATES.md §4/§8.",
        "inputSchema": {
            "type": "object",
            "properties": {"request": {"type": "object"}, "top": {"type": "integer"}},
            "required": ["request"],
            "additionalProperties": False,
        },
    },
    {
        "name": "potok_jobs_match",
        "description": "Подобрать открытые вакансии компании под профиль соискателя (термы + фильтры), "
        "используя открытый Career API без токена. Используй, когда соискатель или сотрудник спрашивает, "
        "какие вакансии компании ему подходят. Никогда не отправляет отклик — только ссылка apply_url.",
        "inputSchema": {
            "type": "object",
            "properties": {"profile": {"type": "object"}},
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
    jobs = js.fetch_jobs_constructor(js.OPEN_BASE_URL, js.CONSTRUCTOR_ID)
    return js.match_jobs(jobs, args["profile"])


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
