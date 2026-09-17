export function createPaymentCalendar({ api, today, openModal, restoreFocus, escapeHtml, money }) {
  const root = document.getElementById("paymentCalendarModal");
  const symbols = { paid: "✓", issued: "●", partial: "◐", overdue: "!", deferred: "◷", draft: "◇", vacant: "", unbilled: "—", incomplete: "…", conflict: "?" };
  let data = null;
  let mode = "utility";
  let month = "";
  let selected = null;
  let generation = 0;
  const collapsed = new Set();
  const dateValue = (value) => new Date(`${value}T12:00:00Z`);
  const iso = (value) => value.toISOString().slice(0, 10);
  const shortDate = (value) => dateValue(value).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "UTC" });
  const shiftMonth = (value, count) => {
    const [year, index] = value.split("-").map(Number);
    return iso(new Date(Date.UTC(year, index - 1 + count, 1))).slice(0, 7);
  };
  const label = (status) => data?.statuses[status] || status;
  const badge = (status) => `<span class="pc-badge pc-${status}">${symbols[status] || ""} ${escapeHtml(label(status))}</span>`;

  function close() {
    generation++;
    root.hidden = true;
    root.innerHTML = "";
    data = null;
    selected = null;
    document.body.classList.remove("payment-calendar-open");
    restoreFocus(root);
  }

  function shell() {
    root.innerHTML = `<div class="modal-card payment-calendar">
      <div class="pc-heading"><div><p class="eyebrow">Коммунальные услуги</p><h3>Календарь оплат</h3></div><button type="button" data-action="close" aria-label="Закрыть календарь">✕</button></div>
      <div class="pc-toolbar">
        <div class="pc-modes" aria-label="Вид начислений"><button type="button" data-mode="rent" aria-pressed="${mode === "rent"}">Аренда</button><button type="button" data-mode="utility" aria-pressed="${mode === "utility"}">Коммуналка</button></div>
        <div class="pc-dates"><button type="button" data-action="previous" aria-label="Предыдущий месяц">‹</button><label class="pc-month-label">Месяц<input type="month" data-month value="${month}" min="1900-01" max="9998-12"></label><button type="button" data-action="next" aria-label="Следующий месяц">›</button><button type="button" data-action="today">Сегодня</button></div>
        <button type="button" data-action="refresh">Обновить</button>
      </div>
      <p class="pc-explanation">Текущий статус счетов за дни проживания. Суммы — за период счёта, не за один день.</p>
      <div class="pc-feedback" role="status" aria-live="polite"></div>
      <div class="pc-scroll" role="region" aria-label="Дни и квартиры: прокрутка влево и вправо" tabindex="0"></div>
      <div class="pc-legend" aria-label="Статусы"></div>
      <section class="pc-detail" aria-label="Выбранный день" aria-live="polite"></section>
    </div>`;
  }

  function findSelection() {
    for (const object of data?.objects || []) {
      const apartment = object.apartments.find((row) => row.id === selected?.apartment);
      const day = apartment?.days.find((item) => item.date === selected?.date);
      if (day) return { object, apartment, day };
    }
    return null;
  }

  function details() {
    const target = root.querySelector(".pc-detail");
    const current = findSelection();
    if (!current) { target.innerHTML = "Выберите день в строке квартиры."; return; }
    const { object, apartment, day } = current;
    const leases = apartment.leases.filter((lease) => day.lease_ids.includes(lease.id));
    const entries = apartment.entries.filter((entry) => day.entry_ids.includes(entry.id));
    const issued = entries.filter((entry) => entry.kind !== "advance" && entry.status !== "draft");
    const total = (key) => issued.reduce((sum, entry) => sum + entry[key], 0);
    const period = (entry) => entry.kind === "rent"
      ? `${shortDate(entry.period_start)} — ${shortDate(entry.period_end)} включительно`
      : `${shortDate(entry.start)} → ${shortDate(entry.end)} (до границы показаний)`;
    target.innerHTML = `<div class="pc-detail-heading"><div><h4>${escapeHtml(object.name)} · ${escapeHtml(apartment.name)}</h4><span>${shortDate(day.date)}</span></div>${badge(day.status)}</div>
      <div class="pc-occupants">${leases.length ? leases.map((lease) => `<span><strong>${escapeHtml(lease.tenant)}</strong> · заезд ${shortDate(lease.start)}${lease.end ? ` · последний день ${shortDate(lease.end)}` : ""}</span>`).join("<br>") : "На этот день проживание не записано."}</div>
      ${day.status === "conflict" ? '<p class="pc-warning">Договоры пересекаются или счёт не соответствует проживанию. Проверьте даты и получателя начисления.</p>' : ""}
      ${day.missing_services.length ? `<p class="pc-warning">Нет выставленного расчёта: ${day.missing_services.map(escapeHtml).join(", ")}.</p>` : ""}
      <div class="pc-totals"><span>Начислено<strong>${money(total("amount"))}</strong></span><span>Оплачено<strong>${money(total("paid"))}</strong></span><span>Остаток<strong>${money(total("debt"))}</strong></span></div>
      <p class="pc-explanation">По выставленным счетам, покрывающим выбранный день. Черновики и авансы показаны отдельно ниже.</p>
      ${entries.length ? `<div class="pc-entry-list">${entries.map((entry) => `<article class="pc-entry">
        <div><strong>${escapeHtml(entry.title)}</strong> ${badge(entry.status)}${entry.forecast ? '<span class="pc-estimated">Расчётные показания</span>' : ""}<small>${period(entry)}</small>${entry.due_date ? `<small>Срок оплаты: ${shortDate(entry.due_date)}${entry.deferred_until ? ` · отсрочка до ${shortDate(entry.deferred_until)}` : ""}</small>` : ""}</div>
        <div class="pc-entry-money"><span>${entry.status === "draft" ? "В черновике" : "Начислено"}: ${money(entry.amount)}</span><span>Оплачено: ${money(entry.paid)}</span><strong>${entry.status === "draft" ? "К выставлению" : "Остаток"}: ${money(entry.debt)}</strong></div>
        ${entry.kind === "rent" ? `<small class="pc-rent-parts">ИП: ${money(entry.ip_paid)} из ${money(entry.ip_due)} · Личный перевод: ${money(entry.personal_paid)} из ${money(entry.personal_due)}</small>` : ""}
      </article>`).join("")}</div>` : `<p class="pc-empty">${leases.length ? "Начислений за этот день пока нет." : "Квартира пустует."}</p>`}`;
  }

  function render({ center = false } = {}) {
    if (!data) return;
    const viewport = root.querySelector(".pc-scroll");
    const oldScroll = { left: viewport.scrollLeft, top: viewport.scrollTop };
    if (!findSelection()) {
      const apartment = data.objects.flatMap((object) => object.apartments)[0];
      selected = apartment ? { apartment: apartment.id, date: data.dates.includes(data.today) && data.today.startsWith(month) ? data.today : `${month}-01` } : null;
    }
    const dayHeader = (day) => `<th scope="col" class="${day === data.today ? "pc-today" : ""}"><span>${day === data.today ? "Сегодня" : dateValue(day).toLocaleDateString("ru-RU", { month: "short", timeZone: "UTC" })}</span><strong>${day.slice(8)}</strong><small>${dateValue(day).toLocaleDateString("ru-RU", { weekday: "short", timeZone: "UTC" })}</small></th>`;
    viewport.innerHTML = `<table class="pc-grid"><caption class="pc-sr-only">Календарь оплат, ${shortDate(data.start)} — ${shortDate(data.end)}. Стрелки на клавиатуре переключают день и квартиру.</caption><thead><tr><th scope="col" class="pc-frozen">Объект / квартира</th>${data.dates.map(dayHeader).join("")}</tr></thead><tbody>
      ${data.objects.map((object) => `<tr class="pc-object"><th scope="row" class="pc-frozen"><button type="button" data-object="${object.id}" aria-expanded="${!collapsed.has(object.id)}">${collapsed.has(object.id) ? "▸" : "▾"} ${escapeHtml(object.name)}${object.active ? "" : " · архив"}</button></th><td colspan="${data.dates.length}">${!object.apartments.length ? "В объекте пока нет квартир" : ""}</td></tr>
        ${collapsed.has(object.id) ? "" : object.apartments.map((apartment) => `<tr data-row="${apartment.id}"><th scope="row" class="pc-frozen">${escapeHtml(apartment.name)}${apartment.active ? "" : '<small>Архив</small>'}</th>${apartment.days.map((day) => {
          const active = selected?.apartment === apartment.id && selected?.date === day.date;
          const tenant = apartment.leases.filter((lease) => day.lease_ids.includes(lease.id)).map((lease) => lease.tenant).join(", ");
          const description = `${object.name}, ${apartment.name}, ${shortDate(day.date)}: ${label(day.status)}${tenant ? `, ${tenant}` : ""}${day.move_in ? ", заезд" : ""}${day.move_out ? ", последний день проживания" : ""}`;
          return `<td class="${day.date === data.today ? "pc-today" : ""}"><button type="button" class="pc-day pc-${day.status}${day.date > data.today ? " pc-future" : ""}" data-apartment="${apartment.id}" data-date="${day.date}" aria-label="${escapeHtml(description)}" title="${escapeHtml(description)}" aria-pressed="${active}" tabindex="${active ? "0" : "-1"}"><span aria-hidden="true">${symbols[day.status]}</span>${day.move_in || day.move_out ? '<i class="pc-event" aria-hidden="true"></i>' : ""}</button></td>`;
        }).join("")}</tr>`).join("")}`).join("")}
      </tbody></table>`;
    root.querySelector(".pc-legend").innerHTML = Object.keys(symbols).map((status) => `<span><i class="pc-swatch pc-${status}" aria-hidden="true">${symbols[status]}</i>${escapeHtml(label(status))}</span>`).join("");
    root.querySelector(".pc-feedback").textContent = data.objects.length ? `${shortDate(data.start)} — ${shortDate(data.end)} · Прокручивайте дни влево и вправо` : "Объектов пока нет. Добавленные объекты появятся здесь автоматически.";
    details();
    if (center) {
      const day = data.today.startsWith(month) ? data.today : `${month}-01`;
      const cell = viewport.querySelector(`[data-date="${day}"]`);
      if (cell) viewport.scrollLeft = Math.max(0, cell.closest("td").offsetLeft - viewport.clientWidth * 0.55);
    } else {
      viewport.scrollLeft = oldScroll.left;
      viewport.scrollTop = oldScroll.top;
    }
  }

  async function load(center = false) {
    if (root.hidden) return;
    const token = ++generation;
    const feedback = root.querySelector(".pc-feedback");
    feedback.textContent = "Загружаю календарь…";
    root.querySelector(".pc-scroll").setAttribute("aria-busy", "true");
    const first = `${shiftMonth(month, -1)}-01`;
    const following = dateValue(`${shiftMonth(month, 2)}-01`);
    following.setUTCDate(0);
    try {
      const response = await api(`/api/utilities/calendar?start=${first}&end=${iso(following)}&mode=${mode}`);
      if (token !== generation || root.hidden) return;
      data = response;
      render({ center });
    } catch (error) {
      if (token !== generation || root.hidden) return;
      data = null;
      root.querySelector(".pc-scroll").innerHTML = "";
      root.querySelector(".pc-detail").innerHTML = "";
      root.querySelector(".pc-legend").innerHTML = "";
      feedback.textContent = `Не удалось загрузить календарь: ${error.message}. Нажмите «Обновить».`;
    } finally {
      if (token === generation && !root.hidden) root.querySelector(".pc-scroll").setAttribute("aria-busy", "false");
    }
  }

  function selectCell(button, reveal = false) {
    selected = { apartment: Number(button.dataset.apartment), date: button.dataset.date };
    root.querySelectorAll(".pc-day").forEach((cell) => {
      cell.setAttribute("aria-pressed", String(cell === button));
      cell.tabIndex = cell === button ? 0 : -1;
    });
    details();
    if (reveal && window.matchMedia("(max-width: 700px)").matches) {
      root.querySelector(".pc-detail").scrollIntoView({ block: "start" });
    }
  }

  root.addEventListener("click", (event) => {
    if (event.target === root) { close(); return; }
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.date) { selectCell(button, true); return; }
    if (button.dataset.object) {
      const id = Number(button.dataset.object);
      collapsed.has(id) ? collapsed.delete(id) : collapsed.add(id);
      render();
      root.querySelector(`[data-object="${id}"]`)?.focus();
      return;
    }
    if (button.dataset.mode) {
      mode = button.dataset.mode;
      root.querySelectorAll("[data-mode]").forEach((item) => item.setAttribute("aria-pressed", String(item.dataset.mode === mode)));
      load();
      return;
    }
    const action = button.dataset.action;
    if (action === "close") close();
    if (action === "refresh") load();
    if (["previous", "next", "today"].includes(action)) {
      month = action === "today" ? today().slice(0, 7) : shiftMonth(month, action === "previous" ? -1 : 1);
      root.querySelector("[data-month]").value = month;
      load(true);
    }
  });
  root.addEventListener("change", (event) => {
    if (!event.target.matches("[data-month]")) return;
    if (!event.target.validity.valid || !event.target.value) { event.target.value = month; return; }
    month = event.target.value;
    load(true);
  });
  root.addEventListener("keydown", (event) => {
    const cell = event.target.closest(".pc-day");
    if (!cell || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const rows = [...root.querySelectorAll("tr[data-row]")];
    const row = cell.closest("tr");
    const rowIndex = rows.indexOf(row);
    const dayIndex = [...row.querySelectorAll(".pc-day")].indexOf(cell);
    const rowStep = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
    const dayStep = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    const next = rows[rowIndex + rowStep]?.querySelectorAll(".pc-day")[dayIndex + dayStep];
    if (next) { selectCell(next); next.focus(); next.scrollIntoView({ block: "nearest", inline: "center" }); }
  });

  return {
    open() {
      month = month || today().slice(0, 7);
      shell();
      root.hidden = false;
      document.body.classList.add("payment-calendar-open");
      openModal(root, close);
      load(true);
    },
    close,
    refreshIfOpen() { if (!root.hidden) load(); },
  };
}
