import { describe, expect, it } from "vitest"

import {
  createBrowserEvidence,
  recordConsoleCategory,
  recordPageError,
  recordRequestFailure,
  serializeBrowserEvidence,
} from "./browserEvidence"

describe("browser acceptance evidence", () => {
  it("serializes only counters and stable route categories", () => {
    const evidence = createBrowserEvidence()
    const secret = "credential=never-persist-this"
    const captureConsole = (event: { type: string; text: string }) => {
      recordConsoleCategory(evidence, event.type)
    }
    const capturePageError = (error: Error) => {
      void error
      recordPageError(evidence)
    }

    captureConsole({ type: "error", text: secret })
    captureConsole({ type: "warning", text: secret })
    capturePageError(new Error(secret))
    recordRequestFailure(
      evidence,
      `http://127.0.0.1/api/v1/talent-tests/sessions/session-one/results?${secret}`,
    )
    recordRequestFailure(evidence, `http://127.0.0.1/private/${secret}`)
    const serialized = serializeBrowserEvidence("passed", "passed", evidence)

    expect(serialized).not.toContain(secret)
    expect(JSON.parse(serialized)).toEqual({
      status: "passed",
      expectedStatus: "passed",
      events: {
        consoleErrors: 1,
        consoleWarnings: 1,
        pageErrors: 1,
        requestFailures: {
          talent_result_save: 1,
          talent_api: 0,
          static_asset: 0,
          other: 1,
        },
      },
    })
  })
})
