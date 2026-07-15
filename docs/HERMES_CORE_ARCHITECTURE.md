# Hermes Core — целевая архитектура

Hermes Core — внутренний stateful runtime Rental Manager. DeepSeek остаётся подключённым LLM-провайдером, но не получает доступ к БД и не определяет права на действия. Все чтения, проверки и изменения выполняет backend.

## Поток данных

1. Изменение бизнес-сущности создаёт идемпотентное `DomainEvent` с actor, source, correlation id и ссылками на сущности.
2. Детерминированный Case Engine сверяет фактические начисления, оплаты, обещания, коммунальные данные, расходы и автоматизации с `OperationalCase`.
3. Briefing Builder выбирает не более трёх новых или изменившихся кейсов, группирует их по объектам и формирует текст до 800 символов без LLM.
4. Telegram, web и Android читают одни и те же backend snapshots и вызывают одни и те же callback/API-команды.
5. Для свободного текста orchestrator сначала классифицирует намерение, выбирает кейс и tool group, затем строит ограниченный `ContextManifest`. LLM получает только выбранный snapshot и возвращает строгий `AgentEnvelope`.
6. Любая мутация проходит backend validation и Action Safety Registry. Уровни 2–3 создают proposal; критический или массовый уровень 3 дополнительно требует повторного PIN в web/Android.

## Компоненты

- `services/hermes/events.py` — domain events и SQLAlchemy listeners.
- `services/hermes/cases.py` — reconciliation, state hash, приоритет, lifecycle и compact case memory.
- `services/hermes/memory.py` — owner preferences, one-shot consumption, commitments и rolling conversation summary.
- `services/hermes/briefing.py` — сохранённая ежедневная сводка и интерактивные case/commitment callbacks.
- `services/hermes/reminders.py` — tenant intent, режимы ответа, dedupe, паузы, outcomes и strategy profile.
- `services/hermes/skills.py` — декларативные версии skills, dry-run, activation, disable и rollback.
- `services/hermes/safety.py` — единый реестр уровней безопасности, независимый от prompt и skills.
- `services/hermes/runtime.py` — scoped contexts, manifests, run audit, feature usage и атомарные grouped proposals.
- `services/hermes/control_center.py` — сериализация и агрегаты web/Android Control Center.
- `services/ai_providers.py` — provider registry; DeepSeek зарегистрирован как текущий adapter.

## Источник истины и совместимость

Финансовые таблицы, договоры, сообщения и owner operations остаются источником истины. Hermes хранит производное операционное состояние и всегда reconciles его с бизнес-данными. Legacy `agent_tasks`, `agent_memories`, `ai_usage_daily`, `ai_action_logs` и proposals не удаляются; полезные task/memory записи копируются новой forward migration с legacy reference.

`/api/bootstrap` и `/api/app-state` сохраняются. Новые `/api/hermes/*` и `/api/android/hermes/*` являются представлениями одного backend слоя; Android-экран реализован нативно на Java, без WebView.

## Ограничение LLM и аудит

LLM используется только для понимания свободного текста, извлечения параметров или сложной формулировки. Шаблонные напоминания, reconciliation, сводка, callbacks и safety decisions выполняются без LLM. Для каждого вызова сохраняются feature, trigger, model, Context Manifest, state hash, tokens, стоимость, tools, proposals, нормализованный envelope, результат и ошибка. Повторный `case_details`/`deep_audit` с тем же state hash не запускается повторно.

