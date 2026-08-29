# Rental Manager API / Backend Reference

Фактический reference на 28 августа 2026 года. Пользовательский смысл операций описан в [PRODUCT_UI_CAPABILITY_MAP.md](PRODUCT_UI_CAPABILITY_MAP.md). JSON-схемы в проекте задаются runtime validation внутри FastAPI handlers, а не отдельными Pydantic DTO; поэтому здесь перечислены значимые поля, а не вымышленная формальная схема.

## Общий контракт

| Обозначение auth | Условие |
| --- | --- |
| Public | Session не нужна. Root/static/health/login, Telegram webhook и APK transport проверяются своими правилами |
| Guest+ | PanelSession роли guest или owner. Guest фактически разрешены только GET/HEAD bootstrap/app-state и `/api/reports/*` |
| Owner | PanelSession role=owner; для POST/PUT/PATCH/DELETE обязателен валидный `X-CSRF-Token` |
| Telegram | Webhook secret/transport validation + owner allowlist или tenant chat link внутри handler |
| Safety | Owner плюс proposal TTL/state hash/idempotency; для critical/mass action может потребоваться повторный owner PIN |

Общие ответы ошибок: `400` некорректные поля/невозможная операция, `401` нет panel session, `403` guest/CSRF/authorization, `404` сущность или безопасный файл не найден, `409` конфликт или защитный запрет, `422` FastAPI transport parsing, `429` login/rate limit, `5xx` integration/runtime failure. Финансовые mutation могут возвращать расширенную per-item информацию, warnings или partial delivery; UI не должен сводить это к одному boolean.

## 1. Public, auth, bootstrap and settings

| Method / path | Auth | Purpose / capability | Request → response | Validation, errors, entities |
| --- | --- | --- | --- | --- |
| `GET /` | Public | Открыть web SPA | — → `static/index.html` | File must exist; UI shell |
| `GET /healthz` | Public | Deployment health | — → status/build/provider | Read-only |
| `GET /health` | Public | Compatibility health alias | — → same as healthz | Legacy alias |
| `GET /mobile-app.apk` | Public transport | Скачать Android artifact | — → APK file | 404 if artifact absent; binary not committed |
| `GET /api/auth/status` | Public | Узнать session role/CSRF state | Cookie → authenticated/role/csrf | PanelSession |
| `POST /api/auth/pin` | Public | Войти owner/guest | PIN, remember → role + cookies + CSRF | Compromised/invalid PIN, throttling/blocked attempts |
| `POST /api/auth/logout` | Owner/guest + CSRF | Завершить текущую session | Cookie → `{ok}` and cleared cookies | Revokes PanelSession |
| `GET /api/bootstrap` | Guest+ | Первичная модель приложения | — → today/auth/settings/dashboard + owner registry | Guest receives public settings and aggregate dashboard only |
| `GET /api/app-state?sections=...` | Guest+ | Progressive section loading | Known section list → keyed payload + perf headers | Unknown section 400; owner-only detailed sections return empty for guest |
| `GET /api/settings` | Owner | Прочитать owner settings | — → non-secret values + configured flags | Secrets never returned; AppSetting/env resolution |
| `POST /api/settings` | Owner | Сохранить global/integration/AI/security settings | Allowed key/value map → normalized settings | Unknown ignored; AI ranges/model/time validated; blank secrets/PIN preserve current; PIN revokes other sessions |
| `GET /api/performance` | Owner | Мониторинг backend/AI/data counts | — → request/background/AI usage snapshot | Read-only technical view |
| `POST /api/ai/test` | Owner | Проверить AI key/model | — → model/result/usage cost | AI disabled/budget/daily feature limits and provider failure |

## 2. Hermes Core

Android aliases return the same representation and permissions as corresponding web endpoints.

| Method / path | Auth | Purpose | Request → response | Validation / entities / capability |
| --- | --- | --- | --- | --- |
| `GET /api/hermes`, `GET /api/android/hermes/summary` | Owner | Control-center summary | — → overview/cases/briefing/counts | OperationalCase, Proposal, Commitment; Hermes overview |
| `GET /api/hermes/overview` | Owner | Compact Hermes metrics | — → counts/severity/waiting | Read-only |
| `GET /api/hermes/cases`, Android alias | Owner | List/filter cases | status, severity, property_id → case cards | Unknown/missing filters produce empty/validated behavior; case management |
| `GET /api/hermes/cases/{case_id}`, Android alias | Owner | Open case details | id/alias → scoped facts/timeline/actions | 404 unknown; no raw DB access |
| `POST /api/hermes/cases/{case_id}/close` | Owner | Close case | optional reason/note → case | 404; transition validation; OperationalCase |
| `POST /api/hermes/cases/{case_id}/snooze` | Owner | Snooze case | until/days/reason → case | Date validation; next review/suppression |
| `GET /api/hermes/commitments`, Android alias | Owner | List owner promises | — → commitments | OwnerCommitment |
| `POST /api/hermes/commitments/{id}/complete` | Owner | Mark commitment done | — → commitment | 404/state rules |
| `POST /api/hermes/commitments/{id}/postpone` | Owner | Move commitment date | due date/hours/note → commitment | Date/state validation |
| `GET /api/hermes/preferences`, Android alias | Owner | List structured preferences | — → preferences | OwnerPreference |
| `POST /api/hermes/preferences` | Owner | Create preference | scope/key/value/mode/validity → preference | Required key/scope and JSON normalization |
| `PATCH /api/hermes/preferences/{id}` | Owner | Update preference | supported fields → preference | 404/validation |
| `DELETE /api/hermes/preferences/{id}` | Owner | Disable preference | id → disabled representation | Soft disable, not physical delete |
| `GET /api/hermes/skills` | Owner | List skill versions/states | — → skills | AiSkill |
| `POST /api/hermes/skills/{id}/dry-run` | Owner | Test skill without execution | sample context → dry-run result | No mutation tools executed; skill status/preconditions |
| `POST /api/hermes/skills/{id}/activate` | Owner | Activate skill | — → skill | Valid draft/proposed state required |
| `POST /api/hermes/skills/{id}/disable` | Owner | Disable skill | — → skill | State transition |
| `POST /api/hermes/skills/{id}/rollback` | Owner | Roll back skill version | — → new/current version | Version history preserved |
| `POST /api/hermes/skills/{id}/version` | Owner | Create next skill version | changed definition → skill | Allowed tools/preconditions/safety validated |
| `GET /api/hermes/strategies` | Owner | List tenant strategies | — → strategies | TenantStrategyProfile |
| `PATCH /api/hermes/strategies/{id}` | Owner | Tune strategy | channel/window/escalation threshold/source → strategy | Threshold 1–20; supported channel/window |
| `POST /api/hermes/strategies/{id}/reset` | Owner | Reset manual strategy | — → derived/default strategy | 404 |
| `GET /api/hermes/proposals`, Android alias | Owner | List action proposals | — → proposals | AgentActionProposal |
| `POST /api/hermes/proposals/{id}/confirm`, Android alias | Safety | Execute proposal | optional confirmation_pin → proposal/result | Pending, unexpired, state hash, idempotency and safety-level validation; 409/403/expired/failed |
| `POST /api/hermes/proposals/{id}/reject`, Android alias | Owner | Reject proposal | id → proposal | Pending only; action not executed |
| `GET /api/hermes/usage`, Android alias | Owner | View Hermes feature/cost usage | — → daily/monthly counters | AiFeatureUsageDaily/AiUsageDaily |
| `GET /api/hermes/runs?limit=` | Owner | List agent runs | bounded limit → run summaries | HermesAgentRun |
| `GET /api/hermes/runs/{run_id}` | Owner | Debug one run | run id → manifest/envelope/result/error | 404; technical UI |
| `GET /api/hermes/briefing/preview` | Owner | Preview next briefing | — → text/items/length | May be empty if no changed cases |
| `GET /api/hermes/settings`, Android alias | Owner | Read Hermes settings | — → safe settings | Secret-free |
| `POST /api/hermes/settings`, Android alias | Owner | Save Hermes settings | allowed flags/limits/times → settings | Same normalization/ranges as general settings |

## 3. Monthly progress, database administration and lease automation

| Method / path | Auth | Purpose | Request → response | Validation / entities / capability |
| --- | --- | --- | --- | --- |
| `GET /api/month-progress` | Owner | Operational snapshot for month | year, month → progress/status groups | Calendar validation; aggregates business tables |
| `GET /api/admin/database-export` | Owner | Download sanitized full export | — → JSON file | Excludes session/login/update tables and secrets |
| `POST /api/admin/database-backup` | Owner | Create protected server backup | exact confirmation → backup metadata/file | Encrypted secrets only; confirmation required |
| `POST /api/admin/database-import/inspect` | Owner | Inspect import before mutation | multipart JSON file → counts/warnings/version/scope | Format/version/table validation; no write |
| `POST /api/admin/database-import` | Owner | Replace database | file + `ИМПОРТ` + confirm_replace + create_backup → counts | Destructive; optional backup; protected secrets preserved; transaction and sequence reset |
| `POST /api/admin/import-release-baseline` | Owner | Import legacy release baseline | — → import summary | Explicit owner action; existing data/scope guards |
| `GET /api/leases/{id}/cadence` | Owner | Read per-lease reminder cadence | lease id → effective/global overrides | 404 Lease |
| `POST /api/leases/{id}/cadence` | Owner | Set per-template cadence | cadence map → effective settings | Supported cadence/template only |
| `DELETE /api/leases/{id}/cadence` | Owner | Clear per-lease overrides | id → global effective cadence | Soft reset |
| `PATCH /api/leases/{id}/automation` | Owner | Set lease automation controls | flags/cadence payload → lease automation view | 404/allowed values |
| `PATCH /api/leases/{id}/ignore` | Owner | Include/exclude lease from calculations | ignored boolean → lease | Uses explicit marker/internal state; financial dashboards recalc |

## 4. Telegram, messages and reminders

| Method / path | Auth | Purpose | Request → response | Validation / entities / capability |
| --- | --- | --- | --- | --- |
| `POST /api/integrations/telegram/webhook` | Telegram | Receive Telegram update | update JSON → `{ok}` quickly; queued/background handling | Secret/header/rate/dedupe; ProcessedTelegramUpdate; tenant/owner routing |
| `POST /api/integrations/telegram/set-webhook` | Owner | Configure Telegram webhook | optional base URL → provider result | Token/base URL/secret required; external API errors |
| `GET /api/integrations/telegram/webhook-info` | Owner | Inspect provider webhook | — → Telegram info | External API failure |
| `POST /api/integrations/telegram/send-test` | Owner | Send test to owner chat | optional text → delivery result | Token + owner chat required |
| `GET /api/messages/targets` | Owner | Load tenants available for messaging | — → linked/unlinked target list | Lease/Tenant/Telegram links |
| `POST /api/messages/preview` | Owner | Render tenant message | lease_id, template_key, optional charge/line/custom → text/context | Target/entity/template validation; uses effective payment profile |
| `POST /api/messages/send` | Owner | Send one tenant message | same target + text/template → MessageLog | Unlinked chat/length/provider failures |
| `POST /api/messages/broadcast` | Owner | Broadcast to selected/all tenants | all or lease_ids + text → sent/failed/skipped arrays | Dedupes chats, skips unlinked; bulk result |
| `GET /api/bot-dialogs` | Owner | List owner/tenant dialogues | — → dialog summaries/unread/latest | AiConversation/MessageLog representation |
| `GET /api/bot-dialogs/{id}/messages` | Owner | Open dialogue timeline | limit → messages | 404/limit bounds |
| `POST /api/bot-dialogs/{id}/send` | Owner | Reply in dialogue | text → saved/sent message | Max Telegram length, link required, provider failure recorded |
| `POST /api/reminders/run` | Owner | Run due reminders now | — → sent/skipped duplicate/legacy/unlinked/failed | Cutoff, global/per-lease cadence, PaymentSituation pause, daily suppression |

## 5. Objects, apartments and payment profiles

| Method / path | Auth | Purpose | Request → response | Validation / entities / capability |
| --- | --- | --- | --- | --- |
| `GET /api/objects` | Owner | List object cards with apartments/services/profile metadata | — → serialized objects | RentalObject, Apartment, UtilityService, PaymentProfile |
| `POST /api/objects` | Owner | Create property | `name`, optional short_code/notes/payment_profile_id → object | Name required/unique; selected profile exists and active; 409 duplicate |
| `PATCH /api/objects/{id}` | Owner | Edit/default/archive/restore | supported fields → object | Empty name/duplicate; inactive new profile; archive blocked by active leases; archive disables apartments |
| `DELETE /api/objects/{id}` | Owner | Delete empty property | id → `{ok}` | 409 if apartments/services; use archive; destructive |
| `POST /api/apartments` | Owner | Add unit to property | object_id, name, sort_order, odn_share_percent, optional profile/active → apartment | Active object required; profile active; name required; numeric conversion |
| `PATCH /api/apartments/{id}` | Owner | Edit/override/enable/disable unit | name/sort/ODN/profile/active → apartment | 404; disabling active lease 409; archived new profile 409; charges regenerated |
| `DELETE /api/apartments/{id}` | Owner | Delete never-used unit | id → `{ok}` | 409 if lease/meter/bill/payment/expense history; destructive |
| `PATCH /api/apartments/{id}/utility-advance` | Owner | Set fixed or auto advance | amount_override, note → apartment | Blank=auto; non-negative; history written |
| `GET /api/payment-profiles` | Owner | List profiles and direct usage links | — → profiles | PaymentProfile |
| `POST /api/payment-profiles` | Owner | Create reusable details | unique name + supported IP/personal fields + notes/active → profile | Name required/unique; strings normalized |
| `PATCH /api/payment-profiles/{id}` | Owner | Edit/archive/restore | supported fields → profile | 404; duplicate/blank name; historical receipts untouched |
| `DELETE /api/payment-profiles/{id}` | Owner | Delete unused profile | id → `{ok}` | 409 when object/apartment assigned; destructive |

Effective resolution is not a public endpoint: shared backend logic resolves apartment override → object default → global settings and feeds message context, tenant requisites/AI and receipt validation.

## 6. Tenants and leases

| Method / path | Auth | Purpose | Request → response | Validation / entities / capability |
| --- | --- | --- | --- | --- |
| `GET /api/tenants` | Owner | List tenant directory | — → tenants | Active first/name sort |
| `GET /api/leases` | Owner | List active/history contracts | — → leases | Lease joined to Tenant/Apartment |
| `POST /api/leases/onboard` | Owner | Onboard tenant | apartment, contacts, start/end, payment_day, IP/personal/deposit/notes/ignored → lease | Apartment required/free; date/day/money validation; creates Tenant+Lease+charges |
| `PATCH /api/leases/{id}` | Owner | Edit tenant/contract terms | supported tenant+lease fields → lease | 404; vacancy/date/day validation; recalculation/generation |
| `POST /api/leases/{id}/transfer` | Owner | Move tenant | target apartment/date/terms → new/old lease payload | Target free; dates and utility transition validated; transactional |
| `POST /api/leases/{id}/move-out` | Owner | End occupancy | end_date and optional final utility inputs → result | Active lease/date rules; final notifications/lines may fail explicitly |
| `DELETE /api/leases/{id}` | Owner | Delete contract under safety rules | id → `{ok}`/summary | Dependencies/history restrictions; recalculates balances; destructive |

## 7. Rent charges, payments, receipts and manual debts

| Method / path | Auth | Purpose | Request → response | Validation / entities / capability |
| --- | --- | --- | --- | --- |
| `GET /api/rent-charges` | Owner | List charges | start, end, limit, offset → charges | Date/range and pagination; generates missing when configured by caller |
| `POST /api/rent-charges/generate` | Owner | Generate missing charges | optional until → created count/list | Lease calendar rules; unique lease+due |
| `POST /api/rent-charges/{id}/payments` | Owner | Pay a specific charge/channel | amount, channel, date, source/recipient/note → charge | Positive amount/channel/charge; allocation and status recalc |
| `GET /api/leases/{id}/payment-history` | Owner | View contract payment history | lease id → timeline/summary | 404 |
| `GET /api/tenants/{id}/payment-history` | Owner | View tenant history across apartments | tenant id → combined history | 404 |
| `POST /api/payment-receipts/manual` | Owner | Record/allocate manual payment or expense-fund receipt | kind/lease/amount/channel/date/target/source/status/notes → receipt(s)/allocation | Positive amount; exact/auto allocation; unmatched amount refused or explicit kind |
| `PATCH /api/payment-receipts/{id}` | Owner | Correct receipt allocation/metadata | supported target/channel/amount/date fields → receipt | 404; reverses/recalculates accepted balances safely |
| `DELETE /api/payment-receipts/{id}` | Owner | Delete receipt and reverse effects | id → `{ok}` | Removes linked expense/advance ledger records and recalculates; destructive |
| `GET /api/payment-receipts/suspicious` | Owner | Review queue | — → suspicious receipts | status=suspicious only |
| `GET /api/payment-receipts/{id}/document` | Owner | Open stored receipt | id → inline file | Path must remain under receipt storage; 404 unsafe/missing |
| `POST /api/payment-receipts/{id}/ignore` | Owner | Hide suspicious receipt | id → receipt | status=ignored + audit note |
| `POST /api/payment-receipts/{id}/moderate` | Owner | Resolve receipt | action, optional channel/note → resulting receipt(s)/allocation | Lease required for rent/utility; supported action; financial recalculation |
| `POST /api/rent-charges/{id}/defer` | Owner | Defer rent | days/until, note → charge | Valid positive period/date; status=deferred |
| `GET /api/leases/{id}/manual-debts` | Owner | List contract manual debts | lease id → debts | 404 |
| `POST /api/manual-debts` | Owner | Create manual debt | lease, title/kind/channel/amount/dates/notes → debt | Positive amount/valid lease/dates |
| `PATCH /api/manual-debts/{id}` | Owner | Edit manual debt | supported fields → debt | 404/amount/date/status recalculation |
| `POST /api/manual-debts/{id}/payments` | Owner | Pay manual debt | amount/date/source/note → debt | Positive amount; partial/paid |
| `DELETE /api/manual-debts/{id}` | Owner | Delete manual debt | id → `{ok}` | 404; destructive |

## 8. Meters, tariffs and utility bills

| Method / path | Auth | Purpose | Request → response | Validation / entities / capability |
| --- | --- | --- | --- | --- |
| `GET /api/meters` | Owner | List meters and latest readings | — → meters | Meter/Service/Apartment |
| `PATCH /api/utility-services/{id}` | Owner | Edit due-day/service settings | supported due days/name/active → service | 1–31/range and 404 validation |
| `POST /api/meter-readings` | Owner | Save one reading | meter_id/date/value/note → reading | Meter exists; date/value validation; duplicate/effective history rules |
| `POST /api/meter-readings/batch` | Owner | Save several readings | readings array/date → results | Per-item validation; transactional behavior documented by response |
| `GET /api/tariffs` | Owner | List tariff history | — → tariffs | Sorted by service/effective date |
| `POST /api/tariffs` | Owner | Add tariff | service_id, starts_on, name, tiers → tariff | Service/date/tier parser; valid monotonic tiers |
| `GET /api/utility-bills` | Owner | List bills/lines/payments/reminders | — → bills | UtilityBill/Line/Receipt |
| `GET /api/utilities/timeline` | Owner | View utility event timeline | — → normalized events | Read-only composite |
| `POST /api/utility-bills/calculate` | Owner | Calculate one service | service_id, period start/end, allow_estimate → bill + warnings | Readings/tariff/period; apartment total cannot exceed object; duplicate draft rules |
| `POST /api/utility-bills/calculate-object` | Owner | Calculate all active services of object | object_id, period, allow_estimate → created/errors arrays | Object/services exist; per-service errors retained |
| `DELETE /api/utility-bills/{id}` | Owner | Delete allowed bill | id → `{ok}` | 404/status/dependency constraints; destructive |
| `GET /api/utility-bills/{id}/issue-preview` | Owner | Preview grouped issue | id → target texts/link state/due/bill ids | 404; no send |
| `POST /api/utility-bills/{id}/issue` | Owner | Issue grouped bills and notify | id → bill + sent/skipped/failed/applied advances | Draft/group validity; zero successful linked sends with failures returns error; partial delivery preserved |
| `POST /api/utility-bills/{id}/provider-paid` | Owner | Mark supplier paid | id → bill | 404; provider timestamp |
| `POST /api/utility-lines/{id}/payments` | Owner | Pay utility/advance line | amount/date/source/status/recipient/note → line | Positive amount; linked lease; creates receipt/ledger and recalculates |

Public create operations for UtilityService and Meter do not exist; current creation happens through seed/import/data setup. This is a documented capability gap for brand-new objects.

## 9. Expenses

| Method / path | Auth | Purpose | Request → response | Validation / entities / capability |
| --- | --- | --- | --- | --- |
| `GET /api/expenses` | Owner | List expenses | limit, offset → expenses | Pagination bounds; newest first |
| `POST /api/expenses` | Owner | Create expense | date, object/apartment, category, amount, funds, method, description/file/note → expense | Positive amount, valid optional scope; personal→pending, other→not_required |
| `POST /api/expenses/{id}/compensate` | Owner | Mark compensation | id → expense | 404; timestamp/status transition |

## 10. Reports

Guest may call these GET endpoints; monthly acceptance remains owner-only.

| Method / path | Auth | Purpose | Request → response | Validation / entities / capability |
| --- | --- | --- | --- | --- |
| `GET /api/reports/rent.xlsx` | Guest+ | Rent Excel | start/end → XLSX | Date range |
| `GET /api/reports/utilities.xlsx` | Guest+ | Utilities Excel | start/end → XLSX | Date range |
| `GET /api/reports/debts.xlsx` | Guest+ | Current debts Excel | — → XLSX | Composite read-only |
| `GET /api/reports/expenses.xlsx` | Guest+ | Expenses Excel | start/end → XLSX | Date range |
| `GET /api/reports/monthly.xlsx` | Guest+ | Monthly workbook | year/month → XLSX | Calendar validation |
| `GET /api/reports/monthly/{year}/{month}` | Guest+ | Monthly JSON details | kind full/preliminary → report | Month/kind validation |
| `POST /api/reports/monthly/{year}/{month}/accept` | Owner | Accept monthly control state | kind → report/accepted marker | Valid month/kind; does not mutate finance rows |
| `GET /api/reports/owner.xlsx` | Guest+ | Owner consolidated Excel | start/end → XLSX | Date range |
| `GET /api/reports/history.xlsx` | Guest+ | Apartment or tenant history Excel | apartment_id or tenant_id → XLSX | At least one valid scope; 404/400 |

## 11. Background and internal operations without public endpoints

These are capabilities of the system but not independent UI actions:

- startup: DB initialization policy, seed-if-empty, performance indexes, domain-event listeners, Telegram/reminder workers;
- rent generation and status reconciliation;
- payment allocation across current/older charges and utilities;
- automatic utility advance draft/ledger application;
- Telegram update queue, rate limit, dedupe and file storage;
- due reminders, payment-situation callbacks, promise/escalation tracking;
- DomainEvent emission and OperationalCase reconciliation;
- Hermes briefing selection, memory/summary updates and usage accounting;
- performance request/background recording and secret redaction.

## 12. Implementation ownership

| Area | Source |
| --- | --- |
| Route composition, serialization and shared orchestration | `rental_manager/main.py` |
| Persisted schema | `rental_manager/models.py` |
| Rent/utility calculations | `rental_manager/services/billing.py` |
| Payment allocation and receipt parsing/matching | `services/payment_allocation.py`, `receipt_parser.py`, `receipt_matching.py` |
| Effective payment details | `services/payment_profiles.py` |
| Telegram transport | `services/telegram_bot.py` plus handlers in `main.py` |
| Hermes domain/safety/runtime | `services/hermes/*` |
| Auth/secrets/sessions | `rental_manager/security/*` |
| Web consumers | `static/index.html`, `static/app.js`, `static/js/*` |
| Android consumers | `android/RentalManager/app/src/main/java/...` |
| Migration history | `migrations/versions/*` |
