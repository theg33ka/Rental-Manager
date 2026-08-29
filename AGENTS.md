# Правила работы с Rental Manager

## Перед изменениями

1. Прочитай `docs/INDEX.md`, затем документы нужной подсистемы.
2. Выполни `git status --short --branch` и сохрани несвязанные изменения пользователя.
3. Уточни источник истины: финансовые таблицы и договоры первичны, Hermes хранит производное операционное состояние.

## Инварианты

- Используй ясные названия и короткие пользовательские тексты.
- Комментарии добавляй только для неочевидных решений и важных ограничений.
- Не дублируй бизнес-логику в FastAPI routes, web UI, Telegram и Android; клиенты вызывают общие backend operations.
- Не обходи backend authorization, Action Safety Registry, proposal/PIN confirmation и audit trail.
- Применённые или опубликованные Alembic-ревизии не редактируются. Исправление схемы, данных или доступа выполняется новой forward migration.
- Не запускай seed, import, downgrade, массовую операцию или production migration без явного scope и проверенного backup.
- Не помещай в Git или сообщения агенту `.env`, PIN, токены, ключи шифрования, database export, PDF, APK и signing key.

## Проверка и передача

- Запускай целевые тесты во время работы и подходящий набор из `docs/TESTING.md` перед передачей.
- Изменение денег, дат, auth, миграций или Hermes safety требует отдельного regression test.
- Для Android-релиза не создавай ветку без прямой команды пользователя; увеличивай `versionCode` и `versionName`, а версию включай в имя APK.
- Обновляй `docs/CURRENT_STATE.md` при изменении версии, Alembic head, deployment topology или статуса крупной подсистемы; долговременные решения записывай в `docs/DECISIONS.md`.
