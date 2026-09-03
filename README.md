# Talent Pool: поиск по кадровому резерву + дедупликация + расширения («Поток»)

Read-only чат-скилл для LLM поверх открытого API «Потока»
(events.potok.io/ai_agent_hr_contest). Что и почему — [`SDD-C06-TALENT-POOL.md`](SDD-C06-TALENT-POOL.md)
(резерв/поиск/дедуп), [`SDD-C07-REOPEN-CANDIDATES.md`](SDD-C07-REOPEN-CANDIDATES.md)
(пересмотр прошлых кандидатов), [`SDD-C08-DELIVERY-EXTENSIONS.md`](SDD-C08-DELIVERY-EXTENSIONS.md)
(полнотекст резюме, режим соискателя, MCP-сервер, Telegram-бот). Триггер и
инструкции для LLM — [`SKILL.md`](SKILL.md).

1. Рекрутер описывает свободным текстом, кто нужен, — скилл ищет подходящих
   кандидатов в кадровом резерве (без активной вакансии, не нанятые), с
   контекстным для сферы вакансии LLM-расширением запроса синонимами,
   evidence-цитатами и, по запросу, полнотекстовым поиском по резюме.
2. Скилл находит дубли кандидатов в базе (точное совпадение телефона/email).
3. Скилл пересматривает прошлых (неактивных) кандидатов вакансии, когда
   условия найма изменились (вилка, график, локация, минимальный опыт,
   профиль) — с проверяемым evidence, а не автоматическим решением.
4. Соискатель свободным текстом описывает себя — скилл подбирает открытые
   вакансии компании через открытый Career API (без токена) и даёт ссылку
   для отклика.

Каналы доставки: чат-скилл (Claude Code и совместимые агенты), MCP-сервер
(Claude Desktop / Cursor / любой MCP-клиент) для рекрутёра, Telegram-бот для
соискателя. Никакой записи в «Поток» ни в одном канале: ни тегов, ни
комментариев, ни перемещений по воронке, ни откликов за кандидата.

## Запуск на fixtures (без токена, за 30 секунд)

```bash
python3 scripts/mock_server.py &
export POTOK_BASE_URL=http://localhost:8765
export POTOK_API_TOKEN=demo
export POTOK_API_V2_BASE_URL=http://localhost:8765        # C07 reopen
export POTOK_OPEN_BASE_URL=http://localhost:8765/open      # поток B
export POTOK_CONSTRUCTOR_ID=1                               # поток B

python3 scripts/talent_pool.py reserve                    # 14 кандидатов резерва
python3 scripts/talent_pool.py dedup                      # 2 пары дублей (phone, email)
python3 scripts/talent_pool.py reserve > /tmp/reserve.json
python3 scripts/talent_pool.py search '[{"term":"питон","kind":"original"},{"term":"python","kind":"synonym"}]' --reserve-file /tmp/reserve.json

python3 scripts/talent_pool.py cv-index --reserve-file /tmp/reserve.json --cache-dir /tmp/cv_cache --limit 3   # 2 ok, 1 no_cv
python3 scripts/talent_pool.py search '[{"term":"fastapi","kind":"original"}]' --reserve-file /tmp/reserve.json --cv-cache-dir /tmp/cv_cache

python3 scripts/job_seeker.py jobs-list
python3 scripts/job_seeker.py jobs-match '{"terms":[{"term":"python","kind":"original"},{"term":"django","kind":"original"}],"filters":{"schedule":"remote"}}'

python3 scripts/talent_pool.py reopen '{"target_job_id":202,"source_job_id":201,"source_represents_previous_criteria":true,"applicant_salary_currency":"RUR","context_terms":{"schedule":["remote","удалённо"]},"declination_reason_mapping":{"experience_minimum":[8],"salary":[12,19]}}'

make test                                                  # 79 тестов ядра (stdlib unittest)
```

`reserve`, `search` и `dedup` возвращают уже собранную часть данных, если API
перестаёт отвечать во время обхода страниц. В этом случае предупреждение
`частичный результат` выводится в stderr: не используйте такую выдачу как
полный отчёт по базе. `reopen` использует собственный, более строгий контракт
частичных результатов (`status: partial|blocked` в JSON, см. ниже).

Фикстуры — `fixtures/*.json` (обезличенные, придуманные данные): 18
кандидатов (10 исходных + 8 для демонстрации `reopen`), 2 активных на
вакансии, 2 нанятых финалиста (в т.ч. один исключённый из резерва только
`reopen`-сценарием), 1 финалист с отменённым наймом (возвращается в резерв),
2 пары дублей, 2 кандидата с резюме (`.docx`, генерируется на лету) для
демонстрации потока A, 3 открытые вакансии для потока B, архивная и текущая
версия вакансии (201/202) с 8 прошлыми кандидатами для `reopen`.

## Команды

### Резерв, поиск, дедуп (C06)

```bash
python3 scripts/talent_pool.py reserve
python3 scripts/talent_pool.py dedup
python3 scripts/talent_pool.py search '<TERMS_JSON>' --reserve-file /tmp/reserve.json [--top 10] [--cv-cache-dir .cv_cache]
```

Без `--cv-cache-dir` выдача `search` побайтово совпадает с версией без потока A.

### Полнотекст резюме (поток A)

```bash
python3 scripts/talent_pool.py cv-index --reserve-file /tmp/reserve.json [--cache-dir .cv_cache] [--limit N]
```

Скачивает `cv_original` каждого кандидата резерва (сначала без токена, при
401/403 — с `Authorization: Bearer`), извлекает текст (`.docx` — stdlib
`zipfile`+`xml.etree`, `.txt` — utf-8/cp1251, `.pdf` — только если установлен
опциональный `pdfminer.six`) и кладёт в локальный JSON-кэш, один файл на
кандидата. Идемпотентно: уже проиндексированный (`status: ok`, тот же
`source_url`) кандидат не перекачивается повторно.

> ⚠️ **CV-кэш содержит персональные данные** (полный текст резюме). Каталог
> кэша (`.cv_cache/` по умолчанию, см. `CV_CACHE_DIR`) в `.gitignore` — не
> коммитьте и не пересылайте его.

### Режим соискателя (поток B)

```bash
python3 scripts/job_seeker.py jobs-list [--fallback-v3]
python3 scripts/job_seeker.py jobs-match '<PROFILE_JSON>' [--jobs-file /tmp/jobs.json] [--top 10] [--fallback-v3]
```

Только открытый Career API (`POTOK_OPEN_BASE_URL`/`POTOK_CONSTRUCTOR_ID`), без
токена. `--fallback-v3` — авторизованный `GET /api/v3/jobs`, если тенант не
отдаёт JSON конструктора карьерной страницы (тогда результат помечен
`"source": "v3_fallback"` и требует токен). Отклик не отправляется никогда —
только ссылка `apply_url` на страницу вакансии.

### Пересмотр прошлых кандидатов (C07 `reopen`)

```bash
python3 scripts/talent_pool.py reopen '<REQUEST_JSON>' --top 20
```

Полный контракт запроса/ответа, модель уверенности, коды `warnings` и правила
частичных результатов — [`SDD-C07-REOPEN-CANDIDATES.md`](SDD-C07-REOPEN-CANDIDATES.md)
§4/§8. Только GET-запросы; `exit code`: `0` — `ok`/безопасный `partial`, `2` —
ошибка входной валидации, `3` — `blocked` (небезопасно неполные данные). При
любом из кодов stdout всё равно содержит JSON-результат.

## Каналы доставки

### MCP-сервер (рекрутёр)

`scripts/mcp_server.py` — stdio JSON-RPC 2.0 сервер, инструменты
`potok_reserve`, `potok_search`, `potok_dedup`, `potok_reopen`,
`potok_jobs_match`. Подключение в Claude Desktop (`claude_desktop_config.json`)
или Cursor:

```json
{
  "mcpServers": {
    "potok-talent-pool": {
      "command": "python3",
      "args": ["/abs/path/ai-agent-potok/scripts/mcp_server.py"],
      "env": {
        "POTOK_BASE_URL": "https://demo.app.potok.io/api/v3",
        "POTOK_API_TOKEN": "...",
        "POTOK_API_V2_BASE_URL": "https://demo.app.potok.io/api/v2",
        "POTOK_OPEN_BASE_URL": "https://demo.potok.io/open",
        "POTOK_CONSTRUCTOR_ID": "1"
      }
    }
  }
}
```

Проверенные клиенты: Claude Desktop, Claude Code (любой MCP-клиент со
`stdio`-транспортом и `protocolVersion: 2025-06-18`; HTTP/SSE не
поддерживается). Ручной прогон без клиента, против mock-сервера:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"potok_reserve","arguments":{}}}' \
  | POTOK_BASE_URL=http://localhost:8765 POTOK_API_TOKEN=demo python3 scripts/mcp_server.py
```

### Telegram-бот (соискатель, опционально)

```bash
export TELEGRAM_BOT_TOKEN=...     # от @BotFather
export ANTHROPIC_API_KEY=...
export POTOK_OPEN_BASE_URL=... POTOK_CONSTRUCTOR_ID=...
python3 scripts/tg_bot.py
```

Long polling, публичный IP/домен/webhook не нужны — работает с любой машины
с доступом в интернет. Бот stateless (история не хранится), логирует только
`chat_id`/timestamp/статус (без текста сообщений), лимит 1 запрос/5 секунд на
`chat_id`, сообщения длиннее 1000 символов отклоняются вежливо. Единственный
компонент проекта, требующий постоянно работающего процесса — опционален,
демо остальных потоков не зависит от бота.

## Устройство

- `scripts/talent_pool.py` — резерв/дедуп/поиск (+CV-полнотекст) + `reopen`,
  только stdlib (`urllib`). Пагинация (страничная и курсорная) и
  429-backoff — внутри.
- `scripts/job_seeker.py` — режим соискателя, импортирует HTTP-обвязку и
  матчер термов из `talent_pool.py` (без копирования).
- `scripts/mcp_server.py` — stdio MCP-сервер, импортирует функции напрямую
  (без subprocess).
- `scripts/tg_bot.py` — Telegram-бот, raw HTTP к Anthropic Messages API (без
  SDK) для извлечения профиля соискателя.
- `scripts/mock_server.py` — мини-HTTP-сервер на `http.server`, отдаёт
  `fixtures/*.json` (и docx-резюме на лету) в форматах ответа v3/v2/open API.
- `scripts/test_*.py` — тесты ядра на stdlib `unittest`, без сети.
- `SKILL.md` — инструкция для LLM: сценарии, как расширять запрос синонимами,
  в каком формате отвечать пользователю.

## Ограничения (осознанно не в MVP)

- Матчинг резерва — по `title`/`tags` и, по явному запросу, полнотексту
  резюме (`.docx`/`.txt` полноценно, `.pdf` — при установленном
  `pdfminer.six`, `.doc`/`.rtf`/сканы — нет).
- Дедуп — точное совпадение нормализованного телефона/email, без fuzzy по
  ФИО (Иван/Ваня, опечатки).
- Резерв не фильтруется по причине отказа отклонённых кандидатов.
- `reopen` не восстанавливает историю условий вакансии автоматически: без
  явных прежних условий или подтверждённой референсной вакансии скилл прямо
  просит уточнение, а не придумывает изменение.
- Формат ответа `/open/constructor/:id` не подтверждён discovery на живом
  тенанте — реализован по минимальному правдоподобному формату (см.
  `fixtures/open_jobs.json`); при отличии реального ответа скорректировать
  парсинг в `job_seeker._parse_open_job` (fallback — `--fallback-v3`).
- Нет семантического поиска/embeddings, нет записи в «Поток» ни в одном
  канале (write-back отсутствует полностью).

Полный разбор — §5 [`SDD-C06-TALENT-POOL.md`](SDD-C06-TALENT-POOL.md), §9/§11
[`SDD-C07-REOPEN-CANDIDATES.md`](SDD-C07-REOPEN-CANDIDATES.md), §15
[`SDD-C08-DELIVERY-EXTENSIONS.md`](SDD-C08-DELIVERY-EXTENSIONS.md).

## Запуск на реальном тенанте

1. Скопировать `.env.example` в `.env`, вписать `POTOK_API_TOKEN` (настройки
   компании → «Генерация токенов») и `POTOK_BASE_URL` (по умолчанию —
   песочница `https://demo.app.potok.io/api/v3`). Для `reopen` при
   нестандартном `POTOK_BASE_URL` дополнительно задать `POTOK_API_V2_BASE_URL`.
   Для потока B — `POTOK_OPEN_BASE_URL`/`POTOK_CONSTRUCTOR_ID`. Для бота —
   `TELEGRAM_BOT_TOKEN`/`ANTHROPIC_API_KEY`.
2. Экспортировать переменные из `.env` в окружение (`set -a; source .env; set +a`
   в bash/zsh) и запускать скрипты как выше, без mock-сервера.

## Проверка и релиз

```bash
make test
make demo
```

GitHub Actions запускает тесты при каждом push и pull request. Это CLI-скилл,
поэтому серверный deploy не требуется: релиз — публичный Git-тег с исходным
кодом, fixtures и этой инструкцией.

## Лицензия

[MIT](LICENSE).
