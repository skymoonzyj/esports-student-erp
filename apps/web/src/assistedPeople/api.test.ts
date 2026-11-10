import { afterEach, expect, it, vi } from "vitest"
import { assistedPeopleApi, assistedErrorMessage, intakeDetailSchema, intakeListSchema } from "./api"
import { ProblemDetailsError, setCsrfToken } from "../api/client"

const id = "30000000-0000-4000-8000-000000000001"
// Catches accepting a broadened authorization catalog or losing the management-only wire shape.
it("loads a strict management identity context with project-qualified staff teams", async () => {
  const body = { role_options: [{ key: "coach", name: "教练", assignable: true, reason: null }], can_assign_staff_teams: false, staff_team_reason: "须由有权人员分配", staff_team_options: [{ id, project_id: id, project_name: "项目甲", name: "合成队伍" }] }
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(body)))
  expect(await assistedPeopleApi.managementIdentityContext(new AbortController().signal)).toEqual(body)
  for (const invalid of [{ ...body, can_assign_admin: true }, { ...body, role_options: [{ key: "admin", name: "管理", assignable: true, reason: null }] }, { ...body, staff_team_options: [{ id, name: "缺项目" }] }]) {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(invalid)))
    await expect(assistedPeopleApi.managementIdentityContext(new AbortController().signal)).rejects.toThrow()
  }
})
it("sends unified identity intent without requiring duplicate activation roles and rejects it on ordinary apply", async () => {
  setCsrfToken("test")
  const writes: unknown[] = []
  vi.stubGlobal("fetch", vi.fn(async (_url, init) => { writes.push(JSON.parse(String(init?.body))); return Response.json({ request_id: null, person_id: id, approval_id: id }) }))
  const input = { full_name: "合成", identity_number: "AB123456", document_type: "passport" as const, document_issuing_country: null, phone: null, email: null, sex: null, date_of_birth: null, guardians: [], coach_person_ids: [], team_id: null, team_allocation_status: "pending" as const,
    identity_selection: { primary_identity: "coach" as const, concurrent_identities: [], primary_staff_identity: null }, staff_teams: [{ staff_role: "coach" as const, team_ids: [id] }] }
  await assistedPeopleApi.directCreatePending(input, id, new AbortController().signal)
  expect(writes).toEqual([{ ...input, idempotency_key: id }])
  await expect(assistedPeopleApi.submit(input as never, new AbortController().signal)).rejects.toThrow()
  await expect(assistedPeopleApi.directCreatePending({ ...input, identity_selection: { ...input.identity_selection, primary_identity: "root" } } as never, id, new AbortController().signal)).rejects.toThrow()
  expect(writes).toHaveLength(1)
})
it.each([
  { identity_selection: { primary_identity: "student", concurrent_identities: ["coach", "team_manager"], primary_staff_identity: null }, staff_teams: [] },
  { identity_selection: { primary_identity: "coach", concurrent_identities: [], primary_staff_identity: "finance" }, staff_teams: [] },
  { identity_selection: { primary_identity: "coach", concurrent_identities: [], primary_staff_identity: null }, staff_teams: [{ staff_role: "manager", team_ids: [id] }] },
  { identity_selection: { primary_identity: "coach", concurrent_identities: [], primary_staff_identity: null }, staff_teams: [{ staff_role: "coach", team_ids: [id] }, { staff_role: "coach", team_ids: [] }] },
])("rejects inconsistent management identity and team intent before transport %#", async selection => {
  const fetcher = vi.fn(async () => Response.json({ request_id: null, person_id: id, approval_id: id }))
  vi.stubGlobal("fetch", fetcher)
  const facts = { full_name: "合成", identity_number: "AB123456", document_type: "passport", document_issuing_country: null, phone: null, email: null, sex: null, date_of_birth: null, guardians: [], coach_person_ids: [], team_id: null, team_allocation_status: "pending" }
  await expect(assistedPeopleApi.directCreatePending({ ...facts, ...selection } as never, id, new AbortController().signal)).rejects.toThrow()
  expect(fetcher).not.toHaveBeenCalled()
})
const item = { id, version: 2, status: "approved", kind: "student", allowed_actions: [], approved_person_id: id, submitted_by: { id, label: "提交人" } }
const pendingPayload = { material_version: 2, kind: "staff", full_name: "Passport Person", identity_number: "Ab123456", document_type: "passport", document_issuing_country: null,
  phone: null, email: null, sex: null, date_of_birth: null, guardians: [], coach_person_ids: [], team_id: null, team_allocation_status: "pending" }
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null) })

it("refuses an injected activation-role field on ordinary submit and resubmit before sending any request", async () => {
  const fetcher = vi.fn(async () => Response.json({ ...item, scoped_payload: null, returned_reason: null }))
  vi.stubGlobal("fetch", fetcher)
  const input = { kind: "student", full_name: "合成档案", identity_number: "110105199001021238", phone: "13800000000", email: null,
    sex: "male", date_of_birth: "1990-01-02", guardians: [], coach_person_ids: [], team_id: null, activation_role_keys: ["coach"] }
  await expect(assistedPeopleApi.submit(input as never, new AbortController().signal)).rejects.toThrow()
  await expect(assistedPeopleApi.resubmit(id, 1, input as never, new AbortController().signal)).rejects.toThrow()
  expect(fetcher).not.toHaveBeenCalled()
})

it("still refuses strict staff on ordinary submit and resubmit despite management correction support", async () => {
  const fetcher = vi.fn(async () => Response.json({ ...item, scoped_payload: null, returned_reason: null }))
  vi.stubGlobal("fetch", fetcher)
  const input = { kind: "staff", full_name: "合成工作人员", identity_number: "110105199001021238", phone: "13800000000", email: null,
    sex: "male", date_of_birth: "1990-01-02", guardians: [], coach_person_ids: [], team_id: null }
  await expect(assistedPeopleApi.submit(input as never, new AbortController().signal)).rejects.toThrow()
  await expect(assistedPeopleApi.resubmit(id, 1, input as never, new AbortController().signal)).rejects.toThrow()
  expect(fetcher).not.toHaveBeenCalled()
})

it.each([
  { items: [{ key: "admin", name: "管理员", assignable: true, reason: null }] },
  { items: [{ key: "super_admin", name: "超级管理员", assignable: true, reason: null }] },
  { items: [{ key: "coach", name: "教练", assignable: true }] },
  { items: [{ key: "coach", name: "教练", assignable: true, reason: null }, { key: "coach", name: "重复", assignable: false, reason: "不足" }] },
  { items: [], broader_authority: true },
])("fails closed on malformed or forbidden role catalog DTO %#", async body => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(body)))
  await expect(assistedPeopleApi.activationRoleOptions(new AbortController().signal)).rejects.toThrow()
})

it("uses no-store and cancellation for private role views and rejects a different person response", async () => {
  const signal = new AbortController().signal
  const fetcher = vi.fn(async () => Response.json({ person_id: id, version: 0, role_keys: [], role_names: [], can_edit: true, account_activated: false }))
  vi.stubGlobal("fetch", fetcher)
  expect(await assistedPeopleApi.activationRoles(id, signal)).toMatchObject({ version: 0, role_keys: [] })
  expect(fetcher).toHaveBeenCalledWith(`/api/v1/personnel-intake/people/${id}/activation-roles`, expect.objectContaining({ signal, cache: "no-store" }))
  await expect(assistedPeopleApi.activationRoles("30000000-0000-4000-8000-000000000002", signal)).rejects.toThrow()
})

it("transmits the explicit atomic staff ending confirmation without modifying selected roles or version", async () => {
  const signal = new AbortController().signal
  const fetcher = vi.fn(async () => Response.json({ person_id: id, version: 3, role_keys: ["guardian"], role_names: ["监护人"], can_edit: true, account_activated: false }))
  vi.stubGlobal("fetch", fetcher)
  await assistedPeopleApi.saveActivationRoles(id, { role_keys: ["guardian"], expected_version: 2, idempotency_key: id, confirm_end_staff_positions: true }, signal)
  expect(fetcher).toHaveBeenCalledWith(`/api/v1/personnel-intake/people/${id}/activation-roles`, expect.objectContaining({ signal, cache: "no-store", method: "PUT", body: JSON.stringify({ role_keys: ["guardian"], expected_version: 2, idempotency_key: id, confirm_end_staff_positions: true }) }))
})

it.each([{ roleKeys: ["admin"] }, { roleKeys: ["super_admin"] }, { roleKeys: ["coach", "coach"] }])("rejects illegal plan updates before transport $roleKeys", async ({ roleKeys }) => {
  const fetcher = vi.fn()
  vi.stubGlobal("fetch", fetcher)
  await expect(assistedPeopleApi.saveActivationRoles(id, { role_keys: roleKeys, expected_version: 0, idempotency_key: id }, new AbortController().signal)).rejects.toThrow()
  expect(fetcher).not.toHaveBeenCalled()
})

it("parses return-permit discriminators in strict, pending and guardian evidence", () => {
  const guardian = { full_name: "合成监护", identity_number: "M87654321", document_type: "hk_mo_permit", document_issuing_country: null,
    phone: "13800000000", email: null, relationship: "母亲", sex: "female", date_of_birth: "1980-01-02", is_primary: true }
  const strict = { kind: "student", full_name: "合成本人", identity_number: "H12345678", document_type: "hk_mo_permit", document_issuing_country: null,
    phone: "19990000001", email: null, sex: "female", date_of_birth: "2012-01-02", guardians: [guardian], coach_person_ids: [], team_id: null }
  for (const payload of [strict, { ...pendingPayload, document_type: "hk_mo_permit", identity_number: "H12345678", guardians: [guardian] }]) {
    expect(intakeDetailSchema.parse({ ...item, scoped_payload: payload, returned_reason: null }).scoped_payload).toEqual(payload)
  }
})

it("parses explicit HK strict evidence and nullable pending evidence while retaining legacy field omissions", () => {
  const base = { ...item, returned_reason: null }
  const strict = { kind: "student", full_name: "测试", identity_number: "A123456(3)", document_type: "hk_id", document_issuing_country: null,
    phone: "13800000000", email: null, sex: "female", date_of_birth: "1990-01-02", guardians: [], coach_person_ids: [], team_id: null }
  expect(intakeDetailSchema.parse({ ...base, scoped_payload: strict }).scoped_payload).toEqual(strict)
  const pending = { ...pendingPayload, document_type: "hk_id", identity_number: "AB123456(9)" }
  expect(intakeDetailSchema.parse({ ...base, scoped_payload: pending }).scoped_payload).toEqual(pending)
})

it("creates an explicit pending passport record without invented personal facts or roles", async () => {
  setCsrfToken("test")
  const fetcher = vi.fn<typeof fetch>(async () => Response.json({ request_id: null, person_id: id, approval_id: id }))
  vi.stubGlobal("fetch", fetcher)
  const input = { kind: "staff" as const, full_name: "Passport Person", identity_number: "AB123456", document_type: "passport" as const,
    document_issuing_country: null, phone: null, email: null, sex: null, date_of_birth: null, guardians: [], coach_person_ids: [], team_id: null, team_allocation_status: "pending" as const }
  const signal = new AbortController().signal
  await assistedPeopleApi.directCreatePending(input, id, signal)
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/personnel-intake/direct-create-pending")
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ ...input, idempotency_key: id })
  expect(fetcher.mock.calls[0][1]).toMatchObject({ signal, cache: "no-store", method: "POST" })
})

it("distinguishes non-sensitive list items from authorized detail and approved profile version", () => {
  expect(intakeListSchema.safeParse({ items: [item], total: 1, page: 1, page_size: 20 }).success).toBe(true)
  expect(intakeListSchema.safeParse({ items: [{ ...item, scoped_payload: {} }], total: 1, page: 1, page_size: 20 }).success).toBe(false)
  expect(intakeDetailSchema.parse({ ...item, scoped_payload: null, returned_reason: null, profile_version: 4 }).profile_version).toBe(4)
  expect(intakeDetailSchema.safeParse({ ...item, scoped_payload: null, returned_reason: null, profile_version: 0 }).success).toBe(false)
})

it("loads a mixed strict and pending-staff list, while detail requires explicit version-2 discrimination", async () => {
  const staff = { ...item, kind: "staff" }
  expect(intakeListSchema.parse({ items: [item, staff], total: 2, page: 1, page_size: 20 }).items.map((row) => row.kind)).toEqual(["student", "staff"])
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ...staff, scoped_payload: pendingPayload, returned_reason: null })))
  expect((await assistedPeopleApi.detail(id, new AbortController().signal)).scoped_payload).toEqual(pendingPayload)
  const { material_version: _version, ...ambiguous } = pendingPayload
  void _version
  expect(intakeDetailSchema.safeParse({ ...staff, scoped_payload: ambiguous, returned_reason: null }).success).toBe(false)
  expect(intakeDetailSchema.safeParse({ ...staff, scoped_payload: { ...pendingPayload, material_version: 3 }, returned_reason: null }).success).toBe(false)
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

it.each([true, false, undefined])("parses independent pending-create capability %s without falling back to ordinary create", async (capability) => {
  const context = { can_submit: true, can_review: true, can_direct_create: true, can_read_managed: false,
    allowed_team_ids: [], eligible_coaches: [], eligible_teams: [], ...(capability === undefined ? {} : { can_direct_create_pending: capability }) }
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(context)))
  const result = await assistedPeopleApi.context(new AbortController().signal)
  expect(result.can_direct_create).toBe(true)
  expect(result.can_direct_create_pending).toBe(capability ?? false)
})

it("rejects SecretStr stars as public authorized detail data rather than displaying a false decrypted record", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ...item, scoped_payload: { kind: "student", full_name: "测试", identity_number: "**********", phone: "**********", email: "**********", sex: "male", date_of_birth: "1990-01-01", guardians: [], coach_person_ids: [], team_id: null }, returned_reason: null })))
  await expect(assistedPeopleApi.detail(id, new AbortController().signal)).rejects.toThrow()
})

it.each([null, "legacy@example.test"])("loads authorized nullable and legacy email detail %s", async (email) => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ...item, scoped_payload: { kind: "student", full_name: "测试", identity_number: "110105199001021238", phone: "13800000000", email, sex: "male", date_of_birth: "1990-01-02", guardians: [], coach_person_ids: [], team_id: null }, returned_reason: null })))
  expect((await assistedPeopleApi.detail(id, new AbortController().signal)).scoped_payload?.email).toBe(email)
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
