# Интеграция Hermes Agent

## Что было до интеграции

Owner chat и диалоги с арендаторами уже вызывали OpenAI-compatible `/v1/chat/completions`. При наличии `YANDEX_API_KEY` backend обращался напрямую в Yandex AI Studio, иначе — в локальный Hermes gateway на `127.0.0.1:8642`.

У проекта уже были:

- Telegram owner chat;
- JSON-протокол ответа агента;
- таблицы диалогов, сообщений, памяти, аудита и pending actions;
- подтверждение действий кнопками в Telegram;
- backend-обработчики отсрочки, выезда, ручного долга и сообщения арендатору.

Проблема была в выборе провайдера: он зависел от `AI_DIRECT_YANDEX`, а Hermes gateway автоматически предпочитал Yandex при наличии его ключа. Получался переключатель с характером: вроде Hermes, но первым всё равно заходил Яндекс.

## Что изменено

Добавлен явный provider layer:

- `hermes` — внешний Hermes Agent gateway;
- `yandex` — прямой Yandex OpenAI-compatible API;
- `deepseek` — прямой DeepSeek API;
- `openai_compatible` — любой совместимый endpoint;
- `amvera_llm` — Amvera LLM Inference через совместимый endpoint.

Backend делает один вызов через основной провайдер. При сетевой ошибке, ошибке конфигурации или HTTP-ошибке пробует `AI_FALLBACK_PROVIDER`. Каждый неудачный вызов записывается в `ai_action_logs` без секретов.

Hermes остаётся отдельным процессом поверх backend. Он не запускает локальную LLM и не получает прямой доступ к базе.

## Включение Hermes

Минимальная конфигурация backend:

```env
AI_ENABLED=1
AI_PROVIDER=hermes
AI_FALLBACK_PROVIDER=yandex

HERMES_ENABLED=true
HERMES_API_BASE_URL=http://127.0.0.1:8642
HERMES_API_KEY=<случайный внутренний токен>
HERMES_MODEL=hermes-agent
HERMES_SYSTEM_PROMPT_PATH=docs/hermes-system-prompt.md
```

Для Hermes нужен внешний inference provider. Рекомендуемый вариант на Amvera — существующий OpenAI-compatible или Amvera LLM endpoint:

```env
HERMES_INFERENCE_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_BASE_URL=https://provider.example/v1
OPENAI_COMPATIBLE_API_KEY=<ключ>
OPENAI_COMPATIBLE_MODEL=<модель>
```

Для DeepSeek:

```env
HERMES_INFERENCE_PROVIDER=deepseek
DEEPSEEK_API_KEY=<ключ>
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

Для Amvera LLM:

```env
HERMES_INFERENCE_PROVIDER=amvera_llm
AMVERA_LLM_BASE_URL=<OpenAI-compatible URL из Amvera>
AMVERA_LLM_API_KEY=<ключ>
AMVERA_LLM_MODEL=<модель>
```

Hermes 0.17 запускается как gateway API server. В контейнере отключены тяжёлые browser/web/image/MCP toolsets: доменные данные передаёт Rental Manager, поэтому слабый тариф не обязан внезапно изображать дата-центр.

## Yandex fallback

```env
AI_FALLBACK_PROVIDER=yandex
YANDEX_AI_API_KEY=<ключ>
YANDEX_AI_FOLDER_ID=<folder id>
YANDEX_AI_MODEL=yandexgpt-lite
```

Старые `YANDEX_API_KEY` и `YANDEX_FOLDER_ID` поддерживаются.

Чтобы полностью вернуться к прямому Yandex:

```env
AI_PROVIDER=yandex
AI_FALLBACK_PROVIDER=
```

## Owner chat

Обычный текст владельца передаётся агенту. Команды `/status`, `/reports`, `/ping` и другие механические команды остаются в backend.

Доступ владельца задаётся через `TELEGRAM_OWNER_CHAT_ID`. Дополнительные Telegram ID можно перечислить через запятую:

```env
OWNER_TELEGRAM_IDS=123456789,987654321
```

## Read-only tools

Сервисный адаптер `rental_manager/services/hermes_tools.py` предоставляет:

- `get_overdue_payments`;
- `get_contracts_ending_soon`;
- `get_rentals_summary`;
- `get_tenants_summary`;
- `get_properties_summary`;
- `get_tenant_details`;
- `get_property_details`;
- `get_owner_chat_context`;
- `search_rentals`.

Сейчас это internal service adapter: он читает данные через ORM/service-слой проекта и формирует структурированный snapshot для owner agent. Следующий шаг для внешнего Hermes MCP — обернуть эти функции в authenticated internal HTTP/MCP transport. Прямой доступ MCP к БД не нужен.

## Write tools и подтверждение

Write-инструменты только формируют предложение:

- `propose_send_message_to_tenant`;
- `propose_defer_rent`;
- `propose_move_out`;
- `propose_create_manual_debt`.

Далее backend:

1. валидирует реальные `lease_id`, `charge_id`, даты, суммы и Telegram-привязку;
2. создаёт pending action;
3. показывает владельцу описание и кнопки подтверждения;
4. проверяет owner ID, исходный owner chat и TTL;
5. после approve вызывает обычный backend-сервис;
6. записывает `pending`, `approved`, `rejected`, `executed`, `failed` или `expired`.

Повторное нажатие не выполняет действие дважды.

## Логи

Основные маркеры:

- `[BOOT] ... ai_providers=hermes,yandex`;
- `[HERMES] wrote config provider=...`;
- `[HERMES] using cron package: ...`;
- `[AI] call provider=...`;
- `[AI] call failed provider=...`;
- `[TELEGRAM] ...`.

Ключи, токены и полные заголовки не логируются.

## Проверка

1. Установить env и отправить commit в репозиторий.
2. В Amvera открыть «Лог сборки» и включить «Режим стрима».
3. После запуска открыть «Лог приложения», также включить stream.
4. Проверить маркеры `[BOOT]` и `[HERMES]`.
5. В owner chat спросить: `Кто просрочил оплату?` — ответ должен прийти без pending action.
6. Написать: `Подготовь сообщение должнику` — должна появиться карточка подтверждения.
7. Нажать отмену — данные и сообщения не меняются.
8. Повторить и подтвердить — backend выполняет действие один раз.

## Ограничения MVP

- Hermes получает structured snapshot, а не делает интерактивные MCP-вызовы во время одного ответа.
- Реализованы четыре write-action; обновление статуса платежа, заметки и удаление сущностей пока намеренно не доступны агенту.
- Стоимость неизвестных OpenAI-compatible моделей оценивается приближённо; фактический биллинг надо смотреть у провайдера.
- Для зарубежных API может потребоваться способ оплаты, недоступный с российскими картами. Поэтому Amvera LLM, российские провайдеры и Yandex fallback остаются практичными вариантами.
