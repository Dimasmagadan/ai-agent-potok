# SDD: C10 - устанавливаемый MCP для Claude Desktop

Дата постановки: 2026-09-04. Заказчик: Дмитрий. Исполнитель: AI-агент.

## 1. Контекст и проблема

В проекте есть рабочий stdio MCP-сервер `scripts/mcp_server.py` с пятью
инструментами. Протокол и обработчики покрыты тестами, ручной stdio-прогон
против mock API проходит.

Подключение сейчас требует вручную открыть `claude_desktop_config.json`,
вписать абсолютный путь к репозиторию и шесть переменных окружения, затем
отдельно запустить mock API. Жюри Potok AI Agent Challenge скорее HR, чем
разработчики: `git clone && make demo` они не запустят. Конкурсный критерий
«проект можно подключить и запустить без участия автора» закрыт только для
технических пользователей.

## 2. Цель

Поставлять локальный MCP-сервер как Claude Desktop Extension (`.mcpb`):

1. пользователь скачивает один файл из GitHub Release;
2. открывает его двойным кликом и нажимает `Install`;
3. по умолчанию получает демо-режим на синтетических fixtures без токена,
   клонирования репозитория и отдельного процесса;
4. при необходимости выключает демо-режим и вводит настройки реального тенанта
   через UI Claude Desktop, не редактируя JSON.

Результат релизится как `potok-recruiting-agent.mcpb` в `v1.1.0`.

## 3. Не входит в C10

- Telegram-бот и его деплой.
- Удалённый HTTP/SSE MCP-сервер, OAuth, Connectors Directory.
- Изменение алгоритмов C06-C09 и контрактов пяти MCP-инструментов.
- Запись данных в «Поток».
- Подпись пакета сертификатом. Для конкурсного MVP допустим unsigned MCPB.
- Windows. Заявляется только macOS.
- Отдельный ручной upload asset после выпуска тега: GitHub Actions собирает и
  прикладывает пакет автоматически.

## 4. Порядок работ

Работа идёт в три фазы. Каждая следующая начинается только после приёмки
предыдущей.

### Фаза 0. Спайк установки

Цель: убедиться, что Claude Desktop на чистом macOS-профиле вообще запускает
Python-расширение. Это главный риск проекта, и его надо снять до написания
сборки и тестов.

1. Собрать вручную минимальный `.mcpb`: `manifest.json`, `scripts/*.py`,
   `fixtures/*.json`, черновой `mcpb_entry.py` из §5.4.
2. Установить на втором macOS-пользователе или чистом профиле, где нет
   репозитория, Homebrew и Xcode CLT в PATH.
3. Записать в этот документ (§10): версия Claude Desktop, что показал клиент
   при установке unsigned-пакета, каким `python3` запустился сервер,
   прошёл ли запрос §6.1.

Известные ловушки:

- GUI-приложения на macOS видят PATH без Homebrew. `command: python3`
  резолвится в `/usr/bin/python3`, который без Xcode CLT показывает диалог
  «установить developer tools» вместо запуска.
- Unsigned-пакет может требовать отдельную галку в
  `Settings -> Extensions -> Advanced settings`.

Решение по итогам спайка:

- `python3` запускается: фаза 1 идёт с `server.type: python`.
- `python3` не запускается: фаза 1 идёт с `server.type: uv`, чтобы клиент
  управлял Python сам. Серверная логика от этого не меняется.
- Не запускается ни так, ни так: C10 закрывается, README оставляет
  manual JSON как единственный путь.

### Фаза 1. Реализация

`mcpb_entry.py`, `manifest.json`, `make mcpb`, тесты §8.1, README и
release notes.

### Фаза 2. Релиз

Пересобрать пакет, повторить ручную проверку §8.2 на чистом профиле, выпустить
`v1.1.0` с приложенным `.mcpb`.

## 5. Решение

### 5.1 Формат доставки

Открытый формат MCP Bundle и официальный CLI `@anthropic-ai/mcpb`, версия
закреплена в `Makefile`. Manifest обязан проходить `mcpb validate`.

Требования к машине пользователя: macOS и Python 3.9+ либо `uv`, по итогам
фазы 0. Внешних Python-зависимостей нет, как и у остального проекта.

### 5.2 Состав пакета

`make mcpb` копирует в свой staging-каталог только перечисленные файлы. Список
копирования и есть allowlist, отдельного файла-списка нет.

```text
manifest.json
LICENSE
scripts/mcpb_entry.py
scripts/mcp_server.py
scripts/talent_pool.py
scripts/job_seeker.py
scripts/mock_server.py
fixtures/*.json
```

В пакет не попадают `.env`, `.claude/`, `.cv_cache/`, `.cv_mock_fallback.json`,
тесты, Git-метаданные, research и Telegram-код, потому что их нет в списке
копирования. После сборки `make mcpb` печатает `unzip -l` архива и падает,
если в нём есть `.env`, `tg_bot`, `research` или `test_`.

### 5.3 Manifest

- имя `potok-talent-pool`, display name `Potok Recruiting Agent`;
- версия `1.1.0`, совпадает с `SERVER_INFO` в `mcp_server.py` и тегом релиза;
- entry point `scripts/mcpb_entry.py`, `server.type` по итогам фазы 0;
- `tools_generated: true`. Список инструментов клиент получает через
  `tools/list`, статического дубля схем в manifest нет;
- MIT license, repository, homepage на GitHub Pages;
- `compatibility.platforms: ["darwin"]`, Python `>=3.9`;
- `user_config` из §5.5.

Описание расширения говорит, что оно read-only и что demo fixtures
синтетические. Нельзя называть демо-результат данными реальной компании.

### 5.4 Точка входа и демо-режим

`scripts/mcpb_entry.py` делает только bootstrap:

1. читает `POTOK_DEMO_MODE`;
2. при `true` поднимает `mock_server.Handler` через `HTTPServer` на
   `("127.0.0.1", 0)` в daemon thread;
3. выставляет для текущего процесса `POTOK_BASE_URL`, `POTOK_API_TOKEN`,
   `POTOK_API_V2_BASE_URL`, `POTOK_OPEN_BASE_URL` и `POTOK_CONSTRUCTOR_ID`
   на адрес этого сервера;
4. только после этого импортирует `mcp_server`, потому что `talent_pool.py`
   и `job_seeker.py` читают env при импорте;
5. вызывает `mcp_server.main()`.

Mock слушает только loopback на случайном порту, поэтому не конфликтует с
`make demo` на `8765` и с другими процессами. Daemon thread умирает вместе с
процессом, когда клиент закрывает stdin.

В real mode bootstrap ничего не поднимает и сразу вызывает `mcp_server.main()`
с окружением, которое передал Claude Desktop.

CV-кэш `potok_search` уже живёт в `tempfile.TemporaryDirectory`, поэтому после
удаления расширения на диске ничего не остаётся.

### 5.5 Настройки пользователя

| Ключ | Тип | По умолчанию | Env |
|---|---|---|---|
| `demo_mode` | boolean | `true` | `POTOK_DEMO_MODE` |
| `potok_base_url` | string | пусто | `POTOK_BASE_URL` |
| `potok_api_token` | string, `sensitive: true` | пусто | `POTOK_API_TOKEN` |
| `potok_api_v2_base_url` | string | пусто | `POTOK_API_V2_BASE_URL` |
| `potok_open_base_url` | string | пусто | `POTOK_OPEN_BASE_URL` |
| `potok_constructor_id` | string | пусто | `POTOK_CONSTRUCTOR_ID` |

В демо-режиме остальные поля игнорируются. В real mode `mcpb_entry.py` до
вызова `mcp_server.main()` проверяет `POTOK_BASE_URL` и `POTOK_API_TOKEN`;
если пусто, пишет в stderr понятную ошибку и завершается с кодом 1.

`sensitive: true` это штатная функция manifest: значение хранится в keychain
ОС и передаётся серверу как env. Отдельной проверки не требуется.

### 5.6 Совместимость с другими MCP-клиентами

`.mcpb` только для Claude Desktop. OpenCode, Cursor и другие stdio-клиенты
продолжают запускать `scripts/mcp_server.py` через свою конфигурацию. C10 этот
путь не трогает.

## 6. Пользовательский поток

### 6.1 Демо без токена

1. Скачать `potok-recruiting-agent.mcpb` из последнего GitHub Release.
2. Открыть файл двойным кликом либо через
   `Settings -> Extensions -> Advanced settings -> Install Extension`.
3. Оставить «Демо-режим» включённым и нажать `Install`.
4. В новом чате спросить:

   > Нужен backend-разработчик со знанием Python и FastAPI. Найди кандидатов в
   > кадровом резерве, проверь текст резюме и объясни каждое совпадение.

5. Claude вызывает `potok_search`; результат содержит кандидатов и evidence.
6. Вторым запросом «Найди дубли кандидатов во всей базе» проверить
   `potok_dedup`.

### 6.2 Реальный тенант

1. В настройках расширения выключить demo mode.
2. Указать v3 URL и токен; при необходимости v2, open и constructor.
3. Перезапустить расширение.
4. Выполнить `potok_reserve` и убедиться, что результат относится к тенанту.

### 6.3 Удаление

Расширение удаляется через `Settings -> Extensions`. Фонового процесса,
открытого порта и файлов вне каталога расширения не остаётся.

## 7. Сборка и релиз

`make mcpb`:

1. очищает свой staging-каталог `dist/mcpb/`;
2. копирует список §5.2;
3. `npx @anthropic-ai/mcpb@<версия> validate` manifest;
4. `npx @anthropic-ai/mcpb@<версия> pack dist/mcpb dist/potok-recruiting-agent.mcpb`;
5. печатает `unzip -l` и падает на запрещённых именах из §5.2.

`dist/` и `*.mcpb` в `.gitignore`. GitHub Actions на теге `v*` запускает
`make test`, затем `make mcpb` и прикладывает пакет к созданному GitHub Release.

README начинает с трёх шагов §6.1, manual JSON уходит ниже как fallback для
клиентов без MCPB. Release notes `v1.1.0` содержат те же три шага и честные
требования к машине по итогам фазы 0.

## 8. Тесты

### 8.1 Автоматические, `scripts/test_mcpb_entry.py`

Один subprocess-тест запускает `scripts/mcpb_entry.py` с
`POTOK_DEMO_MODE=true` и очищенными Potok env, отправляет по stdio
`initialize`, `tools/list`, `potok_search`, `potok_dedup`, закрывает stdin и
проверяет:

- `initialize` отвечает `protocolVersion` `2025-06-18` и версией
  `SERVER_INFO`;
- `tools/list` возвращает пять текущих инструментов;
- `potok_search` и `potok_dedup` возвращают непустой результат без `isError`;
- процесс завершается сам после закрытия stdin.

Второй тест запускает entry с `POTOK_DEMO_MODE=false` без URL и токена и
ожидает код выхода 1 и текст ошибки в stderr.

Третий тест читает `manifest.json` и сравнивает `version` с
`mcp_server.SERVER_INFO["version"]`.

Тест добавляется в `make test`. Существующие 155 тестов не меняются.

### 8.2 Ручные, один раз перед релизом

На чистом macOS-профиле без репозитория: установка двойным кликом, запросы
§6.1, uninstall без зависшего процесса. Результат записывается в §10 с датой и
версией клиента.

## 9. Критерии приёмки

- [ ] Фаза 0 пройдена, §10 заполнен, выбран `server.type`.
- [ ] Пользователь получает один `.mcpb` из публичного GitHub Release.
- [ ] Установка: открыть файл, оставить demo mode, нажать `Install`. JSON не
  редактируется.
- [ ] Демо работает без Git clone, токена, `.env` и отдельного mock-процесса.
- [ ] Тесты §8.1 зелёные, `make test` включает их.
- [ ] `make mcpb` проходит валидацию и проверку состава архива.
- [ ] Прежние 155 тестов зелёные, прямой stdio-путь не изменился.
- [ ] README и release notes начинаются с one-click пути.

## 10. Журнал спайка

Заполняется в фазе 0.

| Дата | Claude Desktop | server.type | Unsigned install | python3 | §6.1 прошёл |
|---|---|---|---|---|---|
| | | | | | |
