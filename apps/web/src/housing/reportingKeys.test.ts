import { describe, expect, it } from "vitest"

import {
  housingReportInvalidationKeys,
  normalizeHousingReportFilters,
  reportingKeys,
} from "./reportingKeys"

describe("housing reporting query keys", () => {
  it("normalizes repeated filters into deterministic order", () => {
    expect(normalizeHousingReportFilters({
      statuses: ["voided", "generated", "generated"],
      buildingIds: ["building-b", "building-a", "building-a"],
      floorIds: ["floor-b", "floor-a"],
      cohortIds: ["cohort-b", "cohort-a"],
      page: 1,
    })).toEqual({
      statuses: ["generated", "voided"],
      buildingIds: ["building-a", "building-b"],
      floorIds: ["floor-a", "floor-b"],
      cohortIds: ["cohort-a", "cohort-b"],
      page: 1,
    })
  })

  it("scopes private report caches to account and access revision", () => {
    expect(reportingKeys.list("account-1", "revision-1", {
      statuses: ["voided", "generated"],
      page: 1,
    })).toEqual([
      "housing",
      "account-1",
      "reports",
      "revision-1",
      "lists",
      { statuses: ["generated", "voided"], page: 1 },
    ])
    expect(reportingKeys.detail("account-1", "revision-1", "batch-1")).toEqual([
      "housing",
      "account-1",
      "reports",
      "revision-1",
      "details",
      "batch-1",
    ])
  })

  it("returns the list root and deterministic exact details for a mutation", () => {
    expect(housingReportInvalidationKeys(
      "account-1",
      "current",
      "revision-1",
      ["batch-2", "batch-1", "batch-2"],
    )).toEqual([
      ["housing", "account-1", "reports", "current", "lists"],
      ["housing", "account-1", "reports", "revision-1", "details", "batch-1"],
      ["housing", "account-1", "reports", "revision-1", "details", "batch-2"],
    ])
  })
})
