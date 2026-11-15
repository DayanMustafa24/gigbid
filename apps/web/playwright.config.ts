import { defineConfig, devices } from "@playwright/test";

// `--project=unit` runs pure logic tests with no browser and no server. e2e starts the Next dev server; the API is mocked at the
// network layer (tests/e2e/mock.ts) so the suite is deterministic and needs neither FastAPI nor model keys.
// `--project=live` (npm run test:live) drives a RUNNING docker compose stack (LIVE_URL, REVIEWER_EMAIL, REVIEWER_PASSWORD); no dev server, no mocks.
const argv = process.argv.join(" ");
const unitOnly = argv.includes("--project=unit");
const liveOnly = argv.includes("--project=live");

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: { baseURL: "http://localhost:3000", trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    { name: "unit", testDir: "./tests/unit" },
    { name: "e2e", testDir: "./tests/e2e", grepInvert: /@mobile/, use: { ...devices["Desktop Chrome"] } },
    { name: "e2e-mobile", testDir: "./tests/e2e", grep: /@mobile/, use: { ...devices["Pixel 7"] } },
    ...(liveOnly
      ? [{ name: "live", testDir: "./tests/live", use: { ...devices["Desktop Chrome"], baseURL: process.env.LIVE_URL ?? "http://localhost:3000" } }]
      : []),
  ],
  webServer:
    unitOnly || liveOnly
      ? undefined
      : { command: "npm run dev", url: "http://localhost:3000/login", reuseExistingServer: !process.env.CI, timeout: 180_000 },
});
