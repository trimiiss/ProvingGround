import { expect, test } from "@playwright/test";

/** The dashboard's login, against a running stack with the default compose account. Needs no seeded data. */

test("a signed-out visitor sees only the login page and the API proxy refuses them", async ({ page }) => {
  await page.goto("/runs?limit=5");
  await expect(page).toHaveURL(/\/login\?next=/);
  await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0);

  const proxied = await page.request.get("/api/pg/v1/config");
  expect(proxied.status()).toBe(401);
});

test("the health check stays public and says nothing else", async ({ request }) => {
  const res = await request.get("/api/health");
  expect(res.ok()).toBe(true);
  expect(await res.json()).toEqual({ ok: true });
});

test("a forged session cookie is refused", async ({ page, context, baseURL }) => {
  const url = new URL(baseURL ?? "http://localhost:3000");
  const forged = `${Buffer.from("admin").toString("base64url")}.9999999999.not-a-signature`;
  await context.addCookies([{ name: "pg_session", value: forged, domain: url.hostname, path: "/" }]);
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
});

test("sign in, land on the page asked for, sign out", async ({ page }) => {
  await page.goto("/jobs");
  await page.getByLabel("Username").fill("admin");
  await page.getByLabel("Password").fill("wrong-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Wrong username or password." })).toBeVisible();

  await page.getByLabel("Password").fill("provingground");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/jobs$/);
  await expect(page.getByRole("heading", { level: 1, name: "Jobs", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.goto("/jobs");
  await expect(page).toHaveURL(/\/login\?next=/);
});

test("a crafted next link can't send the user to another site", async ({ page, baseURL }) => {
  await page.goto("/login?next=//example.com");
  await page.getByLabel("Username").fill("admin");
  await page.getByLabel("Password").fill("provingground");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(`${baseURL}/`);
});
