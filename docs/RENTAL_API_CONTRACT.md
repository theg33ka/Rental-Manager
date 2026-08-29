# Контракт интеграции с Personal Assistant

Канонический плановый контракт клиентской стороны хранится в репозитории Personal Assistant:

- [personal_assistant/docs/RENTAL_API_CONTRACT.md](https://github.com/theg33ka/personal_assistant/blob/main/docs/RENTAL_API_CONTRACT.md)

Локально при соседних checkout файл обычно находится по пути `..\personal_assistant\docs\RENTAL_API_CONTRACT.md` относительно корня Rental Manager.

Сетевой адаптер пока не считается production-контрактом. Не создавайте независимую копию схемы здесь: сначала обновите канонический документ, зафиксируйте version/auth/errors/idempotency/timestamps, затем реализуйте и проверьте обе стороны.
