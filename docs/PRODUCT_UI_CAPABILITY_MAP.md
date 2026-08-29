# Rental Manager: продуктовая и UI-карта системы

Статус: фактическое состояние после изменений 29 августа 2026 года. Документ переводит текущую реализацию на язык продукта и интерфейса. Он предназначен для UI/UX-дизайнера и разработчика; это не предложение новой навигации и не макет.

## 1. Executive Summary

Rental Manager — операционный пульт одного владельца/управляющего недвижимостью. Система объединяет портфель объектов и квартир, договоры, двухчастную аренду, платежи и чеки, коммунальные расчёты, расходы, отчёты, Telegram-коммуникации, автоматические напоминания, Android-клиент и Hermes Core.

Фактически существуют три пользовательские поверхности:

- web-панель — наиболее полная рабочая поверхность owner и ограниченный guest-view;
- Telegram — owner-команды, tenant-чат, чеки, реквизиты, напоминания и AI-диалоги;
- Android — нативный owner-клиент для ежедневной работы, использующий тот же backend.

После текущего изменения web-панель поддерживает полный базовый цикл портфеля: создать и отредактировать объект, добавить и настроить квартиры, заселить жильца, назначить объекту набор реквизитов по умолчанию и переопределить его для отдельной квартиры. Платёжные наборы переиспользуются ссылками, а не копируются по квартирам. Постоянная навигация разделяет «Главную» с общей сводкой и быстрыми действиями и «Сегодня» с полной очередью решений. Новый интерфейс поддерживает белую и неоновую production-палитры; неоновая используется по умолчанию.

Ключевая иерархия реквизитов:

```text
override квартиры
    ↓ если не задан
default объекта
    ↓ если не задан
глобальные реквизиты из настроек
    ↓ если не заполнены
явное состояние «реквизиты не определены»
```

Backend остаётся единственной границей бизнес-операций. Web, Android, Telegram и AI не должны самостоятельно считать деньги, обходить авторизацию, подтверждения или audit trail.

## 2. Product Overview

| Область | Пользовательская задача | Фактическая реализация | Основная поверхность |
| --- | --- | --- | --- |
| Операционный центр | Сразу увидеть деньги, долги, просрочки, показания и сегодняшние действия | Dashboard и месячная сводка, attention-cards, Hermes cases | Web, Android, Telegram `/status` |
| Портфель | Вести объекты, квартиры, жильцов и договоры | Объекты/квартиры, onboarding, редактирование, переезд, выезд, архивирование | Web; просмотр/частичные действия Android |
| Реквизиты | Управлять получателями и назначать их объектам/квартирам | Глобальный fallback + переиспользуемые Payment Profiles + inheritance/override | Web; применение в Telegram/AI/receipt matching |
| Аренда и оплаты | Начислять аренду, видеть две части, учитывать оплаты, отсрочки и долги | RentCharge, PaymentReceipt, ManualDebt, PaymentSituation | Web, Android, Telegram |
| Коммуналка | Ввести показания, рассчитать и выставить счета, принять оплату | Services, meters, readings, tariffs, bills, lines, advances | Web, Android, Telegram-уведомления |
| Расходы | Зафиксировать затраты и вернуть личные деньги | Expense + compensation state | Web, Android |
| Коммуникации | Писать жильцам, рассылать, видеть диалоги и принимать чеки | Telegram webhook, message preview/send, dialog log | Web, Telegram |
| Автоматизация | Не забывать напоминания и не спамить жильцу | Global/per-lease cadence, cutoff, duplicate suppression, situations | Web, Telegram worker |
| Отчёты | Скачать Excel и закрыть месяц | Rent, utilities, debts, expenses, owner, history, monthly | Web, Android download, Telegram links |
| Hermes Core | Получать кейсы, предложения, сводки и контролировать AI | Cases, commitments, preferences, strategies, skills, proposals, runs, usage | Web, Android, Telegram |
| Администрирование | Настроить доступы, интеграции, AI, backup/import | Settings, PIN sessions, CSRF, database tools, performance | Web |

### 2.1. Что является источником истины

Договоры, начисления, оплаты, коммунальные данные, расходы и фактические документы первичны. Hermes cases, briefings, memory и strategy state производны и должны пересобираться из бизнес-данных.

### 2.2. Что не следует выводить из структуры backend

- Один endpoint не равен одной кнопке: «заселить жильца», «выставить коммуналку» и «принять чек» объединяют несколько операций.
- Модули backend не задают будущую навигацию.
- Android — отдельный клиент, а не mobile-web.
- Глобальные реквизиты — совместимый fallback, а Payment Profile — новый переиспользуемый справочник.

## 3. Roles & Permissions

| Actor / роль | Видит | Создаёт и изменяет | Удаляет / специальные действия | Ограничения |
| --- | --- | --- | --- | --- |
| `owner` панели | Все web-разделы и все бизнес-данные | Объекты, квартиры, профили, договоры, оплаты, долги, показания, тарифы, счета, расходы, сообщения, настройки, Hermes | Допустимые удаления, import/export, issue, compensation, proposal confirm/reject | Для любой mutation нужны owner-session и CSRF. Массовые/критические Hermes actions требуют safety flow и иногда повторный PIN |
| `guest` панели | Только агрегированный dashboard и отчёты | Ничего | Только скачивание разрешённых отчётов | Любой иной API возвращает 403; registry и детальные финансовые массивы скрыты |
| Telegram owner | Статус, отчёты, приложение, Hermes/AI-диалог, уведомления | Команды и подтверждённые owner operations | Подтверждает/отклоняет предложения | Chat ID должен быть разрешён; обычный текст не считается подтверждением мутации |
| Привязанный tenant | Собственные долги, реквизиты своей квартиры, сообщения, отправка чеков | Отправляет чек/ответ, может сообщить обещание или запросить решение | Нет административных действий | Контекст изолирован одним жильцом/договором; реквизиты выдаются только привязанному чату |
| Непривязанный Telegram user | `/id`, help, привязка по телефону | Контакт для попытки привязки | Нет | Реквизиты, долги и пульт недоступны |
| Hermes/LLM | Только явно собранный контекст и разрешённые tools | Формирует ответ или proposal | Самостоятельно выполняет только разрешённый safety level | Нет прямого доступа к БД и правам; backend повторно валидирует state hash, TTL и payload |
| Background workers | Сроки, reminders, Telegram queue, audit/case reconciliation | Производные события, логи, напоминания | Не должны обходить tenant links, cutoff и duplicate suppression | Внешние ошибки дают partial/failed state, а не фиктивный success |

Авторизация централизована middleware: публичны root/static, health, login и Telegram webhook; guest получает только GET/HEAD для bootstrap/app-state и reports; остальные `/api/*` требуют owner.

## 4. Domain / Entity Map

```text
Payment Profile ── default → Rental Object ── contains → Apartment
       └────────── override ────────────────────────┘
Global payment settings ── fallback when neither link exists

Rental Object → Utility Service → Meter → Meter Reading
                              └→ Tariff
                              └→ Utility Bill → Utility Bill Line → Payment Receipt

Apartment → Lease → Tenant
              ├→ Rent Charge → Payment Receipt
              ├→ Manual Debt
              ├→ Payment Situation / automation settings
              └→ Telegram dialogue / AI context

Object / Apartment → Expense
Business changes → Domain Event → Operational Case → Briefing / Proposal / Commitment
```

### 4.1. Основные пользовательские сущности

| Сущность | Назначение и UI-поля | Связи и lifecycle | Ограничения |
| --- | --- | --- | --- |
| Payment Profile | Название; реквизиты ИП; реквизиты перевода; заметка; active/archive; места использования | Может быть default нескольких объектов и override нескольких квартир | Архивный профиль остаётся у старых назначений, но не назначается заново; удалить можно только неиспользуемый |
| Rental Object | Название, короткий код, заметка, active, default profile | Содержит квартиры и коммунальные услуги | Имя уникально; объект с активным договором нельзя архивировать; непустой объект не удаляется |
| Apartment | Название, порядок, доля ОДН, active, override profile, эффективный профиль, фиксированный аванс | Принадлежит одному объекту; имеет договоры, счётчики и историю | Квартиру с активным договором нельзя отключить; квартиру с историей нельзя удалить |
| Tenant | ФИО, телефон, Telegram, WhatsApp, заметка, active | Может иметь несколько договоров и историю переездов | Telegram-линк не равен username; чувствительные контакты доступны owner |
| Lease | Квартира, жилец, даты, день оплаты, суммы ИП/перевод, залог, notes, active/ignored | Заселение → активный договор → переезд или выезд → закрытая история | Одна активная неигнорируемая аренда на квартиру; день 1–31; money/date changes требуют перерасчёта |
| Rent Charge | Период, due date, две суммы due/paid, status, deferral | Генерируется из договора; закрывается двумя каналами оплат | Уникален по lease + due date; частичная оплата не закрывает весь платёж |
| Payment Receipt | Сумма, канал, дата, источник, status, actual recipient, документ, связи с начислением/строкой | Manual или Telegram/OCR → accepted/review/rejected/ignored/duplicate | Исторический actual recipient и файл не меняются при смене профиля |
| Manual Debt | Название, вид, канал, период/due, amount/paid, status, active | Создаётся owner, частично/полностью оплачивается, удаляется | Привязан к lease/apartment; влияет на общий долг |
| Utility Service | Вид/название, сроки показаний/оплаты, active | Принадлежит объекту; владеет meters, tariffs, bills | Для нового объекта автоматически не создаётся: см. Open Product Decisions |
| Meter / Reading | Scope object/apartment, имя; дата и значение показания | Meter принадлежит service и, для квартирного, apartment | Показания нужны на обе границы периода; прогноз должен быть заметен |
| Tariff | Service, дата начала, название, tiers | История тарифов по effective date | Ступени парсятся и проверяются backend |
| Utility Bill / Line | Период, тип usage/advance, consumption, cost, due, forecast, provider paid; строки по квартирам | Draft → issued → partial/paid/overdue; авансы могут зачесться | Нельзя честно рассчитать при apartment consumption > object consumption; issue может частично не отправить сообщения |
| Expense | Дата, объект/квартира, категория, сумма, источник, способ, description, compensation | Personal expense: pending → compensated; остальные not_required | Compensation — отдельное действие |
| Message / Dialogue | Получатель, текст, template, delivery status, timestamps | Preview → send → sent/failed; incoming сохраняется | Unlinked tenant нельзя считать доставленным; массовая отправка возвращает per-recipient result |
| Monthly Report | Месяц, kind full/preliminary, issues, accepted state | Генерируется из данных; owner может принять | Принятие фиксирует контрольный статус, не переписывает деньги |
| Hermes Case | Type, severity, priority, status, summary, scope, next review | new/active/waiting/snoozed → resolved/closed | Производная сущность; state reconcile может обновить или закрыть кейс |
| Proposal | Action, payload, preview, safety, TTL, state hash, status | pending → executed/rejected/expired/failed | Confirm повторно валидирует состояние; критическое действие требует PIN |

Внутренние сущности `DomainEvent`, `CaseMemory`, `ReminderOutcome`, usage counters, sessions, login attempts и processed updates важны для надёжности, но не должны превращаться в обычные формы редактирования.

## 5. Product / UI Capability Map

Общее для таблиц ниже: owner mutations защищены session + CSRF; типовые ошибки — 400 validation, 401 no session, 403 role/CSRF, 404 missing entity, 409 conflict/safe refusal, 5xx external/runtime failure. `UI: да` означает реально существующую поверхность, а не желаемый редизайн.

### 5.1. Доступ, обзор и портфель

| Capability | Actor, entry point, UI | Inputs / entities | Result, rules, statuses, errors | Backend / risk / design note |
| --- | --- | --- | --- | --- |
| Войти по PIN | owner/guest; login overlay; UI: да | PIN, remember; PanelSession | Role-specific session + CSRF cookie; rate limit/temporary block; invalid/compromised PIN rejected | `/api/auth/status`, `POST /api/auth/pin`, logout; auth-sensitive |
| Увидеть состояние портфеля | owner/guest; dashboard, Android, `/status`; UI: да | Period/current data | Occupancy, income, debts, attention counts; guest gets aggregates only | bootstrap/app-state/dashboard builders; read-only |
| Найти данные на текущем экране | owner/guest; global search; UI: да | Free text | Client-side hide/show of rendered rows/cards; no backend search or pagination | DOM filter only; empty/no-result state needs redesign attention |
| Создать объект | owner; Портфель → «Добавить объект»; UI: да, расширено | Name required; code, note, default profile optional | Object appears in registry/dashboard and becomes apartment parent; duplicate name 409; may be empty | `POST /api/objects`; non-destructive |
| Открыть и изменить объект | owner; object card; UI: да, новое | Name/code/note/default profile | Updates object; archived profile may remain only if already assigned | `PATCH /api/objects/{id}`; conflict on duplicate/inactive new profile |
| Архивировать/вернуть объект | owner; object card; UI: да, новое | Active toggle | Active lease blocks archive; archive disables child apartments; restore does not auto-enable them | Same PATCH; confirmation; state-changing |
| Удалить пустой объект | owner; object card; UI: да, новое для допустимого случая | Object with no apartments/services | Permanent delete; any structure/history requires archive instead | `DELETE /api/objects/{id}`; destructive + confirmation |
| Создать квартиру | owner; Портфель → «Добавить квартиру»; UI: да, расширено | Active object, name, sort, ODN share, optional override | Immediately available for onboarding, expenses and selectors; archived object/inactive profile rejected | `POST /api/apartments`; non-destructive |
| Изменить квартиру | owner; apartment registry; UI: да, новое | Name, sort, ODN share, profile override | Override wins over object; blank means inherit; effective profile shown | `PATCH /api/apartments/{id}` |
| Отключить/включить квартиру | owner; registry; UI: да | Active toggle | Active lease blocks disable; inactive apartment hidden from normal onboarding/calculations | Same PATCH; confirmation desirable |
| Удалить пустую квартиру | owner; registry; UI: да, новое | Apartment without lease/meter/bill/payment/expense history | History forces safe refusal and archive | `DELETE /api/apartments/{id}`; destructive + confirmation |
| Настроить коммунальный аванс квартиры | owner; registry «Аванс»; UI: да | Amount override or empty=auto, note | History row written; negative amount rejected | `PATCH /api/apartments/{id}/utility-advance` |

### 5.2. Платёжные реквизиты

| Capability | Actor, entry point, UI | Inputs / entities | Result, rules, statuses, errors | Backend / risk / design note |
| --- | --- | --- | --- | --- |
| Настроить глобальные реквизиты | owner; Settings → Payments; UI: да | Все уже поддержанные IP/personal fields | Fallback для квартир без object/apartment profile; incomplete state допускается и показывается | `/api/settings`; existing compatibility layer |
| Создать набор реквизитов | owner; Portfolio → Payment profiles; UI: да, новое | Unique label; существующие IP/personal fields; note | Active reusable profile; duplicate label 409 | `POST /api/payment-profiles`; financial configuration |
| Просмотреть набор и места использования | owner; profile cards; UI: да, новое | Profile | Direct object assignments и effective apartment usage | Registry payload / `GET /api/payment-profiles`; read-only |
| Изменить набор | owner; profile editor; UI: да, новое | Same fields | Следующие инструкции/receipt checks используют новые значения; history unchanged | `PATCH /api/payment-profiles/{id}`; confirmation/audit recommended for future UX |
| Архивировать набор | owner; profile card; UI: да, новое | Active=false | Existing assignments remain effective and visibly archived; new assignments rejected | Same PATCH; confirmation; not deletion |
| Удалить набор | owner; only unassigned card; UI: да, новое | No direct object/apartment links | Permanent delete; used profile returns 409 | `DELETE /api/payment-profiles/{id}`; destructive |
| Назначить default объекту | owner; object create/edit; UI: да, новое | Active profile or global fallback | Все квартиры без override наследуют выбор | object POST/PATCH |
| Назначить override квартире | owner; apartment create/edit; UI: да, новое | Active profile or inherit | Only selected apartment changes effective details | apartment POST/PATCH |
| Получить фактические реквизиты жильца | linked tenant / reminders / AI / receipt matching; UI: Telegram и generated text | Apartment → override/object/global | Один deterministic result; случайный «первый профиль» не выбирается | Shared resolver in `services/payment_profiles.py` |

### 5.3. Жильцы, договоры, аренда и оплаты

| Capability | Actor, entry point, UI | Inputs / entities | Result, rules, statuses, errors | Backend / risk / design note |
| --- | --- | --- | --- | --- |
| Заселить жильца | owner; Portfolio / Android; UI: да | Свободная active apartment, contacts, start/end, payment day, IP/personal amounts, deposit | Tenant + Lease + generated charges; occupied apartment rejected | `POST /api/leases/onboard`; combines several entities |
| Изменить договор/жильца | owner; lease row / Android; UI: да | Apartment, contacts, dates, money, deposit, notes | Updates tenant/lease; recalculates affected charges | `PATCH /api/leases/{id}`; money/date regression-sensitive |
| Оформить переезд | owner; lease action; UI: web да | Target vacant apartment, transfer date, optional terms | Old lease closes, new lease opens, tenant history remains connected; utilities handled separately | `POST /api/leases/{id}/transfer`; transactional/high impact |
| Оформить выезд | owner; lease action / Android; UI: да | End date | Lease closes, final utility handling/notifications run | `POST /api/leases/{id}/move-out`; high impact + confirmation |
| Игнорировать договор в расчётах | owner; lease action; UI: да | Boolean | Lease remains informational but excluded from operational calculations | `PATCH /api/leases/{id}/ignore` |
| Удалить договор | owner; action menu; UI: да | Lease | Backend applies dependency/safety rules and recalculation | `DELETE /api/leases/{id}`; destructive + confirmation |
| Сформировать начисления аренды | owner/background; Finance; UI: косвенно/endpoint | Until date | Missing RentCharges created once per due date | `POST /api/rent-charges/generate`; idempotent by unique lease+due |
| Просмотреть и отфильтровать начисления | owner; Finance / Android; UI: да | Date range; backend limit/offset | Sorted charge list with two channels/statuses | `GET /api/rent-charges`; backend pagination exists, web mainly loads working set |
| Зачесть платёж | owner; Finance / Android; UI: да | Lease/charge or auto allocation, amount, channel/kind, paid_at | Creates receipt(s), reallocates and recalculates balances | charge payment/manual receipt endpoints; financial mutation |
| Посмотреть историю оплат | owner; lease/tenant history; UI: да | Lease or tenant | Combined history including past apartments | lease/tenant payment-history endpoints |
| Создать и погасить ручной долг | owner; lease → Debts; UI: да | Kind/channel/title/dates/amount and payments | open/partial/paid; affects total debt | manual-debt CRUD/payment endpoints |
| Дать отсрочку | owner; charge/payment situation; UI: да | Days/date, note | Charge becomes deferred until date; reminders pause per rules | `POST /api/rent-charges/{id}/defer`; date-sensitive |

### 5.4. Чеки, коммуналка, расходы и отчёты

| Capability | Actor, entry point, UI | Inputs / entities | Result, rules, statuses, errors | Backend / risk / design note |
| --- | --- | --- | --- | --- |
| Принять чек из Telegram | linked tenant; Telegram; UI: да | PDF/document/photo metadata | Parse, dedupe, recipient validation against effective profile, allocation or review | webhook + receipt parser/matcher; file/security-sensitive |
| Проверить подозрительный чек | owner; Messages/Payments / Android; UI: да | Receipt, action, optional channel/note | accept rent/utility/expense or keep/ignore; balances recalculated | moderate/ignore/update/delete/document endpoints; financial + destructive options |
| Внести показание | owner; Meters / Android; UI: да | Meter, date, value, note | Reading saved for later calculation | single/batch reading endpoints; duplicate/date rules handled backend |
| Добавить тариф | owner; Tariffs / Android; UI: да | Service, starts_on, name, tier string | Effective tariff history | `POST /api/tariffs`; parser validation |
| Рассчитать коммуналку | owner; Utilities / Android; UI: да | Service or object, period boundaries, allow_estimate | Draft bill(s), apartment lines, warnings/forecast | calculate/calculate-object; fails if consumption impossible |
| Preview и выставить коммуналку | owner; draft action; UI: да | Bill/group | Due date set, advances applied, messages sent per linked tenant; partial delivery reported | issue-preview/issue; high impact + confirmation |
| Отметить оплату поставщику | owner; bill action; UI: да | Bill | provider_paid timestamp | provider-paid endpoint |
| Принять оплату строки | owner; bill line / Android; UI: да | Amount/date/source | Receipt and line balance/status updated; advance ledger when applicable | utility-line payment endpoint |
| Удалить черновик счёта | owner; bill action; UI: да | Deletable bill | Permanent removal under backend constraints | `DELETE /api/utility-bills/{id}`; destructive |
| Добавить расход | owner; Expenses / Android; UI: да | Date, scope, category, amount, funds, method, description | Personal source enters pending compensation | expenses POST |
| Компенсировать расход | owner; expense action / Android; UI: да | Pending personal expense | compensation_status=compensated + timestamp | compensate endpoint |
| Скачать отчёт | owner/guest; Reports / Android/Telegram links; UI: да | Date/month/entity scope | Excel or JSON monthly detail | report GET endpoints; guest allowed |
| Принять месячный отчёт | owner; dashboard/report; UI: да | Year/month/kind | Accepted key stored; data not rewritten | monthly accept endpoint |

### 5.5. Коммуникации, автоматизация, Hermes и администрирование

| Capability | Actor, entry point, UI | Inputs / entities | Result, rules, statuses, errors | Backend / risk / design note |
| --- | --- | --- | --- | --- |
| Preview/send сообщения | owner; Messages; UI: да | Target lease, template, custom text | Render uses current debts and effective details; sent/failed MessageLog | preview/send endpoints |
| Массовая рассылка | owner; Messages; UI: да | all or lease IDs, text | Per-recipient sent/failed/skipped; unlinked chats skipped | `/api/messages/broadcast`; bulk + confirmation desirable |
| Читать/вести диалог | owner; Incoming; UI: да | Dialog, limit, outgoing text | Incoming/outgoing timeline; Telegram delivery | bot-dialog endpoints |
| Настроить и запустить reminders | owner/background; Automation; UI: да | Global cutoff/cadence, per-lease overrides | Due reminders with duplicate suppression and pause modes | settings, lease cadence/automation, reminders run |
| Подключить Telegram | owner; Settings; UI: да | token/secret/base URL | webhook setup/info/test | integration endpoints; secrets never returned |
| Работать с Hermes cases | owner; Hermes / Android; UI: да | Filters, case action, snooze/close | Case status/next review updates | Hermes case endpoints |
| Управлять proposals | owner; Hermes / Android/Telegram; UI: да | Confirm/reject, optional PIN | executed/rejected/expired/failed after revalidation | proposal endpoints; safety critical |
| Управлять commitments/preferences/strategies/skills | owner; Hermes; UI: да, покрытие неоднородно | Entity-specific fields/actions | Stateful AI control center | grouped Hermes endpoints; backend richer некоторых экранов |
| Контролировать AI usage/settings | owner; Hermes/Settings; UI: да | Models, budgets, limits, modes, instructions | Cost/limits/settings; key test | usage/settings/performance/ai-test |
| Экспортировать/импортировать базу | owner; Settings; UI: да | JSON, confirmation text, backup flag | Inspect → optional backup → full replace; sequences repaired | admin database endpoints; destructive, explicit confirmation |
| Скачать Android APK | authenticated panel; Settings | Existing built artifact | File download if present | `/mobile-app.apk`; repository does not commit release APK |

## 6. State Machines

### 6.1. Портфель и реквизиты

```text
Object: active ──archive(no active leases)──→ archived
        archived ──restore──→ active (apartments remain disabled until enabled)
        empty ──confirmed delete──→ deleted

Apartment: active ──disable(no active lease)──→ inactive ──enable──→ active
           never used ──confirmed delete──→ deleted

Payment Profile: active ──archive──→ inactive ──restore──→ active
                 unassigned ──confirmed delete──→ deleted
```

Запрещено: архивировать объект/квартиру с активным договором; удалять объект со структурой; удалять квартиру с историей; назначать архивный профиль новой связи; удалять назначенный профиль.

### 6.2. Договор и аренда

```text
Lease: active ──move out──→ closed
       active ──transfer──→ old closed + new active
       active ↔ ignored informational mode

RentCharge:
pending ──partial payment──→ partial ──full payment──→ paid
pending/partial ──due passed──→ overdue
pending/partial/overdue ──deferral──→ deferred
deferred ──deferral ends, debt remains──→ overdue
any payable ──overpayment──→ paid_ahead
```

Обе части аренды считаются отдельно. Общий `paid` возможен только когда закрыты IP и personal portions.

### 6.3. Коммунальные счета, чеки и расходы

```text
UtilityBill: draft ──issue──→ issued ──provider payment flag──→ provider_paid=true
UtilityLine: draft ──issue──→ issued ──part payment──→ partial ──full──→ paid
                                      └─due passed──→ overdue
                                      └─overpay──→ paid_ahead

Receipt: parsed → accepted
                ↘ suspicious → owner moderation → accepted / ignored
                ↘ rejected
                ↘ duplicate

Expense(personal): pending ──compensate──→ compensated
Expense(other funds): not_required
```

### 6.4. Hermes

```text
OperationalCase: new → active → waiting_owner / waiting_tenant / auto_monitoring
                      ↘ snoozed → active
                      ↘ resolved / closed

Proposal: pending → executed
                  ↘ rejected
                  ↘ expired
                  ↘ failed

Skill: draft → proposed → active → disabled
                         ↘ version / rollback creates controlled version history
```

## 7. Main User Flows

### Flow 1. Добавить объект и подготовить квартиру к заселению

- Trigger: в портфеле появился новый дом/площадка.
- Actor: owner.
- Preconditions: активная owner-session; при выборе профиля он active.
- Steps: создать объект → выбрать default profile или global → создать одну/несколько квартир → проверить effective profile → при необходимости задать apartment override → открыть onboarding.
- Alternatives: объект можно оставить пустым; квартира может наследовать global через объект без profile.
- Errors: duplicate object name; archived object; archived/missing profile; invalid numeric fields.
- Result: активная свободная квартира появляется во всех apartment selectors и готова к lease onboarding.

### Flow 2. Заселить жильца

- Trigger: согласованы квартира и условия.
- Preconditions: квартира active и без активного неигнорируемого lease.
- Steps: выбрать квартиру → внести контакты/даты/две части аренды/залог → сохранить → backend создаёт Tenant + Lease и генерирует RentCharge.
- Alternatives: минимум данных, payment day по дате заезда; договор может быть informational/ignored.
- Errors: квартира занята/не найдена, неверные даты/день.
- Result: жилец виден в портфеле, финансах, сообщениях и отчётах.

### Flow 3. Разнести квартиры по разным реквизитам

- Trigger: разные получатели внутри одного объекта.
- Preconditions: профили созданы и active.
- Steps: назначить Profile 1 объекту → оставить квартиры 1–2 на inheritance → открыть квартиры 3–4 и выбрать Profile 2 → проверить effective labels.
- Alternative: объект без default использует global; override можно очистить и вернуть inheritance.
- Errors: inactive profile, missing profile.
- Result: сообщения, `/requisites`, tenant AI и проверка новых чеков используют профиль соответствующей квартиры.

### Flow 4. Изменить реквизиты

- Trigger: сменился счёт/банк/получатель.
- Steps: открыть profile → оценить usage → изменить → сохранить.
- Consequence: следующий сгенерированный текст и новая проверка receipt используют новое значение; прошлые MessageLog/PaymentReceipt/document остаются прежними.
- Edge: поведение для ранее созданного unpaid charge требует продуктового решения о snapshot; сейчас действует текущий effective profile.

### Flow 5. Провести ежемесячную аренду

- Trigger: наступил период оплаты.
- Steps: charge существует/генерируется → owner видит pending/today/overdue → жилец получает шаблон с двумя назначениями → приходит платёж/чек → allocation закрывает IP/personal → status пересчитывается.
- Alternatives: partial, overpay, manual receipt, deferral, promise/payment situation.
- Errors: сумма не раскладывается, recipient mismatch, duplicate receipt.
- Result: accepted receipts, актуальный debt/status, audit/message history.

### Flow 6. Принять и проверить чек жильца

- Trigger: tenant отправил документ Telegram.
- Preconditions: чат привязан к lease.
- Steps: сохранить файл → parse → dedupe → определить channel → проверить recipient against effective profile → распределить сумму.
- Alternatives: suspicious/rejected → owner opens document and moderates/ignores.
- Errors: unsupported/missing file, no debt, mismatched recipient, ambiguous allocation.
- Result: accepted payment or explicit review queue.

### Flow 7. Рассчитать и выставить коммуналку

- Trigger: есть boundary readings и тариф.
- Preconditions: service/meters/tariff configured; occupied apartments known.
- Steps: выбрать service/object + period → calculate draft → inspect warnings/forecast/lines → preview recipients → confirm issue → advances apply → linked tenants receive messages.
- Alternatives: allow estimate; object calculation creates several drafts; unlinked tenants are skipped.
- Errors: apartment consumption above object, missing readings/tariff, zero successful sends with failures.
- Result: issued lines with due dates, message delivery summary and provider debt state.

### Flow 8. Оформить переезд или выезд

- Trigger: жилец меняет квартиру или уезжает.
- Steps: выбрать operation/date/target → backend validates vacancy → closes old lease → optionally opens new lease → updates charges/utilities/history → sends configured notifications.
- Edge: final utility state and ignored leases.
- Result: no overlapping active lease; tenant history preserved.

### Flow 9. Компенсировать личный расход

- Trigger: owner оплатил расход личными деньгами.
- Steps: create expense with personal source → dashboard shows pending → owner confirms compensation.
- Result: compensated timestamp/status; reports update.

### Flow 10. Отправить напоминание или рассылку

- Trigger: due/overdue debt or owner message.
- Steps: choose tenant/template or bulk targets → preview → send → record MessageLog.
- Alternatives: automatic cadence; tenant situation pauses; unlinked tenant skipped.
- Result: per-recipient status; no fake success.

### Flow 11. Закрыть месяц и скачать отчёт

- Trigger: завершён календарный месяц или предварительная проверка после 25-го.
- Steps: open monthly issues → resolve/accept → download relevant Excel.
- Result: accepted marker; financial rows remain source of truth.

### Flow 12. Разобрать Hermes case

- Trigger: case/briefing identifies risk.
- Steps: open case → inspect scoped facts → close/snooze or review proposal → confirm/reject → backend revalidates and logs.
- Alternative: critical action requests PIN; expired/state-changed proposal fails safely.
- Result: executed action or explicit no-change status.

## 8. Objects & Apartments Management

### 8.1. Реализованные entry points

- список и occupancy объектов на dashboard;
- формы создания объекта и квартиры в web Portfolio;
- карточка объекта с редактированием, default profile, archive/restore и допустимым delete;
- apartment registry с tenant, ODN share, advance, effective profile, active toggle, edit/delete;
- onboarding selector автоматически получает новые active vacant apartments;
- Android и отчёты получают созданные записи через общий bootstrap/API.

### 8.2. Интеграция нового объекта

После добавления квартиры работают lease onboarding, rent charges, receipts, debts, expenses, history/owner reports, messaging targets и Hermes domain scope. Коммунальные services/meters для нового объекта автоматически не создаются; это не скрывается и вынесено в Open Product Decisions.

### 8.3. Безопасное удаление

- Пустой объект без квартир/services можно удалить после confirmation.
- Объект со структурой только архивируется; active leases блокируют архив.
- Архив объекта отключает квартиры. Restore объекта не включает квартиры автоматически.
- Квартира с любой финансовой/договорной/счётчиковой историей не удаляется; используется inactive state.

## 9. Payment Details Management

### 9.1. Состав набора

Система хранит только уже существовавшие поля:

- IP: получатель, ИНН, ОГРНИП, расчётный счёт, банк, БИК, корреспондентский счёт, ИНН и КПП банка;
- personal transfer: получатель, телефон, банк;
- UI metadata: понятное имя набора, заметка, active/archive, created/updated timestamps.

### 9.2. Effective details

Resolver не выбирает первый доступный набор. Apartment override имеет высший приоритет, затем object default, затем global settings. Архивный, но уже назначенный profile продолжает быть effective и показывается предупреждением. Это предотвращает тихую смену получателя.

### 9.3. Где применяются effective details

- шаблоны rent/utility reminders;
- `/requisites` и кнопка реквизитов tenant Telegram;
- tenant AI context для долгового диалога;
- recipient validation новых Telegram receipts;
- preview сообщений.

### 9.4. История

PaymentReceipt сохраняет фактического получателя, raw parsed details и документ. MessageLog сохраняет уже отправленный текст. Эти записи не обновляются при смене profile. RentCharge и UtilityBillLine не содержат snapshot ожидаемого profile; текущий resolver используется при следующем сообщении/проверке — см. Open Product Decisions.

## 10. Search / Filters / Bulk Operations

| Collection | Search | Filters / sort / pagination | Grouping / bulk | Import / export |
| --- | --- | --- | --- | --- |
| Dashboard/attention | Global client text filter on rendered cards | Severity/business order; no pagination | Cases grouped by issue type | Monthly/owner reports |
| Objects/apartments/profiles | Global client text filter covers cards/table | Objects by name; apartments by object rank/sort/name; profiles active then name; no pagination | No selection/bulk edit | Full DB export only |
| Leases/tenants | Global client text filter | Active first, object/apartment/tenant; no pagination in UI | No bulk lease action | History/owner Excel; release baseline import |
| Rent charges/payments | Global text + date range | Backend `start/end/limit/offset`; web date filters, Android status filters | Allocation may split one payment across charges | Rent/debt/history reports |
| Manual debts | Within lease modal | Backend per lease; no pagination | No bulk | Included in debt/history reports |
| Receipts | Suspicious queue + history | Status-specific backend query; no general pagination UI | No multi-select; moderation one receipt at a time | Document view; DB export |
| Meters/readings | Global text | Sorted by object/service/meter | Batch readings supported | Utility reports |
| Utility bills | Global text + period/service controls | Grouped by object/service/period; no pagination | Calculate whole object; issue group; no checkbox bulk | Utility Excel |
| Expenses | Global text | Backend limit/offset; web working list | No multi-select | Expense Excel |
| Messages/dialogs | Dialog list and current panel search | Messages limit; targets selected/all | Broadcast supports all or explicit lease IDs | Message logs in DB export |
| Hermes cases | Filters status/severity/property | Backend filters; runs have limit | No arbitrary bulk selection; grouped proposals are controlled backend operations | Usage/performance data only |
| Reports | Date/month selectors | By report type/scope | Not applicable | Excel downloads |

Backend capability without full UI: pagination parameters for rent charges/expenses, detailed Hermes debug/run data, and some lower-level administration operations. No generic server-side full-text search exists.

## 11. UI States & Edge Cases

| State | Required presentation |
| --- | --- |
| Initial loading | Existing progressive overlay: auth → dashboard → registry → financial sections; show step and prevent duplicate actions |
| Background refresh | Keep current data, indicate refresh; mutation success may be followed by «saved, but screen did not fully refresh» |
| Empty portfolio | Explain sequence: create object → apartment → lease; empty object is valid |
| No apartments available | Onboarding select shows «Нет свободных квартир»; archived/occupied apartments excluded |
| No profiles | Global card remains; object/apartment selectors offer global/inherit |
| Missing effective details | Explicit warning; generated text uses «не указан», never a random profile |
| Archived effective profile | Keep assignment and show warning; offer reassign/restore, do not silently fall back |
| No search results | Current implementation hides all rows without dedicated message; redesigned UI should distinguish from true empty |
| Validation error | Keep form values; show backend detail near form/action, not only disappearing toast |
| Conflict 409 | Explain safe next action: move out, detach profile, archive instead of delete, choose active profile |
| Permission denied | Guest stays in read-only allowed area; do not show owner controls |
| Partial delivery | Utility issue/broadcast shows sent, skipped unlinked and failed recipients separately |
| Forecast/estimated utility | Warning visible at draft, preview and issued bill levels |
| Destructive confirmation | Required for empty object/apartment/profile delete, receipt/lease/bill delete, DB import; show affected scope |
| Stale concurrent edit | Current backend is last-write-wins except DB conflicts; redesigned UI should refresh after 409 and avoid promising optimistic locking |
| External integration failure | Telegram/AI error must preserve business data and show failed state; never report sent/executed falsely |

## 12. Existing UI Coverage

| Capability | Backend | Existing UI | Route / surface | Notes |
| --- | --- | --- | --- | --- |
| Create object/apartment | Полный | Полный web | Portfolio forms | Существовал базовый create; теперь добавлены edit/cards/profile selection |
| Edit/archive/delete object | Полный, новое | Полный web, новое | Object cards | Delete only empty; archive blocks active leases |
| Edit/disable/delete apartment | Полный, расширено | Полный web | Apartment registry | Android умеет toggle active, но не profile editor |
| Payment Profiles CRUD/archive/usage | Полный, новое | Полный web, новое | Portfolio profiles | Android editor отсутствует; effective result всё равно приходит через shared data |
| Global payment details | Полный legacy/fallback | Полный web, расширено | Settings → Payments | Добавлены все ранее backend-supported IP fields |
| Effective details in messages/receipts | Полный, новое | Автоматически | Telegram/templates/AI | Не отдельный экран |
| Lease onboarding/edit/move-out | Полный | Web + Android | Portfolio/Properties | Transfer доступнее в web |
| Rent charges/payments/history | Полный | Web + Android | Finance/Payments | Backend pagination шире web UI |
| Manual debts | Полный | Web | Lease debt modal | Android coverage ограничено |
| Receipt OCR/matching/moderation | Полный рабочий контур | Web + Android review, Telegram intake | Messages/Payments | Фото может приниматься transport layer, основной рекомендуемый формат PDF document |
| Meter readings | Полный | Web + Android | Meters | Есть batch input |
| Create utility service/meter | Отсутствует публичный create API | UI отсутствует | Seed/import only | Существенный gap для полностью нового utility object |
| Tariff create | Полный | Web + Android | Tariffs | Нет edit/delete endpoint |
| Utility calculation/issue/pay | Полный | Web + Android | Utilities | Partial delivery state важен |
| Expenses/compensation | Полный | Web + Android | Expenses | Нет edit/delete endpoint |
| Reports | Полный | Web + Android download + Telegram links | Reports | Guest download allowed |
| Message preview/send/broadcast | Полный | Web; Telegram delivery | Messages | No generic scheduling UI beyond reminders |
| Dialogs | Полный | Web | Incoming | Limit endpoint exists |
| Reminder automation | Полный | Web | Automation | Per-lease cadence API богаче основной формы |
| Hermes cases/proposals/usage | Полный | Web + Android + Telegram | Hermes | Skills/preferences/strategies coverage неоднородно |
| DB inspect/import/export/backup | Полный | Web | Settings | Destructive import guarded by confirmations |
| Performance/debug | Полный | Web partial | Settings/Hermes | Run debug backend exists, UI technical |

Категории legacy/возможно не используется: старые глобальные реквизиты остаются обязательным compatibility fallback; `/health` дублирует `/healthz`; часть Android alias endpoints существует для одного backend representation.

## 13. Frontend Routes / Entry Points

Web — single-page application на `/`; внутренние разделы переключаются tabs, а не URL routes.

| Entry point | Пользовательские действия |
| --- | --- |
| Dashboard | Сводка, attention, occupancy, quick actions, monthly issues |
| Portfolio | Object create/cards/edit/archive/delete; apartment create/registry/edit/active/delete/advance; lease onboarding/edit/transfer/move-out/debts; payment profiles |
| Finance | Rent charges, manual payments, allocation, payment history |
| Meters | Single/batch readings |
| Utilities | Draft calculation, preview/issue, payment/provider state, timeline |
| Tariffs | Tariff history/create |
| Expenses | Create/compensate |
| Reports | Date/month selection, downloads, monthly acceptance |
| Incoming | Telegram dialogues and direct replies |
| Messages | Targets, templates, preview, send, broadcast, suspicious receipts |
| Automation | Global and per-lease reminder behavior, manual run |
| Hermes | Overview, cases, proposals, commitments, strategies, preferences, skills, usage/runs/settings |
| Settings | General, integrations, AI, security, global payment fallback, database operations, APK download |
| Telegram tenant | `/start`, `/requisites`, `/debts`, receipt upload, finance questions/status callbacks |
| Telegram owner | `/status`, `/reports`, `/app`, `/audit`, `/run_reminders`, AI conversation/proposal callbacks |
| Android | Dashboard, Properties, Payments, Tasks, More/Hermes using shared JSON API |

Эти entry points перечисляют необходимую функциональность, но не предписывают будущую sidebar/layout architecture.

## 14. API / Backend Reference

Полный endpoint reference вынесен в [API_BACKEND_REFERENCE.md](API_BACKEND_REFERENCE.md), чтобы продуктовая карта не превращалась в перечень URL. В нём для каждой operation указаны auth, inputs, result, validation/errors, entities и соответствующая capability.

Backend composition: `rental_manager/main.py`; persisted entities: `rental_manager/models.py`; reusable calculations and operations: `rental_manager/services/*`; schema history: `migrations/versions/*`; web: `static/index.html`, `static/app.js`; Android: `android/RentalManager`.

## 15. Sample Data

Все данные ниже вымышлены; номера и счета маскированы.

### 15.1. Payment Profiles

| Profile | IP details | Personal transfer | State / usage |
| --- | --- | --- | --- |
| «Основной счёт — центр» | ИП Соколова Анна Андреевна; счёт `40702••••••••9012`; БИК `0445•••••` | Анна С.; `+7 900 •••-12-34`; Сбербанк | active; default «Дом на Лесной» |
| «Северный корпус» | ИП Ветров Максим Олегович; счёт `40702••••••••1840` | Максим В.; `+7 913 •••-48-20`; Альфа-Банк | active; override Л-3 и Л-4 |
| «Гостевые помещения» | ИП Соколова Анна Андреевна; счёт `40702••••••••7751` | Анна С.; `+7 900 •••-67-51`; Т-Банк | active; default «Гостевой корпус» |
| «Старый расчётный счёт» | ИП Соколова А.А.; счёт `40702••••••••0028` | — | archived; всё ещё назначен одной закрытой конфигурации до переноса |

### 15.2. Objects, apartments and inheritance

| Object | Default | Apartment | Effective details | Occupancy |
| --- | --- | --- | --- | --- |
| Дом на Лесной | Основной счёт — центр | Л-1 | inherited Основной | Елена Гордеева, active lease, rent partial |
| Дом на Лесной | Основной счёт — центр | Л-2 | inherited Основной | свободна |
| Дом на Лесной | Основной счёт — центр | Л-3 | override Северный корпус | Тимур Агеев, active, rent paid |
| Дом на Лесной | Основной счёт — центр | Л-4 | override Северный корпус | Вера Ланская, overdue + promise |
| Гостевой корпус | Гостевые помещения | Студия 1 | inherited Гостевые | Нина Корнеева, active, utility draft |
| Гостевой корпус | Гостевые помещения | Студия 2 | inherited Гостевые | закрытый lease, квартира active |
| Мастерская-лофт | global fallback | Зал A | global fallback | объект создан, квартира свободна, коммунальные services ещё не настроены |

### 15.3. Financial and operational states

| Person / apartment | Example state |
| --- | --- |
| Елена Гордеева, Л-1 | Аренда 42 000 ₽: IP 32 000 ₽ paid, personal 10 000 ₽ pending → charge partial |
| Тимур Агеев, Л-3 | Аренда paid; коммунальный аванс 3 500 ₽; последний чек accepted |
| Вера Ланская, Л-4 | 38 000 ₽ overdue; promise date 31.08.2026; reminders paused until review |
| Нина Корнеева, Студия 1 | Utility draft 4 870 ₽, forecast warning due to missing final apartment reading |
| Закрытый договор Студии 2 | Ended 12.08.2026; history and receipts read-only, apartment available |
| Расход «Замена циркуляционного насоса» | 18 600 ₽, personal funds, compensation pending |

## 16. Open Product Decisions

### 16.1. Snapshot реквизитов для начисления

1. Вопрос: должен ли RentCharge/UtilityBillLine фиксировать profile/version на момент создания или выставления?
2. Почему важно: сейчас смена assignment/profile меняет следующие инструкции и проверку новых чеков даже для старого unpaid charge.
3. Варианты: всегда current effective; snapshot at charge creation; snapshot at issue/message; explicit effective-from date/version.
4. Рекомендация: versioned profile + snapshot при первом выставлении/отправке, с явным owner action «перевести открытые долги на новые реквизиты».
5. Последствия: нужны assignment history/version fields и UI-warning; зато audit и спорные оплаты однозначны.

### 16.2. Разные реквизиты для аренды и коммуналки

1. Вопрос: достаточно ли одного profile, содержащего IP + personal, для всех назначений квартиры?
2. Почему важно: текущая бизнес-логика отправляет IP portion на расчётный счёт, personal rent и utilities — по телефону. В будущем получатели utilities могут отличаться.
3. Варианты: единый profile; отдельные rent/utility profile links; channel-specific links.
4. Рекомендация: оставить единый profile сейчас; добавлять channel-specific assignment только по подтверждённому кейсу.
5. Последствия: отдельные links усложнят форму, resolver, receipt matching и history.

### 16.3. Коммунальная конфигурация нового объекта

1. Вопрос: как owner создаёт UtilityService и Meter для нового объекта?
2. Почему важно: object/apartment уже готовы к аренде, но public create API/UI для services/meters отсутствует.
3. Варианты: wizard с явным выбором; шаблон-клон существующего объекта; автоматический стандартный набор; оставить seed/import-only.
4. Рекомендация: отдельный setup wizard с явным выбором service, due days и meters; не создавать финансовую структуру молча.
5. Последствия: новые endpoints/forms и validation; utility-ready status у объекта.

### 16.4. Архивирование объекта и дочерних квартир

1. Вопрос: при restore объекта надо ли автоматически возвращать прежние active states квартир?
2. Почему важно: текущая безопасная реализация при archive отключает все квартиры, а restore оставляет их выключенными.
3. Варианты: manual re-enable; snapshot child states; object active как независимый parent filter.
4. Рекомендация: хранить object archive независимо и считать квартиру effective active только если active оба уровня; не переписывать child state.
5. Последствия: потребуется обновить запросы/serializers, но restore станет предсказуемым.

### 16.5. Permissions beyond owner/guest

1. Вопрос: нужен ли отдельный manager/accountant/read-only-by-object?
2. Почему важно: profiles, imports и financial moderation сейчас доступны одному owner role.
3. Варианты: оставить single-owner; capability permissions; object-scoped roles.
4. Рекомендация: не проектировать granular UI до реального второго оператора; при появлении — capability + object scope.
5. Последствия: middleware, audit actor, selectors и redaction потребуют расширения.

### 16.6. Concurrent edits

1. Вопрос: нужна ли optimistic concurrency для object/profile/lease?
2. Почему важно: updated_at у profile есть, но PATCH не проверяет версию; последний save побеждает.
3. Варианты: last-write-wins; `If-Match`/version; edit locks.
4. Рекомендация: version/updated_at conflict для финансовых настроек, если появится несколько owner sessions/users.
5. Последствия: 409 stale state и UI merge/refresh flow.

### 16.7. Legacy global fallback

1. Вопрос: переносить ли глобальные реквизиты в обычный named profile?
2. Почему важно: сейчас это отдельный compatibility level и отдельная форма.
3. Варианты: сохранить навсегда; one-time migration в «Основной» profile; сделать global pointer на profile.
4. Рекомендация: после подтверждения snapshot semantics создать global default profile pointer и оставить legacy keys read-only fallback на переходный период.
5. Последствия: проще единый UI/usage, но нужна безопасная data migration и backward compatibility.

## 17. Изменения для новой функциональности

Добавлены/изменены:

- `rental_manager/models.py` — PaymentProfile и ссылки объекта/квартиры;
- `rental_manager/services/payment_profiles.py` — единый resolver, payload/serialization helpers;
- `rental_manager/main.py` — CRUD, archive/delete safety, registry payload, effective details в messages/AI/receipt validation;
- `migrations/versions/20260828_01_payment_profiles.py` — forward schema migration;
- `static/index.html`, `static/app.js` — object cards/editors, apartment profile controls, global profile registry и все поддержанные payment fields;
- `tests/test_payment_profiles.py` — inheritance, rental integration, receipt validation и destructive guards;
- `docs/API_BACKEND_REFERENCE.md` — полный reference;
- `docs/CURRENT_STATE.md`, `docs/DECISIONS.md`, `docs/INDEX.md`, `docs/MVP_SPEC.md`, `docs/PROJECT_MAP.md`, `docs/TESTING.md` — актуализация состояния и правил.
