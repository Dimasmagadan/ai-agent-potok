# OAuth2 для интеграций

API: `/integration_apps/v1/oauth`

OAuth2 авторизация для сторонних приложений-интеграций. Используйте, если ваш агент должен работать от имени разных компаний (не через фиксированный Bearer-токен).

> Для простых случаев (один агент — одна компания) достаточно Bearer-токена из настроек. OAuth2 нужен для marketplace-интеграций.

---

### Общая схема

```
1. Регистрация приложения → получение client_id, client_secret
2. Пользователь авторизуется → redirect на ваш callback URL с code
3. Обмен code на access_token + refresh_token
4. Запросы к API с access_token
5. Обновление токена через refresh_token
```

---

### GET /integration_apps/v1/oauth/authorize

**Когда использовать:** Перенаправить пользователя для авторизации вашего приложения.

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `client_id` | string | Да | ID приложения |
| `redirect_uri` | string | Да | URL для callback |
| `response_type` | string | Да | `code` |
| `scope` | string | Нет | Запрашиваемые права |

**Ответ:** Редирект на `redirect_uri?code=AUTHORIZATION_CODE`

---

### POST /integration_apps/v1/oauth/token

**Когда использовать:** Обменять authorization code на access token.

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `grant_type` | string | Да | `authorization_code` или `refresh_token` |
| `code` | string | * | Authorization code (для `authorization_code`) |
| `refresh_token` | string | * | Refresh token (для `refresh_token`) |
| `client_id` | string | Да | ID приложения |
| `client_secret` | string | Да | Секрет приложения |
| `redirect_uri` | string | Да | URL callback (должен совпадать) |

**Ответ 200:**
```json
{
  "access_token": "abc123...",
  "token_type": "Bearer",
  "expires_in": 7200,
  "refresh_token": "def456...",
  "scope": "read write"
}
```

---

### POST /integration_apps/v1/oauth/revoke

**Когда использовать:** Отозвать токен (при отключении интеграции).

| Параметр | Тип | Обяз. | Описание |
|----------|-----|-------|----------|
| `token` | string | Да | access_token или refresh_token |
| `client_id` | string | Да | ID приложения |
| `client_secret` | string | Да | Секрет приложения |

---

## Использование токена

После получения `access_token` используйте его как Bearer-токен:

```
Authorization: Bearer <access_token>
```

Все эндпоинты API v2 и v3 принимают OAuth-токены наравне с обычными Bearer-токенами.
