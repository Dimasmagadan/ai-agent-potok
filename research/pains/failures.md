# Этап 1–2: Лог недоступных источников

| id | URL | Причина | Статус |
|---|---|---|---|
| F01 | https://reddit.com/r/recruiting | WebFetch недоступен для reddit.com | Пропущен, fallback: old.reddit.com |
| F02 | https://old.reddit.com/r/recruitinghell | WebFetch недоступен для reddit.com | Пропущен |
| F03 | https://pikabu.ru | Ошибка при доступе (сетевое ограничение) | Пропущен |
| F04 | https://highload.tech/vo-vremya-intervyu-kandidat-nachal-est-borshh-chto-besit-it-rekruterov-v-rabote/ | HTTP 403 Forbidden | Пропущен |
| F05 | https://www.quora.com/What-are-recruiters-and-hiring-managers-biggest-pain-points | HTTP 403 Forbidden | Пропущен |
| F06 | tgstat.ru | Требует отдельной проверки, недоступен через WebFetch | На проверку |
| F07 | Telegram-каналы про рекрутинг | Требуют проверки через t.me/s/ | На проверку |
