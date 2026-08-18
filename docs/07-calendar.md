# Календарь (Calendar)

API v3: `/api/v3/calendar`

Создание встреч (собеседований) и напоминаний в календаре, привязанных к кандидатам и вакансиям.

---

### POST /api/v3/calendar/reminders

**Когда использовать:** Создать напоминание для рекрутера (позвонить кандидату, проверить статус и т.д.). Однократное событие без времени окончания.

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `reminder[from]` | datetime | Да | Дата/время напоминания (должно быть в будущем) |
| `reminder[applicant_id]` | integer | Да | ID кандидата |
| `reminder[author_id]` | integer | Да | ID автора (пользователь компании) |
| `reminder[job_id]` | integer | Нет | ID вакансии |
| `reminder[body]` | string | Нет | Текст напоминания |
| `reminder[subject]` | string | Нет | Тема |
| `reminder[users]` | array | Нет | ID пользователей для добавления |
| `reminder[notify_applicant]` | boolean | Нет | Уведомить кандидата |

**Запрос:**
```json
{"reminder": {"from": "2024-12-20T10:00:00+03:00", "applicant_id": 12345, "author_id": 999, "body": "Позвонить кандидату"}}
```

**Ответ 200:**
```json
{"id": 987654, "type": "Event::Reminder", "from": "2024-12-20T10:00:00.000+03:00", "applicant": {"id": 12345, "first_name": "Иван"}}
```

**Ошибки:** 422 `from_invalid` — дата должна быть в будущем

---

### POST /api/v3/calendar/schedules

**Когда использовать:** Запланировать собеседование или встречу с кандидатом. Событие с временем начала и окончания, возможностью приглашения участников.

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `schedule[from]` | datetime | Да | Начало встречи |
| `schedule[to]` | datetime | Да | Конец встречи |
| `schedule[applicant_id]` | integer | Да | ID кандидата |
| `schedule[job_id]` | integer | Да | ID вакансии |
| `schedule[author_id]` | integer | Да | ID автора |
| `schedule[body]` | string | Да | Описание (не пустое) |
| `schedule[subject]` | string | Нет | Тема встречи |
| `schedule[users]` | array | Нет | ID пользователей-участников |
| `schedule[recipients]` | array | Нет | Email для приглашения |
| `schedule[url]` | string | Нет | Ссылка на видеоконференцию |
| `schedule[notify_applicant]` | boolean | Нет | Уведомить кандидата |

**Запрос:**
```json
{
  "schedule": {
    "from": "2024-12-20T14:00:00+03:00",
    "to": "2024-12-20T15:00:00+03:00",
    "applicant_id": 12345,
    "job_id": 678,
    "author_id": 999,
    "body": "Собеседование на позицию менеджера",
    "users": [1001, 1002],
    "url": "https://zoom.us/j/123456"
  }
}
```

**Ответ 200:**
```json
{"id": 987655, "type": "Event::Schedule", "from": "2024-12-20T14:00:00+03:00", "to": "2024-12-20T15:00:00+03:00"}
```

**Ошибки:** 422 `from_invalid` — дата начала в прошлом; 422 `to_less_than_from` — конец раньше начала
