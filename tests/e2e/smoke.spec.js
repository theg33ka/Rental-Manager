const { test, expect } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;


test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("PIN-код").fill(process.env.E2E_OWNER_PIN || ("12" + "98"));
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.locator("#authOverlay")).toBeHidden();
  await expect(page.locator("#loadingOverlay")).toBeHidden();
  await expect(page.locator("#dashboard")).toBeVisible();
});


test("основной экран и навигация доступны", async ({ page }) => {
  await expect(page.getByRole("heading", { name: "Состояние портфеля" })).toBeVisible();
  await expect(page.locator("#summaryGrid .metric")).toHaveCount(4);
  await page.getByRole("button", { name: "▦ Портфель и финансы", exact: true }).click();
  await page.getByRole("button", { name: "Портфель", exact: true }).click();
  await expect(page.locator("#tenants")).toBeVisible();
  await page.getByRole("button", { name: "+ Новый договор", exact: true }).click();
  await expect(page.locator("#onboardForm")).toBeVisible();
});


test("мобильный web сохраняет полную навигацию и тёмную палитру", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".mobile-nav")).toBeVisible();
  await expect(page.locator(".sidebar")).toBeHidden();
  await expect(page.locator("body")).toHaveCSS("background-color", "rgb(8, 11, 15)");
  await page.locator('.mobile-nav .nav-group[data-group="rentals"]').click();
  await page.locator('.section-tabs .tab[data-tab="rent"]').click();
  await expect(page.locator("#rent")).toBeVisible();
  await expect(page.locator("#manualPaymentTool")).toBeVisible();
});


test("основной экран не содержит критичных ошибок доступности", async ({ page }) => {
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter((item) => ["critical", "serious"].includes(item.impact))).toEqual([]);
});
