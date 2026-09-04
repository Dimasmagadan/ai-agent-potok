# AI-агент для рекрутмента в «Потоке»

[![Tests](https://github.com/Dimasmagadan/ai-agent-potok/actions/workflows/test.yml/badge.svg)](https://github.com/Dimasmagadan/ai-agent-potok/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![GitHub Pages](https://img.shields.io/badge/demo-GitHub%20Pages-186978)](https://dimasmagadan.github.io/ai-agent-potok/)

Read-only AI-агент поверх API [«Потока»](https://potok.io) для рекрутёров,
соискателей и сотрудников компании. Помогает находить кандидатов и вакансии,
не меняя данные в ATS: не ставит теги, не пишет комментарии, не перемещает по
воронке и не откликается за человека.

Проект создан для [Potok AI Agent Challenge](https://events.potok.io/ai_agent_hr_contest)
и работает как чат-скилл, MCP-сервер или Telegram-бот.

**Витрина проекта:** <https://dimasmagadan.github.io/ai-agent-potok/>

## Установка в Claude Desktop

1. Скачайте `potok-recruiting-agent.mcpb` из последнего GitHub Release.
2. Откройте файл двойным кликом либо выберите `Settings -> Extensions -> Advanced settings -> Install Extension`.
3. Оставьте «Демо-режим» включённым и нажмите `Install`.

Демо самодостаточно: используются синтетические fixtures, поэтому не нужны
токен «Потока», clone репозитория или отдельный mock API. Агент только читает
данные, а демо-результаты не относятся к реальной компании.

Для реального тенанта выключите «Демо-режим» в настройках расширения и введите
v3 URL и API-токен. URL v2, Career API и ID конструктора нужны только
соответствующим инструментам. Требования MCPB: macOS и Python 3.9+ (`python3`,
доступный Claude Desktop). Unsigned MVP при необходимости устанавливается через
`Settings -> Extensions -> Advanced settings`.

## Что умеет

| Сценарий | Для кого | Результат |
|---|---|---|
| **Поиск в кадровом резерве** | Рекрутёр | Подходящие кандидаты без активной вакансии и без найма: свободный запрос, синонимы, ранжирование и evidence-цитаты. |
| **Дедупликация базы** | Рекрутёр | Пары кандидатов с одинаковым нормализованным телефоном или email. |
| **Пересмотр прошлых кандидатов** | Рекрутёр | Кандидаты, к которым стоит вернуться после изменения вилки, графика, локации, опыта или профиля. Решение остаётся за рекрутёром, все сигналы проверяемы. |
| **Подбор вакансий** | Соискатель | Открытые вакансии из Career API, подобранные по текстовому описанию профиля, с прямой ссылкой для отклика. |
| **Внутренняя мобильность** | Сотрудник | Подходящие, включая неопубликованные, вакансии компании и отчёт о пробелах профиля для конкретной роли. |

## Каналы

- **Чат-скилл**: Claude Code и совместимые LLM-агенты. Инструкции и примеры запросов: [`SKILL.md`](SKILL.md).
- **MCP-сервер**: Claude Desktop, Cursor и любой MCP-клиент со `stdio`-транспортом; инструменты для поиска, резерва, дублей, пересмотра и вакансий.
- **Telegram-бот**: внешний режим для соискателей и защищённый allowlist-режим для внутренней мобильности сотрудников.

## Быстрый старт для разработки

Никаких зависимостей кроме Python 3: fixtures позволяют проверить все основные
сценарии локально, без токена и аккаунта «Потока».

```bash
git clone https://github.com/Dimasmagadan/ai-agent-potok.git
cd ai-agent-potok
make test
make demo
```

`make demo` поднимает локальный mock API и последовательно показывает резерв,
дубли, поиск по резюме, подбор вакансий и пересмотр кандидатов. Для ручного
запуска и подключения к реальному тенанту используйте разделы ниже.

## Возможности и границы

- Только чтение данных: агент безопасен для запуска в повседневном рекрутменте.
- Никаких внешних Python-зависимостей для основного функционала: HTTP,
  пагинация, 429-backoff и тесты реализованы на стандартной библиотеке.
- Полнотекстовый поиск по `.docx` и `.txt` резюме; `.pdf` поддерживается при
  установке `pdfminer.six`.
- Дедупликация намеренно строгая: только телефон/email, без сомнительного fuzzy
  сопоставления ФИО.

Детали решений: [`SDD-C06-TALENT-POOL.md`](SDD-C06-TALENT-POOL.md) (резерв,
поиск, дубли), [`SDD-C07-REOPEN-CANDIDATES.md`](SDD-C07-REOPEN-CANDIDATES.md)
(пересмотр), [`SDD-C08-DELIVERY-EXTENSIONS.md`](SDD-C08-DELIVERY-EXTENSIONS.md)
(CV, соискатель, MCP, Telegram) и
[`SDD-C09-INTERNAL-MOBILITY.md`](SDD-C09-INTERNAL-MOBILITY.md) (внутренняя
мобильность).

## Запуск на fixtures (без токена, за 30 секунд)

```bash
python3 scripts/mock_server.py &
export POTOK_BASE_URL=http://localhost:8765
export POTOK_API_TOKEN=demo
export POTOK_API_V2_BASE_URL=http://localhost:8765        # C07 reopen
export POTOK_OPEN_BASE_URL=http://localhost:8765/open      # поток B
export POTOK_CONSTRUCTOR_ID=1                               # поток B

python3 scripts/talent_pool.py reserve                    # 18 кандидатов резерва
python3 scripts/talent_pool.py dedup                      # 2 пары дублей (phone, email)
python3 scripts/talent_pool.py reserve > /tmp/reserve.json
python3 scripts/talent_pool.py search '[{"term":"питон","kind":"original"},{"term":"python","kind":"synonym"}]' --reserve-file /tmp/reserve.json

python3 scripts/talent_pool.py cv-index --reserve-file /tmp/reserve.json --cache-dir /tmp/cv_cache --limit 3   # 2 ok, 1 no_cv
python3 scripts/talent_pool.py search '[{"term":"fastapi","kind":"original"}]' --reserve-file /tmp/reserve.json --cv-cache-dir /tmp/cv_cache

python3 scripts/job_seeker.py jobs-list
python3 scripts/job_seeker.py jobs-match '{"terms":[{"term":"python","kind":"original"},{"term":"django","kind":"original"}],"filters":{"schedule":"remote"}}'

python3 scripts/talent_pool.py reopen '{"target_job_id":202,"source_job_id":201,"previous_criteria":{"salary_to":280000,"currency_type":"RUR","schedule_type":"fullDay","experience_minimum_years":3,"city":"1","role_terms":["python","backend"],"profile_terms_any":["django"]},"current_criteria":{"salary_to":350000,"currency_type":"RUR","schedule_type":"remote","experience_minimum_years":2,"city":"2","role_terms":["python","backend"],"profile_terms_any":["django","fastapi"]},"applicant_salary_currency":"RUR","context_terms":{"schedule":["удалённо"]},"declination_reason_mapping":{"experience_minimum":[8]}}'

make test                                                  # 160 тестов ядра (stdlib unittest)
```

`reserve`, `search` и `dedup` возвращают уже собранную часть данных, если API
перестаёт отвечать во время обхода страниц. В этом случае предупреждение
`частичный результат` выводится в stderr: не используйте такую выдачу как
полный отчёт по базе. `reopen` использует собственный, более строгий контракт
частичных результатов (`status: partial|blocked` в JSON, см. ниже).

Фикстуры — `fixtures/*.json` (обезличенные, придуманные данные): 22
кандидата (14 базовых + 8 для демонстрации `reopen`), 2 активных на
вакансии, 2 нанятых финалиста (в т.ч. один исключённый из резерва только
`reopen`-сценарием), 1 финалист с отменённым наймом (возвращается в резерв),
2 пары дублей, 2 кандидата с резюме (`.docx`, генерируется на лету) для
демонстрации потока A, 5 открытых вакансий для потока B (разработка, аналитика,
DevOps, продажи), архивная и текущая версия вакансии (201/202) с 8 прошлыми
кандидатами для `reopen`. Полный `reopen`-пример показывает разблокированные
сигналы зарплаты, локации, нового профиля, подтверждённой причины отказа и
контекста комментария, а также исключение уже нанятого и активного кандидатов.

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
401/403 — с query-параметром `?token=<POTOK_API_TOKEN>`), извлекает текст (`.docx` — stdlib
`zipfile`+`xml.etree`, `.txt` — utf-8/cp1251, `.pdf` — только если установлен
опциональный `pdfminer.six`) и кладёт в локальный JSON-кэш, один файл на
кандидата. Идемпотентно: уже проиндексированный (`status: ok`, тот же
`source_url`) кандидат не перекачивается повторно.

> ⚠️ **CV-кэш содержит персональные данные** (полный текст резюме). Каталог
> кэша (`.cv_cache/` по умолчанию, см. `CV_CACHE_DIR`) в `.gitignore` — не
> коммитьте и не пересылайте его.

> `CV_MOCK_FALLBACK_FILE` (путь к JSON
> `{"<applicant_id>": "текст резюме"}`) подставляет заранее известный текст
> вместо реального скачивания только для явно перечисленных кандидатов — не
> общий фолбэк на любую сетевую ошибку. Использование фиксируется:
> предупреждение в stderr и `"mock_fallback": true` в записи кэша, статус
> при этом `ok` (текст пригоден для поиска).

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

### Внутренняя мобильность (C09, `jobs-gaps`)

```bash
python3 scripts/job_seeker.py jobs-gaps '<PROFILE_JSON>' --job-id 9 --fallback-v3 --internal
```

Отчёт о пробелах профиля относительно конкретной вакансии — симметрично
`jobs-match`. `PROFILE_JSON` — тот же контракт `terms`/`filters`. Полный
алгоритм и формат ответа — [`SDD-C09-INTERNAL-MOBILITY.md`](SDD-C09-INTERNAL-MOBILITY.md)
§3. Salary-gap срабатывает только когда ожидание профиля выше максимума
вилки вакансии; поле, не заданное ни у вакансии, ни у профиля — попадает в
`unknown_fields`, а не в `gaps`.

> **Discovery 2026-09-03:** `city` в `--fallback-v3` — сырой числовой ID
> (`job.city`), не имя. `GET /api/v3/dictionaries/cities` не подходит (UUID,
> другой формат). `GET /api/v3/business_units` документирует `city: {id,
> name}` в том же числовом формате, но на песочнице `demo.app.potok.io` модуль
> штатного расписания не содержит данных для проверки — резолв не
> подтверждён. `city` в `--fallback-v3` поэтому всегда `None`/`filter_unknown`,
> а не тихое несовпадение имени с числом (SDD-C09 §2 п.3, §6).

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
`potok_jobs_match`. Для Claude Desktop используйте MCP Bundle из раздела
«Установка в Claude Desktop» выше. Ручная конфигурация ниже остаётся fallback
для Cursor и клиентов без MCPB:

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

#### Внутренний режим (C09, `JOB_SEEKER_MODE=internal`)

```bash
export TELEGRAM_BOT_TOKEN=...
export ANTHROPIC_API_KEY=...
export POTOK_BASE_URL=... POTOK_API_TOKEN=...
export JOB_SEEKER_MODE=internal
export JOB_SEEKER_ALLOWED_USER_IDS=123456789,987654321
# export JOB_SEEKER_INCLUDE_PRIVATE=1   # см. предупреждение ниже
python3 scripts/tg_bot.py
```

> **⚠️ Этот бот — для сотрудников компании, не для внешней аудитории.**
> `JOB_SEEKER_ALLOWED_USER_IDS` обязателен: укажите через запятую числовые
> Telegram user ID сотрудников. Бот принимает внутренние запросы только в
> личном чате от этих пользователей; группы и все остальные аккаунты
> отклоняются. Бот
> показывает **все** вакансии компании, включая ещё не опубликованные на
> карьерном сайте, — не публиковать ссылку вовне.

Отличия от внешнего (`external`, поведение не меняется без флага):
источник вакансий — авторизованный `GET /api/v3/jobs` (токен компании живёт
на сервере бота, сотрудникам не виден); второй интент диалога — «чего мне не
хватает для вакансии N» (`jobs-gaps`); минимальная in-memory память
последнего профиля на Telegram user ID (не пишется на диск, живёт до перезаписи или
рестарта — слабее гарантии C08 «ничего не хранится»). Без
`POTOK_API_TOKEN`/`POTOK_BASE_URL` бот отказывается стартовать с понятной
ошибкой. Вакансии `private: true` (конфиденциальный найм, замена действующего
сотрудника) исключены по умолчанию — `JOB_SEEKER_INCLUDE_PRIVATE=1` включает
их обратно, только если компания осознанно это допускает. Полный разбор
рисков — [`SDD-C09-INTERNAL-MOBILITY.md`](SDD-C09-INTERNAL-MOBILITY.md) §8.

## Устройство

- `scripts/talent_pool.py` — резерв/дедуп/поиск (+CV-полнотекст) + `reopen`,
  только stdlib (`urllib`). Пагинация (страничная и курсорная) и
  429-backoff — внутри.
- `scripts/job_seeker.py` — режим соискателя (`jobs-list`/`jobs-match`) и
  внутренней мобильности (`jobs-gaps`, C09), импортирует HTTP-обвязку и
  матчер термов из `talent_pool.py` (без копирования).
- `scripts/mcp_server.py` — stdio MCP-сервер, импортирует функции напрямую
  (без subprocess).
- `scripts/tg_bot.py` — Telegram-бот, raw HTTP к Anthropic Messages API (без
  SDK) для извлечения профиля соискателя/сотрудника; режим переключается
  `JOB_SEEKER_MODE` (`external` по умолчанию, `internal` — C09).
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
- `jobs-gaps` (C09) сравнивает профиль только с `title`+`key_skills`
  вакансии, не с `description` — на реальном тенанте `key_skills` пока пуст,
  term-gaps сводятся к токенам `title` и малоинформативны. LLM-сравнение с
  `description` осознанно отложено до v2 (см. SDD-C09 §7).
- `city` в `--fallback-v3` не резолвится в имя — подтверждённого источника
  для числовых ID не нашлось при discovery 2026-09-03 (см. выше и SDD-C09
  §2 п.3); вакансии всегда дают `filter_unknown`/`unknown_fields` по городу.

Полный разбор — §5 [`SDD-C06-TALENT-POOL.md`](SDD-C06-TALENT-POOL.md), §9/§11
[`SDD-C07-REOPEN-CANDIDATES.md`](SDD-C07-REOPEN-CANDIDATES.md), §15
[`SDD-C08-DELIVERY-EXTENSIONS.md`](SDD-C08-DELIVERY-EXTENSIONS.md), §2/§7/§8
[`SDD-C09-INTERNAL-MOBILITY.md`](SDD-C09-INTERNAL-MOBILITY.md).

## Запуск на реальном тенанте

1. Скопировать `.env.example` в `.env`, вписать `POTOK_API_TOKEN` (настройки
   компании → «Генерация токенов») и `POTOK_BASE_URL` (по умолчанию —
   песочница `https://demo.app.potok.io/api/v3`). Для `reopen` при
   нестандартном `POTOK_BASE_URL` дополнительно задать `POTOK_API_V2_BASE_URL`.
   Для потока B — `POTOK_OPEN_BASE_URL`/`POTOK_CONSTRUCTOR_ID`. Для бота —
   `TELEGRAM_BOT_TOKEN`/`ANTHROPIC_API_KEY`. Для внутреннего режима бота
   (C09) — `JOB_SEEKER_MODE=internal` (плюс `POTOK_API_TOKEN`/`POTOK_BASE_URL`
   выше, без них бот откажется стартовать).
2. Экспортировать переменные из `.env` в окружение (`set -a; source .env; set +a`
   в bash/zsh) и запускать скрипты как выше, без mock-сервера.

## Проверка и релиз

```bash
make test
make demo
make mcpb
```

GitHub Actions запускает тесты при каждом push и pull request. Это CLI-скилл,
поэтому серверный deploy не требуется: релиз — публичный Git-тег с исходным
кодом, fixtures и этой инструкцией. Для `v1.2.0` приложите созданный
`dist/potok-recruiting-agent.mcpb`; текст релиз-нотов лежит в
[`RELEASE-NOTES-v1.2.0.md`](RELEASE-NOTES-v1.2.0.md).

## Лицензия

[MIT](LICENSE).
