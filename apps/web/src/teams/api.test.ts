import { afterEach, expect, it, vi } from "vitest"

import { assign, candidates, getTeamContext, join, leave, listAssignments, listMemberships, listTeams, managerCandidates, myTeam, transfer, unassign } from "./api"

afterEach(() => vi.unstubAllGlobals())

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
