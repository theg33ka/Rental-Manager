# Доступы и секреты

Здесь описана схема доступа, а не значения. PIN, пароли, токены, ключи, cookies, database exports, договоры/чеки и signing files нельзя добавлять в Git, Markdown, логи или сообщения агенту.

| Область | Когда нужна | Где хранится | Безопасная проверка |
| --- | --- | --- | --- |
| GitHub `theg33ka/Rental-Manager` | Исходный код и основной remote `origin` | Git credential manager/SSH agent | `git fetch --dry-run origin` |
| Amvera project | Deployment и runtime logs, remote `amvera` | Аккаунт Amvera | Read-only status/log check до любых действий |
| PostgreSQL/SQLite | Миграции и диагностика | Deployment secret store или локальный env | `alembic current`, `/healthz`, read-only query |
| Web panel PIN | Проверка owner/guest доступа | Хэш в settings/миграции, исходный PIN у владельца | Вход через UI без вывода PIN |
| Settings encryption | Защита сохранённых integration settings | `RENTAL_MANAGER_SETTINGS_ENCRYPTION_KEY` либо persistent key file | Наличие и доступность файла без печати содержимого |
| Telegram | Webhook и сообщения | Deployment secret store/settings | webhook-info/test message без печати token/secret |
| DeepSeek | Явно запрошенный AI integration test | Deployment secret store/settings | Provider health/test с синтетическим контекстом |
| Android signing | Установка поверх существующего APK | Явно переданный stable keystore вне Git | Сверка сертификата через `apksigner verify --print-certs` |

Канонический список переменных находится в `.env.example`. Production-значения хранятся в Amvera или другом утверждённом secret store; локальные — в игнорируемом `.env` либо session environment.

## Передача доступа

Новый агент сначала получает read-only Git/deployment доступ и название целевого окружения. Production DB write, migration, secret rotation, Telegram webhook change, PIN reset, import/restore и deployment разрешены только явной задачей.

Ключевой файл `RENTAL_MANAGER_SETTINGS_ENCRYPTION_KEY_FILE` должен находиться на persistent volume: его потеря может сделать сохранённые настройки нечитаемыми. Android-скрипт по умолчанию создаёт development keystore с известным паролем; он не является production signing strategy. Для обновляемого APK передавайте стабильный keystore через `-KeystorePath` и утверждённый защищённый процесс.

При утечке отзовите/замените соответствующий токен или ключ, оцените доступ к зашифрованным данным и только затем возобновляйте deployment. Изменение persisted auth state оформляется по [Migration guide](CHANGE_AND_MIGRATION_GUIDE.md).
