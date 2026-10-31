import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end smoke test against a running stack (API + worker from `docker compose up -d`, seeded data).
 * The dashboard itself is started here unless PG_WEB_URL points at one that is already running.
 */
const baseURL = process.env.PG_WEB_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 20_000 },
  workers: 1,
  reporter: [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    viewport: { width: 1440, height: 900 },
    colorScheme: "light",
    trace: "retain-on-failure",
  },
  webServer: process.env.PG_WEB_URL
    ? undefined
    : { command: "npm run start", url: baseURL, reuseExistingServer: true, timeout: 120_000 },
});
