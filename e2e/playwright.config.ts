import { defineConfig, devices } from "@playwright/test";

/**
 * Browser tests against a running stack: `docker compose up -d`, then
 * `npm test` here. The dev frontend compiles pages on first visit, so
 * timeouts are generous.
 */
export default defineConfig({
  testDir: "./tests",
  globalSetup: "./global-setup.ts",
  // Generous: on a small shared Docker VM a single request can take tens of
  // seconds while the browser, frontend and API compete for CPU. These are
  // functional tests, not performance tests.
  timeout: 600_000,
  expect: { timeout: 120_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3002",
    navigationTimeout: 120_000,
    actionTimeout: 120_000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
