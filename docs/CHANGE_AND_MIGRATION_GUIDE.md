# Изменения и миграции

## Текущая Alembic-цепочка

```text
20260711_01 -> 20260711_02 -> 20260715_01 -> 20260720_01 -> 20260729_01 -> 20260828_01 (head)
```

Применённые или опубликованные ревизии неизменяемы. Даже небольшой repair, PIN reset или исправление ранее перенесённых данных получает новую forward migration.

## Изменение схемы

1. Определите целевую модель в `rental_manager/models.py` и совместимость SQLite/PostgreSQL.
2. Создайте новую линейную revision после текущего head: `python -m alembic revision -m "short_description"`.
3. Заполните `upgrade()` и безопасный `downgrade()` там, где обратимость реальна. Не обещайте downgrade для необратимого data repair.
4. Не удаляйте/переименовывайте данные одним шагом: сначала добавьте совместимое поле/таблицу и backfill, затем переключите код, а cleanup вынесите в более поздний релиз.
5. Для data repair используйте узкий фильтр, идемпотентную логику и сохранение audit/provenance. Не затрагивайте строки вне доказанного набора.

## Проверка upgrade path

- Проверьте `alembic heads` и убедитесь, что head один.
- Поднимите схему на ревизии до новой, добавьте контрольные данные, выполните `alembic upgrade head` и проверьте значения/ограничения.
- Повторите на чистой БД и на disposable копии поддерживаемой существующей схемы.
- Выполните `scripts/validate.ps1`, regression tests изменённой области и Playwright при изменении пользовательского сценария.
- Для PostgreSQL-специфичных ограничений/индексов нужна PostgreSQL-проверка; SQLite smoke её не заменяет.

Production container выполняет `alembic upgrade head` до запуска Uvicorn. Поэтому миграция должна быть безопасной при одном запуске, а deployment — иметь свежий backup и понятный last-known-good commit.

## Auth и доступ

Редактирование старой применённой миграции не меняет deployed database. Восстановление persisted PIN/settings выполняется новой forward migration или явно согласованной runtime-операцией, затем проверяется реальный login flow и deployment state. Plaintext PIN и encryption key не попадают в revision, test output или документацию.

## API и клиенты

Сохраняйте совместимость `/api/bootstrap` и `/api/app-state`, пока web и Android не переведены. Новый/изменённый endpoint получает backend regression test и проверку каждого затронутого клиента. Массовые и критические Hermes actions остаются под safety/proposal/PIN правилами независимо от prompt.

## Откат

Предпочтителен откат приложения на совместимый commit либо новая forward repair migration. `alembic downgrade`, import backup и ручное изменение production-строк выполняются только по отдельному плану с проверенным backup, оценкой потери данных и явным разрешением владельца.
