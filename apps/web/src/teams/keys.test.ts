import { expect, it } from "vitest"

import { teamKeys } from "./keys"

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
