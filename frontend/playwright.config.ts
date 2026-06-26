import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "html",
  use: {
    baseURL: process.env.BASE_URL ?? "http://192.168.1.70:3000",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.CI
    ? {
        command: "pnpm build && pnpm start -H 0.0.0.0",
        url: "http://localhost:3000",
        timeout: 120_000,
        reuseExistingServer: false,
      }
    : undefined,
});
