# Карта AI-архитектуры до Hermes Core

Снимок сделан 15 июля 2026 года перед переработкой агентного слоя.

## Провайдеры и модели

- `services/ai_providers.py` выбирает provider runtime. Рабочий провайдер один — прямой DeepSeek API.
- `services/deepseek_client.py` вызывает chat completions. Поддерживаются `deepseek-v4-flash` и `deepseek-v4-pro`.
- `main.py` считает примерную стоимость, дневные вызовы и месячный бюджет в `ai_usage_daily`.
- Системные prompts находятся в `services/ai_policy.py`; owner prompt можно заменить файлом через `AI_SYSTEM_PROMPT_PATH`.

## Диалоги и контекст

- `ai_conversations` и `ai_messages` хранят owner/tenant Telegram-диалоги.
- `services/ai_context.py` формирует tenant context одного договора и большой owner dashboard context.
- `services/owner_ai_tools.py` добавляет read snapshot и каталог owner operations.
- `agent_memories` хранит общим текстовым форматом facts, preferences, commitments и skills.

## Owner Agent и proposals

- `handle_owner_ai_message` в `main.py` загружает dashboard, read tools, активные договоры, задачи и memory.
- `services/agent_protocol.py` нормализует JSON envelope модели.
- Мутации превращаются в `agent_action_proposals`; `services/owner_operations.py` задаёт разрешённые операции и параметры.
- `handle_agent_callback_query` проверяет owner chat, TTL и статус proposal. Текстовые «да/ок/делай» ничего не выполняют.
- `execute_agent_action` и `execute_owner_web_operation` повторно проверяют сущности и выполняют backend operation.

## Tenant Agent и reminders

- Telegram handler сначала обрабатывает команды, чеки и платёжные ситуации, затем tenant free-text AI.
- Tenant context ограничен активным договором; `agent_tenant_states` хранит обещанную дату и уровень эскалации.
- `payment_situations` управляет паузами, обещаниями и стадиями оплаты.
- `run_due_reminders` отправляет стандартные rent/utility templates без LLM, не дублирует сообщения за день и учитывает паузы.
- Отдельная legacy-функция `run_tenant_rent_dialogue` могла формулировать follow-up через LLM.

## Scheduler и автоаудит

- Фоновый цикл запускается при старте FastAPI и вызывает `run_due_reminders`.
- `sync_agent_tasks` строит `agent_tasks` из dashboard.
- `run_owner_supervisor_digest` передаёт до 20 задач вместе с полным owner context в LLM и отправляет длинный audit response.
- Расписание, модель, лимиты и инструкции задаются через `ai_supervisor_*` settings.

## Web и Android

- Web-панель показывает AI-настройки, статус DeepSeek, бюджет и агрегированную usage-статистику.
- `/api/app-state` и `/api/bootstrap` являются источником данных web/Android.
- Android — нативный Java-клиент. Он не использует WebView, но до Hermes Core не имел экранов cases, commitments и proposals.

## Ограничения исходной схемы

- `AgentTask` не является stateful operational case: нет state hash, waiting actor, suppression, resolution reason и связей.
- Общая текстовая `AgentMemory` смешивает разные жизненные циклы данных.
- Owner/audit context слишком широк и повторно отправляет dashboard и историю.
- Нет сохранённого briefing, case-focused callbacks, safety registry, declarative skills, reminder outcomes и per-feature run audit.

Hermes Core сохраняет работающие DeepSeek adapter, owner operations, платёжные ситуации и callback confirmation, но заменяет широкую audit/memory/task модель специализированными сущностями и сервисами.
