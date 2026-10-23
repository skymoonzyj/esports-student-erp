import { defineConfig } from "@playwright/test"

const executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:18080",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    launchOptions: executablePath ? { executablePath } : undefined,
  },
  projects: [
    {
      name: "desktop-1440",
      use: { viewport: { width: 1440, height: 900 } },
    },
    {
      name: "desktop-1024",
      use: { viewport: { width: 1024, height: 768 } },
    },
    {
      name: "tablet-768",
      use: { viewport: { width: 768, height: 1024 } },
    },
    {
      name: "mobile-360",
      use: { viewport: { width: 360, height: 800 } },
    },
  ],
})
