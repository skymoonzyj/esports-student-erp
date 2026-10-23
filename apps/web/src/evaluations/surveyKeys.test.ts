import { expect, it } from "vitest"
import { surveyKeys } from "./surveyKeys"

it("normalizes survey query keys", () => {
  expect(surveyKeys.contextRoot("a")).toEqual(["camp-surveys", "a", "context"])
  expect(surveyKeys.context("a")).toEqual(["camp-surveys", "a", "context", { page: 1, pageSize: 20 }])
  expect(surveyKeys.admin("a", { identity: "hidden", page: 1, pageSize: 20 })).toEqual([
    "camp-surveys", "a", "admin", { cohortId: null, projectId: null, staffPersonId: null, roleId: null, roleKey: null, identity: "hidden", page: 1, pageSize: 20 },
  ])
})
