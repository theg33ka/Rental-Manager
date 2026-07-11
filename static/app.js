const { api, configureApiClient, downloadFile, triggerNativeDownload } = window.RentalApi;

const state = {
  auth: { authenticated: false, role: null },
  bootstrap: null,
  rentCharges: [],
  utilityBills: [],
  utilityTimeline: [],
  expenses: [],
  tariffs: [],
  messageTargets: [],
  botDialogs: [],
  botDialogMessages: [],
  selectedBotDialogId: null,
  botDialogsLoaded: false,
  botDialogLoading: false,
  messagePreview: null,
  suspiciousReceipts: [],
  paymentHistory: null,
  editingPaymentReceiptId: null,
  utilityIssuePreview: null,
  settings: {},
  editingLeaseId: null,
  dashboardAttentionGroups: [],
  manualAllocation: null,
  manualDebt: null,
  quickReadingArmedUntil: 0,
  performance: null,
  loadFailures: [],
};

const ownerTabs = ["dashboard", "tenants", "rent", "meters", "utilities", "tariffs", "expenses", "reports", "dialogs", "messages", "automation", "settings"];
const guestTabs = ["dashboard", "reports"];
const panelMeta = {
  dashboard: ["Операционный центр", "Состояние портфеля"],
  tenants: ["Портфель", "Жильцы и договоры"],
  rent: ["Финансы", "Начисления и оплаты"],
  meters: ["Коммунальные услуги", "Показания счётчиков"],
  utilities: ["Коммунальные услуги", "Счета и распределение"],
  tariffs: ["Коммунальные услуги", "Тарифы"],
  expenses: ["Финансы", "Расходы"],
  reports: ["Аналитика", "Отчёты и документы"],
  dialogs: ["Коммуникации", "Входящие диалоги"],
  messages: ["Коммуникации", "Рассылки и шаблоны"],
  automation: ["Управление", "Автоматизация"],
  settings: ["Система", "Настройки"],
};
let activeNavGroup = "overview";
const appStateLoadGroups = [
  {
    sections: ["bootstrap"],
    percent: 64,
    title: "Открываю пульт",
    detail: "Загружаю минимум для первого экрана. Остальное не держит дверь.",
  },
  {
    sections: ["registry"],
    percent: 72,
    title: "Готовлю вкладки",
    detail: "Объекты, жильцы, счётчики и услуги догружаются после первого экрана.",
  },
  {
    sections: ["rent_charges", "utility_bills", "expenses", "tariffs"],
    percent: 86,
    title: "Подтягиваю расчёты",
    detail: "Загружаю аренду, коммунальные услуги, расходы и тарифы.",
  },
  {
    sections: ["utility_timeline", "message_targets", "suspicious_receipts"],
    percent: 94,
    title: "Загружаю дополнительные данные",
    detail: "Таймлайн, рассылки и чеки загружаются последними.",
  },
];
const appStateSectionOrder = [...new Set(appStateLoadGroups.flatMap((group) => group.sections))];
const mutationRefreshSections = appStateSectionOrder.filter((section) => section !== "registry");
const registryRefreshSections = [...appStateSectionOrder];
let silentRefreshPromise = null;
const silentRefreshSections = new Set();
const modalReturnFocus = new Map();

function markFrontendPerf(name, detail = {}) {
  try {
    const markName = `rental:${name}`;
    window.performance?.mark?.(markName);
    console.info("[PERF]", name, detail);
  } catch {
    // Сбой диагностической метки не должен мешать работе интерфейса.
  }
}

markFrontendPerf("script_loaded");

function authRole() {
  return state.auth?.role || null;
}

function isOwner() {
  return authRole() === "owner";
}

function isGuest() {
  return authRole() === "guest";
}

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
  accepted: "зачтён",
  moderated: "модерировано",
  rejected: "отклонён",
  ignored: "скрыт",
  cancelled: "отменено",
};

const money = (value) => new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 2 }).format(value || 0);
const today = () => new Date().toISOString().slice(0, 10);
const daysAgo = (days) => {
  const value = new Date();
  value.setDate(value.getDate() - days);
  return new Date(value.getTime() - value.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
};
const monthNames = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const monthNamesNominative = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];

function localDateTimeNow() {
  const now = new Date();
  const shifted = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return shifted.toISOString().slice(0, 16);
}

function currentYear() {
  return new Date().getFullYear();
}

function monthOptions(selectedMonth = 0) {
  return monthNamesNominative.map((label, index) => {
    const value = index + 1;
    return `<option value="${value}" ${selectedMonth === value ? "selected" : ""}>${label}</option>`;
  }).join("");
}

function receiptTargetParts(receipt) {
  const fallbackMonth = new Date().getMonth() + 1;
  const fallbackYear = Number(state.paymentHistory?.current_year) || currentYear();
  if (receipt.target_month) {
    const [yearValue, monthValue] = receipt.target_month.slice(0, 10).split("-").map(Number);
    return {
      month: Number(receipt.target_month_number) || monthValue || fallbackMonth,
      year: Number(receipt.target_year) || yearValue || fallbackYear,
    };
  }
  return {
    month: Number(receipt.target_month_number) || fallbackMonth,
    year: Number(receipt.target_year) || fallbackYear,
  };
}

function qs(selector, root = document) {
  return root.querySelector(selector);
}

function qsa(selector, root = document) {
  return [...root.querySelectorAll(selector)];
}

function on(selector, eventName, handler) {
  const node = typeof selector === "string" ? qs(selector) : selector;
  if (!node) return;
  node.addEventListener(eventName, handler);
}

function openAccessibleModal(root, closeHandler) {
  if (!modalReturnFocus.has(root.id)) modalReturnFocus.set(root.id, document.activeElement);
  root.setAttribute("role", "dialog");
  root.setAttribute("aria-modal", "true");
  const heading = root.querySelector("h3");
  if (heading) {
    heading.id = `${root.id}Title`;
    root.setAttribute("aria-labelledby", heading.id);
  }
  const focusable = () => qsa('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])', root).filter((item) => !item.hidden);
  root.onkeydown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeHandler();
      return;
    }
    if (event.key !== "Tab") return;
    const items = focusable();
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };
  window.setTimeout(() => focusable()[0]?.focus(), 0);
}

function restoreModalFocus(root) {
  const target = modalReturnFocus.get(root.id);
  modalReturnFocus.delete(root.id);
  root.onkeydown = null;
  if (target instanceof HTMLElement && document.contains(target)) window.setTimeout(() => target.focus(), 0);
}

function showAuthOverlay() {
  hideLoadingOverlay();
  document.body.classList.add("auth-locked");
  const overlay = qs("#authOverlay");
  if (overlay) overlay.hidden = false;
  const input = qs("#pinCodeInput");
  if (input) window.setTimeout(() => input.focus(), 40);
}

function hideAuthOverlay() {
  document.body.classList.remove("auth-locked");
  const overlay = qs("#authOverlay");
  if (overlay) overlay.hidden = true;
}

function setLoadingStep(percent, title, detail = "") {
  const overlay = qs("#loadingOverlay");
  const bar = qs("#loadingProgressBar");
  const percentNode = qs("#loadingProgressPercent");
  const titleNode = qs("#loadingTitle");
  const detailNode = qs("#loadingDetail");
  const safePercent = Math.max(0, Math.min(100, Math.round(percent || 0)));
  if (overlay) overlay.hidden = false;
  document.body.classList.add("app-loading");
  if (bar) bar.style.width = `${safePercent}%`;
  if (percentNode) percentNode.textContent = `${safePercent}%`;
  if (titleNode) titleNode.textContent = title || "Загружаю";
  if (detailNode) detailNode.textContent = detail || "Получаю данные с сервера.";
}

function hideLoadingOverlay() {
  document.body.classList.remove("app-loading");
  const overlay = qs("#loadingOverlay");
  if (overlay) overlay.hidden = true;
}

function applyAccessUi() {
  const role = authRole();
  document.body.dataset.role = role || "anonymous";
  const allowedTabs = role === "owner" ? ownerTabs : guestTabs;
  qsa(".tab").forEach((tab) => {
    const allowed = allowedTabs.includes(tab.dataset.tab) && tab.dataset.group === activeNavGroup;
    tab.hidden = !allowed;
    tab.disabled = !allowed;
  });
  qsa(".nav-group").forEach((group) => {
    const hasAllowedTab = qsa(`.tab[data-group="${group.dataset.group}"]`).some((tab) => allowedTabs.includes(tab.dataset.tab));
    group.hidden = !hasAllowedTab;
    group.classList.toggle("active", group.dataset.group === activeNavGroup);
  });
  const activeTab = qs(".tab.active");
  if (!activeTab || activeTab.hidden) {
    const firstAvailableTab = qsa(".tab").find((tab) => !tab.hidden && !tab.disabled);
    if (firstAvailableTab) firstAvailableTab.click();
  }
  const remindersButton = qs("#runRemindersBtn");
  if (remindersButton) remindersButton.hidden = !isOwner();
  const logoutButton = qs("#logoutBtn");
  if (logoutButton) logoutButton.hidden = !role;
  const badge = qs("#authRoleBadge");
  if (badge) {
    badge.textContent = role === "owner" ? "owner" : role === "guest" ? "guest" : "PIN не введён";
    badge.className = `pill ${role === "owner" ? "ok" : role === "guest" ? "warn" : ""}`.trim();
  }
  const visibleActiveTab = qs(".tab.active:not([hidden])");
  if (visibleActiveTab) updateWorkspaceContext(visibleActiveTab.dataset.tab);
}

function activateNavGroup(groupName, selectDefault = true) {
  activeNavGroup = groupName;
  applyAccessUi();
  if (!selectDefault) return;
  const firstAvailable = qsa(`.tab[data-group="${groupName}"]`).find((tab) => !tab.hidden && !tab.disabled);
  if (firstAvailable) firstAvailable.click();
}

function updateWorkspaceContext(tabName) {
  const [context, title] = panelMeta[tabName] || ["Rental Manager", "Рабочая область"];
  const contextNode = qs("#pageContext");
  const titleNode = qs("#pageTitle");
  if (contextNode) contextNode.textContent = isGuest() && tabName === "dashboard" ? "Owner view" : context;
  if (titleNode) titleNode.textContent = isGuest() && tabName === "dashboard" ? "Сводка для владельца" : title;
  document.title = `${title} · Rental Manager`;
}

function filterActivePanel(value) {
  const query = String(value || "").trim().toLocaleLowerCase("ru");
  const panel = qs(".panel.active");
  if (!panel) return;
  qsa("tbody tr, .attention-card, .dialog-item, .tariff-grid > *, .stack > .card", panel).forEach((item) => {
    item.classList.toggle("search-hidden", Boolean(query) && !item.textContent.toLocaleLowerCase("ru").includes(query));
  });
}

function closeQuickActions() {
  const root = qs("#quickActionsModal");
  if (!root) return;
  root.hidden = true;
  root.innerHTML = "";
  restoreModalFocus(root);
}

function openQuickActions() {
  if (!isOwner()) return;
  const root = qs("#quickActionsModal");
  if (!root) return;
  root.innerHTML = `
    <div class="modal-card">
      <div class="section-title"><div><p class="eyebrow">Быстрый доступ</p><h2>Новое действие</h2></div><button class="mini" type="button" onclick="closeQuickActions()">Закрыть</button></div>
      <div class="quick-actions-grid">
        <button class="quick-action" type="button" onclick="runQuickAction('onboard')"><strong>Новый договор</strong><span>Заселить жильца и задать условия</span></button>
        <button class="quick-action" type="button" onclick="runQuickAction('payment')"><strong>Зачесть платёж</strong><span>Наличные, перевод или корректировка</span></button>
        <button class="quick-action" type="button" onclick="runQuickAction('reading')"><strong>Внести показание</strong><span>Добавить значение счётчика</span></button>
        <button class="quick-action" type="button" onclick="runQuickAction('utility')"><strong>Рассчитать коммуналку</strong><span>Создать черновик начислений</span></button>
        <button class="quick-action" type="button" onclick="runQuickAction('expense')"><strong>Добавить расход</strong><span>Зафиксировать операционные затраты</span></button>
        <button class="quick-action" type="button" onclick="runQuickAction('message')"><strong>Отправить сообщение</strong><span>Открыть коммуникации с жильцами</span></button>
      </div>
    </div>
  `;
  openAccessibleModal(root, closeQuickActions);
}

function openOnboardTool() {
  const tool = qs("#onboardTool");
  if (!tool) return;
  tool.open = true;
  tool.scrollIntoView({ behavior: "smooth", block: "start" });
  qs('#onboardForm select[name="apartment_id"]')?.focus();
}

function openManualPaymentTool() {
  const tool = qs("#manualPaymentTool");
  if (!tool) return;
  tool.open = true;
  tool.scrollIntoView({ behavior: "smooth", block: "start" });
  qs("#manualPaymentLeaseSelect")?.focus();
}

function runQuickAction(action) {
  closeQuickActions();
  if (action === "onboard") {
    openTenantsTab();
    window.setTimeout(openOnboardTool, 0);
  } else if (action === "payment") {
    openRentTab();
    window.setTimeout(openManualPaymentTool, 0);
  } else if (action === "reading") {
    openMetersTab();
    window.setTimeout(() => qs('#readingForm select[name="meter_id"]')?.focus(), 0);
  } else if (action === "utility") {
    openUtilitiesTab();
    window.setTimeout(() => qs("#utilityServiceSelect")?.focus(), 0);
  } else if (action === "expense") {
    openExpensesTab();
    window.setTimeout(() => qs('#expenseForm input[name="amount"]')?.focus(), 0);
  } else if (action === "message") {
    openMessagesTab();
    window.setTimeout(() => qs("#broadcastMessageInput")?.focus(), 0);
  }
}

configureApiClient({
  onUnauthorized: () => {
    state.auth = { authenticated: false, role: null };
    applyAccessUi();
    showAuthOverlay();
  },
});

window.downloadFile = downloadFile;

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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll('"', "&quot;");
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

function setValueOptions(select, values, getLabel, placeholder = "Не выбрано") {
  select.innerHTML = "";
  select.append(new Option(placeholder, ""));
  values.forEach((value) => {
    select.append(new Option(getLabel(value), value));
  });
}

function allApartments() {
  return state.bootstrap.objects
    .flatMap((object) => object.apartments.map((apartment) => ({ ...apartment, object_name: object.name })))
    .sort(compareApartmentRefs);
}

function activeApartments() {
  return allApartments().filter((apartment) => apartment.active);
}

function objectSortRank(name = "") {
  const normalized = String(name).toLowerCase().replaceAll("ё", "е");
  if (normalized.includes("бел") || normalized.includes("бд")) return 1;
  if (normalized.includes("чер") || normalized.includes("чд")) return 2;
  if (normalized.includes("бан")) return 3;
  return 20;
}

function apartmentSortNumber(name = "") {
  const match = String(name).match(/\d+/);
  return match ? Number(match[0]) : 999;
}

function compareApartmentRefs(left, right) {
  const leftObject = left.object || left.object_name || "";
  const rightObject = right.object || right.object_name || "";
  const leftApartment = left.apartment || left.name || "";
  const rightApartment = right.apartment || right.name || "";
  return objectSortRank(leftObject) - objectSortRank(rightObject)
    || apartmentSortNumber(leftApartment) - apartmentSortNumber(rightApartment)
    || String(leftObject).localeCompare(String(rightObject), "ru")
    || String(leftApartment).localeCompare(String(rightApartment), "ru");
}

function utilityReadingDates(serviceId) {
  const dates = state.utilityTimeline
    .filter((event) => event.kind === "reading" && Number(event.service_id) === Number(serviceId))
    .map((event) => event.date);
  return [...new Set(dates)].sort();
}

function utilityTargetServices(targetValue) {
  const value = String(targetValue || "");
  if (value.startsWith("object:")) {
    const objectId = Number(value.slice("object:".length));
    return state.bootstrap.services.filter((service) => Number(service.object_id) === objectId);
  }
  const serviceId = Number(value.replace("service:", ""));
  return state.bootstrap.services.filter((service) => Number(service.id) === serviceId);
}

function utilityReadingDatesForTarget(targetValue) {
  const services = utilityTargetServices(targetValue);
  if (!services.length) return [];
  const dateSets = services.map((service) => new Set(utilityReadingDates(service.id)));
  const [firstSet, ...restSets] = dateSets;
  return [...firstSet].filter((value) => restSets.every((set) => set.has(value))).sort();
}

function bootstrapDefaults() {
  return {
    today: today(),
    auth: { role: authRole() },
    objects: [],
    leases: [],
    meters: [],
    services: [],
    settings: {},
    dashboard: {
      object_summary: { occupied: 0, total: 0, by_object: [] },
      month_summary: {},
      monthly_reports: [],
      rent_overdue: [],
      rent_partial: [],
      rent_today: [],
      rent_deferred: [],
      utility_overdue: [],
      utility_partial: [],
      utility_issued: [],
      manual_debts: [],
      pending_personal_expenses: [],
      expense_fund: { received: 0, spent: 0, balance: 0, has_mismatch: false },
      stale_readings: [],
      provider_reading_due: [],
      provider_debts: [],
      suspicious_receipts: [],
    },
  };
}

function mergeBootstrap(payload = {}) {
  const previous = state.bootstrap || bootstrapDefaults();
  const next = { ...bootstrapDefaults(), ...previous, ...payload };
  ["objects", "leases", "meters", "services"].forEach((key) => {
    if (!Array.isArray(payload[key]) || (!payload[key].length && Array.isArray(previous[key]) && previous[key].length)) {
      next[key] = Array.isArray(previous[key]) ? previous[key] : [];
    }
  });
  next.settings = { ...(previous.settings || {}), ...(payload.settings || {}) };
  next.dashboard = { ...(bootstrapDefaults().dashboard), ...(previous.dashboard || {}), ...(payload.dashboard || {}) };
  return next;
}

function applyRegistryPayload(registry = {}) {
  state.bootstrap = mergeBootstrap({
    objects: Array.isArray(registry.objects) ? registry.objects : [],
    leases: Array.isArray(registry.leases) ? registry.leases : [],
    meters: Array.isArray(registry.meters) ? registry.meters : [],
    services: Array.isArray(registry.services) ? registry.services : [],
  });
}

function hydrateUtilityTargetSelect() {
  const select = qs("#utilityServiceSelect");
  if (!select) return;
  const previous = select.value;
  const options = [];
  state.bootstrap.objects.forEach((object) => {
    const services = state.bootstrap.services.filter((service) => Number(service.object_id) === Number(object.id));
    if (services.length > 1) {
      options.push({ value: `object:${object.id}`, label: `${object.name}: общий` });
    }
    services.forEach((service) => {
      options.push({ value: `service:${service.id}`, label: `${service.object}: ${service.name}` });
    });
  });
  select.innerHTML = "";
  options.forEach((item) => select.append(new Option(item.label, item.value)));
  if (options.some((item) => item.value === previous)) {
    select.value = previous;
  }
}

function editingLease() {
  return state.bootstrap?.leases?.find((lease) => lease.id === state.editingLeaseId) || null;
}

function statusPill(status, dueDate = "") {
  const future = isFutureDue(dueDate) && !["paid", "paid_ahead", "accepted", "compensated", "not_required"].includes(status);
  const cls = future
    ? "future"
    : status === "overdue" || status === "suspicious"
    ? "danger"
    : status === "partial" || status === "deferred"
      ? "warn"
      : status === "paid" || status === "paid_ahead" || status === "accepted"
        ? "ok"
        : "";
  return `<span class="pill ${cls}">${statusText[status] || status}</span>`;
}

function isFutureDue(value) {
  const parsed = parseLocalDate(value);
  const current = parseLocalDate(appToday());
  return Boolean(parsed && current && parsed > current);
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
    color_palette: "premium",
    app_base_url: "",
    telegram_owner_chat_id: "",
    notifications_enabled: false,
    notification_cutoff_date: "",
    automation_rent_due_cadence: "daily_evening",
    automation_rent_overdue_cadence: "daily_evening",
    automation_utility_cadence: "daily_evening",
    ai_enabled: false,
    ai_tenant_free_text_enabled: true,
    hermes_api_base_url: "http://127.0.0.1:8642",
    hermes_model_default: "deepseek-V4",
    hermes_model_audit: "deepseek-V4",
    ai_monthly_budget_rub: "1000",
    ip_recipient_name: "",
    ip_recipient_account: "",
    ip_recipient_bik: "",
    personal_recipient_name: "",
    personal_recipient_phone: "",
    personal_recipient_bank: "",
    panel_owner_pin_code_configured: false,
    panel_guest_pin_code_configured: false,
    telegram_bot_token_configured: false,
    telegram_webhook_secret_configured: false,
    hermes_api_key_configured: false,
    ...settings,
  };
  document.body.dataset.palette = "premium";
  const select = qs("#paletteSelect");
  if (select) select.value = "premium";
  const appBase = qs("#appBaseUrlInput");
  const ownerChat = qs("#telegramOwnerChatIdInput");
  const token = qs("#telegramBotTokenInput");
  const secret = qs("#telegramWebhookSecretInput");
  const aiEnabled = qs("#aiEnabledInput");
  const aiTenantFreeText = qs("#aiTenantFreeTextEnabledInput");
  const hermesBaseUrl = qs("#hermesApiBaseUrlInput");
  const hermesApiKey = qs("#hermesApiKeyInput");
  const hermesDefaultModel = qs("#hermesModelDefaultInput");
  const hermesAuditModel = qs("#hermesModelAuditInput");
  const aiBudget = qs("#aiMonthlyBudgetRubInput");
  const ownerPin = qs("#panelOwnerPinCodeInput");
  const guestPin = qs("#panelGuestPinCodeInput");
  const notificationsEnabled = qs("#notificationsEnabledInput");
  const notificationCutoffDate = qs("#notificationCutoffDateInput");
  const automationRentDue = qs("#automationRentDueCadenceInput");
  const automationRentOverdue = qs("#automationRentOverdueCadenceInput");
  const automationUtility = qs("#automationUtilityCadenceInput");
  const ipRecipientName = qs("#ipRecipientNameInput");
  const ipRecipientAccount = qs("#ipRecipientAccountInput");
  const ipRecipientBik = qs("#ipRecipientBikInput");
  const personalRecipientName = qs("#personalRecipientNameInput");
  const personalRecipientPhone = qs("#personalRecipientPhoneInput");
  const personalRecipientBank = qs("#personalRecipientBankInput");
  if (appBase) appBase.value = state.settings.app_base_url || "";
  if (ownerChat) ownerChat.value = state.settings.telegram_owner_chat_id || "";
  if (aiEnabled) aiEnabled.checked = Boolean(state.settings.ai_enabled);
  if (aiTenantFreeText) aiTenantFreeText.checked = Boolean(state.settings.ai_tenant_free_text_enabled);
  if (hermesBaseUrl) hermesBaseUrl.value = state.settings.hermes_api_base_url || "http://127.0.0.1:8642";
  if (hermesDefaultModel) hermesDefaultModel.value = state.settings.hermes_model_default || "deepseek-V4";
  if (hermesAuditModel) hermesAuditModel.value = state.settings.hermes_model_audit || "deepseek-V4";
  if (aiBudget) aiBudget.value = state.settings.ai_monthly_budget_rub || "1000";
  if (notificationsEnabled) notificationsEnabled.checked = Boolean(state.settings.notifications_enabled);
  if (notificationCutoffDate) notificationCutoffDate.value = state.settings.notification_cutoff_date || appToday();
  if (automationRentDue) automationRentDue.value = state.settings.automation_rent_due_cadence || "daily_evening";
  if (automationRentOverdue) automationRentOverdue.value = state.settings.automation_rent_overdue_cadence || "daily_evening";
  if (automationUtility) automationUtility.value = state.settings.automation_utility_cadence || "daily_evening";
  if (token) token.placeholder = state.settings.telegram_bot_token_configured ? "Токен сохранён, пусто = не менять" : "Вставь bot token";
  if (secret) secret.placeholder = state.settings.telegram_webhook_secret_configured ? "Secret сохранён, пусто = не менять" : "Вставь webhook secret";
  if (hermesApiKey) hermesApiKey.placeholder = state.settings.hermes_api_key_configured ? "Hermes key saved, empty = keep" : "Hermes API key";
  if (ownerPin) ownerPin.placeholder = state.settings.panel_owner_pin_code_configured ? "PIN владельца настроен" : "Задайте PIN владельца";
  if (guestPin) guestPin.placeholder = state.settings.panel_guest_pin_code_configured ? "Гостевой PIN настроен" : "Задайте гостевой PIN";
  if (ipRecipientName) ipRecipientName.value = state.settings.ip_recipient_name || "";
  if (ipRecipientAccount) ipRecipientAccount.value = state.settings.ip_recipient_account || "";
  if (ipRecipientBik) ipRecipientBik.value = state.settings.ip_recipient_bik || "";
  if (personalRecipientName) personalRecipientName.value = state.settings.personal_recipient_name || "";
  if (personalRecipientPhone) personalRecipientPhone.value = state.settings.personal_recipient_phone || "";
  if (personalRecipientBank) personalRecipientBank.value = state.settings.personal_recipient_bank || "";
  const templateFields = {
    "#messageRentUpcomingInput": "message_rent_upcoming",
    "#messageRentDueInput": "message_rent_due",
    "#messageRentOverdueInput": "message_rent_overdue",
    "#messageRentStatusRequestInput": "message_rent_status_request",
    "#messageUtilityBillInput": "message_utility_bill",
    "#messageUtilityOverdueInput": "message_utility_overdue",
    "#messageAllDebtsInput": "message_all_debts",
    "#messageReceiptReceivedInput": "message_receipt_received",
    "#messageReceiptReviewInput": "message_receipt_review",
    "#messageReceiptDuplicateInput": "message_receipt_duplicate",
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
      <span class="pill ${state.settings.ai_enabled ? "ok" : "warn"}">Hermes ${state.settings.ai_enabled ? "включён" : "выключен"}</span>
      <span class="pill ${state.settings.hermes_api_key_configured ? "ok" : "warn"}">Hermes key ${state.settings.hermes_api_key_configured ? "сохранён" : "не задан"}</span>
      <span class="pill">AI budget ${state.settings.ai_monthly_budget_rub || "0"} ₽/мес</span>
      <span class="pill ${state.settings.telegram_owner_chat_id ? "ok" : "warn"}">owner chat ${state.settings.telegram_owner_chat_id || "не задан"}</span>
      <span class="pill ${state.settings.notifications_enabled ? "ok" : "warn"}">автонапоминания ${state.settings.notifications_enabled ? "включены" : "выключены"}</span>
      <span class="pill">граница ${formatDate(state.settings.notification_cutoff_date || appToday())}</span>
      <span class="pill ${ipReady ? "ok" : "warn"}">ИП-реквизиты ${ipReady ? "есть" : "неполные"}</span>
      <span class="pill ${personalReady ? "ok" : "warn"}">перевод-реквизиты ${personalReady ? "есть" : "неполные"}</span>
    </div>
    <p class="muted">${state.settings.app_base_url || "Публичный URL не задан. Без него webhook не оживёт, как ни уговаривай."}</p>
  `;
}

function formatPerfMs(value) {
  const number = Number(value || 0);
  return number >= 1000 ? `${(number / 1000).toFixed(1)} c` : `${Math.round(number)} мс`;
}

function formatPerfDate(value) {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function renderPerformanceMonitor() {
  const box = qs("#performanceBox");
  if (!box) return;
  const perf = state.performance;
  if (!perf) {
    box.innerHTML = `<p class="muted">Нажмите «Обновить мониторинг», чтобы получить актуальные показатели. Автоматический опрос отключён для снижения нагрузки.</p>`;
    return;
  }
  const routes = (perf.routes || []).slice(0, 10).map((route) => `
    <tr>
      <td>${escapeHtml(route.route)}</td>
      <td>${route.count || 0}</td>
      <td>${formatPerfMs(route.avg_ms)}</td>
      <td>${formatPerfMs(route.max_ms)}</td>
      <td>${route.slow || 0}</td>
      <td>${route.errors || 0}</td>
    </tr>
  `).join("");
  const slow = (perf.slow_requests || []).slice(0, 8).map((item) => `
    <li><strong>${formatPerfMs(item.duration_ms)}</strong> ${escapeHtml(item.route)} <span class="muted">${formatPerfDate(item.at)}</span></li>
  `).join("");
  const sections = (perf.section_events || []).slice(0, 8).map((item) => {
    const detail = Object.entries(item.detail?.sections || {})
      .map(([name, ms]) => `${escapeHtml(name)} ${formatPerfMs(ms)}`)
      .join(", ");
    return `<li><strong>${formatPerfMs(item.duration_ms)}</strong> ${escapeHtml(item.label)} <span class="muted">${detail}</span></li>`;
  }).join("");
  const background = (perf.background_events || []).slice(0, 8).map((item) => `
    <li><strong>${escapeHtml(item.area)}</strong> ${escapeHtml(item.status)} · ${formatPerfMs(item.duration_ms)} <span class="muted">${formatPerfDate(item.at)}</span></li>
  `).join("");
  const agents = (perf.user_agents || []).slice(0, 6).map((item) => `
    <li><strong>${item.count || 0}</strong> ${escapeHtml(item.user_agent)}</li>
  `).join("");
  const aiRows = (perf.ai_usage || []).slice(0, 10).map((row) => `
    <tr>
      <td>${escapeHtml(row.date)}</td>
      <td>${escapeHtml(row.provider)}</td>
      <td>${escapeHtml(row.model)}</td>
      <td>${row.calls || 0}</td>
      <td>${row.total_tokens || 0}</td>
      <td>${money(row.cost_rub || 0)}</td>
    </tr>
  `).join("");
  const ai = perf.ai_summary || {};
  const counts = perf.data_counts || {};
  box.innerHTML = `
    <div class="performance-head">
      <div>
        <h3>Производительность</h3>
        <p class="muted">Аптайм ${Math.round((perf.uptime_seconds || 0) / 60)} мин · медленным считается ${formatPerfMs(perf.slow_threshold_ms)}</p>
      </div>
      <div class="pill-row">
        <span class="pill ${ai.enabled ? "ok" : "warn"}">AI ${ai.enabled ? "включён" : "выключен"}</span>
        <span class="pill">AI сегодня: ${ai.today_calls || 0} выз.</span>
        <span class="pill">расход ${money(ai.today_cost_rub || 0)}</span>
        <span class="pill">задач агента ${ai.open_agent_tasks || 0}</span>
      </div>
    </div>
    <div class="pill-row">
      <span class="pill">аренда ${counts.rent_charges || 0}</span>
      <span class="pill">строки коммуналки ${counts.utility_bill_lines || 0}</span>
      <span class="pill">чеки ${counts.payment_receipts || 0}</span>
      <span class="pill">логи сообщений ${counts.message_logs || 0}</span>
    </div>
    <div class="performance-grid">
      <div class="muted-box">
        <h4>Endpoint'ы</h4>
        <div class="table-wrap">${table(["Маршрут", "Запросов", "Среднее", "Пик", "Медл.", "Ош."], routes || `<tr><td colspan="6">Пока пусто</td></tr>`)}</div>
      </div>
      <div class="muted-box">
        <h4>Секции загрузки</h4>
        <ul class="compact-list">${sections || "<li>Секций ещё нет</li>"}</ul>
      </div>
      <div class="muted-box">
        <h4>Медленные запросы</h4>
        <ul class="compact-list">${slow || "<li>Медленных запросов нет</li>"}</ul>
      </div>
      <div class="muted-box">
        <h4>Фон и AI</h4>
        <ul class="compact-list">${background || "<li>Фон пока молчит</li>"}</ul>
      </div>
      <div class="muted-box">
        <h4>User-Agent</h4>
        <ul class="compact-list">${agents || "<li>Запросов ещё нет</li>"}</ul>
      </div>
      <div class="muted-box">
        <h4>AI за 14 дней</h4>
        <div class="table-wrap">${table(["Дата", "Провайдер", "Модель", "Выз.", "Токены", "₽"], aiRows || `<tr><td colspan="6">Нет расхода</td></tr>`)}</div>
      </div>
    </div>
  `;
}

async function loadPerformanceMonitor() {
  const button = qs("#performanceRefreshBtn");
  if (button) button.disabled = true;
  try {
    state.performance = await api("/api/performance");
    renderPerformanceMonitor();
    toast("Мониторинг обновлён");
  } finally {
    if (button) button.disabled = false;
  }
}

function applyAppState(payload) {
  if (!payload || typeof payload !== "object") return;
  if ("bootstrap" in payload) state.bootstrap = mergeBootstrap(payload.bootstrap || {});
  else if ("today" in payload || "dashboard" in payload) state.bootstrap = mergeBootstrap(payload);
  if ("registry" in payload) applyRegistryPayload(payload.registry || {});
  if ("rent_charges" in payload) state.rentCharges = payload.rent_charges || [];
  if ("utility_bills" in payload) state.utilityBills = payload.utility_bills || [];
  if ("utility_timeline" in payload) state.utilityTimeline = payload.utility_timeline || [];
  if ("expenses" in payload) state.expenses = payload.expenses || [];
  if ("tariffs" in payload) state.tariffs = payload.tariffs || [];
  if ("message_targets" in payload) state.messageTargets = payload.message_targets || [];
  if ("suspicious_receipts" in payload) state.suspiciousReceipts = payload.suspicious_receipts || [];
}

async function loadAppStateSections(group) {
  setLoadingStep(group.percent, group.title, group.detail);
  const sectionParam = encodeURIComponent(group.sections.join(","));
  const payload = await api(`/api/app-state?sections=${sectionParam}`);
  applyAppState(payload);
  return payload;
}

function applyBootstrapState() {
  if (!state.bootstrap) return;
  state.auth = { authenticated: true, role: state.bootstrap?.auth?.role || authRole() || "owner" };
  applySettings(state.bootstrap?.settings);
  applyAccessUi();
}

function normalizeAppStateSections(sections) {
  const requested = Array.isArray(sections) && sections.length ? sections : appStateSectionOrder;
  const requestedSet = new Set(requested);
  return appStateSectionOrder.filter((section) => requestedSet.has(section));
}

async function loadAppStateSilently(sections = mutationRefreshSections) {
  const selected = normalizeAppStateSections(sections);
  const sectionParam = encodeURIComponent(selected.join(","));
  const payload = await api(`/api/app-state?sections=${sectionParam}`);
  applyAppState(payload);
  applyBootstrapState();
  if (!state.bootstrap) return payload;
  hydrateForms();
  renderAll();
  return payload;
}

async function refreshAfterMutation(sections = mutationRefreshSections) {
  normalizeAppStateSections(sections).forEach((section) => silentRefreshSections.add(section));
  if (silentRefreshPromise) return silentRefreshPromise;
  silentRefreshPromise = (async () => {
    while (silentRefreshSections.size) {
      await new Promise((resolve) => window.setTimeout(resolve, 25));
      const selected = normalizeAppStateSections([...silentRefreshSections]);
      silentRefreshSections.clear();
      await loadAppStateSilently(selected);
    }
  })()
    .catch((error) => {
      toast(`Данные сохранены, но экран не дообновился: ${error.message}`);
    })
    .finally(() => {
      silentRefreshPromise = null;
      if (silentRefreshSections.size) {
        window.setTimeout(() => refreshAfterMutation(), 0);
      }
    });
  return silentRefreshPromise;
}

function renderFirstScreen() {
  hydrateForms();
  renderDashboard();
  renderObjects();
  renderPerformanceMonitor();
  setReportLinks();
}

async function loadPostBootstrapSections() {
  state.loadFailures = [];
  for (const group of appStateLoadGroups.slice(1)) {
    try {
      await loadAppStateSilently(group.sections);
    } catch (error) {
      state.loadFailures.push(`${group.title}: ${error.message}`);
      toast(`Не всё загрузилось: ${state.loadFailures[state.loadFailures.length - 1]}`);
    }
  }
  markFrontendPerf("post_bootstrap_loaded", { failures: state.loadFailures.length });
}

async function loadAll(options = {}) {
  const fullScreen = options.fullScreen ?? !state.bootstrap;
  const refreshSections = options.refreshSections || mutationRefreshSections;
  markFrontendPerf("load_all_start", { fullScreen });
  if (!fullScreen && state.bootstrap) {
    await refreshAfterMutation(refreshSections);
    return;
  }
  state.loadFailures = [];
  setLoadingStep(6, "Подключаюсь к серверу", "Проверяю, отвечает ли пульт, а не просто делает вид.");
  try {
    await loadAppStateSections(appStateLoadGroups[0]);
    markFrontendPerf("bootstrap_loaded");
    applyBootstrapState();
    if (isGuest()) {
      renderGuestView();
      markFrontendPerf("first_screen_rendered", { role: "guest" });
      setLoadingStep(100, "Готово", "Гостевой обзор загружен.");
      window.setTimeout(hideLoadingOverlay, 180);
      return;
    }
    renderFirstScreen();
    markFrontendPerf("first_screen_rendered", { role: authRole() });
    setLoadingStep(100, "Пульт открыт", "Тяжёлые вкладки догружаются в фоне.");
    window.setTimeout(hideLoadingOverlay, 160);
    window.setTimeout(() => loadPostBootstrapSections(), 0);
  } catch (error) {
    hideLoadingOverlay();
    throw error;
  }
}

async function refreshBootstrap() {
  await loadAppStateSilently(["bootstrap", "registry"]);
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

async function loadUtilityTimeline() {
  state.utilityTimeline = await api("/api/utilities/timeline");
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
  const currentLease = editingLease();
  const apartments = allApartments().filter((apartment) => apartment.active || apartment.id === currentLease?.apartment_id);
  const vacantApartments = apartments.filter((apartment) => !apartment.active_lease_id || apartment.id === currentLease?.apartment_id);
  const services = state.bootstrap.services;
  const activeLeases = [...state.bootstrap.leases]
    .filter((lease) => lease.active && !lease.ignored)
    .sort((left, right) => compareApartmentRefs(left, right) || String(left.tenant).localeCompare(String(right.tenant), "ru"));
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
  hydrateUtilityTargetSelect();
  qsa('select[name="lease_id"]').forEach((select) => setOptions(select, activeLeases, (lease) => `${lease.object}: ${lease.apartment} — ${lease.tenant}`));
  const rangeInputIds = new Set(["rentStart", "rentEnd", "reportStart", "reportEnd"]);
  const dateInputs = qsa('input[type="date"]');
  dateInputs.forEach((input) => {
    if (rangeInputIds.has(input.id)) return;
    if (!input.value) input.value = today();
  });
  const paidAtInput = qs("#manualPaymentPaidAtInput");
  if (paidAtInput && !paidAtInput.value) {
    paidAtInput.value = localDateTimeNow();
  }
  const manualTargetMonth = qs("#manualPaymentTargetMonthSelect");
  if (manualTargetMonth && !manualTargetMonth.dataset.ready) {
    manualTargetMonth.innerHTML = `<option value="">Авто</option>${monthOptions(new Date().getMonth() + 1)}`;
    manualTargetMonth.dataset.ready = "true";
  }
  const manualTargetYear = qs("#manualPaymentTargetYearInput");
  if (manualTargetYear && !manualTargetYear.value) {
    manualTargetYear.value = String(currentYear());
  }
  const rangedDefaults = {
    rentStart: daysAgo(30),
    rentEnd: today(),
    reportStart: daysAgo(30),
    reportEnd: today(),
  };
  Object.entries(rangedDefaults).forEach(([id, value]) => {
    const input = qs(`#${id}`);
    if (input && !input.value) input.value = value;
  });
  updateUtilityPeriodControls();
  const cancelBtn = qs("#cancelLeaseEditBtn");
  const submitBtn = qs("#onboardSubmitBtn");
  if (cancelBtn) cancelBtn.hidden = !state.editingLeaseId;
  if (submitBtn) submitBtn.textContent = state.editingLeaseId ? "Сохранить изменения" : "Заселить";
  setReportLinks();
}

function renderAll() {
  if (isGuest()) {
    renderGuestView();
    return;
  }
  renderDashboard();
  renderObjects();
  renderLeases();
  renderApartmentRegistry();
  renderRent();
  renderRentHistory();
  renderMeters();
  renderUtilities();
  renderTariffs();
  renderExpenses();
  renderBotMessenger();
  renderMessages();
  renderMessagePreview();
  renderSuspiciousReceipts();
  renderAutomation();
  renderPerformanceMonitor();
  renderManualAllocationModal();
  renderManualDebtModal();
  setReportLinks();
}

function renderGuestView() {
  renderDashboard();
  renderObjects();
  setReportLinks();
}

function formatDateTime(value) {
  if (!value) return "нет";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
}

function rentChannelSummary(item) {
  const ipDone = item.ip_status === "paid" || item.ip_status === "paid_ahead";
  const personalDone = item.personal_status === "paid" || item.personal_status === "paid_ahead";
  if (ipDone && personalDone) return "всё оплачено";
  if (ipDone && !personalDone) return "ИП оплачен, перевод нет";
  if (!ipDone && personalDone) return "ИП нет, перевод оплачен";
  return "ИП нет, перевода нет";
}

function rentIssueLine(item, issueKind) {
  const month = formatMonth(item.due_date);
  if (issueKind === "rent_today") return `Сегодня срок аренды за ${month}`;
  if (issueKind === "rent_deferred") return `Отсрочка по аренде за ${month} до ${formatDate(item.deferral_until)}`;
  if (issueKind === "rent_partial") return `Частичная оплата аренды за ${month} (${rentChannelSummary(item)})`;
  if (item.total_paid <= 0.009) return `Нет аренды за ${month}`;
  return `Просрочена аренда за ${month} (${rentChannelSummary(item)})`;
}

function utilityIssueLine(item) {
  const prefix = item.paid_amount > 0 ? "Частично оплачен счёт за коммуналку" : "Не оплачен счёт за коммуналку";
  return `${prefix} (${item.period_label || item.bill_period_label})`;
}

function issueSeverity(issueKind) {
  return ["rent_overdue", "utility_overdue"].includes(issueKind) ? 2 : 1;
}

function collectTenantAttentionGroups(dashboard) {
  const groups = new Map();
  const addIssue = (issueKind, item) => {
    if (!item.lease_id) return;
    if (!groups.has(item.lease_id)) {
      groups.set(item.lease_id, {
        leaseId: item.lease_id,
        tenant: item.tenant,
        object: item.object,
        apartment: item.apartment,
        issues: [],
        rentItems: [],
        utilityItems: [],
        maxSeverity: 0,
      });
    }
    const group = groups.get(item.lease_id);
    group.maxSeverity = Math.max(group.maxSeverity, issueSeverity(issueKind));
    if (issueKind.startsWith("rent")) {
      group.rentItems.push(item);
      group.issues.push({ kind: issueKind, text: rentIssueLine(item, issueKind), reminder: item.reminder, date: item.due_date });
    } else {
      group.utilityItems.push(item);
      group.issues.push({ kind: issueKind, text: utilityIssueLine(item), reminder: item.reminder, date: item.bill_period_end || item.due_date });
    }
  };

  dashboard.rent_today.forEach((item) => addIssue("rent_today", item));
  dashboard.rent_overdue.forEach((item) => addIssue("rent_overdue", item));
  dashboard.rent_partial.forEach((item) => addIssue("rent_partial", item));
  dashboard.rent_deferred.forEach((item) => addIssue("rent_deferred", item));
  dashboard.utility_overdue.forEach((item) => addIssue("utility_overdue", item));
  dashboard.utility_partial.forEach((item) => addIssue("utility_partial", item));
  return [...groups.values()].sort((left, right) => right.maxSeverity - left.maxSeverity || `${left.object} ${left.apartment}`.localeCompare(`${right.object} ${right.apartment}`, "ru"));
}

function tenantAttentionBadges(group) {
  const earliest = [...group.issues].sort((left, right) => String(left.date).localeCompare(String(right.date)))[0];
  const rentReminder = group.rentItems[0]?.reminder;
  const utilityReminder = group.utilityItems[0]?.reminder;
  return [
    earliest ? monthMeta(earliest.date) : "",
    rentReminder ? labeledReminderBadge("аренда", rentReminder) : "",
    utilityReminder ? labeledReminderBadge("коммуналка", utilityReminder) : "",
  ].join("");
}

function tenantAttentionActions(group) {
  const rentItem = group.rentItems.find((item) => item.status === "overdue") || group.rentItems[0];
  const utilityItem = group.utilityItems.find((item) => item.status === "overdue") || group.utilityItems[0];
  return `
    <button class="mini primary" onclick="previewTemplateMessage(${group.leaseId}, 'message_all_debts')">Все долги</button>
    ${rentItem ? `<button class="mini" onclick="previewTemplateMessage(${group.leaseId}, 'message_rent_overdue', ${rentItem.id}, null)">Аренда</button>` : ""}
    ${utilityItem ? `<button class="mini" onclick="previewTemplateMessage(${group.leaseId}, 'message_utility_bill', null, ${utilityItem.id})">Коммуналка</button>` : ""}
    ${rentItem ? rentActions(rentItem) : ""}
    ${utilityItem ? utilityActions(utilityItem) : ""}
  `;
}

function tenantAttentionCard(group) {
  const issueList = `<ul class="attention-list">${group.issues.map((issue) => `<li>${issue.text}</li>`).join("")}</ul>`;
  const lead = group.utilityItems.length && group.rentItems.length
    ? "Есть долг по аренде и коммуналке."
    : group.rentItems.length
      ? "Есть долг по аренде."
      : "Есть долг по коммуналке.";
  const type = group.maxSeverity >= 2 ? "danger" : "warn";
  return attentionCard(
    type,
    `${group.object}, ${group.apartment}<br><span class="muted">${group.tenant}</span>`,
    `<p>${lead}</p>${issueList}`,
    tenantAttentionActions(group),
    tenantAttentionBadges(group),
  );
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

  const tenantCards = collectTenantAttentionGroups(dashboard).map(tenantAttentionCard);
  const cards = [...tenantCards];
  dashboard.provider_debts.forEach((item) => cards.push(attentionCard("danger", "Поставщик не отмечен как оплаченный", `${item.object}: ${item.service} за ${formatDateRange(item.period_start, item.period_end)}. Сумма ${money(item.total_cost)}.`, `<button class="mini primary" onclick="providerPaid(${item.id})">Поставщик оплачен</button><button class="mini" onclick="openUtilitiesTab()">Открыть коммуналку</button>`, attentionBadges(item.period_end, null, "деньги у поставщика ещё не закрыты"))));
  dashboard.stale_readings.forEach((item) => cards.push(attentionCard("warn", "Давно нет показаний", `${item.object}: ${item.service}. Последнее: ${item.last_date ? formatDate(item.last_date) : "нет"}.`, `<button class="mini" onclick="openMetersTab()">Открыть счётчики</button><button class="mini" onclick="openUtilitiesTab()">Быстрая передача</button>`, attentionBadges(item.last_date || appToday(), null, item.days ? `${item.days} дн. без обновления` : "пока пусто"))));
  dashboard.suspicious_receipts.forEach((item) => cards.push(attentionCard("danger", "Подозрительный чек", `${money(item.amount)}. ${item.recipient_name || "получатель не распознан"}. ${item.notes || ""}`, `<button class="mini" onclick="openMessagesTab()">Открыть сообщения</button>`, attentionBadges(item.created_at, null, "нужна ручная проверка"))));
  qs("#attentionList").innerHTML = cards.join("") || `<div class="card ok"><h3>Критичных задач нет</h3><p class="muted">Все обязательные действия на текущий момент закрыты.</p></div>`;
}

const dashboardIssueMeta = {
  rent_overdue: { priority: 100, tone: "rent-critical", title: "Просрочена аренда" },
  rent_partial: { priority: 95, tone: "rent-critical", title: "Частичная аренда" },
  rent_today: { priority: 88, tone: "rent-critical", title: "Сегодня срок аренды" },
  rent_deferred: { priority: 70, tone: "rent-critical", title: "Отсрочка по аренде" },
  utility_overdue: { priority: 82, tone: "utility-overdue", title: "Просрочена коммуналка" },
  utility_partial: { priority: 78, tone: "utility-partial", title: "Частичная коммуналка" },
  utility_issued: { priority: 66, tone: "utility-issued", title: "Выставлена коммуналка" },
  manual_debt: { priority: 74, tone: "manual-debt", title: "Ручной долг" },
};

function issuePriority(issueKind, item = null) {
  if (item && isFutureDue(item.due_date)) return 1;
  return dashboardIssueMeta[issueKind]?.priority || 1;
}

function issueTone(issueKind, item = null) {
  if (item && isFutureDue(item.due_date)) return "future";
  return dashboardIssueMeta[issueKind]?.tone || "warn";
}

function issueTitle(issueKind) {
  return dashboardIssueMeta[issueKind]?.title || issueKind;
}

function rentIssueLine(item, issueKind) {
  const month = formatMonth(item.due_date);
  if (issueKind === "rent_today") return `Сегодня срок аренды за ${month}`;
  if (issueKind === "rent_deferred") return `Отсрочка по аренде за ${month} до ${formatDate(item.deferral_until)}`;
  if (issueKind === "rent_partial") return `Частичная оплата аренды за ${month} (${rentChannelSummary(item)})`;
  if (item.total_paid <= 0.009) return `Нет аренды за ${month}`;
  return `Просрочена аренда за ${month} (${rentChannelSummary(item)})`;
}

function utilityIssueLine(item, issueKind = "") {
  if (issueKind === "utility_issued") {
    return `Выставлен счёт за коммуналку (${item.period_label || item.bill_period_label})`;
  }
  const prefix = item.paid_amount > 0 ? "Частично оплачен счёт за коммуналку" : "Просрочен счёт за коммуналку";
  return `${prefix} (${item.period_label || item.bill_period_label})`;
}

function manualDebtIssueLine(item) {
  const period = item.period_label ? ` (${item.period_label})` : "";
  const channel = item.channel_label ? `, ${item.channel_label}` : "";
  return `${item.title || item.kind_label}${period}${channel}: ${money(item.debt)} осталось`;
}

function issueDate(item, issueKind) {
  if (issueKind === "manual_debt") return item.due_date || item.period_end || item.period_start || appToday();
  return issueKind.startsWith("utility") ? (item.bill_period_end || item.due_date || appToday()) : (item.due_date || appToday());
}

function collectTenantAttentionGroups(dashboard) {
  const groups = new Map();
  const ensureGroup = (item) => {
    if (!groups.has(item.lease_id)) {
      groups.set(item.lease_id, {
        leaseId: item.lease_id,
        tenant: item.tenant,
        object: item.object,
        apartment: item.apartment,
        issueMap: new Map(),
        rentMap: new Map(),
        utilityMap: new Map(),
        manualDebtMap: new Map(),
      });
    }
    return groups.get(item.lease_id);
  };
  const addIssue = (issueKind, item) => {
    if (!item?.lease_id) return;
    const group = ensureGroup(item);
    const entryKey = issueKind === "manual_debt" ? `manual_debt:${item.id}` : issueKind.startsWith("utility") ? `utility:${item.id}` : `rent:${item.id}`;
    const existing = group.issueMap.get(entryKey);
    const nextIssue = {
      key: entryKey,
      id: item.id,
      kind: issueKind,
      tone: issueTone(issueKind, item),
      title: issueTitle(issueKind),
      text: issueKind === "manual_debt" ? manualDebtIssueLine(item) : issueKind.startsWith("utility") ? utilityIssueLine(item, issueKind) : rentIssueLine(item, issueKind),
      reminder: item.reminder,
      date: issueDate(item, issueKind),
      item,
    };
    if (!existing || issuePriority(issueKind, item) > issuePriority(existing.kind, existing.item)) {
      group.issueMap.set(entryKey, nextIssue);
    }
    if (issueKind === "manual_debt") group.manualDebtMap.set(item.id, item);
    else if (issueKind.startsWith("utility")) group.utilityMap.set(item.id, item);
    else group.rentMap.set(item.id, item);
  };

  dashboard.rent_overdue.forEach((item) => addIssue("rent_overdue", item));
  dashboard.rent_partial.forEach((item) => addIssue("rent_partial", item));
  dashboard.rent_today.forEach((item) => addIssue("rent_today", item));
  dashboard.rent_deferred.forEach((item) => addIssue("rent_deferred", item));
  dashboard.utility_overdue.forEach((item) => addIssue("utility_overdue", item));
  dashboard.utility_partial.forEach((item) => addIssue("utility_partial", item));
  (dashboard.utility_issued || []).forEach((item) => addIssue("utility_issued", item));
  (dashboard.manual_debts || []).forEach((item) => addIssue("manual_debt", item));

  return [...groups.values()]
    .map((group) => {
      const issues = [...group.issueMap.values()].sort((left, right) =>
        issuePriority(right.kind, right.item) - issuePriority(left.kind, left.item) || String(left.date).localeCompare(String(right.date))
      );
      const rentItems = [...group.rentMap.values()].sort((left, right) => String(left.due_date).localeCompare(String(right.due_date)));
      const utilityItems = [...group.utilityMap.values()].sort((left, right) => String(left.bill_period_end || left.due_date).localeCompare(String(right.bill_period_end || right.due_date)));
      const manualDebtItems = [...group.manualDebtMap.values()].sort((left, right) => String(left.due_date || left.period_end).localeCompare(String(right.due_date || right.period_end)));
      return {
        ...group,
        issues,
        rentItems,
        utilityItems,
        manualDebtItems,
        primaryRent: rentItems[0] || null,
        primaryUtility: utilityItems[0] || null,
        primaryManualDebt: manualDebtItems[0] || null,
        topIssue: issues[0] || null,
      };
    })
    .sort((left, right) =>
      issuePriority(right.topIssue?.kind, right.topIssue?.item) - issuePriority(left.topIssue?.kind, left.topIssue?.item) ||
      `${left.object} ${left.apartment}`.localeCompare(`${right.object} ${right.apartment}`, "ru")
    );
}

function parseDateTimeValue(value) {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function relativeReminderDate(value) {
  const parsed = parseDateTimeValue(value);
  const current = parseLocalDate(appToday());
  if (!parsed || !current) return "давно";
  const localDay = new Date(parsed.getFullYear(), parsed.getMonth(), parsed.getDate());
  const diffDays = Math.round((current - localDay) / 86400000);
  if (diffDays === 0) return "сегодня";
  if (diffDays === 1) return "вчера";
  return formatDate(parsed.toISOString().slice(0, 10));
}

function nextReminderSlotLabel(value) {
  const parsed = parseDateTimeValue(value);
  const current = parseDateTimeValue(`${appToday()}T00:00`);
  if (!parsed || !current) return "по расписанию";
  const slotHour = parsed.getHours();
  const slotLabel = slotHour === 12 ? "в обед" : slotHour >= 19 ? "вечером" : `в ${String(slotHour).padStart(2, "0")}:00`;
  const parsedDay = new Date(parsed.getFullYear(), parsed.getMonth(), parsed.getDate());
  const currentDay = new Date(current.getFullYear(), current.getMonth(), current.getDate());
  const diffDays = Math.round((parsedDay - currentDay) / 86400000);
  if (diffDays === 0) return `сегодня ${slotLabel}`;
  if (diffDays === 1) return `завтра ${slotLabel}`;
  return `${formatDate(parsed.toISOString().slice(0, 10))} ${slotLabel}`;
}

function reminderStatusLine(label, reminder) {
  if (!reminder) return `${label}: напоминаний ещё не было`;
  if (reminder.latest) return `${label}: последнее напоминание ${relativeReminderDate(reminder.latest.created_at)}`;
  if (reminder.block_reason) return `${label}: ${reminder.block_reason}`;
  return `${label}: напоминаний ещё не было`;
}

function nextReminderSummary(group) {
  if (!state.settings.notifications_enabled) return "Автонапоминания выключены";
  const reminders = [
    ...(group.primaryRent ? [group.primaryRent.reminder] : []),
    ...(group.primaryUtility ? [group.primaryUtility.reminder] : []),
    ...(group.primaryManualDebt ? [group.primaryManualDebt.reminder] : []),
  ].filter(Boolean);
  const candidates = reminders
    .filter((item) => item.schedule?.next_auto_at && !item.block_reason)
    .sort((left, right) => String(left.schedule.next_auto_at).localeCompare(String(right.schedule.next_auto_at)));
  if (candidates.length) return `Автонапоминания включены, следующее ${nextReminderSlotLabel(candidates[0].schedule.next_auto_at)}`;
  const blocked = reminders.find((item) => item.block_reason);
  return blocked ? `Автонапоминания ждут: ${blocked.block_reason}` : "Автонапоминания ждут подходящего слота";
}

function manualAllocationOptions(group) {
  const rentOptions = group.rentItems.map((item) => ({
    value: `rent:${item.id}`,
    kind: "rent",
    id: item.id,
    label: formatMonth(item.due_date),
  }));
  const utilityOptions = group.utilityItems.map((item) => ({
    value: `utility:${item.id}`,
    kind: "utility",
    id: item.id,
    label: `ком. услуги ${item.period_label || item.bill_period_label}`,
  }));
  const manualDebtOptions = (group.manualDebtItems || []).map((item) => ({
    value: `manual_debt:${item.id}`,
    kind: "manual_debt",
    id: item.id,
    label: `${item.title || item.kind_label}: ${item.period_label || formatDate(item.due_date || item.period_end || item.period_start)}`,
  }));
  return [...rentOptions, ...utilityOptions, ...manualDebtOptions];
}

function rentChargeManualGroup(charge) {
  return {
    leaseId: charge.lease_id,
    object: charge.object,
    apartment: charge.apartment,
    tenant: charge.tenant,
    rentItems: [charge],
    utilityItems: [],
    topIssue: { kind: "rent", id: charge.id, date: charge.due_date },
  };
}

function manualAllocationGroup() {
  if (!state.manualAllocation) return null;
  if (state.manualAllocation.group) return state.manualAllocation.group;
  return state.dashboardAttentionGroups.find((item) => item.leaseId === state.manualAllocation.leaseId) || null;
}

function openManualAllocation(leaseId) {
  const group = state.dashboardAttentionGroups.find((item) => item.leaseId === leaseId);
  if (!group) return;
  const options = manualAllocationOptions(group);
  const preferredTarget = group.topIssue
    ? `${group.topIssue.kind.startsWith("utility") ? "utility" : "rent"}:${group.topIssue.id}`
    : options[0]?.value || "";
  state.manualAllocation = {
    leaseId,
    target: options.some((item) => item.value === preferredTarget) ? preferredTarget : options[0]?.value || "",
    paidAt: localDateTimeNow(),
  };
  renderManualAllocationModal();
}

function openManualAllocationForRentCharge(chargeId) {
  const charge = state.rentCharges.find((item) => item.id === chargeId);
  if (!charge) return;
  const group = rentChargeManualGroup(charge);
  const options = manualAllocationOptions(group);
  state.manualAllocation = {
    leaseId: charge.lease_id,
    group,
    target: `rent:${charge.id}`,
    paidAt: localDateTimeNow(),
  };
  if (!options.length) return;
  renderManualAllocationModal();
}

function closeManualAllocation() {
  state.manualAllocation = null;
  renderManualAllocationModal();
}

function selectedManualAllocationTarget(group) {
  const targetValue = state.manualAllocation?.target || "";
  return manualAllocationOptions(group).find((item) => item.value === targetValue) || manualAllocationOptions(group)[0] || null;
}

function updateManualAllocationTarget(value) {
  if (!state.manualAllocation) return;
  state.manualAllocation.target = value;
  renderManualAllocationModal();
}

function renderManualAllocationModal() {
  const root = qs("#manualAllocationModal");
  if (!root) return;
  if (!state.manualAllocation) {
    restoreModalFocus(root);
    root.hidden = true;
    root.innerHTML = "";
    return;
  }
  const group = manualAllocationGroup();
  if (!group) {
    closeManualAllocation();
    return;
  }
  const target = selectedManualAllocationTarget(group);
  const options = manualAllocationOptions(group).map((item) =>
    `<option value="${item.value}" ${item.value === target?.value ? "selected" : ""}>${escapeHtml(item.label)}</option>`
  ).join("");
  root.hidden = false;
  root.innerHTML = `
    <div class="modal-card">
      <div class="section-title">
        <div>
          <h3>Зачесть вручную</h3>
          <span>${escapeHtml(group.object)}, ${escapeHtml(group.apartment)}, ${escapeHtml(group.tenant)}</span>
        </div>
        <button class="mini" type="button" onclick="closeManualAllocation()">Закрыть</button>
      </div>
      <form id="manualAllocationForm" class="form-grid compact" onsubmit="submitManualAllocation(event)">
        <label>За что зачесть
          <select id="manualAllocationTargetSelect" onchange="updateManualAllocationTarget(this.value)" required>${options}</select>
        </label>
        <label ${target?.kind !== "rent" ? "hidden" : ""}>Канал аренды
          <select name="channel" id="manualAllocationChannelSelect">
            <option value="personal">По номеру</option>
            <option value="ip">ИП</option>
            <option value="expense_fund">Мне на расходы</option>
          </select>
        </label>
        <label>Источник
          <select name="source">
            <option value="cash">Наличные</option>
            <option value="owner_card">С моей карты</option>
            <option value="manual">Ручная отметка</option>
          </select>
        </label>
        <label>Дата и время
          <input type="datetime-local" name="paid_at" value="${escapeAttr(state.manualAllocation.paidAt || localDateTimeNow())}" required />
        </label>
        <label>Сумма
          <input type="number" min="0" step="0.01" name="amount" required />
        </label>
        <label class="wide">Комментарий
          <textarea name="notes" rows="2" placeholder="Например: наличка, перевод с моей карты, быстрое закрытие из дашборда"></textarea>
        </label>
        <div class="attention-card__footer-primary wide">
          <button class="primary" type="submit">Зачесть платёж</button>
          <button type="button" onclick="closeManualAllocation()">Отмена</button>
        </div>
      </form>
    </div>
  `;
  openAccessibleModal(root, closeManualAllocation);
}

async function submitManualAllocation(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const group = manualAllocationGroup();
  const target = group ? selectedManualAllocationTarget(group) : null;
  if (!group || !target) return;
  const payload = formData(form);
  payload.lease_id = group.leaseId;
  payload.amount = Number(payload.amount);
  payload.paid_at = payload.paid_at || localDateTimeNow();
  if (target.kind === "rent") {
    payload.kind = "rent";
    payload.rent_charge_id = target.id;
  } else if (target.kind === "utility") {
    payload.kind = "utility";
    payload.utility_line_id = target.id;
    delete payload.channel;
  } else if (target.kind === "manual_debt") {
    await api(`/api/manual-debts/${target.id}/payments`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    toast("Ручной долг закрыт");
    closeManualAllocation();
    await loadAll();
    return;
  }
  await api("/api/payment-receipts/manual", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  toast("Ручной платёж сохранён");
  closeManualAllocation();
  await loadAll();
}

async function openManualDebt(leaseId) {
  const lease = state.bootstrap.leases.find((item) => item.id === leaseId);
  if (!lease) return;
  state.manualDebt = {
    leaseId,
    debts: [],
    editingDebtId: null,
    kind: "rent",
    targetMonth: new Date().getMonth() + 1,
    targetYear: currentYear(),
  };
  renderManualDebtModal();
  try {
    state.manualDebt.debts = await api(`/api/leases/${leaseId}/manual-debts`);
  } catch (error) {
    toast(error.message);
  }
  renderManualDebtModal();
}

function closeManualDebt() {
  state.manualDebt = null;
  renderManualDebtModal();
}

function updateManualDebtKind(value) {
  if (!state.manualDebt) return;
  state.manualDebt.kind = value;
  renderManualDebtModal();
}

function editManualDebt(debtId) {
  if (!state.manualDebt) return;
  const debt = (state.manualDebt.debts || []).find((item) => item.id === debtId);
  if (!debt) return;
  state.manualDebt.editingDebtId = debtId;
  state.manualDebt.kind = debt.kind || "other";
  renderManualDebtModal();
}

function resetManualDebtForm() {
  if (!state.manualDebt) return;
  state.manualDebt.editingDebtId = null;
  state.manualDebt.kind = "rent";
  renderManualDebtModal();
}

async function deleteManualDebt(debtId) {
  if (!state.manualDebt || !confirm("Удалить этот ручной долг?")) return;
  const leaseId = state.manualDebt.leaseId;
  await api(`/api/manual-debts/${debtId}`, { method: "DELETE" });
  toast("Долг удалён");
  await loadAll();
  await openManualDebt(leaseId);
}

function renderManualDebtModal() {
  const root = qs("#manualDebtModal");
  if (!root) return;
  if (!state.manualDebt) {
    restoreModalFocus(root);
    root.hidden = true;
    root.innerHTML = "";
    return;
  }
  const lease = state.bootstrap.leases.find((item) => item.id === state.manualDebt.leaseId);
  if (!lease) {
    closeManualDebt();
    return;
  }
  const kind = state.manualDebt.kind || "rent";
  const editing = (state.manualDebt.debts || []).find((item) => item.id === state.manualDebt.editingDebtId) || null;
  const selectedMonth = editing?.period_start ? Number(editing.period_start.slice(5, 7)) : new Date().getMonth() + 1;
  const selectedYear = editing?.period_start ? Number(editing.period_start.slice(0, 4)) : currentYear();
  const selectedChannel = editing?.channel || "ip";
  const debtRows = (state.manualDebt.debts || []).map((debt) => `
    <tr class="${editing?.id === debt.id ? "row-selected" : ""}">
      <td>${escapeHtml(debt.kind_label)}</td>
      <td>${escapeHtml(debt.channel_label || "")}</td>
      <td>${escapeHtml(debt.title || "")}<br><span class="muted">${escapeHtml(debt.period_label || formatDate(debt.due_date))}</span></td>
      <td>${money(debt.amount)}<br><span class="muted">оплачено ${money(debt.paid_amount)}</span></td>
      <td>${money(debt.debt)}</td>
      <td>${statusPill(debt.status, debt.due_date)}</td>
      <td class="actions">
        <button class="mini" type="button" onclick="editManualDebt(${debt.id})">Ред.</button>
        <button class="mini danger-soft" type="button" onclick="deleteManualDebt(${debt.id})">Удалить</button>
      </td>
    </tr>
  `).join("");
  root.hidden = false;
  root.innerHTML = `
    <div class="modal-card">
      <div class="section-title">
        <div>
          <h3>Другие долги</h3>
          <span>${escapeHtml(lease.object)}, ${escapeHtml(lease.apartment)}, ${escapeHtml(lease.tenant)}</span>
        </div>
        <button class="mini" type="button" onclick="closeManualDebt()">Закрыть</button>
      </div>
      <div class="table-wrap">
        ${debtRows ? table(["Тип", "Канал", "Описание", "Сумма", "Остаток", "Статус", "Действия"], debtRows) : '<p class="muted">Ручных долгов пока нет.</p>'}
      </div>
      <form id="manualDebtForm" class="form-grid compact" onsubmit="submitManualDebt(event)">
        <label>Назначение
          <select name="kind" onchange="updateManualDebtKind(this.value)">
            <option value="rent" ${kind === "rent" ? "selected" : ""}>Аренда</option>
            <option value="utility" ${kind === "utility" ? "selected" : ""}>Коммуналка</option>
            <option value="other" ${kind === "other" ? "selected" : ""}>Другое</option>
          </select>
        </label>
        <label ${kind === "rent" ? "" : "hidden"}>Канал
          <select name="channel">
            <option value="ip" ${selectedChannel === "ip" ? "selected" : ""}>ИП</option>
            <option value="personal" ${selectedChannel === "personal" ? "selected" : ""}>По номеру</option>
            <option value="expense_fund" ${selectedChannel === "expense_fund" ? "selected" : ""}>Мне на расходы</option>
          </select>
        </label>
        <label ${kind === "rent" ? "" : "hidden"}>Месяц
          <select name="target_month">${monthOptions(selectedMonth)}</select>
        </label>
        <label ${kind === "rent" ? "" : "hidden"}>Год
          <input type="number" min="2025" step="1" name="target_year" value="${selectedYear}" />
        </label>
        <label ${kind !== "rent" ? "" : "hidden"}>Начало периода
          <input type="date" name="period_start" value="${escapeAttr(editing?.period_start || "")}" />
        </label>
        <label ${kind !== "rent" ? "" : "hidden"}>Конец периода
          <input type="date" name="period_end" value="${escapeAttr(editing?.period_end || "")}" />
        </label>
        <label>Название
          <input name="title" value="${escapeAttr(editing?.title || "")}" placeholder="${kind === "utility" ? "Коммуналка" : kind === "rent" ? "Старый долг по аренде" : "Парковка, ремонт, прочее"}" />
        </label>
        <label>Срок
          <input type="date" name="due_date" value="${escapeAttr(editing?.due_date || today())}" />
        </label>
        <label>Сумма
          <input type="number" min="0" step="0.01" name="amount" value="${editing ? editing.amount : ""}" required />
        </label>
        <label>Уже оплачено
          <input type="number" min="0" step="0.01" name="paid_amount" value="${editing ? editing.paid_amount : 0}" />
        </label>
        <label class="wide">Комментарий
          <textarea name="notes" rows="2">${escapeHtml(editing?.notes || "")}</textarea>
        </label>
        <div class="attention-card__footer-primary wide">
          <button class="primary" type="submit">${editing ? "Сохранить долг" : "Добавить долг"}</button>
          ${editing ? '<button type="button" onclick="resetManualDebtForm()">Новый долг</button>' : ""}
          <button type="button" onclick="closeManualDebt()">Отмена</button>
        </div>
      </form>
    </div>
  `;
  openAccessibleModal(root, closeManualDebt);
}

async function submitManualDebt(event) {
  event.preventDefault();
  if (!state.manualDebt) return;
  const payload = formData(event.currentTarget);
  payload.lease_id = state.manualDebt.leaseId;
  payload.amount = Number(payload.amount);
  payload.paid_amount = Number(payload.paid_amount || 0);
  const editingDebtId = state.manualDebt.editingDebtId;
  await api(editingDebtId ? `/api/manual-debts/${editingDebtId}` : "/api/manual-debts", {
    method: editingDebtId ? "PATCH" : "POST",
    body: JSON.stringify(payload),
  });
  toast(editingDebtId ? "Долг обновлён" : "Долг добавлен");
  const leaseId = state.manualDebt.leaseId;
  await loadAll();
  await openManualDebt(leaseId);
}

function previewGroupReminder(leaseId) {
  const group = state.dashboardAttentionGroups.find((item) => item.leaseId === leaseId);
  if (!group) return;
  if (group.primaryRent && group.primaryUtility) {
    previewTemplateMessage(leaseId, "message_all_debts");
    return;
  }
  if (group.primaryManualDebt) {
    previewTemplateMessage(leaseId, "message_all_debts");
    return;
  }
  if (group.primaryUtility) {
    previewTemplateMessage(leaseId, "message_utility_bill", null, group.primaryUtility.id);
    return;
  }
  if (group.primaryRent) {
    const template = group.topIssue?.kind === "rent_today" ? "message_rent_due" : "message_rent_overdue";
    previewTemplateMessage(leaseId, template, group.primaryRent.id, null);
  }
}

function attentionLeadText(group) {
  if (group.primaryRent && group.primaryUtility) return "Есть долги по аренде и коммуналке.";
  if (group.primaryManualDebt) return "Есть ручной долг или стороннее начисление.";
  if (group.primaryRent) return "Есть вопрос по аренде.";
  if (group.primaryUtility) return "Есть вопрос по коммуналке.";
  return "Нужно посмотреть внимательнее.";
}

function tenantAttentionCard(group) {
  const issueList = group.issues.map((issue) => `
    <article class="attention-issue attention-issue--${issue.kind.replaceAll("_", "-")}">
      <strong>${issue.title}</strong>
      <div>${issue.text}</div>
    </article>
  `).join("");
  const reminderBlock = `
    <div class="attention-card__panel">
      <strong>Напоминания</strong>
      <ul>
        <li>${reminderStatusLine("Коммуналка", group.primaryUtility?.reminder)}</li>
        <li>${reminderStatusLine("Аренда", group.primaryRent?.reminder)}</li>
        <li>${nextReminderSummary(group)}</li>
      </ul>
      <div class="attention-card__footer-primary">
        <button class="mini primary" onclick="previewGroupReminder(${group.leaseId})">Напомнить</button>
      </div>
    </div>
  `;
  return attentionCard(
    group.topIssue?.tone || issueTone(group.topIssue?.kind, group.topIssue?.item),
    `${group.object}, ${group.apartment}<br><span class="muted">${group.tenant}</span>`,
    `<p class="attention-lead">${attentionLeadText(group)}</p><div class="attention-issue-list">${issueList}</div>${reminderBlock}`,
    `<div class="attention-card__footer-actions"><button class="mini primary" onclick="openManualAllocation(${group.leaseId})">Зачесть вручную</button><button class="mini" onclick="openPaymentHistory(${group.leaseId})">История</button>${group.primaryRent ? `<button class="mini" onclick="deferRent(${group.primaryRent.id})">Отсрочка</button>` : `<button class="mini" type="button" disabled>Отсрочка</button>`}</div>`,
    `<span class="pill">${group.issues.length} проблем</span>${group.topIssue ? monthMeta(group.topIssue.date) : ""}`,
  );
}

function providerDebtCard(item) {
  const future = isFutureDue(item.due_date);
  return attentionCard(
    future ? "future" : "provider",
    "Не оплачены услуги поставщика",
    `<p class="attention-lead">${item.object}: ${item.service}</p><div class="attention-issue-list"><article class="attention-issue"><strong>Период</strong><div>${formatDateRange(item.period_start, item.period_end)}. Сумма ${money(item.total_cost)}.</div></article></div>`,
    `<div class="attention-card__footer-actions"><button class="mini primary" onclick="providerPaid(${item.id})">Поставщик оплачен</button><button class="mini" onclick="openUtilitiesTab()">Коммуналка</button></div>`,
    `<span class="pill ${future ? "future" : ""}">${future ? "предстоит" : formatDate(item.period_end)}</span>`,
  );
}

function staleReadingCard(item) {
  return attentionCard(
    "reading",
    "Нет свежих показаний",
    `<p class="attention-lead">${item.object}: ${item.service}</p><div class="attention-issue-list"><article class="attention-issue"><strong>Последнее показание</strong><div>${item.last_date ? formatDate(item.last_date) : "нет"}</div></article></div>`,
    `<div class="attention-card__footer-actions"><button class="mini" onclick="openMetersTab()">Счётчики</button><button class="mini" onclick="openUtilitiesTab()">Быстрая передача</button></div>`,
    `<span class="pill">${item.days ? `${item.days} дн. без обновления` : "пока пусто"}</span>`,
  );
}

function suspiciousReceiptCard(item) {
  return attentionCard(
    "receipt",
    "Подозрительный чек",
    `<p class="attention-lead">${money(item.amount)}</p><div class="attention-issue-list"><article class="attention-issue"><strong>Проверить</strong><div>${escapeHtml(item.recipient_name || "получатель не распознан")}. ${escapeHtml(item.notes || "")}</div></article></div>`,
    `<div class="attention-card__footer-actions"><button class="mini" onclick="openMessagesTab()">Открыть сообщения</button><button class="mini danger-soft" title="Скрыть чек" onclick="ignoreSuspiciousReceipt(${item.id})">×</button></div>`,
    `<span class="pill">нужна ручная проверка</span>`,
  );
}

function expenseFundCard(fund) {
  return attentionCard(
    "expense-fund",
    "Расчёт расходов не сходится",
    `<p class="attention-lead">Получено ${money(fund.received)}, расходов к покрытию ${money(fund.spent)}.</p><div class="attention-issue-list"><article class="attention-issue"><strong>Остаток</strong><div>${money(fund.balance)} учтено как неиспользованное пополнение.</div></article></div>`,
    `<div class="attention-card__footer-actions"><button class="mini" onclick="openExpensesTab()">Расходы</button></div>`,
    `<span class="pill">контроль денег на расходы</span>`,
  );
}

function dashboardDebtTotal(dashboard) {
  const rentDebt = [
    ...(dashboard.rent_overdue || []),
    ...(dashboard.rent_partial || []),
    ...(dashboard.rent_today || []),
    ...(dashboard.rent_deferred || []),
  ].reduce((total, item) => total
    + Math.max(0, Number(item.ip_due || 0) - Number(item.ip_paid || 0))
    + Math.max(0, Number(item.personal_due || 0) - Number(item.personal_paid || 0)), 0);
  const utilityDebt = (dashboard.utility_issued || []).reduce(
    (total, item) => total + Math.max(0, Number(item.total_amount || 0) - Number(item.paid_amount || 0)),
    0,
  );
  const manualDebt = (dashboard.manual_debts || []).reduce(
    (total, item) => total + Number(item.outstanding_amount ?? item.amount ?? 0),
    0,
  );
  return rentDebt + utilityDebt + manualDebt;
}

function metricCard(label, value, meta, tone = "") {
  return `
    <article class="metric ${tone ? `metric-${tone}` : ""}">
      <span>${escapeHtml(label)}</span>
      <strong>${value}</strong>
      <small class="metric-meta">${escapeHtml(meta)}</small>
    </article>
  `;
}

function renderIncomeTrend(dashboard) {
  const chart = qs("#incomeTrendChart");
  const totalNode = qs("#incomeTrendTotal");
  if (!chart) return;
  const trend = dashboard.income_trend || [];
  const total = trend.reduce((sum, item) => sum + Number(item.amount || 0), 0);
  if (totalNode) totalNode.textContent = total ? `Всего ${money(total)}` : "Нет поступлений";
  if (!trend.length || !total) {
    chart.innerHTML = `<div class="empty-state"><strong>Поступления пока не зафиксированы</strong><span>График появится после принятых оплат.</span></div>`;
    return;
  }
  const peak = Math.max(...trend.map((item) => Number(item.amount || 0)), 1);
  chart.innerHTML = `<div class="bar-chart">${trend.map((item) => {
    const height = Math.max(3, Math.round((Number(item.amount || 0) / peak) * 100));
    const monthLabel = formatMonth(`${item.period}-01`);
    return `
      <div class="bar-chart__item" title="${escapeAttr(monthLabel)}: ${escapeAttr(money(item.amount))}">
        <div class="bar-chart__bar" style="height:${height}%"></div>
        <small>${escapeHtml(monthLabel.slice(0, 3))}</small>
      </div>
    `;
  }).join("")}</div>`;
}

function renderMonthSummary(dashboard) {
  const node = qs("#monthSummaryCard");
  if (!node) return;
  const month = dashboard.month_summary || {};
  const received = Number(month.salary_paid || 0) + Number(month.bill_payment_paid || 0) + Number(month.advance_paid || 0);
  const expected = Number(month.salary_due || 0) + Number(month.bill_payment_due || 0) + Number(month.advance_due || 0);
  const progress = expected > 0 ? Math.min(100, Math.round((received / expected) * 100)) : 0;
  const occupied = Number(month.occupied || dashboard.object_summary?.occupied || 0);
  const total = Number(month.total_apartments || dashboard.object_summary?.total || 0);
  node.innerHTML = `
    <div class="section-title"><div><p class="eyebrow">Текущий месяц</p><h2>Исполнение плана</h2></div><span>${progress}%</span></div>
    <div class="month-summary">
      <div class="month-summary__hero">
        <span class="muted">Получено</span>
        <strong>${money(received)}</strong>
        <div class="progress-track"><span style="width:${progress}%"></span></div>
      </div>
      <div class="month-summary__rows">
        <div class="month-summary__row"><span>Ожидается</span><strong>${money(expected)}</strong></div>
        <div class="month-summary__row"><span>Аренда</span><strong>${money(month.salary_paid || 0)}</strong></div>
        <div class="month-summary__row"><span>Коммунальные</span><strong>${money(month.bill_payment_paid || 0)}</strong></div>
        <div class="month-summary__row"><span>Заполняемость</span><strong>${occupied}/${total}</strong></div>
      </div>
    </div>
  `;
}

function renderDashboard() {
  const dashboard = state.bootstrap.dashboard;
  const month = dashboard.month_summary || {};
  const received = Number(month.salary_paid || 0) + Number(month.bill_payment_paid || 0) + Number(month.advance_paid || 0);
  const expected = Number(month.salary_due || 0) + Number(month.bill_payment_due || 0) + Number(month.advance_due || 0);
  const debt = dashboardDebtTotal(dashboard);
  const occupied = Number(month.occupied || dashboard.object_summary?.occupied || 0);
  const totalApartments = Number(month.total_apartments || dashboard.object_summary?.total || 0);
  const occupancy = totalApartments ? Math.round((occupied / totalApartments) * 100) : 0;
  qs("#summaryGrid").innerHTML = [
    metricCard("Поступило", money(received), "Принятые оплаты за месяц", "success"),
    metricCard("Ожидается", money(Math.max(0, expected - received)), "Остаток плановых поступлений", "accent"),
    metricCard("Задолженность", money(debt), debt ? "Требует контроля" : "Просрочек нет", debt ? "danger" : "success"),
    metricCard("Заполняемость", `${occupancy}%`, `${occupied} из ${totalApartments} квартир`, occupancy < 90 ? "accent" : "success"),
  ].join("");
  renderIncomeTrend(dashboard);
  renderMonthSummary(dashboard);
  if (isGuest()) {
    const summary = dashboard.summary_counts || {};
    renderMonthlyReportTray(dashboard.monthly_reports || []);
    const cards = [
      ["Просрочка аренды", Number(summary.rent_overdue || 0)],
      ["Частичная аренда", Number(summary.rent_partial || 0)],
      ["Коммуналка просрочена", Number(summary.utility_overdue || 0)],
      ["Долги поставщикам", Number(summary.provider_debts || 0)],
      ["Показания устарели", Number(summary.stale_readings || 0)],
    ]
      .filter(([, value]) => value > 0)
      .slice(0, 6)
      .map(([label, value]) => attentionCard("warn", label, `<p class="attention-lead">Открыто: ${value}</p><p class="muted">Подробности доступны управляющему.</p>`, "", `<span class="pill">${label}</span>`));
    qs("#attentionList").innerHTML = cards.join("") || `<div class="empty-state"><strong>Портфель работает штатно</strong><span>Критичных отклонений не зафиксировано.</span></div>`;
    return;
  }
  renderMonthlyReportTray(dashboard.monthly_reports);

  state.dashboardAttentionGroups = collectTenantAttentionGroups(dashboard);
  const cards = [
    ...state.dashboardAttentionGroups.map(tenantAttentionCard),
    ...(dashboard.expense_fund?.has_mismatch ? [expenseFundCard(dashboard.expense_fund)] : []),
    ...dashboard.provider_debts.map(providerDebtCard),
    ...dashboard.stale_readings.map(staleReadingCard),
    ...dashboard.suspicious_receipts.map(suspiciousReceiptCard),
  ];
  qs("#attentionList").innerHTML = cards.join("") || `<div class="card ok"><h3>Критичных задач нет</h3><p class="muted">Все обязательные действия на текущий момент закрыты.</p></div>`;
}
function renderMonthlyReportTray(reports = []) {
  const tray = qs("#monthlyReportTray");
  if (!reports.length) {
    tray.innerHTML = `<div class="report-status report-ok"><strong>Месячные отчёты закрыты</strong><span>Открытых проблем по отчётам нет.</span></div>`;
    return;
  }
  tray.innerHTML = reports.map((report) => `
    <article class="report-status report-${report.severity}">
      <div class="report-status__head">
        <button class="report-open" type="button" onclick="openMonthlyReport(${report.year}, ${report.month}, '${report.kind || "full"}')"><strong>${report.title}</strong></button>
        ${isOwner() ? `<button class="mini report-accept" title="Отчёт принят" onclick="acceptMonthlyReport(event, ${report.year}, ${report.month}, '${report.kind || "full"}')">✓</button>` : ""}
      </div>
      <button class="report-summary" type="button" onclick="openMonthlyReport(${report.year}, ${report.month}, '${report.kind || "full"}')">${monthlySeverityText(report)} · ${report.issue_count} проблем</button>
    </article>
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

function labeledReminderBadge(label, reminder) {
  if (!reminder) return `<span class="pill">${label}: без напоминаний</span>`;
  if (reminder.latest) return `<span class="pill ok">${label}: ${reminder.latest.label}</span>`;
  if (reminder.block_reason) return `<span class="pill warn">${label}: ${reminder.block_reason}</span>`;
  return `<span class="pill">${label}: без напоминаний</span>`;
}

function rentAttentionActions(charge, overdueMode = false) {
  const template = overdueMode ? "message_rent_overdue" : "message_rent_due";
  return `
    <button class="mini" onclick="previewTemplateMessage(${charge.lease_id}, '${template}', ${charge.id}, null)">Напомнить</button>
    ${rentActions(charge)}
  `;
}

function utilityAttentionActions(line) {
  return `
    <button class="mini" onclick="previewTemplateMessage(${line.lease_id}, 'message_utility_bill', null, ${line.id})">Напомнить</button>
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
        <div class="attention-card__copy">${text}</div>
      </div>
      <div class="attention-actions">${actions || ""}</div>
    </article>
  `;
}

function renderObjects() {
  const summary = state.bootstrap.dashboard.object_summary;
  const objects = summary.by_object.map((item) => {
    const percent = item.total ? Math.round((item.occupied / item.total) * 100) : 0;
    return `
      <div class="portfolio-row">
        <div><strong>${escapeHtml(item.object)}</strong><span>${item.occupied} из ${item.total} занято</span></div>
        <div class="occupancy-value"><strong>${percent}%</strong><div class="progress-track"><span style="width:${percent}%"></span></div></div>
      </div>
    `;
  }).join("");
  qs("#objectCards").innerHTML = `
    <article class="card">
      ${objects || '<div class="empty-state"><strong>Объектов пока нет</strong><span>Добавьте объект и квартиры в разделе портфеля.</span></div>'}
    </article>
  `;
}
function renderLeases() {
  const rows = [...state.bootstrap.leases]
    .sort((left, right) => Number(right.active) - Number(left.active) || compareApartmentRefs(left, right) || String(left.tenant).localeCompare(String(right.tenant), "ru"))
    .map((lease) => `
    <tr>
      <td>${lease.object}</td>
      <td>${lease.apartment}</td>
      <td>${lease.tenant}<br><span class="muted">${lease.phone || ""}</span></td>
      <td>${formatDate(lease.start_date)}${lease.end_date ? `<br><span class="muted">выезд ${formatDate(lease.end_date)}</span>` : ""}</td>
      <td>${lease.payment_day}</td>
      <td>${money(lease.ip_amount)} / ${money(lease.personal_amount)}</td>
      <td>${lease.deposit_amount ? `${money(lease.deposit_amount)}<br><span class="muted">${lease.deposit_location || ""}</span>` : "нет"}</td>
      <td>${statusPill(lease.active ? "issued" : "paid")}${lease.ignored ? '<br><span class="pill warn">только информация</span>' : ""}${lease.apartment_active ? "" : '<br><span class="pill warn">квартира выключена</span>'}</td>
      <td class="actions">
        ${contactButtons(lease)}
        <button class="mini" onclick="startLeaseEdit(${lease.id})">Изменить</button>
        <button class="mini" onclick="openManualDebt(${lease.id})">Долги</button>
        <details class="action-menu">
          <summary>Ещё</summary>
          <div>
            <label class="checkbox-inline"><input type="checkbox" ${lease.ignored ? "checked" : ""} onchange="toggleLeaseIgnored(${lease.id}, this.checked)" /> Только информация</label>
            ${lease.active ? `<button class="mini" onclick="transferLease(${lease.id})">Оформить переезд</button>` : ""}
            ${lease.active ? `<button class="mini danger-soft" onclick="moveOut(${lease.id})">Оформить выезд</button>` : ""}
            <button class="mini danger-soft" onclick="deleteLease(${lease.id})">Удалить договор</button>
          </div>
        </details>
      </td>
    </tr>
  `).join("");
  qs("#leaseList").innerHTML = table(["Объект", "Квартира", "Жилец", "Период", "День", "ИП / личный", "Залог", "Статус", "Действия"], rows);
}

function renderApartmentRegistry() {
  const rows = allApartments()
    .sort(compareApartmentRefs)
    .map((apartment) => `
      <tr>
        <td>${apartment.object_name}</td>
        <td>${apartment.name}</td>
        <td>${apartment.active_tenant || '<span class="muted">нет жильца</span>'}</td>
        <td>${apartment.odn_share_percent}</td>
        <td>${apartment.utility_advance_override == null ? '<span class="muted">авто</span>' : money(apartment.utility_advance_override)}</td>
        <td><label class="checkbox-inline"><input type="checkbox" ${apartment.active ? "checked" : ""} onchange="toggleApartmentActive(${apartment.id}, this.checked)" /> учитывать</label></td>
        <td><button class="mini" onclick="editUtilityAdvanceOverride(${apartment.id})">...</button></td>
      </tr>
    `).join("");
  qs("#apartmentRegistry").innerHTML = table(["Объект", "Квартира", "Текущий жилец", "ОДН %", "Аванс", "Контроль", ""], rows);
}

async function editUtilityAdvanceOverride(apartmentId) {
  const apartment = allApartments().find((item) => Number(item.id) === Number(apartmentId));
  if (!apartment) return;
  const current = apartment.utility_advance_override == null ? "" : String(apartment.utility_advance_override);
  const value = prompt("Фиксированный аванс коммуналки. Пусто = авторасчёт", current);
  if (value === null) return;
  const payload = { amount_override: value.trim() };
  await api(`/api/apartments/${apartmentId}/utility-advance`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  toast(value.trim() ? "Аванс зафиксирован вручную" : "Аванс вернулся в авторасчёт");
  await loadAll({ refreshSections: registryRefreshSections });
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
  form.elements.end_date.value = lease.end_date || "";
  form.elements.payment_day.value = lease.payment_day || "";
  form.elements.ip_amount.value = lease.ip_amount || "";
  form.elements.personal_amount.value = lease.personal_amount || "";
  form.elements.deposit_amount.value = lease.deposit_amount || "";
  form.elements.deposit_location.value = lease.deposit_location || "";
  form.elements.deposit_terms.value = lease.deposit_terms || "";
  form.elements.notes.value = lease.notes || "";
  form.elements.ignored.checked = Boolean(lease.ignored);
  const tool = qs("#onboardTool");
  if (tool) tool.open = true;
  const title = qs("#onboardFormTitle");
  if (title) title.textContent = "Редактирование договора";
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

function cancelLeaseEdit() {
  state.editingLeaseId = null;
  const form = qs("#onboardForm");
  if (form) form.reset();
  const title = qs("#onboardFormTitle");
  if (title) title.textContent = "Заселение";
  hydrateForms();
}

async function toggleApartmentActive(apartmentId, active) {
  try {
    await api(`/api/apartments/${apartmentId}`, {
      method: "PATCH",
      body: JSON.stringify({ active }),
    });
    await refreshBootstrap();
    toast(active ? "Квартира снова участвует в контроле" : "Квартира выключена из контроля");
  } catch (error) {
    await refreshBootstrap();
    toast(error.message);
  }
}

async function toggleLeaseIgnored(leaseId, ignored) {
  try {
    await api(`/api/leases/${leaseId}/ignore`, {
      method: "PATCH",
      body: JSON.stringify({ ignored }),
    });
    await refreshBootstrap();
    toast(ignored ? "Договор выключен из контроля" : "Договор снова участвует в контроле");
  } catch (error) {
    await refreshBootstrap();
    toast(error.message);
  }
}

async function deleteLease(leaseId) {
  if (!confirm("Удалить запись о жильце и связанную историю? Данные пропадут из приложения.")) return;
  await api(`/api/leases/${leaseId}`, { method: "DELETE" });
  toast("Запись о жильце удалена");
  await loadAll({ refreshSections: registryRefreshSections });
}

function renderRent() {
  const rows = [...state.rentCharges].sort((left, right) => compareApartmentRefs(left, right) || String(left.due_date).localeCompare(String(right.due_date))).map((charge) => `
    <tr>
      <td>${formatDate(charge.due_date)}<br><span class="muted">${formatDateRange(charge.period_start, charge.period_end)}</span></td>
      <td>${charge.object}</td>
      <td>${charge.apartment}</td>
      <td>${charge.tenant}</td>
      <td>${money(charge.ip_due)}<br><span class="muted">оплачено ${money(charge.ip_paid)}</span></td>
      <td>${money(charge.personal_due)}<br><span class="muted">оплачено ${money(charge.personal_paid)}</span></td>
      <td>${money(charge.debt)}</td>
      <td>${statusPill(charge.status, charge.due_date)}</td>
      <td class="actions">${rentActions(charge)}</td>
    </tr>
  `).join("");
  qs("#rentTable").innerHTML = table(["Дата", "Объект", "Квартира", "Жилец", "ИП", "Личный", "Долг", "Статус", "Действия"], rows);
}

function rentActions(charge) {
  return `
    <button class="mini primary" onclick="openManualAllocationForRentCharge(${charge.id})">Зачесть вручную</button>
    <button class="mini" onclick="deferRent(${charge.id})">Отсрочка</button>
    <button class="mini" onclick="openPaymentHistory(${charge.lease_id})">История</button>
  `;
}

function receiptStatusLabel(status) {
  return statusText[status] || status || "неизвестно";
}

async function openPaymentHistory(leaseId) {
  openRentTab();
  state.paymentHistory = await api(`/api/leases/${leaseId}/payment-history`);
  state.editingPaymentReceiptId = null;
  renderRentHistory();
  qs("#rentHistoryPanel")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function closePaymentHistory() {
  state.paymentHistory = null;
  state.editingPaymentReceiptId = null;
  renderRentHistory();
}

function paymentReceiptTargetValue(receipt) {
  if (receipt.rent_charge_id) return `rent:${receipt.rent_charge_id}`;
  if (receipt.utility_line_id) return `utility:${receipt.utility_line_id}`;
  return "";
}

function paymentReceiptTargetOptions(receipt) {
  const targets = state.paymentHistory?.targets || { rent: [], utility: [] };
  const selected = paymentReceiptTargetValue(receipt);
  const rentOptions = (targets.rent || []).map((target) =>
    `<option value="rent:${target.id}" ${selected === `rent:${target.id}` ? "selected" : ""}>${escapeHtml(target.label)} · долг ${money(target.debt)}</option>`
  ).join("");
  const utilityOptions = (targets.utility || []).map((target) =>
    `<option value="utility:${target.id}" ${selected === `utility:${target.id}` ? "selected" : ""}>${escapeHtml(target.label)} · долг ${money(target.debt)}</option>`
  ).join("");
  return `
    <option value="" ${selected ? "" : "selected"}>Не привязан</option>
    ${rentOptions ? `<optgroup label="Аренда">${rentOptions}</optgroup>` : ""}
    ${utilityOptions ? `<optgroup label="Коммуналка">${utilityOptions}</optgroup>` : ""}
  `;
}

function renderRentHistory() {
  const root = qs("#rentHistoryPanel");
  if (!root) return;
  if (!state.paymentHistory) {
    root.innerHTML = "";
    return;
  }
  const rows = state.paymentHistory.receipts.map((receipt) => {
    const editing = state.editingPaymentReceiptId === receipt.id;
    const editorRow = editing ? `
      <tr class="receipt-editor-row">
        <td colspan="8">
          <form id="paymentReceiptEdit-${receipt.id}" class="receipt-editor" onsubmit="savePaymentReceiptEdit(event, ${receipt.id})">
            <label>Сумма
              <input type="number" min="0" step="0.01" name="amount" value="${receipt.amount}" required />
            </label>
            <label>Оплачен
              <input type="datetime-local" name="paid_at" value="${escapeAttr((receipt.paid_at || "").slice(0, 16))}" required />
            </label>
            <label>Зачесть за
              <select name="target_ref">${paymentReceiptTargetOptions(receipt)}</select>
            </label>
            <label>Канал
              <select name="channel">
                <option value="ip" ${receipt.channel === "ip" ? "selected" : ""}>ИП</option>
                <option value="personal" ${receipt.channel === "personal" ? "selected" : ""}>По номеру</option>
                <option value="expense_fund" ${receipt.channel === "expense_fund" ? "selected" : ""}>Мне на расходы</option>
              </select>
            </label>
            <label class="wide">Комментарий
              <input name="notes" value="${escapeAttr(receipt.notes || "")}" />
            </label>
            <div class="receipt-editor__actions">
              <button class="mini primary" type="submit">Сохранить</button>
              ${receipt.status === "suspicious" ? '<button class="mini success-soft" type="submit" data-action="accept">Зачесть платёж</button>' : ""}
              <button class="mini" type="button" onclick="cancelPaymentReceiptEdit()">Отмена</button>
            </div>
          </form>
        </td>
      </tr>
    ` : "";
    return `
    <tr ${editing ? 'class="row-selected"' : ""}>
      <td>${formatDateTime(receipt.paid_at)}</td>
      <td>${money(receipt.amount)}</td>
      <td>${escapeHtml(receipt.channel_label || receipt.channel)}</td>
      <td>${escapeHtml(receipt.source_label || receipt.source || "manual")}</td>
      <td>${receipt.target_label || '<span class="muted">не привязан</span>'}</td>
      <td>${statusPill(receipt.status)}</td>
      <td>${escapeHtml(receipt.notes || "")}</td>
      <td class="actions">
        <button class="mini" onclick="editPaymentReceipt(${receipt.id})">${editing ? "Открыто" : "Редактировать"}</button>
        <button class="mini danger-soft" onclick="deletePaymentReceipt(${receipt.id})">Удалить</button>
      </td>
    </tr>
    ${editorRow}
  `;
  }).join("");
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

function editPaymentReceipt(receiptId) {
  state.editingPaymentReceiptId = state.editingPaymentReceiptId === receiptId ? null : receiptId;
  renderRentHistory();
}

function cancelPaymentReceiptEdit() {
  state.editingPaymentReceiptId = null;
  renderRentHistory();
}

async function savePaymentReceiptEdit(event, receiptId) {
  event.preventDefault();
  const history = state.paymentHistory;
  if (!history) return;
  const receipt = history.receipts.find((item) => item.id === receiptId);
  if (!receipt) return;
  const payload = formData(event.currentTarget);
  const acceptPayment = event.submitter?.dataset.action === "accept";
  if (acceptPayment && !payload.target_ref) {
    toast("Укажите, за что зачесть платёж");
    return;
  }
  payload.amount = Number(payload.amount);
  if (payload.target_ref) {
    const [targetKind, targetId] = payload.target_ref.split(":");
    payload.target_kind = targetKind;
    if (targetKind === "rent") {
      payload.rent_charge_id = Number(targetId);
    } else if (targetKind === "utility") {
      payload.utility_line_id = Number(targetId);
    }
  } else {
    delete payload.channel;
  }
  if (acceptPayment) {
    payload.status = "accepted";
  }
  delete payload.target_ref;
  await api(`/api/payment-receipts/${receiptId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  state.editingPaymentReceiptId = null;
  toast(acceptPayment ? "Платёж проверен и зачтён" : "Платёж обновлён");
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

async function ignoreSuspiciousReceipt(receiptId) {
  if (!confirm("Скрыть этот подозрительный чек из дашборда? Он останется в истории как скрытый.")) return;
  await api(`/api/payment-receipts/${receiptId}/ignore`, { method: "POST", body: "{}" });
  toast("Чек скрыт из дашборда");
  await loadAll();
}

function renderSuspiciousReceipts() {
  const root = qs("#suspiciousReceiptsPanel");
  if (!root) return;
  if (!state.suspiciousReceipts.length) {
    root.innerHTML = `<article class="card"><h3>Подозрительных чеков нет</h3><p class="muted">Все полученные чеки обработаны.</p></article>`;
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
        <button class="mini danger-soft" onclick="ignoreSuspiciousReceipt(${receipt.id})">Скрыть</button>
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
  await loadAll({ refreshSections: registryRefreshSections });
}

function selectedDatabaseImportFile() {
  return qs("#databaseImportFile")?.files?.[0] || null;
}

function renderDatabaseImportInspection(result = {}) {
  const box = qs("#databaseImportStatusBox");
  if (!box) return;
  if (!Object.keys(result).length) {
    box.innerHTML = '<div class="muted">Сюда прилетит разбор файла импорта после проверки.</div>';
    return;
  }
  const counts = Object.entries(result.counts || {})
    .sort((a, b) => a[0].localeCompare(b[0], "ru"))
    .map(([name, count]) => `<span class="pill">${name}: ${count}</span>`)
    .join("");
  const warnings = (result.warnings || []).map((item) => `<li>${item}</li>`).join("");
  box.innerHTML = `
    <div class="stack">
      <div class="pill-row">
        <span class="pill ok">строк: ${result.total_rows || 0}</span>
        <span class="pill">${result.database_url_hint || "db"}</span>
        <span class="pill">${result.exported_at ? formatDateTime(result.exported_at) : "дата неизвестна"}</span>
      </div>
      <div class="pill-row">${counts}</div>
      ${warnings ? `<div class="danger-box"><strong>Предупреждения:</strong><ul>${warnings}</ul></div>` : '<div class="ok-box">Файл выглядит валидно.</div>'}
    </div>
  `;
}

async function exportDatabase() {
  triggerNativeDownload("/api/admin/database-export", "rental-manager-db-export.json");
  toast("Экспорт базы готовится");
}

window.exportDatabase = exportDatabase;

async function inspectDatabaseImport() {
  const file = selectedDatabaseImportFile();
  if (!file) throw new Error("Сначала выбери файл экспорта");
  const body = new FormData();
  body.append("file", file);
  const result = await api("/api/admin/database-import/inspect", { method: "POST", body });
  renderDatabaseImportInspection(result);
  toast("Файл импорта проверен");
}

async function importDatabase() {
  const file = selectedDatabaseImportFile();
  if (!file) throw new Error("Сначала выбери файл экспорта");
  const confirmReplace = qs("#databaseImportConfirmReplace")?.checked;
  const confirmationText = (qs("#databaseImportConfirmationText")?.value || "").trim();
  const createBackup = qs("#databaseImportCreateBackup")?.checked !== false;
  if (!confirmReplace) throw new Error("Подтверди полную замену базы");
  if (confirmationText.toUpperCase() !== "ИМПОРТ") throw new Error('Введи слово "ИМПОРТ"');
  if (!confirm("Импорт полностью заменит текущую базу. Продолжить?")) return;
  const body = new FormData();
  body.append("file", file);
  body.append("confirmation_text", confirmationText);
  body.append("confirm_replace", String(confirmReplace));
  body.append("create_backup", String(createBackup));
  const result = await api("/api/admin/database-import", { method: "POST", body });
  renderDatabaseImportInspection(result.inspection || {});
  toast(`Импорт завершён. Строк: ${result.imported?.total_rows || 0}`);
  await loadAll({ refreshSections: registryRefreshSections });
}

function updateManualPaymentKind() {
  const kind = qs("#manualPaymentKindSelect")?.value || "rent";
  const channelSelect = qs("#manualPaymentChannelSelect");
  const targetMonth = qs("#manualPaymentTargetMonthSelect");
  const targetYear = qs("#manualPaymentTargetYearInput");
  const monthLabel = qs("#manualPaymentTargetMonthLabel");
  const yearLabel = qs("#manualPaymentTargetYearLabel");
  if (!channelSelect) return;
  channelSelect.disabled = kind !== "rent";
  if (kind !== "rent") {
    channelSelect.value = "personal";
  }
  [targetMonth, targetYear].forEach((field) => {
    if (!field) return;
    field.disabled = kind !== "rent";
  });
  [monthLabel, yearLabel].forEach((node) => {
    if (node) node.hidden = kind !== "rent";
  });
}

async function submitManualPayment(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = formData(form);
  if (payload.kind !== "rent") {
    delete payload.channel;
    delete payload.target_month;
    delete payload.target_year;
  } else {
    if (payload.target_month) {
      payload.target_month = Number(payload.target_month);
      payload.target_year = Number(payload.target_year || currentYear());
    } else {
      delete payload.target_month;
      delete payload.target_year;
    }
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

function isActiveUtilityBill(bill) {
  return bill.status === "draft"
    || !bill.provider_paid
    || (bill.lines || []).some((line) => Number(line.debt ?? Math.max(0, line.total_amount - line.paid_amount)) > 0 && ["issued", "partial", "overdue"].includes(line.status));
}

function draftUtilityBasePeriod(bill) {
  if (bill.bill_type !== "advance") {
    return { start: bill.period_start, end: bill.period_end, keyEnd: bill.period_end };
  }
  return { start: "", end: bill.period_start, keyEnd: bill.period_start };
}

function utilityDraftGroupKey(bill) {
  const period = draftUtilityBasePeriod(bill);
  return `draft:${bill.object}:${period.keyEnd}`;
}

function utilityDraftGroups(bills) {
  const groups = new Map();
  bills.filter((bill) => bill.status === "draft").forEach((bill) => {
    const key = utilityDraftGroupKey(bill);
    if (!groups.has(key)) {
      const period = draftUtilityBasePeriod(bill);
      groups.set(key, {
        kind: "draft_group",
        key,
        object: bill.object,
        period_start: period.start || bill.period_start,
        period_end: period.end || bill.period_end,
        bills: [],
      });
    }
    if (bill.bill_type !== "advance") {
      groups.get(key).period_start = bill.period_start;
      groups.get(key).period_end = bill.period_end;
    }
    groups.get(key).bills.push(bill);
  });
  return [...groups.values()].map((group) => ({
    ...group,
    bills: group.bills.sort((left, right) =>
      Number(left.bill_type === "advance") - Number(right.bill_type === "advance")
      || String(left.service).localeCompare(String(right.service), "ru")
      || Number(left.id) - Number(right.id)
    ),
  }));
}

function groupedDraftBillIds(group) {
  return group.bills.map((bill) => bill.id);
}

function groupedDraftPrimaryBillId(group) {
  const usage = group.bills.find((bill) => bill.bill_type !== "advance");
  return (usage || group.bills[0])?.id;
}

function groupedDraftServiceLabel(group) {
  const services = group.bills
    .filter((bill) => bill.bill_type !== "advance")
    .map((bill) => bill.service);
  if (!services.length) return "аванс коммуналки";
  if (services.length === 1) return services[0];
  return "общий";
}

function groupedDraftProviderOpen(group) {
  return group.bills.some((bill) => bill.bill_type !== "advance" && !bill.provider_paid);
}

function groupedDraftRows(group) {
  const rows = new Map();
  group.bills.forEach((bill) => {
    (bill.lines || []).forEach((line) => {
      const row = rows.get(line.apartment_id) || {
        apartment_id: line.apartment_id,
        apartment: line.apartment,
        tenant: line.tenant || "без жильца",
        fact: 0,
        paid: 0,
        advanceBalance: 0,
        advanceCharge: 0,
        statuses: new Set(),
      };
      row.statuses.add(line.status);
      if (line.line_type === "advance") {
        row.advanceBalance = Math.max(row.advanceBalance, Number(line.advance_balance_before || line.advance_balance_available || 0));
        row.advanceCharge += Number(line.total_amount || 0);
        row.paid += Number(line.paid_amount || 0);
      } else {
        row.fact += Number(line.total_amount || 0);
        row.paid += Number(line.paid_amount || 0);
        row.advanceBalance = Math.max(row.advanceBalance, Number(line.advance_balance_available || 0));
      }
      rows.set(line.apartment_id, row);
    });
  });
  return [...rows.values()]
    .map((row) => {
      const advanceImpact = -Math.min(Math.max(row.advanceBalance, 0), Math.max(row.fact, 0));
      const total = Math.max(0, row.fact + advanceImpact) + row.advanceCharge;
      return {
        ...row,
        advanceImpact,
        total,
        status: row.statuses.has("overdue") ? "overdue" : row.statuses.has("partial") ? "partial" : row.statuses.has("issued") ? "issued" : "draft",
      };
    })
    .sort((left, right) => compareApartmentRefs(left, right));
}

function renderGroupedDraftCard(group) {
  const groupedRows = groupedDraftRows(group);
  const rows = groupedRows.map((row) => `
    <tr>
      <td>${escapeHtml(row.apartment)}</td>
      <td><strong>${escapeHtml(row.tenant)}</strong></td>
      <td>${money(row.fact)}</td>
      <td>${row.advanceImpact ? money(row.advanceImpact) : '<span class="muted">0 ₽</span>'}</td>
      <td>${money(row.advanceCharge)}</td>
      <td>${money(row.total)}</td>
      <td>${money(row.paid)}</td>
      <td>${statusPill(row.status)}</td>
    </tr>
  `).join("");
  const services = group.bills
    .filter((bill) => bill.bill_type !== "advance")
    .map((bill) => bill.service)
    .join(", ");
  const advanceBill = group.bills.find((bill) => bill.bill_type === "advance");
  const totalFact = groupedRows.reduce((sum, row) => sum + row.fact, 0);
  const totalAdvanceImpact = groupedRows.reduce((sum, row) => sum + row.advanceImpact, 0);
  const totalAdvanceCharge = groupedRows.reduce((sum, row) => sum + row.advanceCharge, 0);
  const total = groupedRows.reduce((sum, row) => sum + row.total, 0);
  return `<article class="card">
    <h3>${escapeHtml(group.object)}: ${escapeHtml(groupedDraftServiceLabel(group))}</h3>
    <p class="muted">${formatDate(group.period_start)} → ${formatDate(group.period_end)}. ${services ? `Услуги: ${escapeHtml(services)}.` : ""} ${advanceBill ? "Аванс включён в этот черновик." : ""}</p>
    <div class="pill-row">
      ${statusPill("draft")}
      ${groupedDraftProviderOpen(group) ? '<span class="pill warn">поставщик не отмечен</span>' : '<span class="pill ok">поставщик закрыт</span>'}
      <span class="pill">факт ${money(totalFact)}</span>
      <span class="pill">аванс ${money(totalAdvanceImpact)}</span>
      <span class="pill">новый аванс ${money(totalAdvanceCharge)}</span>
      <span class="pill">итого ${money(total)}</span>
    </div>
    <div class="pill-row">
      <button class="mini primary" onclick="issueBill(${groupedDraftPrimaryBillId(group)})">Выставить жильцам</button>
      <button class="mini danger-soft" onclick="deleteUtilityGroup('${groupedDraftBillIds(group).join(",")}')">Удалить черновик</button>
    </div>
    <div class="table-wrap">${table(["Квартира", "Жилец", "Факт", "Аванс", "Аванс к начислению", "Итого", "Оплачено", "Статус"], rows)}</div>
  </article>`;
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

function syncUtilityPeriodInputs() {
  const form = qs("#utilityCalcForm");
  if (!form) return;
  const allowEstimate = Boolean(form.elements.allow_estimate.checked);
  const startInput = qs("#utilityPeriodStartInput");
  const endInput = qs("#utilityPeriodEndInput");
  const startSelect = qs("#utilityPeriodStartSelect");
  const endSelect = qs("#utilityPeriodEndSelect");
  if (!startInput || !endInput || !startSelect || !endSelect) return;
  if (!allowEstimate) {
    startInput.value = startSelect.value || "";
    endInput.value = endSelect.value || "";
  }
}

function updateUtilityPeriodControls() {
  const form = qs("#utilityCalcForm");
  if (!form) return;
  const startInput = qs("#utilityPeriodStartInput");
  const endInput = qs("#utilityPeriodEndInput");
  const startSelect = qs("#utilityPeriodStartSelect");
  const endSelect = qs("#utilityPeriodEndSelect");
  if (!startInput || !endInput || !startSelect || !endSelect) return;

  const allowEstimate = Boolean(form.elements.allow_estimate.checked);
  const targetValue = form.elements.service_id.value;
  const dates = targetValue ? utilityReadingDatesForTarget(targetValue) : [];
  const currentStart = startSelect.value || startInput.value;
  const currentEnd = endSelect.value || endInput.value;

  setValueOptions(startSelect, dates, (value) => formatDate(value), "Выбери дату");
  setValueOptions(endSelect, dates, (value) => formatDate(value), "Выбери дату");
  startSelect.disabled = !dates.length;
  endSelect.disabled = !dates.length;

  if (currentStart && dates.includes(currentStart)) startSelect.value = currentStart;
  if (currentEnd && dates.includes(currentEnd)) endSelect.value = currentEnd;

  startSelect.classList.toggle("field-toggle-hidden", allowEstimate);
  endSelect.classList.toggle("field-toggle-hidden", allowEstimate);
  startInput.classList.toggle("field-toggle-hidden", !allowEstimate);
  endInput.classList.toggle("field-toggle-hidden", !allowEstimate);

  if (!allowEstimate) {
    syncUtilityPeriodInputs();
  } else {
    if (startSelect.value && !startInput.value) startInput.value = startSelect.value;
    if (endSelect.value && !endInput.value) endInput.value = endSelect.value;
  }
}

function utilityTimelineActions(event) {
  if (event.kind !== "reading") return "";
  return `
    <button class="mini" onclick="useTimelineReading(${event.service_id}, 'start', '${event.date}')">В начало</button>
    <button class="mini" onclick="useTimelineReading(${event.service_id}, 'end', '${event.date}')">В конец</button>
  `;
}

function utilityTimelineMeta(event) {
  const badges = [
    `<span class="pill">${formatDate(event.date)}</span>`,
    `<span class="pill">${event.object}</span>`,
    `<span class="pill">${event.service}</span>`,
  ];
  if (event.status) badges.push(statusPill(event.status));
  if (event.kind === "bill") {
    badges.push(`<span class="pill ${event.provider_paid ? "ok" : "warn"}">${event.provider_paid ? "поставщик закрыт" : "поставщик открыт"}</span>`);
  }
  return badges.join("");
}

function renderUtilityTimeline() {
  const root = qs("#utilityTimeline");
  if (!root) return;
  if (!state.utilityTimeline.length) {
    root.innerHTML = `<article class="card"><h3>Таймлайн коммунальных услуг</h3><p class="muted">Добавьте показания и создайте расчётный период, чтобы увидеть события.</p></article>`;
    return;
  }
  root.innerHTML = `
    <article class="card">
      <div class="section-title">
        <div>
          <h3>Таймлайн коммуналки</h3>
          <span>Один платёжный период — промежуток между двумя общедомовыми показаниями.</span>
        </div>
      </div>
      <div class="utility-timeline">
        ${state.utilityTimeline.map((event) => `
          <article class="timeline-event timeline-${event.kind}">
            <div class="pill-row">${utilityTimelineMeta(event)}</div>
            <strong>${event.title}</strong>
            <p class="muted">${event.detail || ""}</p>
            ${utilityTimelineActions(event) ? `<div class="attention-actions">${utilityTimelineActions(event)}</div>` : ""}
          </article>
        `).join("")}
      </div>
    </article>
  `;
}

function renderUtilities() {
  renderQuickReadingFields();
  renderUtilityTimeline();
  updateUtilityPeriodControls();
  const previewCard = state.utilityIssuePreview ? `
    <article class="card warn">
      <div class="section-title">
        <div>
          <h3>Предпросмотр рассылки по коммуналке</h3>
          <span>${escapeHtml(state.utilityIssuePreview.object)}: ${escapeHtml(state.utilityIssuePreview.service)}, ${escapeHtml(state.utilityIssuePreview.period_label)}</span>
        </div>
      </div>
      <div class="pill-row">
        <span class="pill">срок оплаты: ${formatDate(state.utilityIssuePreview.due_date)}</span>
        <span class="pill">${state.utilityIssuePreview.targets.length} сообщений</span>
      </div>
      <div class="stack">
        ${state.utilityIssuePreview.targets.map((target) => `
          <article class="card">
            <div class="section-title">
              <div>
                <strong>${escapeHtml(target.object)}, ${escapeHtml(target.apartment)}, ${escapeHtml(target.tenant)}</strong>
              </div>
              ${target.linked ? '<span class="pill ok">бот отправит</span>' : '<span class="pill warn">ждём /start</span>'}
            </div>
            <pre class="message-preview">${escapeHtml(target.text)}</pre>
          </article>
        `).join("")}
      </div>
      <div class="attention-actions">
        <button class="mini primary" onclick="confirmIssueBill()">Подтвердить рассылку</button>
        <button class="mini" onclick="clearIssueBillPreview()">Отмена</button>
      </div>
    </article>
  ` : "";
  const renderBillCard = (bill) => {
    const lines = bill.lines.map((line) => `
      <tr>
        <td>${line.apartment}</td>
        <td><strong>${line.tenant || "без жильца"}</strong>${line.period_label ? `<br><span class="muted">${line.period_label}</span>` : ""}</td>
        <td>${line.line_type === "advance" ? "аванс" : line.personal_consumption}</td>
        <td>${line.line_type === "advance" ? "предоплата" : line.odn_consumption}</td>
        <td>${money(line.total_amount)}</td>
        <td>${money(line.paid_amount)}</td>
        <td>${statusPill(line.status, line.due_date)}</td>
        <td class="actions">${utilityActions(line)}</td>
      </tr>
    `).join("");
    return `<article class="card">
      <h3>${bill.object}: ${bill.service}</h3>
      <p class="muted">${bill.period_label}. По дому: ${money(bill.total_cost)} и ${bill.total_consumption}. Жильцам сейчас: ${money(bill.resident_total_amount)}.</p>
      <div class="pill-row">
        ${statusPill(bill.status, bill.due_date)}
        ${bill.is_forecast ? '<span class="pill warn">прогноз</span>' : ""}
        ${bill.bill_type === "advance" ? '<span class="pill">без поставщика</span>' : bill.provider_paid ? '<span class="pill ok">поставщик оплачен</span>' : '<span class="pill warn">поставщик не отмечен</span>'}
        <span class="pill">личное ${bill.apartment_consumption}</span>
        <span class="pill">ОДН ${bill.odn_consumption}</span>
        ${bill.provider_paid_at ? `<span class="pill ok">закрыт ${formatDate(bill.provider_paid_at)}</span>` : ""}
      </div>
      <div class="pill-row">
        ${bill.status === "draft" ? `<button class="mini primary" onclick="issueBill(${bill.id})">Выставить жильцам</button>` : ""}
        <button class="mini danger-soft" onclick="deleteUtilityBill(${bill.id}, '${bill.status}')">${bill.status === "draft" ? "Удалить черновик" : "Удалить счёт"}</button>
        ${!bill.provider_paid && bill.bill_type !== "advance" ? `<button class="mini primary" onclick="providerPaid(${bill.id})">Поставщик оплачен</button>` : ""}
      </div>
      <div class="table-wrap">${table(["Квартира", "Жилец / сегмент", "Личное", "ОДН", "Сумма", "Оплачено", "Статус", "Действия"], lines)}</div>
      ${bill.notes ? `<p class="muted">${bill.notes.replace(/\n/g, "<br>")}</p>` : ""}
    </article>`;
  };
  const draftGroups = utilityDraftGroups(state.utilityBills);
  const activeBills = [
    ...draftGroups,
    ...state.utilityBills.filter((bill) => bill.status !== "draft" && isActiveUtilityBill(bill)),
  ];
  const historyBills = state.utilityBills.filter((bill) => !isActiveUtilityBill(bill));
  const bills = [
    activeBills.length ? `<div class="section-title"><h3>Активные счета</h3><span>Черновики, долги жильцов и поставщики без отметки оплаты.</span></div>${activeBills.map((item) => item.kind === "draft_group" ? renderGroupedDraftCard(item) : renderBillCard(item)).join("")}` : "",
    historyBills.length ? `<div class="section-title"><h3>История коммуналки</h3><span>Закрытые периоды, чтобы были под рукой, но не под ногами.</span></div>${historyBills.map(renderBillCard).join("")}` : "",
  ].join("");
  qs("#utilityBills").innerHTML = `${previewCard}${bills || `<div class="card"><p class="muted">Коммунальных счетов пока нет.</p></div>`}`;
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
          <td>
            <input class="inline-number" id="serviceReadingDue${service.id}" type="number" min="1" max="31" value="${service.provider_reading_due_day || 20}" />
          </td>
          <td>${current ? formatDate(current.starts_on) : "нет"}</td>
          <td>${current ? tiersText(current.tiers) : "нет тарифа"}</td>
          <td>
            <button class="mini primary" onclick="saveUtilityServiceSettings(${service.id})">Срок</button>
            <button class="mini" onclick="prefillTariff(${service.id})">Тариф</button>
          </td>
        </tr>
      `;
    }).join("");
    return `<article class="card"><h3>${object.name}</h3><div class="table-wrap">${table(["Услуга", "Показания до", "Действует с", "Тариф", "Действие"], rows)}</div></article>`;
  }).join("");
}

function tiersText(tiers = []) {
  return tiers.map((tier) => `${tier.limit ?? "*"}: ${tier.price}`).join("; ");
}

async function saveUtilityServiceSettings(serviceId) {
  const input = qs(`#serviceReadingDue${serviceId}`);
  const providerReadingDueDay = Math.min(31, Math.max(1, Number(input?.value || 20)));
  const updated = await api(`/api/utility-services/${serviceId}`, {
    method: "PATCH",
    body: JSON.stringify({ provider_reading_due_day: providerReadingDueDay }),
  });
  const service = state.bootstrap.services.find((item) => item.id === serviceId);
  if (service) Object.assign(service, updated);
  renderTariffs();
  toast("Срок передачи показаний сохранён");
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
  const fund = state.bootstrap.dashboard.expense_fund || {};
  const summary = `
    <div class="expense-summary">
      <article><span>Получено на расходы</span><strong>${money(fund.received)}</strong></article>
      <article><span>Некомпенсированные расходы</span><strong>${money(fund.spent)}</strong></article>
      <article class="${fund.balance > 0 ? "warn" : "ok"}"><span>Разница</span><strong>${money(fund.balance)}</strong></article>
    </div>
  `;
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
  qs("#expenseList").innerHTML = `${summary}${table(["Дата", "Объект", "Квартира", "Категория", "Сумма", "Источник", "Компенсация", "Описание", "Действия"], rows)}`;
}

function botDialogById(dialogId = state.selectedBotDialogId) {
  return state.botDialogs.find((dialog) => dialog.id === dialogId) || null;
}

function botDialogStatus(dialog) {
  if (!dialog) return "";
  if (!dialog.linked) return '<span class="pill warn">ждём /start</span>';
  if (dialog.kind === "owner") return '<span class="pill">owner</span>';
  return '<span class="pill ok">бот привязан</span>';
}

function botDialogMessageTime(value) {
  return value ? formatDateTime(value) : "";
}

function scrollBotDialogToBottom() {
  const node = qs("#botDialogMessages");
  if (node) node.scrollTop = node.scrollHeight;
}

function renderBotDialogList() {
  const root = qs("#botDialogList");
  if (!root) return;
  if (state.botDialogLoading && !state.botDialogs.length) {
    root.innerHTML = `<div class="dialog-empty">Загружаю диалоги</div>`;
    return;
  }
  if (!state.botDialogs.length) {
    root.innerHTML = `<div class="dialog-empty">Диалогов пока нет</div>`;
    return;
  }
  root.innerHTML = state.botDialogs.map((dialog) => {
    const active = dialog.id === state.selectedBotDialogId;
    const lastPrefix = dialog.last_direction === "outgoing" ? "Бот: " : dialog.last_direction === "incoming" ? "" : "";
    return `
      <button class="dialog-item ${active ? "active" : ""}" type="button" onclick="selectBotDialog('${escapeAttr(dialog.id)}')">
        <span class="dialog-item__avatar">${escapeHtml((dialog.title || "?").slice(0, 1).toUpperCase())}</span>
        <span class="dialog-item__body">
          <span class="dialog-item__top">
            <strong>${escapeHtml(dialog.title || "Без имени")}</strong>
            <small>${escapeHtml(botDialogMessageTime(dialog.last_at))}</small>
          </span>
          <span class="dialog-item__place">${escapeHtml(dialog.subtitle || dialog.chat_id || "")}</span>
          <span class="dialog-item__last">${escapeHtml(dialog.last_text ? `${lastPrefix}${dialog.last_text}` : dialog.linked ? "чат готов" : "чат не привязан")}</span>
        </span>
      </button>
    `;
  }).join("");
}

function renderBotDialogHeader() {
  const root = qs("#botDialogHeader");
  if (!root) return;
  const dialog = botDialogById();
  if (!dialog) {
    root.innerHTML = `
      <div>
        <h3>Диалог не выбран</h3>
        <span class="muted">История появится после выбора чата</span>
      </div>
    `;
    return;
  }
  root.innerHTML = `
    <div>
      <h3>${escapeHtml(dialog.title || "Диалог")}</h3>
      <span class="muted">${escapeHtml(dialog.subtitle || dialog.chat_id || "")}</span>
    </div>
    <div class="pill-row">
      ${botDialogStatus(dialog)}
      ${dialog.chat_id ? `<span class="pill">chat ${escapeHtml(dialog.chat_id)}</span>` : ""}
    </div>
  `;
}

function renderBotDialogMessages() {
  const root = qs("#botDialogMessages");
  if (!root) return;
  const dialog = botDialogById();
  if (!dialog) {
    root.innerHTML = `<div class="messenger-empty">Диалог не выбран</div>`;
    return;
  }
  if (!state.botDialogMessages.length) {
    root.innerHTML = `<div class="messenger-empty">История пока пустая</div>`;
    return;
  }
  root.innerHTML = state.botDialogMessages.map((message) => {
    const direction = message.direction === "incoming" ? "incoming" : message.direction === "system" ? "system" : "outgoing";
    const statusClass = message.status === "failed" ? "failed" : "";
    const meta = [
      message.author || (direction === "incoming" ? "Собеседник" : "Бот"),
      botDialogMessageTime(message.created_at),
      message.status === "failed" ? "не отправлено" : "",
    ].filter(Boolean).join(" · ");
    return `
      <article class="message-bubble ${direction} ${statusClass}">
        <div class="message-bubble__meta">${escapeHtml(meta)}</div>
        <div class="message-bubble__text">${escapeHtml(message.text || "").replaceAll("\n", "<br>")}</div>
      </article>
    `;
  }).join("");
}

function setBotDialogComposeState() {
  const dialog = botDialogById();
  const input = qs("#botDialogInput");
  const button = qs("#botDialogForm button[type='submit']");
  const disabled = !dialog || !dialog.linked;
  if (input) {
    input.disabled = disabled;
    input.placeholder = disabled ? "Чат ещё не привязан" : "Текст от имени бота";
  }
  if (button) button.disabled = disabled;
}

function renderBotMessenger() {
  renderBotDialogList();
  renderBotDialogHeader();
  renderBotDialogMessages();
  setBotDialogComposeState();
}

async function loadBotDialogMessages(dialogId = state.selectedBotDialogId, options = {}) {
  if (!dialogId) {
    state.botDialogMessages = [];
    renderBotMessenger();
    return;
  }
  const payload = await api(`/api/bot-dialogs/${encodeURIComponent(dialogId)}/messages`);
  state.botDialogMessages = payload.messages || [];
  renderBotMessenger();
  if (options.scroll !== false) window.setTimeout(scrollBotDialogToBottom, 0);
}

async function loadBotDialogs(force = false) {
  if (state.botDialogLoading) return;
  if (state.botDialogsLoaded && !force) {
    renderBotMessenger();
    return;
  }
  state.botDialogLoading = true;
  renderBotMessenger();
  try {
    state.botDialogs = await api("/api/bot-dialogs");
    state.botDialogsLoaded = true;
    if (!botDialogById() && state.botDialogs.length) {
      state.selectedBotDialogId = state.botDialogs[0].id;
    }
    if (!state.botDialogs.some((dialog) => dialog.id === state.selectedBotDialogId)) {
      state.selectedBotDialogId = state.botDialogs[0]?.id || null;
    }
    await loadBotDialogMessages(state.selectedBotDialogId, { scroll: true });
  } finally {
    state.botDialogLoading = false;
    renderBotMessenger();
  }
}

async function selectBotDialog(dialogId) {
  if (!dialogId || dialogId === state.selectedBotDialogId) return;
  state.selectedBotDialogId = dialogId;
  state.botDialogMessages = [];
  renderBotMessenger();
  await loadBotDialogMessages(dialogId, { scroll: true });
}

async function submitBotDialogMessage(event) {
  event.preventDefault();
  const dialog = botDialogById();
  const input = qs("#botDialogInput");
  const text = input?.value.trim() || "";
  if (!dialog || !dialog.linked) {
    toast("Этот чат ещё не привязан к Telegram");
    return;
  }
  if (!text) {
    toast("Введите текст сообщения");
    return;
  }
  const button = qs("#botDialogForm button[type='submit']");
  if (button) button.disabled = true;
  try {
    await api(`/api/bot-dialogs/${encodeURIComponent(dialog.id)}/send`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    if (input) input.value = "";
    toast("Сообщение отправлено от имени бота");
    state.botDialogsLoaded = false;
    await loadBotDialogs(true);
  } finally {
    if (button) button.disabled = false;
    setBotDialogComposeState();
  }
}

function groupedBroadcastTargets() {
  const groups = new Map();
  [...state.messageTargets].sort(compareApartmentRefs).forEach((target) => {
    const objectName = target.object || "Без объекта";
    if (!groups.has(objectName)) groups.set(objectName, []);
    groups.get(objectName).push(target);
  });
  return [...groups.entries()];
}

function renderBroadcastRecipients() {
  const root = qs("#broadcastRecipients");
  if (!root) return;
  const targets = [...state.messageTargets].sort(compareApartmentRefs);
  const linkedCount = targets.filter((target) => target.linked).length;
  const summary = qs("#broadcastRecipientSummary");
  if (summary) summary.textContent = linkedCount ? `доступно ${linkedCount}` : "нет привязанных чатов";
  if (!targets.length) {
    root.innerHTML = `<p class="muted">Активных жильцов пока нет.</p>`;
    return;
  }
  root.innerHTML = `
    <label>
      <input type="checkbox" data-broadcast-all ${linkedCount ? "" : "disabled"} />
      <strong>Все</strong>
      <span class="muted">${linkedCount} получ.</span>
    </label>
    ${groupedBroadcastTargets().map(([objectName, items], groupIndex) => {
      const linkedItems = items.filter((item) => item.linked);
      return `
        <div class="recipient-tree__group">
          <label>
            <input type="checkbox" data-broadcast-parent="${groupIndex}" ${linkedItems.length ? "" : "disabled"} />
            <strong>${escapeHtml(objectName)}</strong>
            <span class="muted">${linkedItems.length}/${items.length}</span>
          </label>
          <div class="recipient-tree__children">
            ${items.map((target) => `
              <label class="${target.linked ? "" : "disabled"}">
                <input type="checkbox" data-broadcast-child="${groupIndex}" data-broadcast-lease-id="${target.lease_id}" ${target.linked ? "" : "disabled"} />
                <span>${escapeHtml(target.apartment)} · ${escapeHtml(target.tenant)}</span>
                <span class="muted">${target.linked ? "бот привязан" : "ждём /start"}</span>
              </label>
            `).join("")}
          </div>
        </div>
      `;
    }).join("")}
  `;
  updateBroadcastRecipientState();
}

function updateBroadcastRecipientState() {
  const root = qs("#broadcastRecipients");
  if (!root) return;
  const leafs = qsa("[data-broadcast-lease-id]", root).filter((input) => !input.disabled);
  qsa("[data-broadcast-parent]", root).forEach((parent) => {
    const children = qsa(`[data-broadcast-child="${parent.dataset.broadcastParent}"]`, root).filter((input) => !input.disabled);
    const checked = children.filter((input) => input.checked).length;
    parent.checked = children.length > 0 && checked === children.length;
    parent.indeterminate = checked > 0 && checked < children.length;
  });
  const all = qs("[data-broadcast-all]", root);
  if (all) {
    const checked = leafs.filter((input) => input.checked).length;
    all.checked = leafs.length > 0 && checked === leafs.length;
    all.indeterminate = checked > 0 && checked < leafs.length;
  }
  const summary = qs("#broadcastRecipientSummary");
  if (summary) {
    const selected = leafs.filter((input) => input.checked).length;
    summary.textContent = selected ? `выбрано ${selected}` : `доступно ${leafs.length}`;
  }
}

function handleBroadcastRecipientChange(event) {
  const root = qs("#broadcastRecipients");
  const input = event.target;
  if (!root || input?.type !== "checkbox") return;
  if (input.dataset.broadcastAll !== undefined) {
    qsa("[data-broadcast-lease-id]", root).forEach((child) => {
      if (!child.disabled) child.checked = input.checked;
    });
  } else if (input.dataset.broadcastParent !== undefined) {
    qsa(`[data-broadcast-child="${input.dataset.broadcastParent}"]`, root).forEach((child) => {
      if (!child.disabled) child.checked = input.checked;
    });
  }
  updateBroadcastRecipientState();
}

function selectedBroadcastLeaseIds() {
  return qsa("#broadcastRecipients [data-broadcast-lease-id]:checked")
    .map((input) => Number(input.dataset.broadcastLeaseId))
    .filter(Boolean);
}

function renderBroadcastStatus(result) {
  const root = qs("#broadcastStatus");
  if (!root) return;
  if (!result) {
    root.innerHTML = "";
    return;
  }
  root.innerHTML = `
    <strong>Итог рассылки</strong>
    <div class="pill-row">
      <span class="pill ok">отправлено ${result.sent || 0}</span>
      <span class="pill ${result.failed ? "danger" : ""}">ошибок ${result.failed || 0}</span>
      <span class="pill warn">без /start ${result.skipped_unlinked || 0}</span>
      <span class="pill">дубли чатов ${result.skipped_duplicate || 0}</span>
    </div>
  `;
}

async function submitBroadcastMessage(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const message = qs("#broadcastMessageInput")?.value.trim() || "";
  const leaseIds = selectedBroadcastLeaseIds();
  if (!message) {
    toast("Введите текст рассылки");
    return;
  }
  if (!leaseIds.length) {
    toast("Выберите хотя бы одного получателя");
    return;
  }
  if (!confirm(`Отправить сообщение выбранным получателям: ${leaseIds.length}?`)) return;
  const result = await api("/api/messages/broadcast", {
    method: "POST",
    body: JSON.stringify({ message, lease_ids: leaseIds }),
  });
  toast(`Рассылка завершена: отправлено ${result.sent || 0}, ошибок ${result.failed || 0}`);
  renderBroadcastStatus(result);
  form.reset();
  renderBroadcastRecipients();
  await loadAll();
}

function renderMessages() {
  const root = qs("#messageTargets");
  if (!root) return;
  renderBroadcastRecipients();
  const rows = state.messageTargets.map((target) => `
    <tr>
      <td>${target.object}</td>
      <td>${target.apartment}</td>
      <td>${target.tenant}<br><span class="muted">${target.telegram || "без @username"}</span></td>
      <td>${target.linked ? '<span class="pill ok">бот знает жильца</span>' : '<span class="pill warn">ждём /start от жильца</span>'}</td>
      <td>${target.rent_charge_id ? `${statusPill(target.rent_status)}<br><span class="muted">${money(target.rent_debt)}</span><br>${compactReminderText(target.rent_reminder)}` : '<span class="muted">нет</span>'}</td>
      <td>${target.utility_line_id ? `${statusPill(target.utility_status)}<br><span class="muted">${money(target.utility_debt)}</span><br>${compactReminderText(target.utility_reminder)}` : '<span class="muted">нет</span>'}</td>
      <td class="actions">
        ${target.rent_charge_id ? `<button class="mini primary" onclick="previewTemplateMessage(${target.lease_id}, 'message_rent_due', ${target.rent_charge_id}, null)">Аренда</button>` : ""}
        ${target.rent_charge_id ? `<button class="mini" onclick="previewTemplateMessage(${target.lease_id}, 'message_rent_overdue', ${target.rent_charge_id}, null)">Просрочка</button>` : ""}
        ${target.utility_line_id ? `<button class="mini primary" onclick="previewTemplateMessage(${target.lease_id}, 'message_utility_bill', null, ${target.utility_line_id})">Коммуналка</button>` : ""}
        <button class="mini" onclick="previewTemplateMessage(${target.lease_id}, 'message_all_debts')">Все долги</button>
        <button class="mini" onclick="previewCustomMessage(${target.lease_id})">Свой текст</button>
      </td>
    </tr>
  `).join("");
  root.innerHTML = table(["Объект", "Квартира", "Жилец", "Связка", "Аренда", "Коммуналка", "Действия"], rows);
  updateManualPaymentKind();
}

function cadenceLabel(value) {
  return {
    inherit: "по общей настройке",
    twice_daily: "2 раза в день каждый день",
    daily_evening: "вечером каждого дня",
    every_two_days: "раз в два дня",
    never: "выключено",
  }[value] || value || "не задано";
}

function cadenceSelect(name, value) {
  const options = [
    ["inherit", "по общей настройке"],
    ["twice_daily", "2 раза в день"],
    ["daily_evening", "вечером каждый день"],
    ["every_two_days", "раз в два дня"],
    ["never", "выключено"],
  ];
  return `<select name="${name}">${options.map(([key, label]) => `<option value="${key}" ${value === key ? "selected" : ""}>${label}</option>`).join("")}</select>`;
}

function issueCountLabel(target) {
  const rentCount = target.rent_items?.length || 0;
  const utilityItems = target.utility_items || [];
  const manualCount = target.manual_debt_items?.length || 0;
  const issuedCount = utilityItems.filter((item) => item.status === "issued").length;
  const overdueCount = utilityItems.length - issuedCount;
  const parts = [];
  if (rentCount) parts.push(`аренда: ${rentCount}`);
  if (overdueCount) parts.push(`коммуналка долг: ${overdueCount}`);
  if (issuedCount) parts.push(`коммуналка выставлена: ${issuedCount}`);
  if (manualCount) parts.push(`ручные долги: ${manualCount}`);
  return parts.join(", ") || "активных проблем нет";
}

function automationReminderLine(label, reminder, cadenceValue) {
  const schedule = reminder?.schedule;
  const cadence = cadenceLabel(cadenceValue);
  if (!reminder) return `<strong>${label}:</strong> проблем нет`;
  if (reminder.latest) {
    const nextText = schedule?.next_auto_at ? `, дальше ${nextReminderSlotLabel(schedule.next_auto_at)}` : "";
    return `<strong>${label}:</strong> последнее ${relativeReminderDate(reminder.latest.created_at)} (${cadence}${nextText})`;
  }
  if (reminder.block_reason) {
    return `<strong>${label}:</strong> ${escapeHtml(reminder.block_reason)} (${cadence})`;
  }
  const nextText = schedule?.next_auto_at ? `, следующее ${nextReminderSlotLabel(schedule.next_auto_at)}` : "";
  return `<strong>${label}:</strong> ещё не отправляли (${cadence}${nextText})`;
}

function renderAutomation() {
  const root = qs("#automationList");
  if (!root) return;
  const targets = state.messageTargets
    .filter((target) => (target.rent_items?.length || 0) + (target.utility_items?.length || 0) + (target.manual_debt_items?.length || 0) > 0)
    .sort((left, right) => `${left.object} ${left.apartment}`.localeCompare(`${right.object} ${right.apartment}`, "ru"));
  if (!targets.length) {
    root.innerHTML = `<article class="card ok"><h3>Автоматизация без активных задач</h3><p class="muted">Активных долгов и счетов для напоминаний сейчас нет.</p></article>`;
    return;
  }
  root.innerHTML = `
    <div class="automation-grid">
      ${targets.map((target) => `
        <article class="automation-card">
          <div class="section-title">
            <div>
              <h3>${escapeHtml(target.object)}, ${escapeHtml(target.apartment)}</h3>
              <span>${escapeHtml(target.tenant)}</span>
            </div>
            ${target.linked ? '<span class="pill ok">бот привязан</span>' : '<span class="pill warn">ждём /start</span>'}
          </div>
          <div class="pill-row">
            <span class="pill">${issueCountLabel(target)}</span>
          </div>
          <div class="automation-card__rows">
            <div class="automation-card__row">${automationReminderLine("Аренда: просрочка", target.rent_reminder, state.settings.automation_rent_overdue_cadence)}</div>
            <div class="automation-card__row">${automationReminderLine("Коммуналка", target.utility_reminder, state.settings.automation_utility_cadence)}</div>
          </div>
          <form class="automation-card__rows" onsubmit="saveLeaseAutomation(event, ${target.lease_id})">
            <label>День оплаты <span class="muted">один раз в 10:00</span></label>
            <label>Долг по аренде ${cadenceSelect("message_rent_overdue", target.automation?.message_rent_overdue || "inherit")}</label>
            <label>Коммуналка ${cadenceSelect("message_utility_bill", target.automation?.message_utility_bill || "inherit")}</label>
            <button class="mini primary" type="submit">Сохранить для жильца</button>
          </form>
          <div class="attention-actions">
            <button class="mini primary" onclick="previewTemplateMessage(${target.lease_id}, 'message_all_debts')">Все долги</button>
            ${target.rent_charge_id ? `<button class="mini" onclick="previewTemplateMessage(${target.lease_id}, '${target.rent_status === "pending" ? "message_rent_due" : "message_rent_overdue"}', ${target.rent_charge_id}, null)">Напомнить по аренде</button>` : ""}
            ${target.utility_line_id ? `<button class="mini" onclick="previewTemplateMessage(${target.lease_id}, 'message_utility_bill', null, ${target.utility_line_id})">Напомнить по коммуналке</button>` : ""}
          </div>
        </article>
      `).join("")}
    </div>
  `;
}

async function submitAutomationSettings(event) {
  event.preventDefault();
  const settings = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify(formData(event.currentTarget)),
  });
  applySettings(settings);
  renderAutomation();
  toast("Частота автонапоминаний сохранена");
}

async function saveLeaseAutomation(event, leaseId) {
  event.preventDefault();
  await api(`/api/leases/${leaseId}/automation`, {
    method: "PATCH",
    body: JSON.stringify(formData(event.currentTarget)),
  });
  toast("Индивидуальные настройки уведомлений сохранены");
  await loadAll({ refreshSections: registryRefreshSections });
}

function renderMessagePreview() {
  const root = qs("#messagePreviewPanel");
  if (!root) return;
  if (!state.messagePreview) {
    root.innerHTML = "";
    return;
  }
  const preview = state.messagePreview;
  root.innerHTML = `
    <article class="card">
      <div class="section-title">
        <div>
          <h3>Предпросмотр сообщения</h3>
          <span>${preview.object}, ${preview.apartment}, ${preview.tenant}</span>
        </div>
        ${preview.linked ? '<span class="pill ok">бот может отправить</span>' : '<span class="pill warn">ждём /start от жильца</span>'}
      </div>
      <div class="pill-row">
        <span class="pill">${preview.template_label}</span>
      </div>
      <pre class="message-preview">${preview.text}</pre>
      <div class="attention-actions">
        <button class="mini primary" onclick="sendPreviewedMessage()">Отправить</button>
        <button class="mini" onclick="clearMessagePreview()">Скрыть</button>
      </div>
    </article>
  `;
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

async function deleteUtilityBill(id, status = "draft") {
  const issued = status !== "draft";
  const question = issued
    ? "Удалить этот уже выставленный счёт? Если по нему нет зачтённых оплат, его можно будет пересоздать заново."
    : "Удалить этот черновик коммуналки? Потом можно пересоздать заново.";
  if (!confirm(question)) return;
  await api(`/api/utility-bills/${id}`, { method: "DELETE" });
  toast(issued ? "Счёт удалён" : "Черновик удалён");
  await loadAll();
}

async function deleteUtilityGroup(idsText) {
  const ids = String(idsText || "")
    .split(",")
    .map((value) => Number(value))
    .filter(Boolean);
  if (!ids.length) return;
  if (!confirm("Удалить этот общий черновик коммуналки вместе с авансом? Потом можно пересоздать заново.")) return;
  for (const id of ids) {
    await api(`/api/utility-bills/${id}`, { method: "DELETE" });
  }
  toast("Общий черновик удалён");
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

async function issueBill(id) {
  state.utilityIssuePreview = await api(`/api/utility-bills/${id}/issue-preview`);
  renderUtilities();
  qs("#utilityBills")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function clearIssueBillPreview() {
  state.utilityIssuePreview = null;
  renderUtilities();
}

async function confirmIssueBill() {
  if (!state.utilityIssuePreview?.bill_id) return;
  const result = await api(`/api/utility-bills/${state.utilityIssuePreview.bill_id}/issue`, { method: "POST", body: "{}" });
  const sent = Number(result.sent || 0);
  const skipped = Number(result.skipped_unlinked || 0);
  const failed = Number(result.failed || 0);
  state.utilityIssuePreview = null;
  toast(`Счёт выставлен. Отправлено: ${sent}. Ждут /start: ${skipped}.`);
  if (failed) toast(`Telegram errors: ${failed}.`);
  await loadAll();
}

async function previewTemplateMessage(leaseId, templateKey, chargeId = null, utilityLineId = null, customText = "") {
  openMessagesTab();
  state.messagePreview = await api("/api/messages/preview", {
    method: "POST",
    body: JSON.stringify({
      lease_id: leaseId,
      template_key: templateKey,
      charge_id: chargeId,
      utility_line_id: utilityLineId,
      custom_text: customText,
    }),
  });
  renderMessagePreview();
  qs("#messagePreviewPanel")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function previewCustomMessage(leaseId) {
  const customText = prompt("Текст сообщения");
  if (!customText) return;
  await previewTemplateMessage(leaseId, "custom", null, null, customText);
}

function clearMessagePreview() {
  state.messagePreview = null;
  renderMessagePreview();
}

async function sendPreviewedMessage() {
  if (!state.messagePreview?.payload) return;
  await api("/api/messages/send", {
    method: "POST",
    body: JSON.stringify(state.messagePreview.payload),
  });
  toast("Сообщение отправлено через бота");
  state.messagePreview = null;
  renderMessagePreview();
  await loadAll();
}

async function moveOut(id) {
  const endDate = prompt("Дата выезда", today());
  if (!endDate) return;
  const result = await api(`/api/leases/${id}/move-out`, { method: "POST", body: JSON.stringify({ end_date: endDate }) });
  const s = result.summary;
  alert(`Выезд оформлен.\nПолных месяцев: ${s.full_months_lived}\nПоследний оплаченный день: ${formatDate(s.last_paid_day)}\nДолг аренда: ${money(s.rent_debt)}\nДолг коммуналка: ${money(s.utility_debt)}\nЗалог: ${money(s.deposit_amount)}\nУсловия: ${s.deposit_terms || "нет"}`);
  await loadAll({ refreshSections: registryRefreshSections });
}

function moneyPromptValue(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return null;
  const parsed = Number(raw.replaceAll("\u00a0", "").replaceAll(" ", "").replace(",", "."));
  return Number.isFinite(parsed) ? parsed : null;
}

async function transferLease(id) {
  const lease = state.bootstrap.leases.find((item) => Number(item.id) === Number(id));
  if (!lease) return;
  const transferDate = prompt("Дата переезда", today());
  if (!transferDate) return;

  const targets = activeApartments()
    .filter((apartment) => Number(apartment.id) !== Number(lease.apartment_id) && !apartment.active_lease_id)
    .sort(compareApartmentRefs);
  if (!targets.length) {
    alert("Нет свободных активных квартир для переезда.");
    return;
  }
  const targetText = targets.map((apartment, index) => `${index + 1}. ${apartment.object_name}, ${apartment.name}`).join("\n");
  const selectedIndexRaw = prompt(`Куда переезжает жилец?\n${targetText}`);
  if (!selectedIndexRaw) return;
  const selectedIndex = Number(selectedIndexRaw);
  const target = targets[selectedIndex - 1];
  if (!target) {
    alert("Не понял номер квартиры. Нумерация скучная, зато честная.");
    return;
  }

  const ipRaw = prompt("Новый платёж на ИП. Оставь текущее значение, если не меняется.", lease.ip_amount || 0);
  if (ipRaw === null) return;
  const personalRaw = prompt("Новый личный перевод. Оставь текущее значение, если не меняется.", lease.personal_amount || 0);
  if (personalRaw === null) return;
  const ipAmount = moneyPromptValue(ipRaw) ?? Number(lease.ip_amount || 0);
  const personalAmount = moneyPromptValue(personalRaw) ?? Number(lease.personal_amount || 0);
  if (ipAmount < 0 || personalAmount < 0) {
    alert("Стоимость аренды не может быть отрицательной.");
    return;
  }

  const result = await api(`/api/leases/${id}/transfer`, {
    method: "POST",
    body: JSON.stringify({
      apartment_id: target.id,
      transfer_date: transferDate,
      ip_amount: ipAmount,
      personal_amount: personalAmount,
    }),
  });
  toast(`Переезд оформлен: ${result.old_lease.apartment} → ${result.new_lease.apartment}`);
  await loadAll({ refreshSections: registryRefreshSections });
}

function openReportsTab() {
  const tab = qs('.tab[data-tab="reports"]');
  if (tab) tab.click();
}

function openTenantsTab() {
  const tab = qs('.tab[data-tab="tenants"]');
  if (tab) tab.click();
}

function openRentTab() {
  const tab = qs('.tab[data-tab="rent"]');
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

function useTimelineReading(serviceId, edge, dateValue) {
  const form = qs("#utilityCalcForm");
  if (!form) return;
  form.elements.service_id.value = `service:${serviceId}`;
  updateUtilityPeriodControls();
  if (form.elements.allow_estimate.checked) {
    form.elements[edge === "start" ? "period_start" : "period_end"].value = dateValue;
  } else {
    const select = qs(edge === "start" ? "#utilityPeriodStartSelect" : "#utilityPeriodEndSelect");
    if (select) select.value = dateValue;
    syncUtilityPeriodInputs();
  }
  form.scrollIntoView({ behavior: "smooth", block: "start" });
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

function openMonthlyReport(year, month, kind = "full") {
  const report =
    state.bootstrap.dashboard.monthly_reports.find((item) => item.year === year && item.month === month && (item.kind || "full") === kind) ||
    state.bootstrap.dashboard.monthly_reports.find((item) => item.year === year && item.month === month);
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
        <div class="attention-actions">
          <a class="button primary" href="${report.download_url}">Скачать месячный отчёт</a>
          <button class="mini" onclick="acceptMonthlyReport(event, ${report.year}, ${report.month}, '${report.kind || "full"}')">Отчёт принят</button>
        </div>
      </div>
      <p class="muted">${formatDateRange(report.period_start, report.period_end)} · ${monthlySeverityText(report)}</p>
      ${table(["Важность", "Проблема", "Кол-во", "Комментарий"], rows)}
    </article>
  `;
}

async function acceptMonthlyReport(event, year, month, kind = "full") {
  event?.stopPropagation?.();
  await api(`/api/reports/monthly/${year}/${month}/accept`, {
    method: "POST",
    body: JSON.stringify({ kind }),
  });
  toast("Отчёт отправлен в архив");
  await loadAll();
}

function setReportLinks() {
  const start = qs("#reportStart")?.value || "";
  const end = qs("#reportEnd")?.value || "";
  const query = start && end ? `?start=${start}&end=${end}` : "";
  if (qs("#rentReport")) qs("#rentReport").href = `/api/reports/rent.xlsx${query}`;
  if (qs("#utilitiesReport")) qs("#utilitiesReport").href = `/api/reports/utilities.xlsx${query}`;
  if (qs("#expensesReport")) qs("#expensesReport").href = `/api/reports/expenses.xlsx${query}`;
  if (qs("#ownerReport")) qs("#ownerReport").href = `/api/reports/owner.xlsx${query}`;
}

async function connectTelegramWebhook() {
  const result = await api("/api/integrations/telegram/set-webhook", {
    method: "POST",
    body: JSON.stringify({ app_base_url: window.location.origin }),
  });
  const warnings = [result.delete_warning, result.commands_warning].filter(Boolean);
  toast(warnings.length ? `${result.description || "Webhook подключён"}; предупреждение: ${warnings[0]}` : result.description || "Webhook подключён");
  await loadAll({ fullScreen: true });
}

async function telegramWebhookInfo() {
  const result = await api("/api/integrations/telegram/webhook-info");
  const summary = [
    result.url ? `URL: ${result.url}` : "Webhook пока не установлен",
    result.expected_url ? `Ожидаемый URL: ${result.expected_url}` : "",
    result.url ? `Совпадает с текущим приложением: ${result.matches_expected ? "да" : "нет"}` : "",
    result.pending_update_count ? `Pending: ${result.pending_update_count}` : "Pending: 0",
    result.allowed_updates?.length ? `Allowed updates: ${result.allowed_updates.join(", ")}` : "",
    result.last_error_message ? `Ошибка: ${result.last_error_message}` : "Ошибок от Telegram нет",
  ].filter(Boolean).join("\n");
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
  toast(`Напоминания: отправлено ${result.sent}, дубликаты ${result.skipped_duplicate}, старые долги пропущены ${result.skipped_legacy}`);
  await loadAll({ fullScreen: true });
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

async function loadSessionState() {
  try {
    state.auth = await api("/api/auth/status");
    applyAccessUi();
    if (!state.auth.authenticated) {
      showAuthOverlay();
      return false;
    }
    hideAuthOverlay();
    return true;
  } catch (error) {
    state.auth = { authenticated: false, role: null };
    applyAccessUi();
    showAuthOverlay();
    throw error;
  }
}

async function submitPinLogin(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = formData(form);
  try {
    state.auth = await api("/api/auth/pin", { method: "POST", body: JSON.stringify(payload) });
    applyAccessUi();
    hideAuthOverlay();
    form.reset();
    const remember = qs("#rememberDeviceInput");
    if (remember) remember.checked = true;
    await loadAll();
  } catch (error) {
    showAuthOverlay();
    toast(error.message);
  }
}

async function logoutPanel() {
  await api("/api/auth/logout", { method: "POST", body: "{}" });
  state.auth = { authenticated: false, role: null };
  state.bootstrap = null;
  applyAccessUi();
  showAuthOverlay();
}

async function initApp() {
  const authenticated = await loadSessionState();
  if (!authenticated) return;
  await loadAll();
}

function bindEvents() {
  qsa(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      if (tab.dataset.group !== activeNavGroup) activateNavGroup(tab.dataset.group, false);
      qsa(".tab").forEach((item) => item.classList.remove("active"));
      qsa(".panel").forEach((item) => item.classList.remove("active"));
      tab.classList.add("active");
      qs(`#${tab.dataset.tab}`)?.classList.add("active");
      updateWorkspaceContext(tab.dataset.tab);
      const search = qs("#globalSearch");
      if (search) search.value = "";
      filterActivePanel("");
      if (tab.dataset.tab === "dialogs") {
        loadBotDialogs().catch((error) => toast(error.message));
      }
    });
  });
  qsa(".nav-group").forEach((group) => {
    group.addEventListener("click", () => activateNavGroup(group.dataset.group));
  });
  qsa(".settings-nav-btn").forEach((button) => {
    button.addEventListener("click", () => {
      qsa(".settings-nav-btn").forEach((item) => item.classList.toggle("active", item === button));
      qsa(".settings-pane").forEach((pane) => {
        pane.hidden = pane.dataset.settingsPanel !== button.dataset.settingsTarget;
      });
    });
  });

  on("#refreshBtn", "click", () => loadAll({ fullScreen: true }));
  on("#quickActionsBtn", "click", openQuickActions);
  on("#globalSearch", "input", (event) => filterActivePanel(event.currentTarget.value));
  on("#pinLoginForm", "submit", submitPinLogin);
  on("#logoutBtn", "click", logoutPanel);
  on("#runRemindersBtn", "click", runRemindersNow);
  on("#runRemindersInlineBtn", "click", runRemindersNow);
  on("#importBaselineBtn", "click", importBaseline);
  on("#databaseExportBtn", "click", exportDatabase);
  on("#databaseImportInspectBtn", "click", inspectDatabaseImport);
  on("#databaseImportApplyBtn", "click", importDatabase);
  on("#manualPaymentForm", "submit", submitManualPayment);
  on("#manualPaymentKindSelect", "change", updateManualPaymentKind);
  on("#openTariffsBtn", "click", () => {
    const tab = qs('.tab[data-tab="tariffs"]');
    if (tab) tab.click();
  });
  on("#telegramWebhookBtn", "click", connectTelegramWebhook);
  on("#telegramWebhookInfoBtn", "click", telegramWebhookInfo);
  on("#telegramTestBtn", "click", sendTelegramTest);
  on("#botDialogsRefreshBtn", "click", () => loadBotDialogs(true));
  on("#botDialogForm", "submit", submitBotDialogMessage);
  on("#performanceRefreshBtn", "click", loadPerformanceMonitor);
  on("#loadRentBtn", "click", async () => {
    await loadRent();
    renderRent();
  });

  on("#onboardForm", "submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = formData(form);
    if (state.editingLeaseId) {
      await api(`/api/leases/${state.editingLeaseId}`, { method: "PATCH", body: JSON.stringify(data) });
      toast("Изменения по жильцу сохранены");
      state.editingLeaseId = null;
    } else {
      await api("/api/leases/onboard", { method: "POST", body: JSON.stringify(data) });
      toast("Жилец заселён");
    }
    form.reset();
    const tool = qs("#onboardTool");
    if (tool) tool.open = false;
    const title = qs("#onboardFormTitle");
    if (title) title.textContent = "Заселение";
    await loadAll({ refreshSections: registryRefreshSections });
  });
  on("#cancelLeaseEditBtn", "click", cancelLeaseEdit);
  on("#objectForm", "submit", async (event) => {
    event.preventDefault();
    await api("/api/objects", { method: "POST", body: JSON.stringify(formData(event.currentTarget)) });
    event.currentTarget.reset();
    toast("Объект создан");
    await loadAll({ refreshSections: registryRefreshSections });
  });
  on("#apartmentForm", "submit", async (event) => {
    event.preventDefault();
    await api("/api/apartments", { method: "POST", body: JSON.stringify(formData(event.currentTarget)) });
    event.currentTarget.reset();
    toast("Квартира создана");
    await loadAll({ refreshSections: registryRefreshSections });
  });

  on("#readingForm", "submit", async (event) => {
    event.preventDefault();
    await api("/api/meter-readings", { method: "POST", body: JSON.stringify(formData(event.currentTarget)) });
    toast("Показание сохранено");
    await loadAll();
  });
  on("#quickReadingsForm", "submit", submitQuickReadings);

  on("#utilityCalcForm", "submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    syncUtilityPeriodInputs();
    const payload = formData(form);
    const targetValue = String(payload.service_id || "");
    let result;
    if (targetValue.startsWith("object:")) {
      payload.object_id = Number(targetValue.slice("object:".length));
      delete payload.service_id;
      result = await api("/api/utility-bills/calculate-object", { method: "POST", body: JSON.stringify(payload) });
      toast(`Черновики объекта созданы: ${result.created?.length || 0}`);
    } else {
      payload.service_id = Number(targetValue.replace("service:", ""));
      result = await api("/api/utility-bills/calculate", { method: "POST", body: JSON.stringify(payload) });
      toast(`Черновик создан: ${money(result.total_cost)}`);
    }
    await loadAll();
  });
  on("#utilityServiceSelect", "change", updateUtilityPeriodControls);
  on('#utilityCalcForm input[name="allow_estimate"]', "change", updateUtilityPeriodControls);
  on("#utilityPeriodStartSelect", "change", syncUtilityPeriodInputs);
  on("#utilityPeriodEndSelect", "change", syncUtilityPeriodInputs);

  on("#tariffForm", "submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    await api("/api/tariffs", { method: "POST", body: JSON.stringify(formData(form)) });
    form.reset();
    toast("Тариф добавлен");
    await loadAll();
  });

  on("#expenseForm", "submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    await api("/api/expenses", { method: "POST", body: JSON.stringify(formData(form)) });
    form.reset();
    toast("Расход добавлен");
    await loadAll();
  });

  on("#messageTemplatesForm", "submit", async (event) => {
    event.preventDefault();
    const data = formData(event.currentTarget);
    const settings = await api("/api/settings", { method: "POST", body: JSON.stringify(data) });
    applySettings(settings);
    toast("Шаблоны сохранены");
  });
  on("#broadcastRecipients", "change", handleBroadcastRecipientChange);
  on("#broadcastForm", "submit", submitBroadcastMessage);

  on("#automationSettingsForm", "submit", submitAutomationSettings);

  on("#settingsForm", "submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = formData(form);
    const settings = await api("/api/settings", { method: "POST", body: JSON.stringify(data) });
    applySettings(settings);
    form.querySelectorAll('input[type="password"]').forEach((input) => {
      if (["panel_owner_pin_code", "panel_guest_pin_code", "telegram_bot_token", "telegram_webhook_secret", "hermes_api_key"].includes(input.name)) {
        input.value = "";
      }
    });
    toast("Настройки сохранены");
  });
  on("#paletteSelect", "change", (event) => {
    applySettings({ ...state.settings, color_palette: event.currentTarget.value });
  });

  ["reportStart", "reportEnd"].forEach((id) => on(`#${id}`, "change", setReportLinks));
  renderDatabaseImportInspection();
}
window.addEventListener("unhandledrejection", (event) => {
  const message = event.reason?.message || String(event.reason || "Ошибка");
  toast(message);
});

window.addEventListener("error", (event) => {
  if (event?.message) toast(event.message);
});

bindEvents();
initApp().catch((error) => {
  showAuthOverlay();
  toast(error.message);
});
