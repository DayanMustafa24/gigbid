import { expect, test } from "@playwright/test";

// Live-stack smoke: a real browser against the running docker compose stack (web -> /api proxy -> FastAPI -> Postgres -> MCP -> GigSim).
// Nothing is mocked. Needs LIVE_URL (default http://localhost:3000), REVIEWER_EMAIL and REVIEWER_PASSWORD of the seeded reviewer account.
const email = process.env.REVIEWER_EMAIL ?? "";
const password = process.env.REVIEWER_PASSWORD ?? "";

test.skip(!email || !password, "REVIEWER_EMAIL / REVIEWER_PASSWORD not set");

test("reviewer signs in and the whole stack answers", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/(inbox|onboarding)/);

  // same-origin proxy, session cookie carried by the browser context
  const ready = await page.request.get("/api/health/ready");
  expect(ready.status()).toBe(200);
  const body = await ready.json();
  expect(body.components.database).toBe(true);
  expect(body.components.checkpointer).toBe(true);
  expect(body.components.marketplace).toBe("up"); // API -> Marketplace MCP -> GigSim
  expect(body.checkpointer_kind).toBe("postgres");

  const status = await page.request.get("/api/marketplace/status");
  expect(status.status()).toBe(200);
  expect((await status.json()).status).toBe("up");

  const jobs = await page.request.get("/api/jobs/search?q=&limit=5");
  expect(jobs.status()).toBe(200);
  expect(Array.isArray(await jobs.json())).toBe(true);

  const personas = await page.request.get("/api/personas");
  expect(personas.status()).toBe(200);
  expect((await personas.json()).length).toBeGreaterThan(0);
});

test("wrong password is rejected and nothing is revealed", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password + "-wrong");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
});
