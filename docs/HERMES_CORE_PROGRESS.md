# Hermes Core — прогресс реализации

Обновлено: 15 июля 2026 года.

## Реализуемые блоки

- [x] Карта исходной AI-архитектуры.
- [x] ORM-сущности Hermes Core без удаления legacy-таблиц.
- [x] Forward migration с переносом полезных `AgentTask` и `AgentMemory` ссылок.
- [x] Идемпотентные domain events и reconciliation источника данных.
- [x] Operational Case Engine и compact case memory.
- [x] Owner Commitment Engine.
- [x] Structured owner preferences и one-shot consumption.
- [x] Declarative Skills Library и safety validation.
- [x] Action Safety Registry и grouped atomic proposals.
- [x] Детерминированный Briefing Builder и case callbacks.
- [x] Scoped Owner/Tenant orchestrator, Context Manifest и run audit.
- [x] Adaptive Reminder outcomes и tenant strategy.
- [x] Hermes Control Center API/web.
- [x] Android API и нативные экраны Hermes.
- [x] Unit, integration, callback и compatibility tests.
- [x] Полный backend/web/Android validation.

## Совместимость и ограничения миграции

- Legacy `agent_tasks`, `agent_memories`, `ai_usage_daily`, `ai_action_logs` и `agent_action_proposals` сохраняются.
- Legacy task импортируется как case с `legacy-task:*`; исходный id остаётся в metadata.
- Legacy preferences и skills получают `legacy_memory_id`.
- `ai_supervisor_*` settings используются как расписание ежедневного briefing до переименования UI.
- Старый LLM autoaudit заменён Briefing Builder; расписание `ai_supervisor_*` сохранено для обратной совместимости.

## Проверки

- Forward migration проверена поверх существующей ревизии `20260711_02` с переносом legacy task и preference.
- Hermes Core покрыт отдельными unit/integration сценариями; прежние callback и non-AI regression tests сохранены.
- Проверяются Python 3.11-совместимый lint, backend tests, compileall, web JavaScript/Playwright, secrets scan и нативная Android-сборка.

## Оставшиеся эксплуатационные ограничения

- В registry сейчас зарегистрирован один рабочий LLM adapter — DeepSeek; добавление провайдера не требует изменения orchestrator.
- Автономный уровень 1 выключен по умолчанию и включается владельцем в Control Center.
- Telegram не выполняет операции уровня 3: повторный PIN вводится в web или Android Control Center.
