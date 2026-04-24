const state = {
  bootstrap: null,
  rentCharges: [],
  utilityBills: [],
  expenses: [],
  tariffs: [],
  messageTargets: [],
  suspiciousReceipts: [],
  paymentHistory: null,
  settings: {},
  editingLeaseId: null,
  quickReadingArmedUntil: 0,
};

const statusText = {
  pending: "ожидается",
  overdue: "просрочено",
  partial: "частично",
  paid: "оплачено",
  paid_ahead: "оплачено вперёд",
  deferred: "отсрочка",
  draft: "черновик",
  issued: "выставлено",
  compensated: "компенсировано",
  not_required: "не требуется",
  suspicious: "проверить",
  accepted: "принят",
  moderated: "модерировано",
  rejected: "отклонён",
};

const money = (value) => new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 2 }).format(value || 0);
const today = () => new Date().toISOString().slice(0, 10);
const monthNames = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const monthNamesNominative = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];

function localDateTimeNow() {
  const now = new Date();
  const shifted = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return shifted.toISOString().slice(0, 16);
}

function qs(selector, root = document) {
  return root.querySelector(selector);
}

function qsa(selector, root = document) {
  return [...root.querySelectorAll(selector)];
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let message = "Ошибка запроса";
    try {
      const data = await response.json();
      message = data.detail || message;
    } catch {
      message = await response.text();
    }
    throw new Error(message);
  }
  return response.json();
}

function toast(message) {
  const node = qs("#toast");
  node.textContent = message;
  node.classList.add("show");
  window.setTimeout(() => node.classList.remove("show"), 3200);
}

function formData(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  qsa('input[type="checkbox"]', form).forEach((input) => {
    data[input.name] = input.checked;
  });
  return data;
}

function setOptions(select, items, getLabel, includeEmpty = false) {
  select.innerHTML = "";
  if (includeEmpty) {
    select.append(new Option("Не выбрано", ""));
  }
  items.forEach((item) => {
    select.append(new Option(getLabel(item), item.id));
  });
}

function allApartments() {
  return state.bootstrap.objects.flatMap((object) => object.apartments.map((apartment) => ({ ...apartment, object_name: object.name })));
}

function editingLease() {
  return state.bootstrap?.leases?.find((lease) => lease.id === state.editingLeaseId) || null;
}

function statusPill(status) {
  const cls = status === "overdue" || status === "suspicious"
    ? "danger"
    : status === "partial" || status === "deferred"
      ? "warn"
      : status === "paid" || status === "paid_ahead" || status === "accepted"
        ? "ok"
        : "";
  return `<span class="pill ${cls}">${statusText[status] || status}</span>`;
}

function appToday() {
  return state.bootstrap?.today || today();
}

function parseLocalDate(value) {
  if (!value) return null;
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  if (!year || !month || !day) return null;
  return new Date(year, month - 1, day);
}

function formatDate(value) {
  const parsed = parseLocalDate(value);
  const current = parseLocalDate(appToday());
  if (!parsed || !current) return value || "";
  const sameYear = parsed.getFullYear() === current.getFullYear();
  const sameMonth = sameYear && parsed.getMonth() === current.getMonth();
  if (sameMonth) return `${parsed.getDate()} число`;
  if (sameYear) return `${parsed.getDate()} ${monthNames[parsed.getMonth()]}`;
  return `${parsed.getDate()} ${monthNames[parsed.getMonth()]} ${parsed.getFullYear()}`;
}

function formatDateRange(start, end) {
  return `${formatDate(start)} - ${formatDate(end)}`;
}

function formatMonth(value) {
  const parsed = parseLocalDate(value);
  const current = parseLocalDate(appToday());
  if (!parsed || !current) return "месяц не указан";
  const month = monthNamesNominative[parsed.getMonth()];
  return parsed.getFullYear() === current.getFullYear() ? month : `${month} ${parsed.getFullYear()}`;
}

function monthMeta(value) {
  return `<span class="pill">месяц: ${formatMonth(value)}</span>`;
}

function applySettings(settings = {}) {
  state.settings = {
    color_palette: "classic",
    app_base_url: "",
    telegram_owner_chat_id: "",
    notifications_enabled: false,
    notification_cutoff_date: "",
    ip_recipient_name: "",
    ip_recipient_account: "",
    ip_recipient_bik: "",
    personal_recipient_name: "",
    personal_recipient_phone: "",
    personal_recipient_bank: "",
    telegram_bot_token_configured: false,
    telegram_webhook_secret_configured: false,
    ...settings,
  };
  document.body.dataset.palette = state.settings.color_palette || "classic";
  const select = qs("#paletteSelect");
  if (select) select.value = state.settings.color_palette || "classic";
  const appBase = qs("#appBaseUrlInput");
  const ownerChat = qs("#telegramOwnerChatIdInput");
  const token = qs("#telegramBotTokenInput");
  const secret = qs("#telegramWebhookSecretInput");
  const notificationsEnabled = qs("#notificationsEnabledInput");
  const notificationCutoffDate = qs("#notificationCutoffDateInput");
  const ipRecipientName = qs("#ipRecipientNameInput");
  const ipRecipientAccount = qs("#ipRecipientAccountInput");
  const ipRecipientBik = qs("#ipRecipientBikInput");
  const personalRecipientName = qs("#personalRecipientNameInput");
  const personalRecipientPhone = qs("#personalRecipientPhoneInput");
  const personalRecipientBank = qs("#personalRecipientBankInput");
  if (appBase) appBase.value = state.settings.app_base_url || "";
  if (ownerChat) ownerChat.value = state.settings.telegram_owner_chat_id || "";
  if (notificationsEnabled) notificationsEnabled.checked = Boolean(state.settings.notifications_enabled);
  if (notificationCutoffDate) notificationCutoffDate.value = state.settings.notification_cutoff_date || appToday();
  if (token) token.placeholder = state.settings.telegram_bot_token_configured ? "Токен сохранён, пусто = не менять" : "Вставь bot token";
  if (secret) secret.placeholder = state.settings.telegram_webhook_secret_configured ? "Secret сохранён, пусто = не менять" : "Вставь webhook secret";
  if (ipRecipientName) ipRecipientName.value = state.settings.ip_recipient_name || "";
  if (ipRecipientAccount) ipRecipientAccount.value = state.settings.ip_recipient_account || "";
  if (ipRecipientBik) ipRecipientBik.value = state.settings.ip_recipient_bik || "";
  if (personalRecipientName) personalRecipientName.value = state.settings.personal_recipient_name || "";
  if (personalRecipientPhone) personalRecipientPhone.value = state.settings.personal_recipient_phone || "";
  if (personalRecipientBank) personalRecipientBank.value = state.settings.personal_recipient_bank || "";
  const templateFields = {
    "#messageRentDueInput": "message_rent_due",
    "#messageRentOverdueInput": "message_rent_overdue",
    "#messageUtilityBillInput": "message_utility_bill",
    "#messageReceiptReceivedInput": "message_receipt_received",
    "#messageReceiptReviewInput": "message_receipt_review",
    "#messageOwnerReceiptAlertInput": "message_owner_receipt_alert",
  };
  Object.entries(templateFields).forEach(([selector, key]) => {
    const field = qs(selector);
    if (field) field.value = state.settings[key] || "";
  });
  renderTelegramStatus();
}

function renderTelegramStatus() {
  const box = qs("#telegramStatusBox");
  if (!box) return;
  const ipReady = Boolean(state.settings.ip_recipient_name && state.settings.ip_recipient_account);
  const personalReady = Boolean(state.settings.personal_recipient_name && state.settings.personal_recipient_phone);
  box.innerHTML = `
    <h3>Telegram</h3>
    <div class="pill-row">
      <span class="pill ${state.settings.telegram_bot_token_configured ? "ok" : "warn"}">token ${state.settings.telegram_bot_token_configured ? "сохранён" : "не задан"}</span>
      <span class="pill ${state.settings.telegram_webhook_secret_configured ? "ok" : "warn"}">secret ${state.settings.telegram_webhook_secret_configured ? "сохранён" : "не задан"}</span>
      <span class="pill ${state.settings.telegram_owner_chat_id ? "ok" : "warn"}">owner chat ${state.settings.telegram_owner_chat_id || "не задан"}</span>
      <span class="pill ${state.settings.notifications_enabled ? "ok" : "warn"}">автонапоминания ${state.settings.notifications_enabled ? "включены" : "выключены"}</span>
      <span class="pill">граница ${formatDate(state.settings.notification_cutoff_date || appToday())}</span>
      <span class="pill ${ipReady ? "ok" : "warn"}">ИП-реквизиты ${ipReady ? "есть" : "неполные"}</span>
      <span class="pill ${personalReady ? "ok" : "warn"}">перевод-реквизиты ${personalReady ? "есть" : "неполные"}</span>
    </div>
    <p class="muted">${state.settings.app_base_url || "Публичный URL не задан. Без него webhook не оживёт, как ни уговаривай."}</p>
  `;
}

async function loadAll() {
  state.bootstrap = await api("/api/bootstrap");
  applySettings(state.bootstrap.settings);
  await Promise.all([loadRent(), loadUtilityBills(), loadExpenses(), loadTariffs(), loadMessageTargets(), loadSuspiciousReceipts()]);
  hydrateForms();
  renderAll();
}

async function loadRent() {
  const start = qs("#rentStart").value;
  const end = qs("#rentEnd").value;
  const query = start && end ? `?start=${start}&end=${end}` : "";
  state.rentCharges = await api(`/api/rent-charges${query}`);
}

async function loadUtilityBills() {
  state.utilityBills = await api("/api/utility-bills");
}

async function loadExpenses() {
  state.expenses = await api("/api/expenses");
}

async function loadTariffs() {
  state.tariffs = await api("/api/tariffs");
}

async function loadMessageTargets() {
  state.messageTargets = await api("/api/messages/targets");
}

async function loadSuspiciousReceipts() {
  state.suspiciousReceipts = await api("/api/payment-receipts/suspicious");
}

function hydrateForms() {
  const apartments = allApartments();
  const currentLease = editingLease();
  const vacantApartments = apartments.filter((apartment) => !apartment.active_lease_id || apartment.id === currentLease?.apartment_id);
  const services = state.bootstrap.services;
  const activeLeases = [...state.bootstrap.leases]
    .filter((lease) => lease.active)
    .sort((left, right) => `${left.object} ${left.apartment} ${left.tenant}`.localeCompare(`${right.object} ${right.apartment} ${right.tenant}`, "ru"));
  qsa('select[name="apartment_id"]').forEach((select) => {
    if (select.closest("#onboardForm")) {
      setOptions(select, vacantApartments, (a) => `${a.object_name}: ${a.name}`);
      if (!vacantApartments.length) {
        select.innerHTML = "";
        select.append(new Option("Нет свободных квартир", ""));
      }
      return;
    }
    setOptions(select, apartments, (a) => `${a.object_name}: ${a.name}`, Boolean(select.closest("#expenseForm")));
  });
  qsa('select[name="object_id"]').forEach((select) => setOptions(select, state.bootstrap.objects, (o) => o.name, true));
  qsa('select[name="meter_id"]').forEach((select) => setOptions(select, state.bootstrap.meters, (m) => `${m.object}: ${m.name}`));
  qsa('select[name="service_id"]').forEach((select) => setOptions(select, services, (s) => `${s.object}: ${s.name}`));
  qsa('select[name="lease_id"]').forEach((select) => setOptions(select, activeLeases, (lease) => `${lease.object}: ${lease.apartment} — ${lease.tenant}`));
  const dateInputs = qsa('input[type="date"]');
  dateInputs.forEach((input) => {
    if (!input.value) input.value = today();
  });
  const paidAtInput = qs("#manualPaymentPaidAtInput");
  if (paidAtInput && !paidAtInput.value) {
    paidAtInput.value = localDateTimeNow();
  }
  const cancelBtn = qs("#cancelLeaseEditBtn");
  const submitBtn = qs("#onboardSubmitBtn");
  if (cancelBtn) cancelBtn.hidden = !state.editingLeaseId;
  if (submitBtn) submitBtn.textContent = state.editingLeaseId ? "Сохранить изменения" : "Заселить";
  setReportLinks();
}

function renderAll() {
  renderDashboard();
  renderObjects();
  renderLeases();
  renderRent();
  renderRentHistory();
  renderMeters();
  renderUtilities();
  renderTariffs();
  renderExpenses();
  renderMessages();
  renderSuspiciousReceipts();
  setReportLinks();
}

function formatDateTime(value) {
  if (!value) return "нет";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
}

function renderDashboard() {
  const dashboard = state.bootstrap.dashboard;
  const metrics = [
    ["Просрочка аренды", dashboard.rent_overdue.length],
    ["Частичная аренда", dashboard.rent_partial.length],
    ["Коммуналка долг", dashboard.utility_overdue.length],
    ["Личные расходы", dashboard.pending_personal_expenses.length],
    ["Счётчики давно", dashboard.stale_readings.length],
    ["Подозрительные чеки", dashboard.suspicious_receipts.length],
    ["Отчёты открыты", dashboard.monthly_reports.length],
  ];
  qs("#summaryGrid").innerHTML = metrics.map(([label, value]) => `<div class="metric"><strong>${value}</strong><span>${label}</span></div>`).join("");
  renderMonthlyReportTray(dashboard.monthly_reports);

  const cards = [];
  dashboard.rent_today.forEach((item) => cards.push(attentionCard("warn", "Сегодня оплата аренды", `${item.object}, ${item.apartment}, ${item.tenant}. К оплате ${money(item.debt)}.`, rentAttentionActions(item), attentionBadges(item.due_date, item.reminder, `срок ${formatDate(item.due_date)}`))));
  dashboard.rent_overdue.forEach((item) => cards.push(attentionCard("danger", "Просрочена аренда", `${item.object}, ${item.apartment}, ${item.tenant}. Долг ${money(item.debt)}. ИП ${money(Math.max(0, item.ip_due - item.ip_paid))}, перевод ${money(Math.max(0, item.personal_due - item.personal_paid))}.`, rentAttentionActions(item, true), attentionBadges(item.due_date, item.reminder, `срок ${formatDate(item.due_date)}`))));
  dashboard.rent_partial.forEach((item) => cards.push(attentionCard("warn", "Аренда оплачена частично", `${item.object}, ${item.apartment}, ${item.tenant}. Осталось ${money(item.debt)}.`, rentAttentionActions(item, item.due_date <= appToday()), attentionBadges(item.due_date, item.reminder, `срок ${formatDate(item.due_date)}`))));
  dashboard.rent_deferred.forEach((item) => cards.push(attentionCard("warn", "Скоро конец отсрочки", `${item.object}, ${item.apartment}, ${item.tenant}. Осталось ${item.deferral_days_left} дн., до ${formatDate(item.deferral_until)}.${item.deferral_note ? ` ${item.deferral_note}` : ""}`, rentAttentionActions(item, true), attentionBadges(item.due_date, item.reminder, `срок ${formatDate(item.due_date)}`))));
  dashboard.utility_overdue.forEach((item) => cards.push(attentionCard("danger", "Просрочена коммуналка", `${item.apartment}, ${item.tenant}. Долг ${money(item.debt)}.`, utilityAttentionActions(item), attentionBadges(item.due_date, item.reminder, `срок ${formatDate(item.due_date)}`))));
  dashboard.utility_partial.forEach((item) => cards.push(attentionCard("warn", "Коммуналка оплачена частично", `${item.apartment}, ${item.tenant}. Осталось ${money(item.debt)}.`, utilityAttentionActions(item), attentionBadges(item.due_date, item.reminder, `срок ${formatDate(item.due_date)}`))));
  dashboard.provider_debts.forEach((item) => cards.push(attentionCard("danger", "Поставщик не отмечен как оплаченный", `${item.object}: ${item.service} за ${formatDateRange(item.period_start, item.period_end)}. Сумма ${money(item.total_cost)}.`, `<button class="mini primary" onclick="providerPaid(${item.id})">Поставщик оплачен</button><button class="mini" onclick="openUtilitiesTab()">Открыть коммуналку</button>`, attentionBadges(item.period_end, null, "деньги у поставщика ещё не закрыты"))));
  dashboard.stale_readings.forEach((item) => cards.push(attentionCard("warn", "Давно нет показаний", `${item.object}: ${item.service}. Последнее: ${item.last_date ? formatDate(item.last_date) : "нет"}.`, `<button class="mini" onclick="openMetersTab()">Открыть счётчики</button><button class="mini" onclick="openUtilitiesTab()">Быстрая передача</button>`, attentionBadges(item.last_date || appToday(), null, item.days ? `${item.days} дн. без обновления` : "пока пусто"))));
  dashboard.pending_personal_expenses.forEach((item) => cards.push(attentionCard("warn", "Личный расход ждёт компенсации", `${item.object || "без объекта"} ${item.apartment || ""}: ${money(item.amount)}. ${item.description || item.category}`, `<button class="mini primary" onclick="compensateExpense(${item.id})">Компенсировать</button><button class="mini" onclick="openExpensesTab()">Открыть расходы</button>`, attentionBadges(item.expense_date, null, item.source_funds === "personal" ? "из личных" : item.source_funds))));
  dashboard.suspicious_receipts.forEach((item) => cards.push(attentionCard("danger", "Подозрительный чек", `${money(item.amount)}. ${item.recipient_name || "получатель не распознан"}. ${item.notes || ""}`, `<button class="mini" onclick="openMessagesTab()">Открыть сообщения</button>`, attentionBadges(item.created_at, null, "нужна ручная проверка"))));
  qs("#attentionList").innerHTML = cards.join("") || `<div class="card ok"><h3>Критичных задач нет</h3><p class="muted">Редкий момент, когда приложение не ругается. Подозрительно, но приятно.</p></div>`;
}

function renderMonthlyReportTray(reports = []) {
  const tray = qs("#monthlyReportTray");
  if (!reports.length) {
    tray.innerHTML = `<div class="report-status report-ok"><strong>Месячные отчёты закрыты</strong><span>Ничего не торчит. Почти скучно.</span></div>`;
    return;
  }
  tray.innerHTML = reports.map((report) => `
    <button class="report-status report-${report.severity}" onclick="openMonthlyReport(${report.year}, ${report.month})">
      <strong>${report.title}</strong>
      <span>${monthlySeverityText(report)} · ${report.issue_count} проблем</span>
    </button>
  `).join("");
}

function monthlySeverityText(report) {
  if (report.severity === "critical") return "много проблем";
  if (report.severity === "danger") return "критично";
  if (report.severity === "warn") return "есть вопросы";
  return "закрыт";
}

function reminderBadge(reminder) {
  if (!reminder) return '<span class="pill">напоминаний ещё не было</span>';
  if (reminder.latest) {
    return `<span class="pill ok">${reminder.latest.label}: ${formatDateTime(reminder.latest.created_at)}</span>`;
  }
  if (reminder.block_reason) {
    const cls = reminder.block_reason === "ждём /start" ? "warn" : "";
    return `<span class="pill ${cls}">${reminder.block_reason}</span>`;
  }
  return '<span class="pill">напоминаний ещё не было</span>';
}

function attentionBadges(monthValue, reminder, extra = "") {
  return [monthMeta(monthValue), reminderBadge(reminder), extra ? `<span class="pill">${extra}</span>` : ""].join("");
}

function rentAttentionActions(charge, overdueMode = false) {
  const template = overdueMode ? "message_rent_overdue" : "message_rent_due";
  return `
    <button class="mini" onclick="sendTemplateMessage(${charge.lease_id}, '${template}', ${charge.id}, null)">Напомнить</button>
    ${rentActions(charge)}
  `;
}

function utilityAttentionActions(line) {
  return `
    <button class="mini" onclick="sendTemplateMessage(${line.lease_id}, 'message_utility_bill', null, ${line.id})">Напомнить</button>
    ${utilityActions(line)}
  `;
}

function compactReminderText(reminder) {
  if (!reminder) return '<span class="muted">нет</span>';
  if (reminder.latest) return `<span class="muted">${reminder.latest.label}: ${formatDateTime(reminder.latest.created_at)}</span>`;
  if (reminder.block_reason) return `<span class="muted">${reminder.block_reason}</span>`;
  return '<span class="muted">ещё не отправлялось</span>';
}

function attentionCard(type, title, text, actions, badges) {
  return `
    <article class="attention-card ${type}">
      <div class="pill-row attention-card__badges">${badges || ""}</div>
      <div class="attention-card__body">
        <h3>${title}</h3>
        <p>${text}</p>
      </div>
      <div class="attention-actions">${actions || ""}</div>
    </article>
  `;
}

function renderObjects() {
  const summary = state.bootstrap.dashboard.object_summary;
  const objects = summary.by_object.map((item) => `<span class="pill ${item.occupied === item.total ? "ok" : "warn"}">${item.object}: ${item.occupied}/${item.total}</span>`).join("");
  qs("#objectCards").innerHTML = `
    <article class="card">
      <h3>Заселено ${summary.occupied}/${summary.total}</h3>
      <div class="pill-row">${objects}</div>
      <button class="mini" onclick="openTenantsTab()">Подробнее</button>
    </article>
  `;
}

function renderLeases() {
  const rows = state.bootstrap.leases.map((lease) => `
    <tr>
      <td>${lease.object}</td>
      <td>${lease.apartment}</td>
      <td>${lease.tenant}<br><span class="muted">${lease.phone || ""}</span></td>
      <td>${formatDate(lease.start_date)}</td>
      <td>${lease.payment_day}</td>
      <td>${money(lease.ip_amount)} / ${money(lease.personal_amount)}</td>
      <td>${lease.deposit_amount ? `${money(lease.deposit_amount)}<br><span class="muted">${lease.deposit_location || ""}</span>` : "нет"}</td>
      <td>${statusPill(lease.active ? "issued" : "paid")}</td>
      <td class="actions">
        ${contactButtons(lease)}
        <button class="mini" onclick="startLeaseEdit(${lease.id})">Ред.</button>
        ${lease.active ? `<button class="mini danger-soft" onclick="moveOut(${lease.id})">Выезд</button>` : ""}
      </td>
    </tr>
  `).join("");
  qs("#leaseList").innerHTML = table(["Объект", "Квартира", "Жилец", "Заезд", "День", "ИП / личный", "Залог", "Статус", "Действия"], rows);
}

function startLeaseEdit(leaseId) {
  const lease = state.bootstrap.leases.find((item) => item.id === leaseId);
  const form = qs("#onboardForm");
  if (!lease || !form) return;
  state.editingLeaseId = leaseId;
  hydrateForms();
  form.elements.apartment_id.value = String(lease.apartment_id);
  form.elements.full_name.value = lease.tenant || "";
  form.elements.phone.value = lease.phone || "";
  form.elements.telegram.value = lease.telegram || "";
  form.elements.whatsapp.value = lease.whatsapp || "";
  form.elements.start_date.value = lease.start_date || "";
  form.elements.payment_day.value = lease.payment_day || "";
  form.elements.ip_amount.value = lease.ip_amount || "";
  form.elements.personal_amount.value = lease.personal_amount || "";
  form.elements.deposit_amount.value = lease.deposit_amount || "";
  form.elements.deposit_location.value = lease.deposit_location || "";
  form.elements.deposit_terms.value = lease.deposit_terms || "";
  form.elements.notes.value = lease.notes || "";
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

function cancelLeaseEdit() {
  state.editingLeaseId = null;
  const form = qs("#onboardForm");
  if (form) form.reset();
  hydrateForms();
}

function renderRent() {
  const rows = state.rentCharges.map((charge) => `
    <tr>
      <td>${formatDate(charge.due_date)}<br><span class="muted">${formatDateRange(charge.period_start, charge.period_end)}</span></td>
      <td>${charge.object}</td>
      <td>${charge.apartment}</td>
      <td>${charge.tenant}</td>
      <td>${money(charge.ip_due)}<br><span class="muted">оплачено ${money(charge.ip_paid)}</span></td>
      <td>${money(charge.personal_due)}<br><span class="muted">оплачено ${money(charge.personal_paid)}</span></td>
      <td>${money(charge.debt)}</td>
      <td>${statusPill(charge.status)}</td>
      <td class="actions">${rentActions(charge)}</td>
    </tr>
  `).join("");
  qs("#rentTable").innerHTML = table(["Дата", "Объект", "Квартира", "Жилец", "ИП", "Личный", "Долг", "Статус", "Действия"], rows);
}

function rentActions(charge) {
  const ipLeft = Math.max(0, charge.ip_due - charge.ip_paid);
  const personalLeft = Math.max(0, charge.personal_due - charge.personal_paid);
  return `
    ${ipLeft > 0 ? `<button class="mini primary" onclick="payRent(${charge.id}, 'ip', ${ipLeft})">ИП подтверждён</button>` : ""}
    ${personalLeft > 0 ? `<button class="mini primary" onclick="payRent(${charge.id}, 'personal', ${personalLeft})">Перевод подтверждён</button>` : ""}
    <button class="mini" onclick="deferRent(${charge.id})">Отсрочка</button>
    <button class="mini" onclick="openPaymentHistory(${charge.lease_id})">История</button>
  `;
}

function receiptStatusLabel(status) {
  return statusText[status] || status || "неизвестно";
}

async function openPaymentHistory(leaseId) {
  state.paymentHistory = await api(`/api/leases/${leaseId}/payment-history`);
  renderRentHistory();
  qs("#rentHistoryPanel")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function closePaymentHistory() {
  state.paymentHistory = null;
  renderRentHistory();
}

function renderRentHistory() {
  const root = qs("#rentHistoryPanel");
  if (!root) return;
  if (!state.paymentHistory) {
    root.innerHTML = "";
    return;
  }
  const rows = state.paymentHistory.receipts.map((receipt) => `
    <tr>
      <td>${formatDateTime(receipt.paid_at)}</td>
      <td>${money(receipt.amount)}</td>
      <td>${receipt.channel}</td>
      <td>${receipt.source || "manual"}</td>
      <td>${receipt.target_label || '<span class="muted">не привязан</span>'}</td>
      <td>${statusPill(receipt.status)}</td>
      <td>${receipt.notes || ""}</td>
      <td class="actions">
        <button class="mini" onclick="editPaymentReceipt(${receipt.id})">Редактировать</button>
        <button class="mini danger-soft" onclick="deletePaymentReceipt(${receipt.id})">Удалить</button>
      </td>
    </tr>
  `).join("");
  root.innerHTML = `
    <article class="card">
      <div class="section-title">
        <div>
          <h2>История платежей: ${state.paymentHistory.apartment}</h2>
          <span>${state.paymentHistory.tenant}</span>
        </div>
        <button class="mini" type="button" onclick="closePaymentHistory()">Скрыть</button>
      </div>
      <div class="pill-row">
        <span class="pill">Жилец: ${state.paymentHistory.tenant}</span>
        <span class="pill">Квартира: ${state.paymentHistory.apartment}</span>
      </div>
      <div class="table-wrap">${table(["Оплачен", "Сумма", "Канал", "Источник", "За что зачтён", "Статус", "Примечание", "Действия"], rows)}</div>
    </article>
  `;
}

async function editPaymentReceipt(receiptId) {
  const history = state.paymentHistory;
  if (!history) return;
  const receipt = history.receipts.find((item) => item.id === receiptId);
  if (!receipt) return;
  const amount = prompt("Новая сумма платежа", String(receipt.amount));
  if (amount === null) return;
  const paidAt = prompt("Дата и время платежа (YYYY-MM-DDTHH:MM)", (receipt.paid_at || "").slice(0, 16));
  if (paidAt === null) return;
  const notes = prompt("Комментарий", receipt.notes || "");
  if (notes === null) return;
  const payload = {
    amount: Number(amount),
    paid_at: paidAt,
    notes,
  };
  if (receipt.rent_charge_id) {
    payload.channel = prompt("Канал: ip или personal", receipt.channel || "personal") || receipt.channel;
  }
  await api(`/api/payment-receipts/${receiptId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  toast("Платёж обновлён");
  await openPaymentHistory(history.lease_id);
  await loadAll();
}

async function deletePaymentReceipt(receiptId) {
  if (!confirm("Удалить этот платёж из истории?")) return;
  await api(`/api/payment-receipts/${receiptId}`, { method: "DELETE" });
  toast("Платёж удалён");
  if (state.paymentHistory) {
    await openPaymentHistory(state.paymentHistory.lease_id);
  }
  await loadAll();
}

function renderSuspiciousReceipts() {
  const root = qs("#suspiciousReceiptsPanel");
  if (!root) return;
  if (!state.suspiciousReceipts.length) {
    root.innerHTML = `<article class="card"><h3>Подозрительных чеков нет</h3><p class="muted">Тихо. Даже бот перестал драматизировать.</p></article>`;
    return;
  }
  root.innerHTML = state.suspiciousReceipts.map((receipt) => `
    <article class="card">
      <div class="section-title">
        <div>
          <h2>${receipt.apartment || "Квартира не определена"}</h2>
          <span>${receipt.tenant || "Неопознанный отправитель"} · ${money(receipt.amount)} · ${receiptStatusLabel(receipt.status)}</span>
        </div>
        ${receipt.file_path ? `<span class="pill">${receipt.file_path.split("/").pop()}</span>` : ""}
      </div>
      <div class="pill-row">
        ${receipt.target_month ? monthMeta(receipt.target_month) : '<span class="pill">месяц не определён</span>'}
        <span class="pill">${receipt.channel || "канал не определён"}</span>
        ${receipt.recipient_name ? `<span class="pill">${receipt.recipient_name}</span>` : ""}
      </div>
      <p>${receipt.notes || "Бот засомневался и позвал владельца. Правильно сделал."}</p>
      <div class="attention-actions">
        <button class="mini primary" onclick="moderateReceipt(${receipt.id}, 'accept_rent')">Зачесть в аренду</button>
        <button class="mini primary" onclick="moderateReceipt(${receipt.id}, 'accept_utility')">Зачесть в коммуналку</button>
        <button class="mini danger-soft" onclick="moderateReceipt(${receipt.id}, 'reject')">Отклонить</button>
      </div>
    </article>
  `).join("");
}

async function moderateReceipt(receiptId, action) {
  const note = prompt("Комментарий модератора", "") ?? "";
  let channel = "";
  if (action === "accept_rent") {
    channel = prompt("Канал для аренды: ip или personal", "personal") || "personal";
  }
  await api(`/api/payment-receipts/${receiptId}/moderate`, {
    method: "POST",
    body: JSON.stringify({ action, note, channel }),
  });
  toast("Чек отмодерирован");
  await loadAll();
}

async function importBaseline() {
  if (!confirm("Импортировать данные из старой базы и перезаписать текущие demo-данные?")) return;
  const result = await api("/api/admin/import-release-baseline", { method: "POST", body: "{}" });
  toast(`Импорт завершён: жильцов ${result.tenants}, аренд ${result.leases}, платежей ${result.receipts}`);
  await loadAll();
}

function updateManualPaymentKind() {
  const kind = qs("#manualPaymentKindSelect")?.value || "rent";
  const channelSelect = qs("#manualPaymentChannelSelect");
  if (!channelSelect) return;
  channelSelect.disabled = kind !== "rent";
  if (kind !== "rent") {
    channelSelect.value = "personal";
  }
}

async function submitManualPayment(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = formData(form);
  if (payload.kind !== "rent") {
    delete payload.channel;
  }
  payload.amount = Number(payload.amount);
  await api("/api/payment-receipts/manual", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  form.reset();
  const paidAtInput = qs("#manualPaymentPaidAtInput");
  if (paidAtInput) paidAtInput.value = localDateTimeNow();
  updateManualPaymentKind();
  toast("Ручной платёж сохранён");
  await loadAll();
}

function utilityActions(line) {
  const left = Math.max(0, line.total_amount - line.paid_amount);
  return `${left > 0 ? `<button class="mini primary" onclick="payUtility(${line.id}, ${left})">Оплачено</button>` : ""}`;
}

function contactButtons(lease) {
  const buttons = [];
  if (lease.phone) buttons.push(`<a class="button mini contact-call" href="tel:${lease.phone}">Звонок</a>`);
  if (lease.telegram) buttons.push(`<a class="button mini contact-telegram" target="_blank" href="https://t.me/${lease.telegram.replace("@", "")}">Telegram</a>`);
  if (lease.whatsapp || lease.phone) buttons.push(`<a class="button mini contact-whatsapp" target="_blank" href="https://wa.me/${(lease.whatsapp || lease.phone).replace(/\D/g, "")}">WhatsApp</a>`);
  return buttons.join("");
}

function renderMeters() {
  const rows = state.bootstrap.meters.map((meter) => `
    <tr>
      <td>${meter.object}</td>
      <td>${meter.service}</td>
      <td>${meter.scope === "object" ? "общий" : meter.apartment}</td>
      <td>${meter.name}</td>
      <td>${meter.latest_date ? formatDate(meter.latest_date) : "нет"}</td>
      <td>${meter.latest_value ?? ""}</td>
    </tr>
  `).join("");
  qs("#meterList").innerHTML = table(["Объект", "Услуга", "Тип", "Счётчик", "Последняя дата", "Показание"], rows);
}

function renderQuickReadingFields() {
  const root = qs("#quickReadingFields");
  if (!root) return;
  root.innerHTML = state.bootstrap.objects.map((object) => {
    const meters = state.bootstrap.meters
      .filter((meter) => meter.object === object.name)
      .sort((a, b) => (a.scope === b.scope ? (a.apartment || a.name).localeCompare(b.apartment || b.name, "ru") : a.scope === "object" ? -1 : 1));
    if (!meters.length) return "";
    const fields = meters.map((meter) => {
      const label = meter.scope === "object" ? `${meter.service}: общий` : `${meter.apartment}: ${meter.service}`;
      return `<label>${label}<input class="quick-reading-input" type="number" step="0.001" data-meter-id="${meter.id}" placeholder="${meter.latest_value ?? ""}" /></label>`;
    }).join("");
    return `<article class="quick-object"><h3>${object.name}</h3><div class="quick-meter-fields">${fields}</div></article>`;
  }).join("");
}

function renderUtilities() {
  renderQuickReadingFields();
  const bills = state.utilityBills.map((bill) => {
    const lines = bill.lines.map((line) => `
      <tr>
        <td>${line.apartment}</td>
        <td>${line.tenant}</td>
        <td>${line.personal_consumption}</td>
        <td>${line.odn_consumption}</td>
        <td>${money(line.total_amount)}</td>
        <td>${money(line.paid_amount)}</td>
        <td>${statusPill(line.status)}</td>
        <td class="actions">${utilityActions(line)}</td>
      </tr>
    `).join("");
    return `<article class="card">
      <h3>${bill.object}: ${bill.service}</h3>
      <p class="muted">${formatDateRange(bill.period_start, bill.period_end)}, расход ${bill.total_consumption}, сумма ${money(bill.total_cost)}, средняя цена ${bill.average_unit_price}</p>
      <div class="pill-row">
        ${statusPill(bill.status)}
        ${bill.is_forecast ? '<span class="pill warn">прогноз</span>' : ""}
        ${bill.provider_paid ? '<span class="pill ok">поставщик оплачен</span>' : '<span class="pill warn">поставщик не отмечен</span>'}
      </div>
      <div class="pill-row">
        ${bill.status === "draft" ? `<button class="mini primary" onclick="issueBill(${bill.id})">Выставить жильцам</button>` : ""}
        ${!bill.provider_paid ? `<button class="mini primary" onclick="providerPaid(${bill.id})">Поставщик оплачен</button>` : ""}
      </div>
      <div class="table-wrap">${table(["Квартира", "Жилец", "Личный", "ОДН", "Сумма", "Оплачено", "Статус", "Действия"], lines)}</div>
      ${bill.notes ? `<p class="muted">${bill.notes}</p>` : ""}
    </article>`;
  }).join("");
  qs("#utilityBills").innerHTML = bills || `<div class="card"><p class="muted">Коммунальных счетов пока нет.</p></div>`;
}

function renderTariffs() {
  const root = qs("#tariffCards");
  if (!root) return;
  const byService = new Map();
  state.tariffs.forEach((tariff) => {
    if (!byService.has(tariff.service_id)) byService.set(tariff.service_id, []);
    byService.get(tariff.service_id).push(tariff);
  });
  root.innerHTML = state.bootstrap.objects.map((object) => {
    const services = state.bootstrap.services.filter((service) => service.object === object.name);
    const rows = services.map((service) => {
      const tariffs = byService.get(service.id) || [];
      const current = tariffs[0];
      return `
        <tr>
          <td>${service.name}</td>
          <td>${current ? formatDate(current.starts_on) : "нет"}</td>
          <td>${current ? tiersText(current.tiers) : "нет тарифа"}</td>
          <td><button class="mini" onclick="prefillTariff(${service.id})">Задать</button></td>
        </tr>
      `;
    }).join("");
    return `<article class="card"><h3>${object.name}</h3><div class="table-wrap">${table(["Услуга", "Действует с", "Тариф", "Действие"], rows)}</div></article>`;
  }).join("");
}

function tiersText(tiers = []) {
  return tiers.map((tier) => `${tier.limit ?? "*"}: ${tier.price}`).join("; ");
}

function prefillTariff(serviceId) {
  const service = state.bootstrap.services.find((item) => item.id === serviceId);
  const form = qs("#tariffForm");
  form.elements.service_id.value = serviceId;
  form.elements.starts_on.value = today();
  form.elements.name.value = service ? `Актуальный ${service.object}: ${service.name}` : "Актуальный тариф";
  form.elements.tiers.value = service?.kind === "electricity" ? "3900:4.18; 6000:6.01; 7.48" : "*:0";
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderExpenses() {
  const rows = state.expenses.map((expense) => `
    <tr>
      <td>${formatDate(expense.expense_date)}</td>
      <td>${expense.object || ""}</td>
      <td>${expense.apartment || ""}</td>
      <td>${expense.category}</td>
      <td>${money(expense.amount)}</td>
      <td>${expense.source_funds}</td>
      <td>${statusPill(expense.compensation_status === "compensated" ? "paid" : expense.compensation_status === "pending" ? "partial" : "issued")}</td>
      <td>${expense.description || ""}</td>
      <td class="actions">${expense.compensation_status !== "compensated" && expense.source_funds === "personal" ? `<button class="mini primary" onclick="compensateExpense(${expense.id})">Компенсировано</button>` : ""}</td>
    </tr>
  `).join("");
  qs("#expenseList").innerHTML = table(["Дата", "Объект", "Квартира", "Категория", "Сумма", "Источник", "Компенсация", "Описание", "Действия"], rows);
}

function renderMessages() {
  const root = qs("#messageTargets");
  if (!root) return;
  const rows = state.messageTargets.map((target) => `
    <tr>
      <td>${target.object}</td>
      <td>${target.apartment}</td>
      <td>${target.tenant}<br><span class="muted">${target.telegram || "без @username"}</span></td>
      <td>${target.linked ? '<span class="pill ok">бот знает жильца</span>' : '<span class="pill warn">ждём /start от жильца</span>'}</td>
      <td>${target.rent_charge_id ? `${statusPill(target.rent_status)}<br><span class="muted">${money(target.rent_debt)}</span><br>${compactReminderText(target.rent_reminder)}` : '<span class="muted">нет</span>'}</td>
      <td>${target.utility_line_id ? `${statusPill(target.utility_status)}<br><span class="muted">${money(target.utility_debt)}</span><br>${compactReminderText(target.utility_reminder)}` : '<span class="muted">нет</span>'}</td>
      <td class="actions">
        ${target.rent_charge_id ? `<button class="mini primary" onclick="sendTemplateMessage(${target.lease_id}, 'message_rent_due', ${target.rent_charge_id}, null)">Аренда</button>` : ""}
        ${target.rent_charge_id ? `<button class="mini" onclick="sendTemplateMessage(${target.lease_id}, 'message_rent_overdue', ${target.rent_charge_id}, null)">Просрочка</button>` : ""}
        ${target.utility_line_id ? `<button class="mini primary" onclick="sendTemplateMessage(${target.lease_id}, 'message_utility_bill', null, ${target.utility_line_id})">Коммуналка</button>` : ""}
        <button class="mini" onclick="sendCustomMessage(${target.lease_id})">Свой текст</button>
      </td>
    </tr>
  `).join("");
  root.innerHTML = table(["Объект", "Квартира", "Жилец", "Связка", "Аренда", "Коммуналка", "Действия"], rows);
  updateManualPaymentKind();
}

function table(headers, rows) {
  return `<table><thead><tr>${headers.map((header) => `<th>${header}</th>`).join("")}</tr></thead><tbody>${rows || `<tr><td colspan="${headers.length}" class="muted">Пока пусто</td></tr>`}</tbody></table>`;
}

async function payRent(id, channel, suggested) {
  const value = prompt("Сумма оплаты", suggested);
  if (!value) return;
  await api(`/api/rent-charges/${id}/payments`, {
    method: "POST",
    body: JSON.stringify({ channel, amount: Number(value), source: "manual" }),
  });
  toast("Оплата аренды отмечена");
  await loadAll();
}

async function deferRent(id) {
  const rawDays = prompt("На сколько дней дать отсрочку?", "3");
  if (!rawDays) return;
  const days = Number(rawDays);
  if (!Number.isInteger(days) || days <= 0) {
    toast("Введите целое количество дней больше нуля");
    return;
  }
  const note = prompt("Комментарий к отсрочке", "") || "";
  const result = await api(`/api/rent-charges/${id}/defer`, {
    method: "POST",
    body: JSON.stringify({ deferral_days: days, deferral_note: note }),
  });
  toast(`Отсрочка сохранена до ${formatDate(result.deferral_until)}`);
  await loadAll();
}

async function payUtility(id, suggested) {
  const value = prompt("Сумма оплаты коммуналки", suggested);
  if (!value) return;
  await api(`/api/utility-lines/${id}/payments`, {
    method: "POST",
    body: JSON.stringify({ amount: Number(value), source: "manual" }),
  });
  toast("Оплата коммуналки отмечена");
  await loadAll();
}

async function issueBill(id) {
  await api(`/api/utility-bills/${id}/issue`, { method: "POST", body: "{}" });
  toast("Счёт выставлен жильцам");
  await loadAll();
}

async function providerPaid(id) {
  await api(`/api/utility-bills/${id}/provider-paid`, { method: "POST", body: "{}" });
  toast("Оплата поставщику отмечена");
  await loadAll();
}

async function compensateExpense(id) {
  await api(`/api/expenses/${id}/compensate`, { method: "POST", body: "{}" });
  toast("Расход компенсирован");
  await loadAll();
}

async function sendTemplateMessage(leaseId, templateKey, chargeId = null, utilityLineId = null) {
  await api("/api/messages/send", {
    method: "POST",
    body: JSON.stringify({
      lease_id: leaseId,
      template_key: templateKey,
      charge_id: chargeId,
      utility_line_id: utilityLineId,
    }),
  });
  toast("Сообщение отправлено через бота");
  await loadAll();
}

async function sendCustomMessage(leaseId) {
  const customText = prompt("Текст сообщения");
  if (!customText) return;
  await api("/api/messages/send", {
    method: "POST",
    body: JSON.stringify({
      lease_id: leaseId,
      template_key: "custom",
      custom_text: customText,
    }),
  });
  toast("Сообщение отправлено через бота");
  await loadAll();
}

async function moveOut(id) {
  const endDate = prompt("Дата выезда", today());
  if (!endDate) return;
  const result = await api(`/api/leases/${id}/move-out`, { method: "POST", body: JSON.stringify({ end_date: endDate }) });
  const s = result.summary;
  alert(`Выезд оформлен.\nПолных месяцев: ${s.full_months_lived}\nПоследний оплаченный день: ${formatDate(s.last_paid_day)}\nДолг аренда: ${money(s.rent_debt)}\nДолг коммуналка: ${money(s.utility_debt)}\nЗалог: ${money(s.deposit_amount)}\nУсловия: ${s.deposit_terms || "нет"}`);
  await loadAll();
}

function openReportsTab() {
  const tab = qs('.tab[data-tab="reports"]');
  if (tab) tab.click();
}

function openTenantsTab() {
  const tab = qs('.tab[data-tab="tenants"]');
  if (tab) tab.click();
}

function openMetersTab() {
  const tab = qs('.tab[data-tab="meters"]');
  if (tab) tab.click();
}

function openUtilitiesTab() {
  const tab = qs('.tab[data-tab="utilities"]');
  if (tab) tab.click();
}

function openExpensesTab() {
  const tab = qs('.tab[data-tab="expenses"]');
  if (tab) tab.click();
}

function openMessagesTab() {
  const tab = qs('.tab[data-tab="messages"]');
  if (tab) tab.click();
}

function issueSeverityText(severity) {
  if (severity === "danger") return "критично";
  if (severity === "warn") return "вопрос";
  return severity;
}

function openMonthlyReport(year, month) {
  const report = state.bootstrap.dashboard.monthly_reports.find((item) => item.year === year && item.month === month);
  if (!report) return;
  openReportsTab();
  qs("#reportStart").value = report.period_start;
  qs("#reportEnd").value = report.period_end;
  setReportLinks();
  const rows = report.issues.map((issue) => `
    <tr>
      <td><span class="pill ${issue.severity === "danger" ? "danger" : "warn"}">${issueSeverityText(issue.severity)}</span></td>
      <td>${issue.title}</td>
      <td>${issue.count}</td>
      <td>${issue.detail}</td>
    </tr>
  `).join("");
  qs("#monthlyReportDetails").innerHTML = `
    <article class="card monthly-current report-${report.severity}">
      <div class="section-title">
        <h2>${report.title}</h2>
        <a class="button primary" href="${report.download_url}">Скачать месячный отчёт</a>
      </div>
      <p class="muted">${formatDateRange(report.period_start, report.period_end)} · ${monthlySeverityText(report)}</p>
      ${table(["Важность", "Проблема", "Кол-во", "Комментарий"], rows)}
    </article>
  `;
}

function setReportLinks() {
  const start = qs("#reportStart").value || "";
  const end = qs("#reportEnd").value || "";
  const query = start && end ? `?start=${start}&end=${end}` : "";
  qs("#rentReport").href = `/api/reports/rent.xlsx${query}`;
  qs("#utilitiesReport").href = `/api/reports/utilities.xlsx${query}`;
  qs("#expensesReport").href = `/api/reports/expenses.xlsx${query}`;
}

async function connectTelegramWebhook() {
  const result = await api("/api/integrations/telegram/set-webhook", { method: "POST", body: "{}" });
  toast(result.description || "Webhook подключён");
  await loadAll();
}

async function telegramWebhookInfo() {
  const result = await api("/api/integrations/telegram/webhook-info");
  const summary = [
    result.url ? `URL: ${result.url}` : "Webhook пока не установлен",
    result.pending_update_count ? `Pending: ${result.pending_update_count}` : "Pending: 0",
    result.last_error_message ? `Ошибка: ${result.last_error_message}` : "Ошибок от Telegram нет",
  ].join("\n");
  alert(summary);
}

async function sendTelegramTest() {
  await api("/api/integrations/telegram/send-test", { method: "POST", body: "{}" });
  toast("Тестовое сообщение отправлено");
}

async function runRemindersNow() {
  const result = await api("/api/reminders/run", { method: "POST", body: "{}" });
  if (!result.enabled) {
    toast(`Автонапоминания выключены. Граница сейчас ${formatDate(result.cutoff_date)}.`);
    return;
  }
  toast(`Напоминания: отправлено ${result.sent}, дубликаты ${result.skipped_duplicate}, старые долги под молчанием ${result.skipped_legacy}`);
  await loadAll();
}

function resetQuickReadingConfirm() {
  state.quickReadingArmedUntil = 0;
  const button = qs("#quickReadingsSubmit");
  if (button) button.textContent = "Передать показания";
}

async function submitQuickReadings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const readings = qsa(".quick-reading-input", form)
    .filter((input) => input.value.trim() !== "")
    .map((input) => ({ meter_id: Number(input.dataset.meterId), value: Number(input.value) }));
  if (!readings.length) {
    toast("Нет заполненных показаний");
    return;
  }
  const now = Date.now();
  if (now > state.quickReadingArmedUntil) {
    state.quickReadingArmedUntil = now + 8000;
    qs("#quickReadingsSubmit").textContent = "Нажать ещё раз для передачи";
    toast(`Проверка от случайного тыка: будет передано ${readings.length} показаний. Нажми ещё раз.`);
    return;
  }
  const result = await api("/api/meter-readings/batch", {
    method: "POST",
    body: JSON.stringify({ reading_date: qs("#quickReadingDate").value || today(), readings }),
  });
  resetQuickReadingConfirm();
  qsa(".quick-reading-input", form).forEach((input) => {
    input.value = "";
  });
  toast(`Показаний сохранено: ${result.saved}`);
  await loadAll();
}

function bindEvents() {
  qsa(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      qsa(".tab").forEach((item) => item.classList.remove("active"));
      qsa(".panel").forEach((item) => item.classList.remove("active"));
      tab.classList.add("active");
      qs(`#${tab.dataset.tab}`).classList.add("active");
    });
  });

  qs("#refreshBtn").addEventListener("click", loadAll);
  qs("#runRemindersBtn").addEventListener("click", runRemindersNow);
  qs("#importBaselineBtn")?.addEventListener("click", importBaseline);
  qs("#manualPaymentForm")?.addEventListener("submit", submitManualPayment);
  qs("#manualPaymentKindSelect")?.addEventListener("change", updateManualPaymentKind);
  qs("#openTariffsBtn").addEventListener("click", () => {
    const tab = qs('.tab[data-tab="tariffs"]');
    if (tab) tab.click();
  });
  qs("#telegramWebhookBtn").addEventListener("click", connectTelegramWebhook);
  qs("#telegramWebhookInfoBtn").addEventListener("click", telegramWebhookInfo);
  qs("#telegramTestBtn").addEventListener("click", sendTelegramTest);
  qs("#generateRentBtn").addEventListener("click", async () => {
    const result = await api("/api/rent-charges/generate", { method: "POST", body: "{}" });
    toast(`Создано начислений: ${result.created}`);
    await loadAll();
  });
  qs("#loadRentBtn").addEventListener("click", async () => {
    await loadRent();
    renderRent();
  });

  qs("#onboardForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = formData(form);
    if (state.editingLeaseId) {
      await api(`/api/leases/${state.editingLeaseId}`, { method: "PATCH", body: JSON.stringify(data) });
      toast("????????? ?? ?????? ?????????");
      state.editingLeaseId = null;
    } else {
      await api("/api/leases/onboard", { method: "POST", body: JSON.stringify(data) });
      toast("????? ???????");
    }
    form.reset();
    await loadAll();
  });
  qs("#cancelLeaseEditBtn")?.addEventListener("click", cancelLeaseEdit);

  qs("#readingForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await api("/api/meter-readings", { method: "POST", body: JSON.stringify(formData(event.currentTarget)) });
    toast("Показание сохранено");
    await loadAll();
  });
  qs("#quickReadingsForm").addEventListener("submit", submitQuickReadings);

  qs("#utilityCalcForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const result = await api("/api/utility-bills/calculate", { method: "POST", body: JSON.stringify(formData(event.currentTarget)) });
    toast(`Черновик создан: ${money(result.total_cost)}`);
    await loadAll();
  });

  qs("#tariffForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await api("/api/tariffs", { method: "POST", body: JSON.stringify(formData(event.currentTarget)) });
    event.currentTarget.reset();
    toast("Тариф добавлен");
    await loadAll();
  });

  qs("#expenseForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await api("/api/expenses", { method: "POST", body: JSON.stringify(formData(event.currentTarget)) });
    event.currentTarget.reset();
    toast("Расход добавлен");
    await loadAll();
  });

  qs("#messageTemplatesForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = formData(event.currentTarget);
    const settings = await api("/api/settings", { method: "POST", body: JSON.stringify(data) });
    applySettings(settings);
    toast("Шаблоны сохранены");
  });

  qs("#settingsForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = formData(event.currentTarget);
    const settings = await api("/api/settings", { method: "POST", body: JSON.stringify(data) });
    applySettings(settings);
    toast("Настройки сохранены");
  });

  qs("#paletteSelect").addEventListener("change", (event) => {
    applySettings({ ...state.settings, color_palette: event.currentTarget.value });
  });

  ["reportStart", "reportEnd"].forEach((id) => qs(`#${id}`).addEventListener("change", setReportLinks));
}

window.addEventListener("unhandledrejection", (event) => {
  const message = event.reason?.message || String(event.reason || "Ошибка");
  toast(message);
});

window.addEventListener("error", (event) => {
  if (event?.message) toast(event.message);
});

bindEvents();
loadAll().catch((error) => toast(error.message));
