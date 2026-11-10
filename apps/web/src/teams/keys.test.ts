import { expect, it } from "vitest"

import { teamKeys } from "./keys"

it("isolates person staff searches and unified rosters by actor, team, role, filter and pagination", () => {
  const keys = teamKeys("account-a")
  expect(keys.personStaffCandidates("team-a", "coach", "甲")).toEqual(["teams", "account-a", "person-staff-candidates", "team-a", "coach", "甲", 20])
  expect(keys.personStaffCandidates("team-a", "coach", "甲")).not.toEqual(keys.staffCandidates("team-a", "coach", "甲"))
  expect(keys.personStaffCandidates("team-a", "coach", "甲")).not.toEqual(keys.personStaffCandidates("team-a", "assistant_coach", "甲"))
  expect(keys.personStaffCandidates("team-a", "coach", "甲")).not.toEqual(teamKeys("account-b").personStaffCandidates("team-a", "coach", "甲"))
  expect(keys.staffPositions("team-a", "current", 2)).toEqual(["teams", "account-a", "staff-positions", "team-a", "current", 2, 20])
  expect(keys.staffPositions("team-a", "current", 2)).not.toEqual(keys.staffPositions("team-a", "ended", 2))
  expect(keys.staffPositions("team-a", "current", 2)).not.toEqual(keys.staffPositions("team-b", "current", 2))
  expect(keys.staffPositions("team-a", "current", 2)).not.toEqual(keys.staffPositions("team-a", "current", 1))
  expect(keys.staffPositions("team-a", "current", 2, 10)).not.toEqual(keys.staffPositions("team-a", "current", 2, 20))
})

it("isolates staff candidates by identity, team and role and keeps history caches separate", () => {
  const keys = teamKeys("account-a")
  expect(keys.staffCandidates("team-a", "coach", "张")).toEqual(["teams", "account-a", "staff-candidates", "team-a", "coach", "张"])
  expect(keys.staffCandidates("team-a", "coach", "张")).not.toEqual(keys.staffCandidates("team-a", "assistant_coach", "张"))
  expect(keys.staffAssignments("team-a", "ended", 2)).toEqual(["teams", "account-a", "staff-assignments", "team-a", "ended", 2])
  expect(keys.staffAssignments("team-a", "current", 1)).not.toEqual(teamKeys("account-b").staffAssignments("team-a", "current", 1))
})

it("isolates every roster query beneath the current account", () => {
  const keys = teamKeys("account-a")
  expect(keys.context()).toEqual(["teams", "account-a", "context"])
  expect(keys.teams({ projectId: "division-a", page: 2 })).toEqual([
    "teams", "account-a", "list", { projectId: "division-a", page: 2 },
  ])
  expect(keys.members("team-a", { status: "left", page: 3 })).toEqual([
    "teams", "account-a", "members", "team-a", { status: "left", page: 3 },
  ])
  expect(keys.candidates("team-a", "王", null)).toEqual([
    "teams", "account-a", "candidates", "team-a", "王", null,
  ])
  expect(keys.assignments("team-a", 1)).toEqual([
    "teams", "account-a", "assignments", "team-a", 1,
  ])
  expect(keys.self(2)).toEqual(["teams", "account-a", "self", 2])
})
