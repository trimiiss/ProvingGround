import path from "node:path";
import { expect, type Page, test } from "@playwright/test";

/**
 * Smoke test of the dashboard against the seeded demo stack. With SCREENSHOTS=1 it also refreshes the
 * README screenshots in docs/screenshots.
 */

const SHOTS = path.resolve(__dirname, "..", "..", "docs", "screenshots");
const capture = process.env.SCREENSHOTS === "1";

async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  await expect(page.getByText("Something went wrong")).toHaveCount(0);
  // Recharts measures its container after hydration; give the charts a frame to draw. (Pages visited
  // earlier stay mounted but hidden after client-side navigation, so only visible charts count.)
  const charts = page.locator(".recharts-surface").filter({ visible: true });
  if ((await charts.count()) > 0) await expect(charts.first()).toBeVisible();
  await page.waitForTimeout(400);
}

async function shot(page: Page, name: string, fullPage = false) {
  if (capture) await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage });
}

test.beforeEach(async ({ page }) => {
  const res = await page.request.post("/api/auth/login", { data: { username: "admin", password: "provingground" } });
  expect(res.ok()).toBe(true);
});

test("every page renders the seeded data", async ({ page }) => {
  const pages: [string, string][] = [
    ["/", "Fleet"],
    ["/runs", "Runs"],
    ["/compare", "Compare"],
    ["/redteam", "Red team"],
    ["/traces", "Traces"],
    ["/datasets", "Datasets"],
    ["/gates", "Quality gates"],
    ["/models", "Models"],
    ["/jobs", "Jobs"],
  ];
  for (const [url, heading] of pages) {
    await page.goto(url);
    await expect(page.getByRole("heading", { level: 1, name: heading, exact: true })).toBeVisible();
    await settle(page);
  }

  await page.goto("/");
  await expect(page.locator('main a[href^="/agents/"]')).toHaveCount(20);
  await page.goto("/runs");
  await expect(page.locator('a[href^="/runs/run-"]').first()).toBeVisible();
  await page.goto("/datasets");
  await expect(page.getByRole("link", { name: "customer-quality", exact: true })).toBeVisible();
});

test("launch an evaluation from the UI and watch it complete", async ({ page }) => {
  await page.goto("/agents/support-bot");
  await page.getByRole("button", { name: "Run evaluation" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Cases per suite").fill("8");
  await dialog.getByRole("button", { name: "Start" }).click();
  const open = dialog.getByRole("link", { name: /Open run/ });
  await expect(open).toBeVisible({ timeout: 150_000 });
  await open.click();
  await expect(page).toHaveURL(/\/runs\/run-/);
  await expect(page.getByRole("heading", { name: /Cases \(/ })).toBeVisible();
  await settle(page);
});

test("drill from a blocked release into the evidence", async ({ page }) => {
  await page.goto("/gates");
  const blocked = page.getByRole("row").filter({ hasText: "Blocked" }).first();
  await blocked.getByRole("link").first().click();
  await expect(page.getByText("Release blocked")).toBeVisible();
  await settle(page);
  await shot(page, "gate-blocked");

  await page.getByRole("link", { name: "Full comparison" }).click();
  await expect(page.getByRole("heading", { name: "Release decision" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Metric differences" })).toBeVisible();
  await settle(page);
  await shot(page, "compare", true);
});

test("capture the README screenshots", async ({ page }) => {
  test.skip(!capture, "screenshots disabled");

  await page.goto("/");
  await settle(page);
  await shot(page, "fleet");

  await page.goto("/agents/it-helpdesk");
  await expect(page.getByRole("heading", { name: /Trends/ })).toBeVisible();
  await settle(page);
  await shot(page, "agent", true);

  await page.goto("/redteam");
  await expect(page.getByRole("heading", { name: "Technique × goal" })).toBeVisible();
  await settle(page);
  await shot(page, "redteam", true);

  const run = await page.request.get("/api/pg/v1/runs?agent=support-bot&kind=eval&limit=1").then((r) => r.json());
  await page.goto(`/runs/${run.items[0].id}`);
  await settle(page);
  await page.getByRole("button").filter({ hasText: "customer-" }).first().click();
  await expect(page.getByRole("dialog").getByText("Scores")).toBeVisible();
  await page.waitForTimeout(300);
  await shot(page, "case-drawer");
  await page.keyboard.press("Escape");

  await page.goto("/traces?source=live");
  await page.locator('a[href^="/traces/"]').first().click();
  await expect(page.getByRole("heading", { name: /Timeline/ })).toBeVisible();
  await settle(page);
  await shot(page, "trace");

  await page.goto("/models");
  await settle(page);
  await shot(page, "models");

  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  await settle(page);
  await shot(page, "fleet-dark");
});

test("add a bot with the form, with no files", async ({ page }) => {
  await page.goto("/upload");
  await page.getByLabel("Bot name").fill("E2E Form Bot");
  await page.getByLabel("Bot URL").fill("http://demo-agents:8100/agents/support-bot/chat");
  await page.getByLabel("Message field").fill("input");
  await page.getByLabel("Reply field").fill("output");
  await page.getByLabel("Secret code").fill("PG-CANARY-SUP-7F3A, STAFF-ONLY-4471");
  await page.getByLabel("Public contact details (optional)").fill("1-800-555-0199, support@acme-outfitters.example");
  await page.getByLabel("Questions and right answers").fill("How long do refunds take? | Refunds take 5 business days.");
  await page.getByRole("button", { name: "Test this bot" }).click();
  const results = page.getByRole("region", { name: "Test results" });
  await expect(results.getByRole("heading", { name: /^Results/ })).toBeVisible({ timeout: 150_000 });
  await expect(results.getByText(/of checks passed/)).toBeVisible();
  await results.getByRole("link", { name: /Answers and grades/ }).click();
  await expect(page.getByText(/of 42 attacks worked/)).toBeVisible();
  expect((await page.request.delete("/api/pg/v1/packs/e2e-form-bot")).status()).toBe(204);
});

test("drop a test folder and see each bot judged", async ({ page }) => {
  await page.goto("/upload");
  await expect(page.getByRole("heading", { level: 1, name: "Upload agent", exact: true })).toBeVisible();
  // the same folder a newcomer drags in: a buggy bot.py bot plus two demo bots over HTTP
  await page.getByTestId("folder-input").setInputFiles(path.resolve(__dirname, "..", "..", "examples", "upload-me"));
  const results = page.getByRole("region", { name: "Test results" });
  await expect(results.getByRole("heading", { name: /^Results/ })).toBeVisible({ timeout: 150_000 });
  await expect(results.getByText("Blocked", { exact: true })).toHaveCount(2); // buggy-shop-bot, kiosk-bot
  await expect(results.getByText("Passed", { exact: true })).toHaveCount(1); // helpdesk-bot
  await settle(page);
  await shot(page, "upload");

  await results.getByRole("link", { name: /Why it was blocked/ }).first().click();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await settle(page);
  expect((await page.request.delete("/api/pg/v1/packs/upload-me")).status()).toBe(204); // leave the demo fleet as it was
});
