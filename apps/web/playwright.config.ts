import { defineConfig, devices } from "@playwright/test";

// `--project=unit` runs pure logic tests with no browser and no server. e2e starts the Next dev server; the API is mocked at the
// network layer (tests/e2e/mock.ts) so the suite is deterministic and needs neither FastAPI nor model keys.
const unitOnly = process.argv.includes("--project=unit");

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
  ],
  webServer: unitOnly
    ? undefined
    : { command: "npm run dev", url: "http://localhost:3000/login", reuseExistingServer: !process.env.CI, timeout: 180_000 },
});
