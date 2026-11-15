import { expect, test } from "@playwright/test";
import { mockApi } from "./mock";

test("first run: no persona yet sends you to onboarding, saving opens the inbox", async ({ page }) => {
  const m = await mockApi(page, { persona: null });
  await page.goto("/inbox");
  await expect(page).toHaveURL(/\/onboarding$/);
  await expect(page.getByRole("heading", { name: "Set up GigBid" })).toBeVisible();
  await expect(page.getByText("Human approval is required for every proposal")).toBeVisible();

  await page.getByLabel("Weekly Connects budget").fill("60");
  await page.getByLabel(/Search phrases/).fill("RAG, dashboards");
  await page.getByRole("button", { name: "Save and open the inbox" }).click();

  await expect(page).toHaveURL(/\/inbox$/);
  await expect(page.getByRole("heading", { name: "Inbox" })).toBeVisible();
  const put = m.calls.find((c) => c.method === "PUT" && c.path === "/settings");
  expect(put?.body).toMatchObject({ default_persona: "ai_ml", scout_queries: ["RAG", "dashboards"], weekly_connects_budget: 60, hourly_rate_usd: 55 });
});

test("inbox lists jobs and shows the draft waiting for approval", async ({ page }) => {
  await mockApi(page);
  await page.goto("/inbox");
  await expect(page.getByRole("link", { name: "RAG assistant for support tickets" }).first()).toBeVisible();
  await expect(page.getByRole("region", { name: /Awaiting your approval/ })).toBeVisible();
  await page.getByRole("link", { name: "Review draft" }).click();
  await expect(page).toHaveURL(/\/studio\/run-1$/);
});

test("job page shows the requirement-to-evidence matrix", async ({ page }) => {
  await mockApi(page);
  await page.goto("/jobs/job-1");
  const table = page.getByRole("table", { name: "Requirements and evidence" });
  await expect(table).toContainText("Experience with RAG");
  await table.getByRole("button", { name: /Evidence for Experience with RAG/ }).hover();
  await expect(page.getByRole("tooltip")).toContainText("Contract QA assistant");
});
