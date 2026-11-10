import { defineConfig, devices } from "@playwright/test"

const executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH
const outputDir = process.env.PLAYWRIGHT_OUTPUT_DIR
const htmlReportDir = process.env.PLAYWRIGHT_HTML_REPORT_DIR
const esportsTestFiles = /esports-(?:pc|touch|recovery|capabilities)\.spec\.ts/
const reactionxTestFile = /reactionx-integration\.spec\.ts/
const privateTestFiles = [esportsTestFiles, reactionxTestFile]
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
      testIgnore: privateTestFiles,
      use: { ...chromiumLaunchOptions, viewport: { width: 1440, height: 900 } },
    },
    {
      name: "desktop-1024",
      testIgnore: privateTestFiles,
      use: { ...chromiumLaunchOptions, viewport: { width: 1024, height: 768 } },
    },
    {
      name: "tablet-768",
      testIgnore: privateTestFiles,
      use: { ...chromiumLaunchOptions, viewport: { width: 768, height: 1024 } },
    },
    {
      name: "mobile-360",
      testIgnore: privateTestFiles,
      use: { ...chromiumLaunchOptions, viewport: { width: 360, height: 800 } },
    },
    {
      name: "touch-mobile-390",
      testIgnore: privateTestFiles,
      use: {
        ...chromiumLaunchOptions,
        hasTouch: true,
        isMobile: true,
        viewport: { width: 390, height: 844 },
      },
    },
    {
      name: "reactionx-desktop-1440",
      testMatch: reactionxTestFile,
      use: {
        ...chromiumLaunchOptions,
        ...privateEsportsArtifacts,
        viewport: { width: 1440, height: 900 },
        // UI simulation only; not Windows/Chrome/Edge or real-light acceptance.
        userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
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
