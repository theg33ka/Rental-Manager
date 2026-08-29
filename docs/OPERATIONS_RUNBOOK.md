# Операционный runbook

## Локальная диагностика

1. Проверьте Git status, активный Python и переменные `RENTAL_MANAGER_DATABASE_URL`/`DATABASE_URL` без вывода их значений.
2. Выполните `python -m alembic current` и `python -m alembic heads`.
3. Запустите `scripts/validate.ps1`; затем JS/Playwright проверки из [TESTING.md] для web-изменений.
4. Запустите Uvicorn и проверьте `/healthz`, auth status и изменённый API на синтетических данных.
5. При нестабильном browser smoke убедитесь, что на порту `8000` не остался старый Uvicorn process.

## Перед deployment

- Зафиксируйте целевой commit, окружение и ответственного.
- Проверьте новый Alembic upgrade path и наличие одного head.
- Сделайте свежий database backup; отдельно сохраните persistent settings-encryption key file.
- Проверьте diff и `scripts/check_secrets.py`.
- Подтвердите runtime variables, public base URL, Telegram webhook target и health-check.
- Не запускайте demo/screenshot seed, database import или downgrade на production.

Контейнер сам выполняет `alembic upgrade head` перед Uvicorn. Успешная сборка без успешной миграции не считается успешным deployment.

## Incident flow

1. Запишите время, environment, deployed commit, Alembic current/head и первый симптом.
2. Сначала выполните read-only проверки `/healthz`, runtime logs, DB connectivity, persistent volume и external provider status.
3. Разделите проблемы кода, миграции, secret/configuration, данных и внешней интеграции.
4. Если схема совместима, откатите приложение на last-known-good commit. Для persisted repair создайте и проверьте новую forward migration.
5. Database restore, Alembic downgrade, PIN reset, encryption-key replacement и Telegram webhook mutation требуют отдельного одобрения и плана восстановления.

## Android release

Перед каждым release измените `versionCode` и `versionName` в manifest, затем запустите `android/RentalManager/build-apk.ps1` со стабильным явно переданным `-KeystorePath`. Скрипт по умолчанию создаёт development keystore; не выдавайте такой APK за production update. Сверьте сертификат, установите APK поверх предыдущей версии, проверьте server URL/session/Hermes/notifications и сохраните versioned APK.

Handoff включает commit, Alembic state, backup location без секрета, выполненные проверки, внешний smoke и безопасный следующий шаг.
