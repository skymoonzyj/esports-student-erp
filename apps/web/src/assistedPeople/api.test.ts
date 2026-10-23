import { afterEach, expect, it, vi } from "vitest"
import { assistedPeopleApi, assistedErrorMessage, intakeDetailSchema, intakeListSchema } from "./api"
import { ProblemDetailsError } from "../api/client"

const id = "30000000-0000-4000-8000-000000000001"
const item = { id, version: 2, status: "approved", kind: "student", allowed_actions: [], approved_person_id: id, submitted_by: { id, label: "提交人" } }
afterEach(() => vi.unstubAllGlobals())

it("distinguishes non-sensitive list items from authorized detail and approved profile version", () => {
  expect(intakeListSchema.safeParse({ items: [item], total: 1, page: 1, page_size: 20 }).success).toBe(true)
  expect(intakeListSchema.safeParse({ items: [{ ...item, scoped_payload: {} }], total: 1, page: 1, page_size: 20 }).success).toBe(false)
  expect(intakeDetailSchema.parse({ ...item, scoped_payload: null, returned_reason: null, profile_version: 4 }).profile_version).toBe(4)
  expect(intakeDetailSchema.safeParse({ ...item, scoped_payload: null, returned_reason: null, profile_version: 0 }).success).toBe(false)
})

it("uses explicit mine/review server filters and parses authorized submitter labels", async () => {
  let requested = ""
  vi.stubGlobal("fetch", vi.fn(async (url) => {
    requested = String(url)
    return Response.json({ items: [{ ...item, submitted_by: { id, label: "提交人员" } }], total: 1, page: 2, page_size: 20 })
  }))
  const result = await assistedPeopleApi.list(2, new AbortController().signal, { view: "review", status: "pending", kind: "both", submitter_query: "测试" })
  expect(result.items[0].submitted_by?.label).toBe("提交人员")
  expect(requested).toContain("view=review")
  expect(requested).toContain("status=pending")
  expect(requested).toContain("kind=both")
  expect(new URL(requested, "https://erp.example").searchParams.get("submitter_query")).toBe("测试")
})

it("passes cancellation and uses actual expected-version action shapes without a review note", async () => {
  const calls: Array<[string, RequestInit | undefined]> = []
  vi.stubGlobal("fetch", vi.fn(async (url, init) => {
    calls.push([String(url), init])
    return Response.json({ request_id: id, person_id: id, approval_id: id })
  }))
  const controller = new AbortController()
  await assistedPeopleApi.approve(id, 2, controller.signal)
  expect(calls[0][0]).toBe(`/api/v1/personnel-intake/requests/${id}/approve`)
  expect(JSON.parse(String(calls[0][1]?.body))).toEqual({ expected_version: 2 })
  expect(calls[0][1]?.signal).toBe(controller.signal)
})

it("rejects malformed context instead of inferring authority from partial flags", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ can_submit: true })))
  await expect(assistedPeopleApi.context(new AbortController().signal)).rejects.toThrow()
})

it("rejects SecretStr stars as public authorized detail data rather than displaying a false decrypted record", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ...item, scoped_payload: { kind: "student", full_name: "测试", identity_number: "**********", phone: "**********", email: "**********", sex: "male", date_of_birth: "1990-01-01", guardians: [], coach_person_ids: [], team_id: null }, returned_reason: null })))
  await expect(assistedPeopleApi.detail(id, new AbortController().signal)).rejects.toThrow()
})

it.each([401, 403, 404, 409, 422, 500])("maps private status %s without reflecting backend secret content", (status) => {
  const error = new ProblemDetailsError({ type: "about:blank", title: "private", status, code: "private", request_id: "", detail: "sensitive-identity" })
  expect(assistedErrorMessage(error)).not.toContain("sensitive-identity")
  if (status === 409) expect(assistedErrorMessage(error)).toContain("草稿已保留")
})

it("reads managed people with strict private DTO, server filters and consumed cancellation", async () => {
  let request: { url: string; init?: RequestInit } | undefined
  let extra = false
  vi.stubGlobal("fetch", vi.fn(async (url, init) => {
    request = { url: String(url), init }
    return Response.json({ items: [{ person_id: id, full_name: "所属人员", nickname: null, kinds: ["student", "player"], authorized_affiliations: [{ kind: "team", id, label: "授权队伍" }], housing: { status: "unarranged", stay: null, next_planned_stay: null }, ...(extra ? { phone: "private" } : {}) }], next_cursor: null, total: 23 })
  }))
  const signal = new AbortController().signal
  const result = await assistedPeopleApi.managedPeople({ query: " 名字 ", kind: "both", team_id: id, housing_status: "unarranged", cursor: "opaque/next", page_size: 20 }, signal)
  expect(result.total).toBe(23)
  expect(result.items[0].full_name).toBe("所属人员")
  const url = new URL(request!.url, "https://erp.example")
  expect(url.pathname).toBe("/api/v1/personnel-intake/managed-people")
  expect(Object.fromEntries(url.searchParams)).toEqual({ query: "名字", kind: "both", team_id: id, housing_status: "unarranged", cursor: "opaque/next", page_size: "20" })
  expect(request!.init?.signal).toBe(signal)
  expect(request!.init?.cache).toBe("no-store")
  extra = true
  await expect(assistedPeopleApi.managedPeople({}, signal)).rejects.toThrow()
})

it("rejects managed filter boundaries before any request", async () => {
  const fetch = vi.fn()
  vi.stubGlobal("fetch", fetch)
  for (const filter of [{ query: "a".repeat(81) }, { cursor: "a".repeat(1025) }, { page_size: 101 }, { team_id: "raw-id" }, { housing_status: "unknown" }]) {
    await expect(assistedPeopleApi.managedPeople(filter as never, new AbortController().signal)).rejects.toThrow()
  }
  expect(fetch).not.toHaveBeenCalled()
})

it.each([
  { housing: { status: "checked_in", stay: { roommates: ["private"] }, next_planned_stay: null } },
  { housing: { status: "unknown", stay: null, next_planned_stay: null } },
  { authorized_affiliations: [{ kind: "account", id, label: "broader" }] },
  { kinds: ["guardian"] },
])("rejects malformed or broader managed projection %# at the real transport boundary", async (override) => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ items: [{ person_id: id, full_name: "所属人员", nickname: null, kinds: ["student"], authorized_affiliations: [], housing: { status: "unarranged", stay: null, next_planned_stay: null }, ...override }], next_cursor: null, total: 1 })))
  await expect(assistedPeopleApi.managedPeople({}, new AbortController().signal)).rejects.toThrow()
})
