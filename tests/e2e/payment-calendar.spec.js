const { test, expect } = require("@playwright/test");

const statuses = { paid: "Оплачено", issued: "Счёт выставлен", partial: "Оплачено частично", overdue: "Просрочено", deferred: "Отсрочка", draft: "Черновик", vacant: "Пустует", unbilled: "Не начислено", incomplete: "Не всё начислено", conflict: "Проверить договоры" };

function calendarFixture(url) {
  const params = new URL(url).searchParams;
  const dates = [];
  const end = params.get("end");
  for (let cursor = new Date(`${params.get("start")}T12:00:00Z`); cursor.toISOString().slice(0, 10) <= end; cursor.setUTCDate(cursor.getUTCDate() + 1)) dates.push(cursor.toISOString().slice(0, 10));
  const mode = params.get("mode");
  const row = (id, name, type) => ({ id, name, active: true,
    leases: [{ id: 10, tenant: "Прежний жилец", start: "2026-06-26", end: "2026-09-07" }, { id: 11, tenant: "Дарья", start: "2026-09-15", end: null }],
    entries: [{ id: "utility:1", lease_id: 11, title: mode === "rent" ? "Аренда" : "Вода", kind: "usage", start: "2026-09-15", end: "2026-09-18", period_start: "2026-09-15", period_end: "2026-09-17", amount: 420, paid: 100, debt: 320, due_date: "2026-09-20", status: "partial", forecast: false }],
    periods: [{ id: `period:${id}`, lease_id: 11, title: mode === "rent" ? "Аренда" : "Вода", kind: "usage", start: "2026-09-15", end: "2026-09-18", days: 3, amount: 420, paid: 100, debt: 320, status: "partial" }],
    days: dates.map((date) => {
      const occupied = date < "2026-09-08" || date >= "2026-09-15";
      const status = type === "turnover" ? date < "2026-09-08" ? "paid" : date < "2026-09-15" ? "vacant" : date <= "2026-09-17" ? "partial" : "unbilled" : type;
      return { date, status, lease_ids: occupied ? [date < "2026-09-08" ? 10 : 11] : [], entry_ids: date >= "2026-09-15" && date <= "2026-09-17" ? ["utility:1"] : [], missing_services: [], move_in: date === "2026-09-15", move_out: date === "2026-09-07", forecast: false };
    }),
  });
  return { start: params.get("start"), end, today: "2026-09-17", mode, dates, statuses, objects: [
    { id: 1, name: "Белый дом", active: true, apartments: [row(1, "Квартира 1", "paid"), row(2, "Квартира 2", "issued"), row(3, "Квартира 3", "turnover"), row(4, "Квартира 4", "overdue")] },
    { id: 2, name: "Чёрный дом", active: true, apartments: [row(5, "Квартира 1", "paid"), row(6, "Квартира 2", "draft")] },
    { id: 42, name: "Новый корпус <Юг>", active: true, apartments: [row(77, "Студия Б", "vacant")] },
  ] };
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("PIN-код").fill(process.env.E2E_OWNER_PIN || ("12" + "98"));
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page.locator("#authOverlay")).toBeHidden();
  await expect(page.locator("#loadingOverlay")).toBeHidden();
  await page.locator('.sidebar .nav-group[data-tab="utilities"]').click();
});

async function openCalendar(page) {
  await page.route("**/api/utilities/calendar/summary?**", (route) => route.fulfill({ json: { apartments: [{ apartment_id: 4, issues: [{ start: "2024-05-05", end: "2024-06-07", status: "overdue" }] }] } }));
  await page.route("**/api/utilities/calendar?**", (route) => route.fulfill({ json: calendarFixture(route.request().url()) }));
  await page.getByRole("button", { name: "Календарь оплат", exact: true }).click();
  await expect(page.locator(".pc-scroll")).toHaveAttribute("aria-busy", "false");
  await page.locator("[data-month]").fill("2026-09");
  await expect(page.locator('.pc-day[data-date="2026-09-16"]').first()).toBeAttached();
}

test("календарь показывает динамические объекты, проживание и суммы счёта", async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 1100 });
  await openCalendar(page);
  const dialog = page.locator("#paymentCalendarModal");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('[data-object="42"]')).toHaveText("▾ Новый корпус <Юг>");
  const day = page.locator('.pc-day[data-apartment="3"][data-date="2026-09-16"]');
  await day.click();
  await expect(page.locator(".pc-detail")).toContainText("Дарья");
  await expect(page.locator(".pc-detail")).toContainText("320");
  await expect(page.locator(".pc-detail")).toContainText("15.09.2026 → 18.09.2026");
  await page.screenshot({ path: "test-results/payment-calendar-desktop.png" });
  await day.press("ArrowLeft");
  await expect(page.locator('.pc-day[data-apartment="3"][data-date="2026-09-15"]')).toBeFocused();
  await page.keyboard.press("ArrowLeft");
  await expect(page.locator(".pc-detail")).toContainText("Квартира пустует");
  const label = page.locator('[data-row="3"] .pc-frozen');
  const before = await label.boundingBox();
  await page.locator(".pc-scroll").evaluate((node) => { node.scrollLeft += 420; });
  const after = await label.boundingBox();
  expect(Math.abs(before.x - after.x)).toBeLessThan(1);
  await dialog.getByRole("button", { name: "Аренда", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Аренда", exact: true })).toHaveAttribute("aria-pressed", "true");
  await day.click();
  await expect(page.locator(".pc-detail .pc-entry")).toContainText("Аренда");
  await dialog.getByRole("button", { name: "Следующий месяц" }).click();
  await expect(page.locator("[data-month]")).toHaveValue("2026-10");
  await expect(page.locator('.pc-day[data-date="2026-10-01"]').first()).toBeAttached();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(page.locator("#openPaymentCalendarBtn")).toBeFocused();
});

test("мобильный календарь растёт по странице и поддерживает светлую тему", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => setPalette("white"));
  await openCalendar(page);
  await page.locator('.pc-day[data-apartment="3"][data-date="2026-09-16"]').click();
  const layout = await page.locator(".payment-calendar").evaluate((node) => ({ width: node.getBoundingClientRect().width, client: node.clientWidth, scroll: node.scrollWidth }));
  expect(layout.width).toBeLessThanOrEqual(374);
  expect(layout.scroll).toBeLessThanOrEqual(layout.client + 1);
  await expect(page.locator("body")).toHaveAttribute("data-palette", "white");
  await expect(page.locator(".pc-detail")).toContainText("Дарья");
  await expect(page.locator(".pc-detail-heading")).toBeInViewport();
  const colors = await page.locator('.pc-day[data-date="2026-09-16"]').evaluateAll((nodes) => nodes.map((node) => getComputedStyle(node).backgroundColor));
  expect(new Set(colors).size).toBeGreaterThan(3);
  await page.screenshot({ path: "test-results/payment-calendar-mobile.png" });
  await page.locator('[data-object="42"]').click();
  await expect(page.locator('[data-row="77"]')).toHaveCount(0);
});

test("ошибка загрузки не оставляет устаревшие суммы и позволяет повторить запрос", async ({ page }) => {
  await openCalendar(page);
  await page.route("**/api/utilities/calendar?**", (route) => route.fulfill({ status: 503, json: { detail: "Проверочная ошибка" } }));
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.locator(".pc-feedback")).toContainText("Не удалось загрузить часть истории");
  await expect(page.locator(".pc-apartment")).toHaveCount(0);
  await page.unroute("**/api/utilities/calendar?**");
  await page.route("**/api/utilities/calendar?**", (route) => route.fulfill({ json: calendarFixture(route.request().url()) }));
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.locator(".pc-apartment").first()).toBeVisible();
});

test("все квартиры на странице, точные полосы и переход к старой скрытой просрочке", async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 900 });
  await openCalendar(page);
  const scroll = page.locator(".pc-scroll");
  const sizes = await scroll.evaluate((node) => ({ height: node.clientHeight, scroll: node.scrollHeight }));
  expect(sizes.scroll).toBeLessThanOrEqual(sizes.height + 1);
  await expect(page.locator('[data-row="77"]')).toBeAttached();
  const band = page.locator('.pc-period[data-apartment="3"]');
  await band.hover();
  await expect(page.getByRole("tooltip")).toContainText("15.09.2026 → 18.09.2026 · 3 дн.");
  await expect(page.getByRole("tooltip")).toContainText("Дарья");
  await expect(page.getByRole("tooltip")).toContainText("420");
  await page.locator('[data-issue="4"]').click();
  await expect(page.locator('.pc-day[data-apartment="4"][data-date="2024-05-05"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("[data-month]")).toHaveValue("2024-05");
  await expect(page.locator(".pc-detail")).toContainText("05.05.2024");
});

test("бесконечная шкала подгружает край без скачка и ограничивает кеш и DOM", async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 900 });
  const requests = [];
  page.on("request", (request) => { if (request.url().includes("/calendar?")) requests.push(request.url()); });
  await openCalendar(page);
  const scroll = page.locator(".pc-scroll");
  const expectedDate = await scroll.evaluate((node) => {
    const cell = node.querySelector(".pc-day[data-date]");
    const width = parseFloat(getComputedStyle(document.getElementById("paymentCalendarModal")).getPropertyValue("--pc-day-width"));
    const origin = Date.parse(`${cell.dataset.date}T00:00:00Z`) / 86400000 - (parseFloat(cell.style.left) - 184) / width;
    node.scrollLeft = 0;
    return new Date(origin * 86400000).toISOString().slice(0, 10);
  });
  await expect(page.locator(`.pc-day[data-apartment="1"][data-date="${expectedDate}"]`)).toBeAttached();
  await expect.poll(() => scroll.evaluate((node) => node.scrollLeft)).toBeGreaterThan(9000);
  const first = await page.locator(`.pc-day[data-apartment="1"][data-date="${expectedDate}"]`).boundingBox();
  const label = await page.locator('[data-row="1"] .pc-frozen').boundingBox();
  expect(Math.abs(first.x - label.x - label.width)).toBeLessThan(5);
  expect(requests.length).toBeGreaterThan(1);
  for (let year = 2010; year < 2021; year++) {
    await page.locator("[data-month]").fill(`${year}-09`);
    await expect(page.locator(`.pc-day[data-apartment="1"][data-date="${year}-09-01"]`)).toBeAttached();
  }
  expect(Number(await page.locator("#paymentCalendarModal").getAttribute("data-cached-pages"))).toBeLessThanOrEqual(8);
  expect(await page.locator(".pc-day").count()).toBeLessThan(700);
  await expect(page.locator(".pc-today-line")).toHaveCount(0);
});

test("масштаб колёсиком держит дату под курсором, ползунок и сброс работают", async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 900 });
  await openCalendar(page);
  const cell = page.locator('.pc-day[data-apartment="1"][data-date="2026-09-01"]');
  await cell.scrollIntoViewIfNeeded();
  const before = await cell.boundingBox();
  const anchor = before.x + before.width / 2;
  await page.mouse.move(anchor, before.y + 15);
  await page.mouse.wheel(0, 120);
  await expect(page.locator(".pc-zoom output")).not.toHaveText("100%");
  const after = await cell.boundingBox();
  expect(Math.abs(after.x + after.width / 2 - anchor)).toBeLessThan(2);
  await page.locator(".pc-zoom input").fill("10");
  await expect(page.locator(".pc-zoom output")).toHaveText("10%");
  await expect(page.locator("#paymentCalendarModal")).toHaveClass(/pc-overview/);
  expect(await page.locator(".pc-day").count()).toBeLessThan(3000);
  await page.locator('[data-action="reset-zoom"]').click();
  await expect(page.locator(".pc-zoom output")).toHaveText("100%");
  await expect(page.locator("#paymentCalendarModal")).not.toHaveClass(/pc-overview/);
});
