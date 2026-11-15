import { expect, test } from "@playwright/test";
import { mockApi, TEXT } from "./mock";

test.describe("proposal approval gate", () => {
  test("the draft shows claims; hovering a claim shows its portfolio evidence", async ({ page }) => {
    await mockApi(page);
    await page.goto("/studio/run-1");
    await expect(page.getByTestId("draft-text")).toContainText("Hi Sam");
    await page.getByRole("button", { name: "Claim: supported" }).hover();
    await expect(page.getByRole("tooltip")).toContainText(TEXT.evidence);
    await expect(page.getByRole("tooltip")).toContainText("Contract QA assistant");
  });

  test("approve submits exactly one approve decision and shows the outcome", async ({ page }) => {
    const m = await mockApi(page);
    await page.goto("/studio/run-1");
    await expect(page.getByTestId("bid-amount")).toHaveText("$650");
    await page.getByRole("button", { name: /Approve and submit/ }).click();
    await expect(page.getByTestId("outcome")).toContainText("Submitted");
    const decisions = m.calls.filter((c) => c.path === "/runs/run-1/decision");
    expect(decisions).toHaveLength(1);
    expect(decisions[0]?.body).toEqual({ action: "approve" });
  });

  test("reject asks for an optional reason and sends it", async ({ page }) => {
    const m = await mockApi(page);
    await page.goto("/studio/run-1");
    await page.getByRole("button", { name: "Reject" }).click();
    await page.getByLabel(/Why\?/).fill("Too generic for this client");
    await page.getByRole("button", { name: "Confirm reject" }).click();
    await expect(page.getByTestId("outcome")).toContainText("Nothing was sent");
    expect(m.calls.find((c) => c.path === "/runs/run-1/decision")?.body).toEqual({ action: "reject", reason: "Too generic for this client" });
  });

  test("editing the draft sends the human's text, not the agent's", async ({ page }) => {
    const m = await mockApi(page);
    await page.goto("/studio/run-1");
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    const box = page.getByLabel(/Your version/);
    await expect(box).toHaveValue(TEXT.draft);
    await box.fill("Hi Sam, short and mine.");
    await page.getByRole("button", { name: /Save edit and submit/ }).click();
    await expect(page.getByTestId("outcome")).toContainText("Submitted");
    expect(m.calls.find((c) => c.path === "/runs/run-1/decision")?.body).toEqual({ action: "edit", edited_text: "Hi Sam, short and mine." });
  });

  test("regenerating a paragraph loads a candidate into the editor; the human still has to submit it", async ({ page }) => {
    const m = await mockApi(page);
    await page.goto("/studio/run-1");
    await page.getByRole("button", { name: "Regenerate a paragraph" }).click();
    await page.getByLabel(/What should change/).fill("shorter");
    await page.getByRole("button", { name: "Regenerate paragraph" }).click();
    await expect(page.getByLabel(/Your version/)).toHaveValue(TEXT.regenerated);
    expect(m.calls.find((c) => c.path === "/runs/run-1/regenerate")?.body).toEqual({ paragraph: 0, instruction: "shorter" });
    expect(m.calls.filter((c) => c.path === "/runs/run-1/decision")).toHaveLength(0); // nothing is decided by regenerating
  });

  test("a pending approval survives a refresh (state lives on the server)", async ({ page }) => {
    await mockApi(page);
    await page.goto("/studio/run-1");
    await expect(page.getByRole("button", { name: /Approve and submit/ })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("button", { name: /Approve and submit/ })).toBeVisible();
  });
});
