const state = {
  bootstrap: null,
  rentCharges: [],
  utilityBills: [],
  expenses: [],
  tariffs: [],
  settings: {},
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
};

const money = (value) => new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 2 }).format(value || 0);
const today = () => new Date().toISOString().slice(0, 10);
const monthNames = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const monthNamesNominative = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];

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

function statusPill(status) {
  const cls = status === "overdue" ? "danger" : status === "partial" || status === "deferred" ? "warn" : status === "paid" || status === "paid_ahead" ? "ok" : "";
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
  if (appBase) appBase.value = state.settings.app_base_url || "";
  if (ownerChat) ownerChat.value = state.settings.telegram_owner_chat_id || "";
  if (token) token.placeholder = state.settings.telegram_bot_token_configured ? "Токен сохранён, пусто = не менять" : "Вставь bot token";
  if (secret) secret.placeholder = state.settings.telegram_webhook_secret_configured ? "Secret сохранён, пусто = не менять" : "Вставь webhook secret";
  renderTelegramStatus();
}

function renderTelegramStatus() {
  const box = qs("#telegramStatusBox");
  if (!box) return;
  box.innerHTML = `
    <h3>Telegram</h3>
    <div class="pill-row">
      <span class="pill ${state.settings.telegram_bot_token_configured ? "ok" : "warn"}">token ${state.settings.telegram_bot_token_configured ? "сохранён" : "не задан"}</span>
      <span class="pill ${state.settings.telegram_webhook_secret_configured ? "ok" : "warn"}">secret ${state.settings.telegram_webhook_secret_configured ? "сохранён" : "не задан"}</span>
      <span class="pill ${state.settings.telegram_owner_chat_id ? "ok" : "warn"}">owner chat ${state.settings.telegram_owner_chat_id || "не задан"}</span>
    </div>
    <p class="muted">${state.settings.app_base_url || "Публичный URL не задан. Без него webhook не оживёт, как ни уговаривай."}</p>
  `;
}

async function loadAll() {
  state.bootstrap = await api("/api/bootstrap");
  applySettings(state.bootstrap.settings);
  await Promise.all([loadRent(), loadUtilityBills(), loadExpenses(), loadTariffs()]);
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

function hydrateForms() {
  const apartments = allApartments();
  const services = state.bootstrap.services;
  qsa('select[name="apartment_id"]').forEach((select) => setOptions(select, apartments, (a) => `${a.object_name}: ${a.name}`, select.closest("#expenseForm")));
  qsa('select[name="object_id"]').forEach((select) => setOptions(select, state.bootstrap.objects, (o) => o.name, true));
  qsa('select[name="meter_id"]').forEach((select) => setOptions(select, state.bootstrap.meters, (m) => `${m.object}: ${m.name}`));
  qsa('select[name="service_id"]').forEach((select) => setOptions(select, services, (s) => `${s.object}: ${s.name}`));
  const dateInputs = qsa('input[type="date"]');
  dateInputs.forEach((input) => {
    if (!input.value) input.value = today();
  });
  setReportLinks();
}

function renderAll() {
  renderDashboard();
  renderObjects();
  renderLeases();
  renderRent();
  renderMeters();
  renderUtilities();
  renderTariffs();
  renderExpenses();
  setReportLinks();
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
  dashboard.rent_today.forEach((item) => cards.push(attentionCard("warn", "Сегодня оплата аренды", `${item.apartment}, ${item.tenant}: ${money(item.debt)}`, rentActions(item), item.due_date)));
  dashboard.rent_overdue.forEach((item) => cards.push(attentionCard("danger", "Просрочена аренда", `${item.apartment}, ${item.tenant}: ${money(item.debt)}`, rentActions(item), item.due_date)));
  dashboard.rent_partial.forEach((item) => cards.push(attentionCard("warn", "Аренда оплачена частично", `${item.apartment}, ${item.tenant}: осталось ${money(item.debt)}`, rentActions(item), item.due_date)));
  dashboard.rent_deferred.forEach((item) => cards.push(attentionCard("warn", "Скоро конец отсрочки", `${item.apartment}, ${item.tenant}: осталось ${item.deferral_days_left} дн., до ${formatDate(item.deferral_until)}`, rentActions(item), item.due_date)));
  dashboard.utility_overdue.forEach((item) => cards.push(attentionCard("danger", "Просрочена коммуналка", `${item.apartment}, ${item.tenant}: ${money(item.debt)}`, utilityActions(item), item.due_date)));
  dashboard.stale_readings.forEach((item) => cards.push(attentionCard("warn", "Давно нет показаний", `${item.object}: ${item.service}. Последнее: ${item.last_date ? formatDate(item.last_date) : "нет"}`, "", item.last_date || appToday())));
  dashboard.pending_personal_expenses.forEach((item) => cards.push(attentionCard("warn", "Личный расход ждёт компенсации", `${item.object || "без объекта"} ${item.apartment || ""}: ${money(item.amount)} — ${item.description || item.category}`, `<button class="mini primary" onclick="compensateExpense(${item.id})">Компенсировать</button>`, item.expense_date)));
  dashboard.suspicious_receipts.forEach((item) => cards.push(attentionCard("danger", "Подозрительный чек", `${money(item.amount)}: ${item.recipient_name || "получатель не распознан"}`, "", item.created_at)));
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

function attentionCard(type, title, text, actions, monthValue) {
  return `<article class="attention-card ${type}"><div class="pill-row">${monthMeta(monthValue)}</div><h3>${title}</h3><p>${text}</p><div class="pill-row">${actions || ""}</div></article>`;
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
        ${lease.active ? `<button class="mini danger-soft" onclick="moveOut(${lease.id})">Выезд</button>` : ""}
      </td>
    </tr>
  `).join("");
  qs("#leaseList").innerHTML = table(["Объект", "Квартира", "Жилец", "Заезд", "День", "ИП / личный", "Залог", "Статус", "Действия"], rows);
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
    ${ipLeft > 0 ? `<button class="mini primary" onclick="payRent(${charge.id}, 'ip', ${ipLeft})">ИП оплачено</button>` : ""}
    ${personalLeft > 0 ? `<button class="mini primary" onclick="payRent(${charge.id}, 'personal', ${personalLeft})">Личный оплачено</button>` : ""}
    <button class="mini" onclick="deferRent(${charge.id})">Отсрочка</button>
  `;
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
  form.elements.tiers.value = service?.kind === "electricity" ? "1000:4.18; 2200:4.7; 3000:7; 9" : "*:0";
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
    await api("/api/leases/onboard", { method: "POST", body: JSON.stringify(formData(event.currentTarget)) });
    event.currentTarget.reset();
    toast("Жилец заселён");
    await loadAll();
  });

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

bindEvents();
loadAll().catch((error) => toast(error.message));
