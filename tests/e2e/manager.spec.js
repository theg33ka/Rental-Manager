const { test, expect } = require("@playwright/test");

test("управляющий показывает ответ, состояние и настройки доставки", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("PIN-код").fill(process.env.E2E_OWNER_PIN || ("12" + "98"));
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page.locator("#loadingOverlay")).toBeHidden();
  await expect(page.locator("#authOverlay")).toBeHidden();
  await page.evaluate(() => openWorkspaceTab("hermes"));
  await expect(page.locator("#managerHealth")).toContainText("AI:");
  let question;
  await page.route("**/api/hermes/chat", async route => {
    question = route.request().postDataJSON();
    await route.fulfill({ json: { reply: "Проверено: договор, аренда, коммуналка. <script>ошибка</script>" } });
  });
  await page.locator("#managerQuestion").fill("Покажи долг по квартире 7");
  await page.locator("#managerAskButton").click();
  await expect(page.locator("#managerAnswer")).toContainText("Проверено: договор");
  await expect(page.locator("#managerAnswer script")).toHaveCount(0);
  expect(question.text).toBe("Покажи долг по квартире 7");
  await page.getByText("Каналы и время уведомлений", { exact: true }).click();
  await page.locator("#managerNotifyMode").selectOption("critical_push");
  const form = page.locator("form").filter({ has: page.locator("#managerNotifyMode") });
  await form.getByRole("button", { name: "Сохранить", exact: true }).click();
  await expect(page.locator("#managerNotifyMode")).toHaveValue("critical_push");
  await page.reload();
  await expect(page.locator("#loadingOverlay")).toBeHidden();
  await page.evaluate(() => openWorkspaceTab("hermes"));
  await expect(page.locator("#managerNotifyMode")).toHaveValue("critical_push");
  await page.screenshot({ path: "test-results/manager-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator("#managerQuestion")).toBeVisible();
  await page.screenshot({ path: "test-results/manager-mobile.png", fullPage: true });
});
