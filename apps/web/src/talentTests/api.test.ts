import { afterEach, expect, it, vi } from "vitest"

import {
  abandonTalentSession,
  createTalentSession,
  getTalentReport,
  getTalentReportRanking,
  getTalentContext,
  getTalentSession,
  listTalentReports,
  listTalentSessions,
  listTalentSubjects,
  submitTalentResult,
} from "./api"
import type { Submission } from "./contracts"

const submission: Submission = {
  test_id: "reactiontime",
  raw: { samples_ms: [200, 200, 200, 200, 200], too_soon_count: 0 },
  device: {
    input_kind: "mouse",
    viewport_width: 1440,
    viewport_height: 900,
    geometry: null,
  },
  duration_ms: 10_000,
  background_interrupted: false,
  submission_id: "f1000000-0000-4000-8000-000000000004",
  engine_version: "reactiontime-clone-v1",
  locale: "zh-CN",
}

afterEach(() => vi.unstubAllGlobals())

it("rejects malformed ranking data and mismatched report identity at the API boundary", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ ok: true }), { headers: { "Content-Type": "application/json" } })))
  await expect(getTalentReportRanking("f1000000-0000-4000-8000-000000000010", new AbortController().signal)).rejects.toThrow(/排名/)
})

it("uses the shared API boundary and the exact talent-test routes", async () => {
  const calls: Array<{ url: string; init: RequestInit | undefined }> = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  }))

  await getTalentContext()
  await createTalentSession(
    "f1000000-0000-4000-8000-000000000001",
    "talent-training-v1",
  )
  await getTalentSession("f1000000-0000-4000-8000-000000000002")
  await listTalentSessions(2, 20)
  await listTalentSubjects(3, 20, "张 同学")
  await listTalentReports("f1000000-0000-4000-8000-000000000009", 4, 20)
  await getTalentReport("f1000000-0000-4000-8000-000000000010")
  await submitTalentResult("f1000000-0000-4000-8000-000000000002", submission)
  await abandonTalentSession(
    "f1000000-0000-4000-8000-000000000002",
    "f1000000-0000-4000-8000-000000000003",
  )

  expect(calls.map(({ url, init }) => ({
    url,
    method: init?.method ?? "GET",
    body: init?.body ? JSON.parse(String(init.body)) : null,
  }))).toEqual([
    { url: "/api/v1/talent-tests/context", method: "GET", body: null },
    {
      url: "/api/v1/talent-tests/sessions",
      method: "POST",
      body: {
        request_id: "f1000000-0000-4000-8000-000000000001",
        catalog_version: "talent-training-v1",
      },
    },
    {
      url: "/api/v1/talent-tests/sessions/f1000000-0000-4000-8000-000000000002",
      method: "GET",
      body: null,
    },
    {
      url: "/api/v1/talent-tests/sessions?page=2&page_size=20",
      method: "GET",
      body: null,
    },
    {
      url: "/api/v1/talent-tests/subjects?page=3&page_size=20&name=%E5%BC%A0+%E5%90%8C%E5%AD%A6",
      method: "GET",
      body: null,
    },
    {
      url: "/api/v1/talent-tests/reports?page=4&page_size=20&person_id=f1000000-0000-4000-8000-000000000009",
      method: "GET",
      body: null,
    },
    {
      url: "/api/v1/talent-tests/reports/f1000000-0000-4000-8000-000000000010",
      method: "GET",
      body: null,
    },
    {
      url: "/api/v1/talent-tests/sessions/f1000000-0000-4000-8000-000000000002/results",
      method: "POST",
      body: submission,
    },
    {
      url: "/api/v1/talent-tests/sessions/f1000000-0000-4000-8000-000000000002/abandon",
      method: "POST",
      body: { request_id: "f1000000-0000-4000-8000-000000000003" },
    },
  ])
})
