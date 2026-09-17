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
    entries: [{ id: "utility:1", lease_id: 11, title: mode === "rent" ? "Аренда" : "Вода", kind: "usage", start: "2026-09-15", end: "2026-09-18", amount: 420, paid: 100, debt: 320, due_date: "2026-09-20", status: "partial", forecast: false }],
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
  await page.route("**/api/utilities/calendar?**", (route) => route.fulfill({ json: calendarFixture(route.request().url()) }));
  await page.getByRole("button", { name: "Календарь оплат", exact: true }).click();
  await expect(page.locator(".pc-scroll")).toHaveAttribute("aria-busy", "false");
  await page.locator("[data-month]").fill("2026-09");
  await expect(page.locator('.pc-day[data-date="2026-09-16"]').first()).toBeAttached();
}

test("календарь показывает динамические объекты, проживание и суммы счёта", async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 1100 });
  await openCalendar(page);
  const dialog = page.getByRole("dialog", { name: "Календарь оплат" });
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
  await expect(page.locator('.pc-day[data-date="2026-11-30"]').first()).toBeAttached();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(page.locator("#openPaymentCalendarBtn")).toBeFocused();
});

test("мобильный календарь прокручивается внутри окна и поддерживает светлую тему", async ({ page }) => {
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
  await expect(page.locator(".pc-feedback")).toContainText("Проверочная ошибка");
  await expect(page.locator(".pc-grid")).toHaveCount(0);
  await page.unroute("**/api/utilities/calendar?**");
  await page.route("**/api/utilities/calendar?**", (route) => route.fulfill({ json: calendarFixture(route.request().url()) }));
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.locator(".pc-grid")).toBeVisible();
});
