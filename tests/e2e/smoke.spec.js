const { test, expect } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;


test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("PIN-код").fill(process.env.E2E_OWNER_PIN || ("12" + "98"));
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.locator("#authOverlay")).toBeHidden({ timeout: 15_000 });
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


test("AI settings save models, limits, auto audit and prompt adaptations", async ({ page }) => {
  await page.locator('.sidebar .nav-group[data-group="administration"]').click();
  await page.locator('.section-tabs .tab[data-tab="settings"]').click();
  await page.locator('.settings-nav-btn[data-settings-target="ai"]').click();

  const modelSelect = page.locator("#deepseekModelSelect");
  await expect(modelSelect).toBeVisible();
  await expect(modelSelect.locator("option")).toHaveCount(2);
  const currentModel = await modelSelect.inputValue();
  const targetModel = currentModel === "deepseek-v4-flash" ? "deepseek-v4-pro" : "deepseek-v4-flash";

  await modelSelect.selectOption(targetModel);
  await page.locator("#aiSupervisorEnabledInput").check();
  await page.locator("#aiSupervisorCadenceSelect").selectOption("weekly");
  await page.locator("#aiSupervisorWeekdaySelect").selectOption("4");
  await page.locator("#aiSupervisorTimeInput").fill("09:30");
  await page.locator("#aiSupervisorModelSelect").selectOption("deepseek-v4-pro");
  await page.locator("#aiSupervisorMaxTokensInput").fill("900");
  await page.locator("#aiDailyCallLimitInput").fill("25");
  await page.locator("#aiMaxOutputTokensInput").fill("1400");
  await page.locator('[name="ai_feature_owner_chat_daily_limit"]').fill("17");
  await page.locator('[name="ai_output_chars_daily_briefing"]').fill("700");
  await page.locator("#aiUsdRubRateInput").fill("92.5");
  await page.locator("#aiActionConfirmationTtlInput").fill("24");
  await page.locator("#aiOwnerInstructionsInput").fill("Сначала покажи финансовый итог.");
  await page.locator("#aiTenantInstructionsInput").fill("Не используй эмодзи.");
  await page.locator("#aiAuditInstructionsInput").fill("Проверяй просрочки старше трёх дней.");
  await page.locator("#deepseekApiKeyInput").fill("sk-e2e-deepseek-key");
  await page.locator('#settingsForm button[type="submit"]').click();
  await expect(page.locator("#telegramStatusBox")).toContainText(targetModel);
  await expect(page.locator("#telegramStatusBox")).toContainText("DeepSeek key сохранён");
  await expect(page.locator("#telegramStatusBox")).toContainText("автоаудит раз в неделю · 09:30");
  await expect(page.locator("#deepseekApiKeyInput")).toHaveValue("");

  await page.reload();
  await expect(page.locator("#loadingOverlay")).toBeHidden();
  await page.locator('.sidebar .nav-group[data-group="administration"]').click();
  await page.locator('.section-tabs .tab[data-tab="settings"]').click();
  await page.locator('.settings-nav-btn[data-settings-target="ai"]').click();
  await expect(page.locator("#deepseekModelSelect")).toHaveValue(targetModel);
  await expect(page.locator("#aiSupervisorEnabledInput")).toBeChecked();
  await expect(page.locator("#aiSupervisorCadenceSelect")).toHaveValue("weekly");
  await expect(page.locator("#aiSupervisorWeekdaySelect")).toHaveValue("4");
  await expect(page.locator("#aiSupervisorTimeInput")).toHaveValue("09:30");
  await expect(page.locator("#aiSupervisorModelSelect")).toHaveValue("deepseek-v4-pro");
  await expect(page.locator("#aiDailyCallLimitInput")).toHaveValue("25");
  await expect(page.locator('[name="ai_feature_owner_chat_daily_limit"]')).toHaveValue("17");
  await expect(page.locator('[name="ai_output_chars_daily_briefing"]')).toHaveValue("700");
  await expect(page.locator("#aiOwnerInstructionsInput")).toHaveValue("Сначала покажи финансовый итог.");
  await expect(page.locator("#aiTenantInstructionsInput")).toHaveValue("Не используй эмодзи.");
  await expect(page.locator("#aiAuditInstructionsInput")).toHaveValue("Проверяй просрочки старше трёх дней.");
});
