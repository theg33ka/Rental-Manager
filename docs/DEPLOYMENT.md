# Облако и Telegram

Ниже минимальный путь, чтобы приложение заработало в облаке и Telegram мог достучаться до webhook.

## Что уже готово в коде

- `Dockerfile` для контейнерного запуска;
- `render.yaml` для быстрого старта на Render;
- поддержка PostgreSQL через `RENTAL_MANAGER_DATABASE_URL`;
- Telegram webhook на `/api/integrations/telegram/webhook`;
- кнопки в настройках для:
  - сохранения `app_base_url`;
  - сохранения `Telegram owner chat id`;
  - подключения webhook;
  - проверки webhook;
  - отправки тестового сообщения.

## Переменные окружения

Для облака лучше использовать PostgreSQL, а не SQLite.

Пример:

```text
RENTAL_MANAGER_DATABASE_URL=postgresql+psycopg://user:password@host:5432/rental_manager
```

Готовый шаблон лежит в корне: `.env.example`.

Для Railway можно просто сослаться на стандартную переменную `DATABASE_URL`. Приложение само приведёт её к формату `postgresql+psycopg://...`.

## Порядок запуска в облаке

1. Подними FastAPI-приложение по публичному `https://...`
   - для health-check можно использовать `/healthz`;
   - на Render можно стартовать прямо из `render.yaml`;
2. Открой настройки приложения.
3. Заполни:
   - `Публичный URL приложения`
   - `Telegram owner chat id`
   - `Telegram bot token`
   - `Telegram webhook secret`
4. Нажми `Сохранить настройки`.
5. Нажми `Подключить Telegram webhook`.
   - приложение сразу настроит webhook и меню команд бота;
6. В Telegram отправь боту `/start`.
7. Для проверки:
   - `/id`
   - `/status`
   - `/reports`

## Первый сценарий Telegram

Сейчас бот уже умеет:

- показать `chat id`;
- дать owner-статус по пульту;
- показать открытые месячные отчёты;
- отправить ссылку на веб-пульт;
- принимать файл как заглушку под будущий OCR чеков.

Следующий этап после этого фундамента:

- owner-уведомления по событиям;
- сценарий жильца с загрузкой чека;
- OCR и проверка получателя;
- авторазбор оплат в начисления.
