const { test, expect } = require("@playwright/test");

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.waitForFunction(() => typeof groupedDraftRows === "function");
});

test("непрерывные отрезки объединены, разрыв и другой договор сохранены", async ({ page }) => {
  const rows = await page.evaluate(() => {
    const line = (lease, start, end, amount) => ({ apartment_id: 3, apartment: "ЧД3", lease_id: lease,
      tenant: `Жилец ${lease}`, period_start: start, period_end: end, total_amount: amount, status: "draft" });
    return groupedDraftRows({ bills: [{ service_id: 1, service: "Электричество", lines: [
      line(1, "2026-10-17", "2026-10-20", 10), line(1, "2026-10-08", "2026-10-17", 20),
      line(1, "2026-10-25", "2026-10-28", 30), line(2, "2026-10-20", "2026-10-25", 40),
    ] }] }).map((row) => ({ fact: row.fact, periods: [...row.periods], tenant: row.tenant }));
  });
  expect(rows).toHaveLength(2);
  expect(rows[0].fact).toBe(60);
  expect(rows[0].periods).toHaveLength(2);
  expect(rows[0].periods[0]).toContain("8");
  expect(rows[0].periods[0]).toContain("20");
  expect(rows[1].fact).toBe(40);
  expect(rows[1].periods).toHaveLength(1);
});

test("даты различают выставленные начисления и черновики по услуге и реальным отрезкам", async ({ page }) => {
  const result = await page.evaluate(() => {
    state.bootstrap = { services: [{ id: 1, object_id: 1 }, { id: 2, object_id: 1 }, { id: 3, object_id: 2 }] };
    const bill = (service_id, service, status, extra = {}) => ({ service_id, service, status,
      bill_type: "utility", period_start: "2026-10-01", period_end: "2026-10-28",
      lines: [{ status, period_start: "2026-10-01", period_end: "2026-10-17" }], ...extra });
    state.utilityBills = [bill(1, "Электричество", "issued"), bill(2, "Вода", "draft"),
      bill(1, "Отменённый", "cancelled"), bill(1, "Аванс", "issued", { bill_type: "advance" }),
      bill(3, "Другой дом", "issued")];
    return { label: utilityReadingDateLabel("object:1", "2026-10-17"),
      later: utilityReadingDateLabel("object:1", "2026-10-28"),
      start: utilityReadingDateLabel("object:1", "2026-10-01"),
      service: utilityReadingDateLabel("service:1", "2026-10-17") };
  });
  expect(result.label).toContain("Электричество: есть выставленные начисления");
  expect(result.label).toContain("Вода: черновик");
  expect(result.label).not.toMatch(/Отменённый|Аванс|Другой дом/);
  expect(result.later).not.toContain("начисления");
  expect(result.start).not.toContain("начисления");
  expect(result.service).not.toContain("Вода");
});

test("разные услуги и новый аванс имеют отдельные периоды без двойной подписи", async ({ page }) => {
  const periods = await page.evaluate(() => groupedUtilityPeriods([
    { bill: { service_id: 1, service: "Электричество" }, line: { period_start: "2026-10-01", period_end: "2026-10-28" } },
    { bill: { service_id: 2, service: "Вода" }, line: { period_start: "2026-10-01", period_end: "2026-10-28" } },
    { bill: { service_id: 1, service: "Аванс коммуналки" }, line: { line_type: "advance", period_start: "2026-10-28", period_end: "2026-11-28" } },
  ]));
  expect(periods).toHaveLength(3);
  expect(periods[2].match(/Аванс коммуналки/g)).toHaveLength(1);
});

test("аванс предыдущего жильца не покрывает нового и не повторяется в нескольких строках владельца", async ({ page }) => {
  const result = await page.evaluate(() => {
    const line = (lease_id, owner, amount, balance) => ({ apartment_id: 3, apartment: "К3", lease_id,
      advance_owner_key: owner, total_amount: amount, advance_balance_available: balance, status: "draft" });
    return groupedDraftRows({ bills: [{ service: "Электричество", lines: [
      line(1, "3:11", 570, 600), line(2, "3:22", 70, 0), line(3, "3:11", 50, 600),
    ] }] }).map((row) => ({ fact: row.fact, advance: row.advanceImpact }));
  });
  expect(result.map((row) => row.advance)).toEqual([-570, 0, -30]);
});

test("удаление группы предупреждает о возврате оплат и обновляет экран после частичной ошибки", async ({ page }) => {
  const calls = [];
  await page.route("**/api/utility-bills/*", async (route) => {
    calls.push(new URL(route.request().url()).pathname);
    await route.fulfill({ status: calls.length === 1 ? 200 : 400, contentType: "application/json",
      body: JSON.stringify(calls.length === 1 ? { ok: true } : { detail: "Оплаченный аванс" }) });
  });
  const result = await page.evaluate(async () => {
    const original = { loadAll, confirm: window.confirm, toast };
    let question = "";
    let refreshed = 0;
    try {
      window.confirm = (text) => { question = text; return true; };
      loadAll = async () => { refreshed += 1; };
      toast = () => {};
      let error = "";
      try { await deleteUtilityGroup("68,69"); } catch (caught) { error = caught.message; }
      return { question, refreshed, error };
    } finally {
      loadAll = original.loadAll;
      window.confirm = original.confirm;
      toast = original.toast;
    }
  });
  expect(result.question).toContain("вернутся в аванс тех же жильцов");
  expect(calls).toEqual(["/api/utility-bills/68", "/api/utility-bills/69"]);
  expect(result.refreshed).toBe(1);
  expect(result.error).toBe("Оплаченный аванс");
});
