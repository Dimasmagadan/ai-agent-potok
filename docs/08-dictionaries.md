# Справочники (Dictionaries)

API v3: `/api/v3/dictionaries`
API v2: `/api/v2/declination_reasons`, `/api/v2/company_departments`

Справочники — наборы данных для выбора значений: причины отказа, города, станции метро, уровни образования и т.д.

---

### GET /api/v3/dictionaries

**Когда использовать:** Получить список всех справочников компании (названия и ID). Используйте для обнаружения доступных справочников.

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `page_size` | integer | Нет | Записей на странице (по умолчанию 20, макс 100) |
| `page_cursor` | string | Нет | Курсор |
| `q` | string | Нет | Поиск по названию |
| `filter[editable]` | boolean | Нет | Только редактируемые |
| `filter[archived]` | boolean | Нет | Только архивные |

**Ответ 200:**
```json
{
  "objects": [
    {"id": 123, "name": "declination_reasons", "archived": false},
    {"id": 124, "name": "education_levels", "archived": false}
  ],
  "has_next_page": true,
  "page_next_cursor": "eyJpZCI6MTI0fQ=="
}
```

---

### GET /api/v3/dictionaries/:id

**Когда использовать:** Получить конкретный справочник по имени (name). Сначала ищет среди справочников компании, затем среди глобальных.

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `id` | string | Да | Имя справочника (path), например `declination_reasons` |

---

### GET /api/v3/dictionaries/:dictionary_id/items

**Когда использовать:** Получить элементы справочника (например, все причины отказа).

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `dictionary_id` | string | Да | Имя справочника (path) |
| `page_size` | integer | Нет | Записей на странице |
| `page_cursor` | string | Нет | Курсор |
| `q` | string | Нет | Поиск по названию элемента |
| `filter[archived]` | boolean | Нет | Только архивные |

**Ответ 200:**
```json
{
  "objects": [
    {"id": 1, "name": "Не подходит по опыту", "archived": false},
    {"id": 2, "name": "Зарплатные ожидания", "archived": false}
  ],
  "has_next_page": false
}
```

---

### GET /api/v3/dictionaries/cities

**Когда использовать:** Получить справочник городов. Города используются при создании вакансий (поле `city`).

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `q` | string | Нет | Поиск по названию города |

---

### GET /api/v3/dictionaries/metro

**Когда использовать:** Получить справочник станций метро (для фильтрации по локации).

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `city_id` | string | Нет | Фильтр по городу |

---

### GET /api/v2/declination_reasons

**Когда использовать:** Получить причины отказа. Нужны для операции decline кандидата (POST /jobs/:id/:applicant_id/decline).

**Ответ 200:**

> ⚠️ Ответ — **массив напрямую**, без обёртки `{data: [...]}`.

```json
[
  {"id": 1, "name": "Недостаток мотивации", "initiator": "from_recruiter", "archived": false},
  {"id": 2, "name": "Неинтересные задачи", "initiator": "from_applicant", "archived": false},
  {"id": 3, "name": "Не одобрен руководителем", "initiator": "from_director", "archived": false}
]
```

**Поле `initiator`:**
- `from_recruiter` — отказ инициирован рекрутером
- `from_applicant` — кандидат сам отказался
- `from_director` — отказ от руководителя/заказчика

---

### GET /api/v2/company_departments

**Когда использовать:** Получить список отделов/подразделений компании. Используются при создании вакансий (поле `company_department_id`).

**Ответ 200:**
```json
{"data": [{"id": 100, "name": "IT Department"}, {"id": 101, "name": "HR"}]}
```
