export function createPaymentCalendar({ api, today, escapeHtml, money }) {
  const root = document.getElementById("paymentCalendarModal");
  const symbols = { paid: "✓", issued: "●", partial: "◐", overdue: "!", deferred: "◷", draft: "◇", vacant: "", unbilled: "—", incomplete: "…", conflict: "?", recent: "", to_bill: "!", gap: "!" };
  let data = null;
  let mode = "utility";
  let month = "";
  let selected = null;
  let generation = 0;
  let zoom = 100;
  let origin = 0;
  let limit = 0;
  let returnFocus = null;
  let frame = 0;
  let summary = null;
  let summaryError = false;
  const pages = new Map();
  const pending = new Map();
  const failures = new Set();
  const PAGE_DAYS = 120;
  const CACHE_PAGES = 8;
  const DAY_MS = 86400000;
  const ordinal = (value) => Math.floor(Date.parse(`${value}T00:00:00Z`) / DAY_MS);
  const dayString = (day) => new Date(day * DAY_MS).toISOString().slice(0, 10);
  const dayWidth = () => 42 * zoom / 100;
  const frozenWidth = () => window.matchMedia("(max-width: 700px)").matches ? 132 : 184;
  const viewport = () => root.querySelector(".pc-scroll");
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
    pages.clear();
    pending.clear();
    failures.clear();
    selected = null;
    root.closest("#utilities").classList.remove("pc-open");
    if (returnFocus?.isConnected) returnFocus.focus();
  }

  function shell() {
    root.innerHTML = `<div class="payment-calendar">
      <div class="pc-heading"><div><p class="eyebrow">Коммунальные услуги</p><h3>Календарь оплат</h3></div><button type="button" data-action="close" aria-label="Закрыть календарь">✕</button></div>
      <div class="pc-toolbar">
        <div class="pc-modes" aria-label="Вид начислений"><button type="button" data-mode="rent" aria-pressed="${mode === "rent"}">Аренда</button><button type="button" data-mode="utility" aria-pressed="${mode === "utility"}">Коммуналка</button></div>
        <div class="pc-dates"><button type="button" data-action="previous" aria-label="Предыдущий месяц">‹</button><label class="pc-month-label">Перейти к<input type="month" data-month value="${month}" min="1900-01" max="9998-12"></label><button type="button" data-action="next" aria-label="Следующий месяц">›</button><button type="button" data-action="today">Сегодня</button></div>
        <label class="pc-zoom">Масштаб <output>100%</output><input type="range" min="10" max="200" step="5" value="100" aria-label="Масштаб календаря"></label><button type="button" data-action="reset-zoom">100%</button>
        <button type="button" data-action="refresh">Обновить</button>
      </div>
      <p class="pc-explanation">Колесо над шкалой — масштаб у курсора · Shift + колесо — перемещение. Полосы — периоды счетов; суммы за весь период.</p>
      <div class="pc-feedback" role="status" aria-live="polite"></div>
      <div class="pc-scroll" role="region" aria-label="Дни и квартиры: прокрутка влево и вправо" tabindex="0"><div class="pc-canvas"></div></div>
      <div class="pc-legend" aria-label="Статусы"></div>
      <section class="pc-detail" aria-label="Выбранный день" aria-live="polite"></section>
      <div class="pc-tooltip" role="tooltip" hidden></div>
    </div>`;
    viewport().addEventListener("scroll", () => {
      extend();
      schedule();
      hideTooltip();
    });
    viewport().addEventListener("wheel", (event) => {
      if (event.ctrlKey || event.metaKey) return;
      if (event.shiftKey) {
        event.preventDefault();
        viewport().scrollLeft += event.deltaY || event.deltaX;
      } else if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
        event.preventDefault();
        const step = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
        setZoom(zoom * Math.exp(-step * 0.0025), event.clientX - viewport().getBoundingClientRect().left - frozenWidth());
      }
    }, { passive: false });
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
      ${day.status === "gap" ? '<p class="pc-warning">Есть более поздний оплаченный период этого договора. Проверьте пропущенные начисления.</p>' : ""}
      ${day.status === "to_bill" ? '<p class="pc-warning">Прошло больше 31 дня — пора выставить коммуналку.</p>' : ""}
      ${day.missing_services.length ? `<p class="pc-warning">Нет выставленного расчёта: ${day.missing_services.map(escapeHtml).join(", ")}.</p>` : ""}
      <div class="pc-totals"><span>Начислено<strong>${money(total("amount"))}</strong></span><span>Оплачено<strong>${money(total("paid"))}</strong></span><span>Остаток<strong>${money(total("debt"))}</strong></span></div>
      <p class="pc-explanation">По выставленным счетам, покрывающим выбранный день. Черновики и авансы показаны отдельно ниже.</p>
      ${entries.length ? `<div class="pc-entry-list">${entries.map((entry) => `<article class="pc-entry">
        <div><strong>${escapeHtml(entry.title)}</strong> ${badge(entry.status)}${entry.forecast ? '<span class="pc-estimated">Расчётные показания</span>' : ""}<small>${period(entry)}</small>${entry.due_date ? `<small>Срок оплаты: ${shortDate(entry.due_date)}${entry.deferred_until ? ` · отсрочка до ${shortDate(entry.deferred_until)}` : ""}</small>` : ""}</div>
        <div class="pc-entry-money"><span>${entry.status === "draft" ? "В черновике" : "Начислено"}: ${money(entry.amount)}</span><span>Оплачено: ${money(entry.paid)}</span><strong>${entry.status === "draft" ? "К выставлению" : "Остаток"}: ${money(entry.debt)}</strong></div>
        ${entry.kind === "rent" ? `<small class="pc-rent-parts">ИП: ${money(entry.ip_paid)} из ${money(entry.ip_due)} · Личный перевод: ${money(entry.personal_paid)} из ${money(entry.personal_due)}</small>` : ""}
      </article>`).join("")}</div>` : `<p class="pc-empty">${leases.length ? "Начислений за этот день пока нет." : "Квартира пустует."}</p>`}`;
  }

  function range() {
    const left = origin + viewport().scrollLeft / dayWidth();
    const right = left + Math.max(1, viewport().clientWidth - frozenWidth()) / dayWidth();
    return { left, right, start: Math.max(origin, Math.floor(left) - 14), end: Math.min(limit, Math.ceil(right) + 14) };
  }

  function sizeCanvas() {
    root.style.setProperty("--pc-day-width", `${dayWidth()}px`);
    root.style.setProperty("--pc-label-width", `${frozenWidth()}px`);
    root.querySelector(".pc-canvas").style.width = `${frozenWidth() + (limit - origin) * dayWidth()}px`;
    root.classList.toggle("pc-overview", zoom < 80);
  }

  function extend() {
    const view = viewport();
    if (!view || !view.clientWidth) return;
    if (view.scrollLeft < 60 * dayWidth()) {
      origin -= 240;
      sizeCanvas();
      view.scrollLeft += 240 * dayWidth();
    } else if (view.scrollWidth - view.clientWidth - view.scrollLeft < 60 * dayWidth()) {
      limit += 240;
      sizeCanvas();
    }
  }

  function schedule() {
    if (frame || root.hidden) return;
    frame = requestAnimationFrame(() => { frame = 0; if (!root.hidden) render(); });
  }

  function mergedData() {
    const objects = new Map();
    let latest = null;
    for (const payload of pages.values()) {
      latest = payload;
      for (const object of payload.objects) {
        if (!objects.has(object.id)) objects.set(object.id, { ...object, apartments: new Map() });
        const rows = objects.get(object.id).apartments;
        for (const row of object.apartments) {
          if (!rows.has(row.id)) rows.set(row.id, { ...row, days: new Map(), leases: new Map(), entries: new Map(), periods: new Map() });
          const merged = rows.get(row.id);
          for (const key of ["days", "leases", "entries", "periods"]) for (const item of row[key] || []) merged[key].set(key === "days" ? item.date : item.id, item);
        }
      }
    }
    return latest ? { ...latest, objects: [...objects.values()].map((object) => ({ ...object, apartments: [...object.apartments.values()].map((row) => ({ ...row, days: [...row.days.values()], leases: [...row.leases.values()], entries: [...row.entries.values()], periods: [...row.periods.values()] })) })) } : null;
  }

  function hiddenIssue(apartment, visible) {
    const issues = summary?.apartments.find((item) => item.apartment_id === apartment)?.issues || [];
    return issues.find((issue) => ordinal(issue.start) < Math.floor(visible.left) || ordinal(issue.end) > Math.ceil(visible.right));
  }

  function position(day) { return frozenWidth() + (day - origin) * dayWidth(); }

  function periodDescription(period, apartment) {
    const tenant = apartment.leases.find((lease) => lease.id === period.lease_id)?.tenant || "Без договора";
    return `${period.title} · ${tenant}\n${shortDate(period.start)} → ${shortDate(period.end)} · ${period.days} дн.\n${label(period.status)} · ${money(period.amount)}\nОплачено: ${money(period.paid)} · остаток: ${money(period.debt)}`;
  }

  function render() {
    sizeCanvas();
    const visible = range();
    requestPages(visible);
    data = mergedData();
    const feedback = root.querySelector(".pc-feedback");
    feedback.textContent = failures.size ? "Не удалось загрузить часть истории. Нажмите «Обновить»." : summaryError ? "История доступна; сводку скрытых просрочек загрузить не удалось. Нажмите «Обновить»." : pending.size ? "Подгружаю историю…" : `${shortDate(dayString(Math.floor(visible.left)))} — ${shortDate(dayString(Math.ceil(visible.right)))} · История подгружается при прокрутке`;
    viewport().setAttribute("aria-busy", String(pending.size > 0));
    if (!data) { root.querySelector(".pc-canvas").innerHTML = '<p class="pc-empty">Загружаю объекты и квартиры…</p>'; details(); return; }
    if (!selected) {
      const row = data.objects.flatMap((object) => object.apartments)[0];
      if (row) selected = { apartment: row.id, date: data.today };
    }
    const active = document.activeElement?.matches(".pc-day") ? { ...document.activeElement.dataset } : null;
    const ordinals = Array.from({ length: visible.end - visible.start }, (_, i) => visible.start + i);
    const dates = ordinals.map(dayString);
    const step = zoom >= 80 ? 1 : zoom >= 30 ? 7 : 30;
    const headers = ordinals.filter((day) => day % step === 0).map((day) => {
      const text = dayString(day);
      return `<div class="pc-date-label" style="left:${position(day)}px;width:${dayWidth() * step}px"><span>${text === data.today ? "Сегодня" : dateValue(text).toLocaleDateString("ru-RU", { month: "short", year: zoom < 80 ? "numeric" : undefined, timeZone: "UTC" })}</span><strong>${text.slice(8)}</strong>${zoom >= 80 ? `<small>${dateValue(text).toLocaleDateString("ru-RU", { weekday: "short", timeZone: "UTC" })}</small>` : ""}</div>`;
    }).join("");
    const todayOrdinal = ordinal(data.today);
    const todayLine = todayOrdinal >= visible.start && todayOrdinal < visible.end ? `<div class="pc-today-line" style="left:${position(todayOrdinal) + dayWidth() / 2}px" aria-hidden="true"></div>` : "";
    const rows = data.objects.map((object) => `<div class="pc-object pc-row"><div class="pc-frozen"><button type="button" data-object="${object.id}" aria-expanded="${!collapsed.has(object.id)}">${collapsed.has(object.id) ? "▸" : "▾"} ${escapeHtml(object.name)}${object.active ? "" : " · архив"}</button></div></div>${collapsed.has(object.id) ? "" : object.apartments.map((apartment) => {
      const dayMap = new Map(apartment.days.map((day) => [day.date, day]));
      const issue = hiddenIssue(apartment.id, visible);
      const laneEnds = [];
      const bars = apartment.periods.filter((period) => ordinal(period.start) < visible.end && ordinal(period.end) > visible.start).sort((a, b) => a.start.localeCompare(b.start) || a.id.localeCompare(b.id)).map((period) => {
        let lane = laneEnds.findIndex((end) => end <= period.start);
        if (lane < 0) lane = laneEnds.length;
        laneEnds[lane] = period.end;
        const start = Math.max(ordinal(period.start), visible.start);
        const end = Math.min(ordinal(period.end), visible.end);
        return `<button type="button" class="pc-period pc-${period.status}" data-period="${escapeHtml(period.id)}" data-apartment="${apartment.id}" style="left:${position(start)}px;width:${Math.max(2, (end-start) * dayWidth() - 2)}px;top:${50 + lane * 25}px" aria-label="${escapeHtml(periodDescription(period, apartment))}">${escapeHtml(period.title)} · ${escapeHtml(apartment.leases.find((lease) => lease.id === period.lease_id)?.tenant || "Без договора")}</button>`;
      }).join("");
      return `<div class="pc-row pc-apartment" data-row="${apartment.id}" style="height:${54 + laneEnds.length * 25}px"><div class="pc-frozen"><span>${escapeHtml(apartment.name)}${apartment.active ? "" : '<small>Архив</small>'}</span>${issue ? `<button type="button" class="pc-issue" data-issue="${apartment.id}" aria-label="Перейти к скрытой просрочке: ${escapeHtml(apartment.name)}">!</button>` : ""}</div>${dates.map((text, i) => {
        const day = dayMap.get(text);
        if (!day) return `<span class="pc-day pc-loading" style="left:${position(ordinals[i])}px" aria-label="Данные загружаются"></span>`;
        const isSelected = selected?.apartment === apartment.id && selected?.date === text;
        const tenant = apartment.leases.filter((lease) => day.lease_ids.includes(lease.id)).map((lease) => lease.tenant).join(", ");
        const description = `${object.name}, ${apartment.name}, ${shortDate(text)}: ${label(day.status)}${tenant ? `, ${tenant}` : ""}${day.move_in ? ", заезд" : ""}${day.move_out ? ", последний день проживания" : ""}`;
        return `<button type="button" class="pc-day pc-${day.status}${text > data.today ? " pc-future" : ""}" style="left:${position(ordinals[i])}px" data-apartment="${apartment.id}" data-date="${text}" aria-label="${escapeHtml(description)}" title="${escapeHtml(description)}" aria-pressed="${isSelected}" tabindex="${isSelected ? "0" : "-1"}"><span aria-hidden="true">${symbols[day.status] || ""}</span>${day.move_in || day.move_out ? '<i class="pc-event" aria-hidden="true"></i>' : ""}</button>`;
      }).join("")}${bars}</div>`;
    }).join("")}`).join("");
    root.querySelector(".pc-canvas").innerHTML = `<div class="pc-row pc-header"><div class="pc-frozen">Объект / квартира</div>${headers}</div>${rows}${todayLine}`;
    if (active) root.querySelector(`.pc-day[data-apartment="${active.apartment}"][data-date="${active.date}"]`)?.focus({ preventScroll: true });
    root.querySelector(".pc-legend").innerHTML = Object.keys(symbols).map((status) => `<span><i class="pc-swatch pc-${status}" aria-hidden="true">${symbols[status]}</i>${escapeHtml(label(status))}</span>`).join("");
    details();
    if (!data.objects.length) feedback.textContent = "Объектов пока нет. Новые объекты появятся автоматически.";
  }

  function requestPages(visible) {
    const needed = new Set();
    for (let page = Math.floor(visible.start / PAGE_DAYS); page <= Math.floor((visible.end - 1) / PAGE_DAYS); page++) needed.add(page);
    for (const page of needed) {
      if (pages.has(page) || pending.has(page) || failures.has(page)) continue;
      const token = generation;
      const promise = api(`/api/utilities/calendar?start=${dayString(page * PAGE_DAYS)}&end=${dayString((page + 1) * PAGE_DAYS - 1)}&mode=${mode}`)
        .then((response) => { if (token === generation && !root.hidden) pages.set(page, response); })
        .catch(() => { if (token === generation && !root.hidden) failures.add(page); })
        .finally(() => { if (token === generation) { pending.delete(page); schedule(); } });
      pending.set(page, promise);
    }
    for (const key of pages.keys()) if (pages.size > CACHE_PAGES && !needed.has(key)) pages.delete(key);
    root.dataset.cachedPages = String(pages.size);
  }

  function goTo(date) {
    const day = ordinal(date);
    if (day < origin + 80 || day > limit - 80) { origin = day - 360; limit = day + 360; }
    sizeCanvas();
    viewport().scrollLeft = (day - origin) * dayWidth() - Math.max(0, viewport().clientWidth - frozenWidth()) / 2;
    month = date.slice(0, 7);
    root.querySelector("[data-month]").value = month;
    schedule();
  }

  function setZoom(value, x = (viewport().clientWidth - frozenWidth()) / 2) {
    x = Math.max(0, Math.min(x, viewport().clientWidth - frozenWidth()));
    const day = origin + (viewport().scrollLeft + x) / dayWidth();
    zoom = Math.max(10, Math.min(200, value));
    sizeCanvas();
    viewport().scrollLeft = (day - origin) * dayWidth() - x;
    root.querySelector(".pc-zoom input").value = String(zoom);
    root.querySelector(".pc-zoom output").textContent = `${Math.round(zoom)}%`;
    extend();
    schedule();
    hideTooltip();
  }

  function load(center = false) {
    generation++;
    pages.clear(); pending.clear(); failures.clear(); summary = null; summaryError = false; data = null;
    if (center) goTo(today());
    const token = generation;
    api(`/api/utilities/calendar/summary?mode=${mode}`)
      .then((response) => { if (token === generation && !root.hidden) summary = response; })
      .catch(() => { if (token === generation) summaryError = true; })
      .finally(() => { if (token === generation) schedule(); });
    schedule();
  }

  function hideTooltip() { const tip = root.querySelector(".pc-tooltip"); if (tip) tip.hidden = true; }
  function showPeriod(button) {
    const apartment = data?.objects.flatMap((object) => object.apartments).find((row) => row.id === Number(button.dataset.apartment));
    const period = apartment?.periods.find((item) => item.id === button.dataset.period);
    if (!period) return;
    const tip = root.querySelector(".pc-tooltip");
    tip.textContent = periodDescription(period, apartment);
    tip.hidden = false;
    const box = button.getBoundingClientRect();
    tip.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - tip.offsetWidth - 8))}px`;
    tip.style.top = `${Math.max(8, Math.min(box.bottom + 8, window.innerHeight - tip.offsetHeight - 8))}px`;
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
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.period) { showPeriod(button); return; }
    if (button.dataset.issue) {
      const issue = hiddenIssue(Number(button.dataset.issue), range());
      if (issue) { selected = { apartment: Number(button.dataset.issue), date: issue.start }; goTo(issue.start); }
      return;
    }
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
    if (action === "reset-zoom") setZoom(100);
    if (["previous", "next", "today"].includes(action)) {
      month = action === "today" ? today().slice(0, 7) : shiftMonth(month, action === "previous" ? -1 : 1);
      root.querySelector("[data-month]").value = month;
      goTo(action === "today" ? today() : `${month}-01`);
    }
  });
  root.addEventListener("change", (event) => {
    if (!event.target.matches("[data-month]")) return;
    if (!event.target.validity.valid || !event.target.value) { event.target.value = month; return; }
    month = event.target.value;
    goTo(`${month}-01`);
  });
  root.addEventListener("input", (event) => {
    if (event.target.matches(".pc-zoom input")) setZoom(Number(event.target.value));
  });
  root.addEventListener("pointerover", (event) => { const button = event.target.closest(".pc-period"); if (button) showPeriod(button); });
  root.addEventListener("pointerout", (event) => { if (event.target.closest(".pc-period")) hideTooltip(); });
  root.addEventListener("focusin", (event) => { if (event.target.matches(".pc-period")) showPeriod(event.target); });
  root.addEventListener("focusout", hideTooltip);
  window.addEventListener("resize", () => { if (!root.hidden) schedule(); });
  root.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { event.preventDefault(); close(); return; }
    const cell = event.target.closest(".pc-day");
    if (!cell || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const rows = [...root.querySelectorAll("[data-row]")];
    const row = cell.closest("[data-row]");
    const rowIndex = rows.indexOf(row);
    const dayIndex = [...row.querySelectorAll(".pc-day")].indexOf(cell);
    const rowStep = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
    const dayStep = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    const next = rows[rowIndex + rowStep]?.querySelectorAll(".pc-day")[dayIndex + dayStep];
    if (next) { selectCell(next); next.focus(); next.scrollIntoView({ block: "nearest", inline: "center" }); }
  });

  return {
    open() {
      returnFocus = document.activeElement;
      month = month || today().slice(0, 7);
      origin = ordinal(today()) - 360;
      limit = ordinal(today()) + 360;
      shell();
      root.hidden = false;
      root.closest("#utilities").classList.add("pc-open");
      load(true);
      setZoom(zoom);
      root.querySelector('[data-action="close"]').focus({ preventScroll: true });
      root.scrollIntoView({ block: "start" });
    },
    close,
    refreshIfOpen() { if (!root.hidden) load(); },
  };
}
