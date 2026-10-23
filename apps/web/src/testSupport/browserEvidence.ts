export type RequestFailureCategory =
  | "talent_result_save"
  | "talent_api"
  | "static_asset"
  | "other"

export type BrowserEvidence = {
  consoleErrors: number
  consoleWarnings: number
  pageErrors: number
  requestFailures: Record<RequestFailureCategory, number>
}

export function createBrowserEvidence(): BrowserEvidence {
  return {
    consoleErrors: 0,
    consoleWarnings: 0,
    pageErrors: 0,
    requestFailures: {
      talent_result_save: 0,
      talent_api: 0,
      static_asset: 0,
      other: 0,
    },
  }
}

export function recordConsoleCategory(
  evidence: BrowserEvidence,
  type: string,
): void {
  if (type === "error") evidence.consoleErrors += 1
  if (type === "warning") evidence.consoleWarnings += 1
}

export function recordPageError(evidence: BrowserEvidence): void {
  evidence.pageErrors += 1
}

export function recordRequestFailure(
  evidence: BrowserEvidence,
  rawUrl: string,
): void {
  let category: RequestFailureCategory = "other"
  try {
    const pathname = new URL(rawUrl).pathname
    if (/^\/api\/v1\/talent-tests\/sessions\/[^/]+\/results$/.test(pathname)) {
      category = "talent_result_save"
    } else if (
      pathname === "/api/v1/talent-tests"
      || pathname.startsWith("/api/v1/talent-tests/")
    ) {
      category = "talent_api"
    } else if (pathname.startsWith("/assets/")) {
      category = "static_asset"
    }
  } catch {
    // Malformed and non-URL values stay in the fixed "other" category.
  }
  evidence.requestFailures[category] += 1
}

export function serializeBrowserEvidence(
  status: string | undefined,
  expectedStatus: string,
  evidence: BrowserEvidence,
): string {
  return `${JSON.stringify({ status, expectedStatus, events: evidence }, null, 2)}\n`
}
