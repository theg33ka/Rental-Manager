const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "tests/e2e",
  timeout: 30_000,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:8000",
    trace: "retain-on-failure",
  },
  webServer: process.env.CI
    ? undefined
    : {
        command: ".venv\\Scripts\\python.exe -m uvicorn rental_manager.main:app --host 127.0.0.1 --port 8000",
        url: "http://127.0.0.1:8000/healthz",
        reuseExistingServer: true,
        env: {
          RENTAL_MANAGER_DATABASE_URL: "sqlite:///data/playwright.db",
          RENTAL_MANAGER_ENV: "development",
        },
      },
});
