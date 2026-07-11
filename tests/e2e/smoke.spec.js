const { test, expect } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;


test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("PIN-код").fill(process.env.E2E_OWNER_PIN || ("12" + "98"));
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.locator("#dashboard")).toBeVisible();
});


test("основной экран и навигация доступны", async ({ page }) => {
  await expect(page.getByRole("heading", { name: "Дашборд" })).toBeVisible();
  await page.getByRole("button", { name: "Арендаторы и платежи" }).click();
  await page.getByRole("button", { name: "Арендаторы", exact: true }).click();
  await expect(page.locator("#tenants")).toBeVisible();
});


test("основной экран не содержит критичных ошибок доступности", async ({ page }) => {
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter((item) => ["critical", "serious"].includes(item.impact))).toEqual([]);
});
