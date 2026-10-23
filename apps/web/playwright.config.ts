import { defineConfig, devices } from "@playwright/test"

const executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH
const outputDir = process.env.PLAYWRIGHT_OUTPUT_DIR
const htmlReportDir = process.env.PLAYWRIGHT_HTML_REPORT_DIR
const esportsTestFiles = /esports-(?:pc|touch|recovery|capabilities)\.spec\.ts/
const chromiumLaunchOptions = executablePath ? { launchOptions: { executablePath } } : {}
const privateEsportsArtifacts = {
  trace: "off" as const,
  screenshot: "off" as const,
  video: "off" as const,
}
const simulatedPhone = devices["Pixel 7 landscape"]

export default defineConfig({
  testDir: "./tests",
  ...(outputDir ? { outputDir } : {}),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ["list"],
    ["html", { open: "never", ...(htmlReportDir ? { outputFolder: htmlReportDir } : {}) }],
  ],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:18080",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "desktop-1440",
      testIgnore: esportsTestFiles,
      use: { ...chromiumLaunchOptions, viewport: { width: 1440, height: 900 } },
    },
    {
      name: "desktop-1024",
      testIgnore: esportsTestFiles,
      use: { ...chromiumLaunchOptions, viewport: { width: 1024, height: 768 } },
    },
    {
      name: "tablet-768",
      testIgnore: esportsTestFiles,
      use: { ...chromiumLaunchOptions, viewport: { width: 768, height: 1024 } },
    },
    {
      name: "mobile-360",
      testIgnore: esportsTestFiles,
      use: { ...chromiumLaunchOptions, viewport: { width: 360, height: 800 } },
    },
    {
      name: "touch-mobile-390",
      testIgnore: esportsTestFiles,
      use: {
        ...chromiumLaunchOptions,
        hasTouch: true,
        isMobile: true,
        viewport: { width: 390, height: 844 },
      },
    },
    {
      name: "esports-desktop-1440",
      testMatch: /esports-(?:pc|recovery|capabilities)\.spec\.ts/,
      grep: /EA-(?:PC|REC|CAP-(?:DESKTOP|NONPHONE))/,
      use: {
        // Native macOS Pointer Lock needs an actual focused window, not DOM focus emulation.
        headless: process.platform !== 'darwin',
        ...chromiumLaunchOptions,
        ...privateEsportsArtifacts,
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: "esports-touch-844x390",
      testMatch: /esports-(?:touch|capabilities)\.spec\.ts/,
      grep: /EA-(?:TOUCH|CAP-(?:RESPONSIVE|ROTATE))/,
      use: {
        ...simulatedPhone,
        ...chromiumLaunchOptions,
        ...privateEsportsArtifacts,
        hasTouch: true,
        isMobile: true,
        viewport: { width: 844, height: 390 },
      },
    },
    {
      name: "esports-webkit-capabilities",
      testMatch: /esports-capabilities\.spec\.ts/,
      grep: /EA-CAP-WEBKIT/,
      use: {
        ...privateEsportsArtifacts,
        browserName: "webkit",
        viewport: { width: 1440, height: 900 },
      },
    },
  ],
})
