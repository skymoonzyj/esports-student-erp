import { afterEach, describe, expect, it, vi } from "vitest"

import { setCsrfToken } from "../api/client"
import {
  createEvaluationTicket,
  evaluationKeys,
  getEvaluationContext,
} from "./api"

afterEach(() => {
  setCsrfToken(null)
  vi.unstubAllGlobals()
})

describe("evaluation API", () => {
  it("uses the fixed evaluation account endpoints", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () =>
      new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    vi.stubGlobal("fetch", fetchMock)

    await getEvaluationContext()
    await createEvaluationTicket("90000000-0000-4000-8000-000000000001")

    expect(fetchMock.mock.calls.map(([request]) => String(request))).toEqual([
      "/api/v1/evaluations/context",
      "/api/v1/evaluations/bridge/tickets",
    ])
    const ticketInit = fetchMock.mock.calls[1]?.[1]
    expect(ticketInit?.method).toBe("POST")
    expect(new Headers(ticketInit?.headers).get("Content-Type")).toBe(
      "application/json",
    )
    expect(ticketInit?.body).toBe(
      JSON.stringify({
        request_id: "90000000-0000-4000-8000-000000000001",
      }),
    )
  })

  it("scopes private evaluation keys to the current account", () => {
    expect(evaluationKeys.root("account-1")).toEqual([
      "evaluations",
      "account-1",
    ])
    expect(evaluationKeys.context("account-1")).toEqual([
      "evaluations",
      "account-1",
      "context",
    ])
  })
})
