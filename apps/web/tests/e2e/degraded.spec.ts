import { expect, test } from "@playwright/test";
import { mockApi } from "./mock";

test("marketplace down: banner says what still works, drafts stay reachable", async ({ page }) => {
  await mockApi(page, { degraded: ["marketplace"] });
  await page.goto("/runs");
  const banner = page.getByTestId("degraded-banner");
  await expect(banner).toContainText("Degraded mode");
  await expect(banner).toContainText("marketplace");
  await expect(banner).toContainText("drafts, runs and analytics still work");
});

test("no model configured: banner names the fallback", async ({ page }) => {
  await mockApi(page, { degraded: ["models"] });
  await page.goto("/runs");
  await expect(page.getByTestId("degraded-banner")).toContainText("deterministic fallbacks");
});

test("healthy system shows no banner", async ({ page }) => {
  await mockApi(page);
  await page.goto("/runs");
  await expect(page.getByRole("heading", { name: "Agent runs" })).toBeVisible();
  await expect(page.getByTestId("degraded-banner")).toHaveCount(0);
});

test("API unreachable after load: banner appears, screen keeps its content", async ({ page }) => {
  await mockApi(page, { apiDown: true });
  await page.goto("/runs");
  await expect(page.getByTestId("degraded-banner")).toContainText("API unreachable");
});

test("@mobile the approval screen is usable on a phone", async ({ page }) => {
  await mockApi(page, { degraded: ["marketplace"] });
  await page.goto("/studio/run-1");
  await expect(page.getByTestId("degraded-banner")).toBeVisible();
  await expect(page.getByRole("button", { name: /Approve and submit/ })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  expect(overflow).toBe(false);
});
