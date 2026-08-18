# Вакансии (Jobs)

API v3: `/api/v3/jobs`

## Состояния вакансий

| state_id | Описание | Группа |
|----------|----------|--------|
| `opened` | Активная, в работе | Активные |
| `paused` | Приостановлена | Активные |
| `canceled` | Отменена | Архив |
| `closed` | Успешно закрыта | Архив |

---

### GET /api/v3/jobs

**Когда использовать:** Получить список вакансий компании. По умолчанию возвращает только активные. Используйте `by_scope=all` для получения архивных.

> ⚠️ **Этот эндпоинт не поддерживает поиск по названию.** Для полнотекстового поиска используйте `GET /api/v3/cursor_paginated/jobs?q=...` (описан ниже). См. также Рецепт 1 в [13-recipes.md](13-recipes.md).

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `by_scope` | string | Нет | `active` (по умолчанию) или `all` |
| `page` | integer | Нет | Номер страницы (с 1) |
| `per_page` | integer | Нет | Записей на странице (по умолчанию 50, макс 100) |

**Ответ 200:**
```json
{
  "data": [{
    "id": 12345,
    "name": "Frontend Developer",
    "state_id": "opened",
    "city": "1",
    "salary_from": 150000,
    "salary_to": 200000,
    "currency_type": "RUR",
    "schedule_type": "fullDay",
    "experience_type": "between1And3",
    "demand": 2,
    "closure_date": "2024-03-15",
    "private": false,
    "archived": false,
    "applicants_count": {"all": 45, "active": 32},
    "stages": [
      {"id": 1001, "name": "Sourced", "serial": 0, "stage_type": "sourced", "active_applicants": 10},
      {"id": 1002, "name": "HR Interview", "serial": 1, "stage_type": "custom", "active_applicants": 8}
    ],
    "company_department": {"id": 100, "name": "IT Department"},
    "executive_recruiter": {"id": 502, "name": "Петров Иван", "email": "petrov@company.com"},
    "author": {"id": 501, "name": "Иванова Мария", "email": "ivanova@company.com"}
  }],
  "page": 1, "pages": 5, "per_page": 50
}
```

**Ключевые поля ответа:**
- `stages` — этапы воронки с количеством активных кандидатов на каждом
- `applicants_count.active` — сколько кандидатов сейчас в работе
- `executive_recruiter` — ответственный рекрутер
- `state_id` — текущее состояние вакансии

---

### GET /api/v3/jobs/:id

**Когда использовать:** Получить детали конкретной вакансии, включая этапы воронки и команду.

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `id` | integer | Да | ID вакансии (path) |

**Ответ:** аналогичен одному элементу из `data` в GET /jobs.

**Следующий шаг:** GET /jobs/:id/ajs_joins — получить кандидатов на вакансии

---

### POST /api/v3/jobs

**Когда использовать:** Создать новую вакансию.

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `name` | string | Да | Название вакансии |
| `city` | string | Нет | ID города |
| `description` | string | Нет | Описание (HTML допустим) |
| `salary_from` | integer | Нет | Зарплата от |
| `salary_to` | integer | Нет | Зарплата до |
| `currency_type` | string | Нет | Валюта: `RUR`, `USD`, `EUR` |
| `schedule_type` | string | Нет | `fullDay`, `shift`, `flexible`, `remote`, `flyInFlyOut` |
| `experience_type` | string | Нет | `noExperience`, `between1And3`, `between3And6`, `moreThan6` |
| `employment_type` | string | Нет | Тип занятости: `full` и др. |
| `company_department_id` | integer | Нет | ID отдела |
| `demand` | integer | Нет | Количество позиций |
| `closure_date` | date | Нет | Плановая дата закрытия |
| `private` | boolean | Нет | Приватная вакансия |
| `job_type` | string | Нет | `point` (точечная) или `mass` (массовая) |
| `priority` | string | Нет | `highest`, `high`, `medium`, `low` |

**Запрос:**
```json
{"name": "Python Developer", "city": "1", "salary_from": 200000, "salary_to": 350000, "schedule_type": "remote", "experience_type": "between3And6"}
```

**Следующий шаг:** POST /ajs_joins — добавить кандидатов на созданную вакансию

---

### PUT /api/v3/jobs/:id

**Когда использовать:** Обновить данные вакансии (частичное обновление — передавайте только изменяемые поля).

Параметры — те же, что у POST. Передавайте только поля, которые нужно изменить.

---

### POST /api/v3/jobs/:job_id/change_state

**Когда использовать:** Приостановить, закрыть, отменить или возобновить вакансию.

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `job_id` | integer | Да | ID вакансии (path) |
| `transition` | string | Да | `pause`, `close`, `cancel`, `reopen` |

**Запрос:** `{"transition": "pause"}`

**Ответ 200:** `{"job_id": 12345, "state_id": "paused", "transition": "pause"}`

**Ошибки:** 422 `Transition not allowed` — переход невозможен из текущего состояния

---

### POST /api/v3/jobs/:job_id/assign_executive_recruiter

**Когда использовать:** Назначить ответственного рекрутера на вакансию. Пользователь должен быть в команде вакансии.

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `job_id` | integer | Да | ID вакансии (path) |
| `email` | string | Да | Email рекрутера |

**Запрос:** `{"email": "recruiter@company.com"}`

**Ошибки:** 404 `Пользователь не найден в команде по вакансии`

---

### GET /api/v3/jobs/:job_id/ajs_joins

**Когда использовать:** Получить всех кандидатов на вакансии с их текущими этапами. Использует курсорную пагинацию.

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `job_id` | integer | Да | ID вакансии (path) |
| `applicant_id` | integer | Нет | Фильтр по конкретному кандидату |
| `page_size` | integer | Нет | Записей на странице (1-100, по умолчанию 20) |
| `page_cursor` | string | Нет | Курсор для следующей страницы |

**Ответ 200:**
```json
{
  "objects": [{
    "id": 50001,
    "job_id": 12345,
    "applicant_id": 6001,
    "stage": {"id": 1001, "name": "HR Interview", "serial": 1, "stage_type": "custom"},
    "responsible_user_id": 501,
    "active": true,
    "datetime_of_create": "2024-01-16T14:30:00.000Z"
  }],
  "has_next_page": true,
  "page_next_cursor": "eyJpZCI6NTAwMDEsIm..."
}
```

**Следующий шаг:** POST /ajs_joins/:id/move_to_next_stage — переместить кандидата дальше по воронке

---

### GET /api/v3/jobs/state_histories

**Когда использовать:** Получить историю смены состояний вакансий (для аналитики и аудита).

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `page_size` | integer | Нет | Записей на странице (по умолчанию 20) |
| `page_cursor` | string | Нет | Курсор |
| `created_at[from]` | datetime | Нет | Начало периода |
| `created_at[to]` | datetime | Нет | Конец периода |

**Ответ:** курсорная пагинация с `objects`, `has_next_page`, `page_next_cursor`

---

### POST /api/v3/jobs_users

**Когда использовать:** Массово добавить пользователей в команды вакансий.

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `jobs_users` | array | Да | Массив объектов |
| `jobs_users[].job_id` | integer | Да | ID вакансии |
| `jobs_users[].user_id` | integer | * | ID пользователя (или user_email) |
| `jobs_users[].user_email` | string | * | Email пользователя (или user_id) |

**Запрос:**
```json
{"jobs_users": [{"job_id": 12345, "user_id": 501}, {"job_id": 12345, "user_email": "recruiter@company.com"}]}
```

Если связь уже существует — повторно не создаётся.

---

---

### GET /api/v3/cursor_paginated/jobs

**Когда использовать:** Полнотекстовый поиск вакансий по названию. В отличие от GET /api/v3/jobs, этот эндпоинт поддерживает параметр `q` для поиска через Elasticsearch. Также используйте для больших объёмов данных — курсорная пагинация эффективнее страничной.

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `q` | string | Нет | Поисковый запрос (полнотекстовый по названию) |
| `by_scope` | string | Нет | `active` (по умолчанию) или `all` |
| `f` | object | Нет | Фильтры (Elasticsearch) |
| `per_page` | integer | Нет | Записей на странице (по умолчанию 50, макс 100) |
| `page_cursor` | string | Нет | Курсор для следующей страницы |

**Запрос:**
```
GET /api/v3/cursor_paginated/jobs?q=Руководитель 1С&by_scope=all&per_page=20
```

**Ответ 200:**
```json
{
  "objects": {
    "jobs": [{
      "id": 12345,
      "name": "Руководитель отдела 1С",
      "state_id": "opened",
      "stages": [...],
      "applicants_count": {"all": 45, "active": 32}
    }],
    "counters": {"unarchived": 150, "archived": 320}
  },
  "has_next_page": true,
  "page_next_cursor": "eyJpZCI6MTIzNDV9",
  "total_count": 3
}
```

**Отличия от GET /api/v3/jobs:**
- Поиск по названию через `q`
- Курсорная пагинация (вместо страничной)
- `total_count` — точное количество результатов
- `counters` — общее количество архивных/неархивных вакансий

---

## Справочник значений

**schedule_type:** `fullDay`, `shift`, `flexible`, `remote`, `flyInFlyOut`

**experience_type:** `noExperience`, `between1And3`, `between3And6`, `moreThan6`

**job_type:** `point` (точечная), `mass` (массовая)

**priority:** `highest`, `high`, `medium`, `low`
