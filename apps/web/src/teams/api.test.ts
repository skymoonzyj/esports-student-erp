import { afterEach, expect, it, vi } from "vitest"

import { assign, candidates, getTeamContext, join, leave, listAssignments, listMemberships, listTeams, managerCandidates, myTeam, transfer, unassign } from "./api"
import * as teamApi from "./api"

afterEach(() => vi.unstubAllGlobals())

const teamId = "10000000-0000-4000-8000-000000000001"
const personId = "20000000-0000-4000-8000-000000000001"
const positionId = "30000000-0000-4000-8000-000000000001"
const operationId = "40000000-0000-4000-8000-000000000001"
const positionRow = { row_key: `person:${positionId}`, source_kind: "person", person_position_id: positionId, account_assignment_id: null,
  person_id: personId, account_id: null, full_name: "合成教练", nickname: "甲", staff_role: "coach", account_status: "not_activated", authorization_status: "reconfirmation_required", started_on: "2026-11-06", ended_on: null, version: 2, allowed_actions: ["end", "reconfirm"] }
const mutation = { operation_id: operationId, membership_id: null, closed_membership_id: null, assignment_id: positionId }

it("uses private source-aware staff routes with bounded role search and versioned commands", async () => {
  const fetcher = vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === "POST") return Response.json(mutation)
    if (String(url).includes("person-staff-candidates")) return Response.json({ items: [{ person_id: personId, account_id: null, full_name: "合成教练", nickname: "甲" }], next_cursor: "next" })
    return Response.json({ items: [positionRow], page: 2, page_size: 20, total: 21 })
  })
  vi.stubGlobal("fetch", fetcher)
  const signal = new AbortController().signal
  expect((await teamApi.listStaffPositions(teamId, { status: "current", page: 2, pageSize: 20 }, signal)).items[0]).toEqual(positionRow)
  await teamApi.personStaffCandidates(teamId, { staffRole: "coach", query: "  合成  ", cursor: "next", pageSize: 20 }, signal)
  await teamApi.assignPersonStaff(teamId, { person_id: personId, staff_role: "coach", idempotency_key: operationId }, signal)
  await teamApi.endPersonStaff(positionId, { reason: "  岗位结束  ", expected_version: 2, idempotency_key: operationId }, signal)
  await teamApi.reconfirmPersonStaff(positionId, { reason: "已核实", expected_version: 2, idempotency_key: operationId }, signal)
  const urls = fetcher.mock.calls.map(([url]) => new URL(String(url), "http://local.test"))
  expect(urls.map(url => url.pathname)).toEqual([
    `/api/v1/team-management/teams/${teamId}/staff-positions`, `/api/v1/team-management/teams/${teamId}/person-staff-candidates`,
    `/api/v1/team-management/teams/${teamId}/person-staff-positions`, `/api/v1/team-management/person-staff-positions/${positionId}/end`, `/api/v1/team-management/person-staff-positions/${positionId}/reconfirm`,
  ])
  expect(Object.fromEntries(urls[0].searchParams)).toEqual({ status: "current", page: "2", page_size: "20" })
  expect(Object.fromEntries(urls[1].searchParams)).toEqual({ staff_role: "coach", query: "合成", cursor: "next", page_size: "20" })
  expect(JSON.parse(String(fetcher.mock.calls[3][1]?.body))).toEqual({ reason: "岗位结束", expected_version: 2, idempotency_key: operationId })
  for (const [, init] of fetcher.mock.calls) expect(init).toMatchObject({ signal, cache: "no-store" })
})

it.each([
  { version: "2" }, { version: 0 }, { row_key: `account:${positionId}` }, { source_kind: "account" },
  { allowed_actions: ["delete"] }, { ended_on: "2026-11-07", allowed_actions: ["end"] }, { phone: "private" },
])("rejects malformed staff source/status/action DTOs: %j", async patch => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => Response.json({ items: [{ ...positionRow, ...patch }], page: 1, page_size: 20, total: 1 })))
  await expect(teamApi.listStaffPositions(teamId)).rejects.toThrow()
})

it("rejects mismatched maintenance responses and invalid commands without transmitting invalid requests", async () => {
  const fetcher = vi.fn<typeof fetch>(async () => Response.json({ ...mutation, assignment_id: personId }))
  vi.stubGlobal("fetch", fetcher)
  await expect(teamApi.endPersonStaff(positionId, { expected_version: 2, reason: "结束", idempotency_key: operationId })).rejects.toThrow()
  await expect(teamApi.reconfirmPersonStaff(positionId, { expected_version: 0, reason: " ", idempotency_key: operationId })).rejects.toThrow()
  await expect(teamApi.personStaffCandidates(teamId, { staffRole: "coach", query: "x", pageSize: 21 })).rejects.toThrow()
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it.each(["endPersonStaff", "reconfirmPersonStaff"] as const)("rejects a reason longer than the audited 200-character limit without truncation or transport: %s", async command => {
  const fetcher = vi.fn<typeof fetch>(async () => Response.json(mutation))
  vi.stubGlobal("fetch", fetcher)
  const input = { expected_version: 2, reason: "核".repeat(201), idempotency_key: operationId }
  await expect(teamApi[command](positionId, input)).rejects.toThrow()
  expect(fetcher).not.toHaveBeenCalled()
  expect(input.reason).toHaveLength(201)
  await teamApi[command](positionId, { ...input, reason: "  " + "核".repeat(200) + "  " })
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body)).reason).toBe("核".repeat(200))
})

it("keeps read-only staff routes, role filters, history and operation payloads separate from legacy management", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({ items: [], next_cursor: null }), { headers: { "Content-Type": "application/json" } }))
  vi.stubGlobal("fetch", fetchMock)
  await teamApi.staffCandidates("team-a", { staffRole: "assistant_coach", query: "张", cursor: "next", pageSize: 20 })
  await teamApi.listStaffAssignments("team-a", { page: 2, pageSize: 20, status: "ended" })
  await teamApi.assignStaff("team-a", { account_id: "staff-a", staff_role: "coach", idempotency_key: "stable-key" })
  await teamApi.endStaffAssignment("assignment-a", { idempotency_key: "end-key" })
  const urls = fetchMock.mock.calls.map(([input]) => new URL(String(input), "http://local.test"))
  expect(urls.map((url) => url.pathname)).toEqual([
    "/api/v1/team-management/teams/team-a/staff-candidates",
    "/api/v1/team-management/teams/team-a/staff-assignments",
    "/api/v1/team-management/teams/team-a/staff-assignments",
    "/api/v1/team-management/staff-assignments/assignment-a/end",
  ])
  expect(Object.fromEntries(urls[0]!.searchParams)).toEqual({ staff_role: "assistant_coach", query: "张", cursor: "next", page_size: "20" })
  expect(Object.fromEntries(urls[1]!.searchParams)).toEqual({ page: "2", page_size: "20", status: "ended" })
  expect(JSON.parse(String(fetchMock.mock.calls[2]?.[1]?.body))).toEqual({ account_id: "staff-a", staff_role: "coach", idempotency_key: "stable-key" })
  expect(JSON.parse(String(fetchMock.mock.calls[3]?.[1]?.body))).toEqual({ idempotency_key: "end-key" })
})

it("accepts accountless member candidates but rejects accountless manager candidates", async () => {
  const candidate = { account_id: null, person_id: "person-1", full_name: "未激活队员", nickname: null, age: 20, is_minor: false }
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ items: [candidate], next_cursor: null }), { headers: { "Content-Type": "application/json" } })))
  expect((await candidates("team-a", { query: "未激活" })).items[0].account_id).toBeNull()
  await expect(managerCandidates("team-a", { query: "未激活" })).rejects.toThrow()
})

it("uses account-cookie roster routes, exact filters, and stable operation keys", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }), { status: 200, headers: { "Content-Type": "application/json" } }))
  vi.stubGlobal("fetch", fetchMock)
  await getTeamContext()
  await listTeams({ projectId: "division-a", page: 2, pageSize: 20 })
  await listMemberships("team-a", { status: "left", page: 3 })
  await candidates("team-a", { query: "王", cursor: "cursor-a" })
  await managerCandidates("team-a", { query: "张" })
  await myTeam({ page: 2 })
  await listAssignments("team-a", { page: 3 })
  await join("team-a", { person_id: "person-a", idempotency_key: "key-a" })
  await leave("membership-a", { idempotency_key: "key-b" })
  await transfer("membership-a", { target_team_id: "team-b", reason: "调队", idempotency_key: "key-c" })
  await assign("team-a", { account_id: "account-b", idempotency_key: "key-d" })
  await unassign("assignment-a", { idempotency_key: "key-e" })

  const urls = fetchMock.mock.calls.map(([input]) => new URL(String(input), "http://local.test"))
  expect(urls.map((url) => url.pathname)).toEqual([
    "/api/v1/team-management/context", "/api/v1/team-management/teams",
    "/api/v1/team-management/teams/team-a/memberships",
    "/api/v1/team-management/teams/team-a/candidates",
    "/api/v1/team-management/teams/team-a/manager-candidates",
    "/api/v1/team-management/me",
    "/api/v1/team-management/teams/team-a/manager-assignments",
    "/api/v1/team-management/teams/team-a/memberships",
    "/api/v1/team-management/memberships/membership-a/leave",
    "/api/v1/team-management/memberships/membership-a/transfer",
    "/api/v1/team-management/teams/team-a/manager-assignments",
    "/api/v1/team-management/manager-assignments/assignment-a/end",
  ])
  expect(urls[1]?.searchParams.get("project_id")).toBe("division-a")
  expect(urls[2]?.searchParams.get("status")).toBe("left")
  expect(urls[3]?.searchParams.get("query")).toBe("王")
  expect(urls[3]?.searchParams.get("cursor")).toBe("cursor-a")
  expect(JSON.parse(String(fetchMock.mock.calls[7]?.[1]?.body))).toEqual({ person_id: "person-a", idempotency_key: "key-a" })
  expect(JSON.parse(String(fetchMock.mock.calls[9]?.[1]?.body))).toEqual({ target_team_id: "team-b", reason: "调队", idempotency_key: "key-c" })
})
