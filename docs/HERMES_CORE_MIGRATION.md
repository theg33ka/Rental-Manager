# Миграция Hermes Core

Ревизия: `20260715_01` после `20260711_02`.

Миграция создаёт новые таблицы Hermes Core и расширяет proposals полями case, safety, idempotency и validation hash. Старые AI-данные не удаляются.

Перенос данных:

- активные и закрытые `agent_tasks` копируются в `operational_cases` с legacy reference;
- `AgentMemory(kind=preference)` копируется в `owner_preferences`;
- `AgentMemory(kind=skill)` копируется как draft в `ai_skills`;
- исходные строки остаются на месте для аудита и отката.

После миграции Case Engine выполняет reconciliation с текущими начислениями, платежами, коммуналкой, договорами и расходами. Это устраняет устаревшие legacy cases без изменения финансовых данных.
