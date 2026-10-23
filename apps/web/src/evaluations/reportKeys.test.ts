import { describe, expect, it } from "vitest"

import {
  normalizeReportTargetFilters,
  normalizeReportVersionFilters,
  reportKeys,
} from "./reportKeys"

const cohortId = "40000000-0000-4000-8000-000000000001"
const projectId = "50000000-0000-4000-8000-000000000001"
const classId = "60000000-0000-4000-8000-000000000001"
const studentId = "30000000-0000-4000-8000-000000000001"
const reportId = "10000000-0000-4000-8000-000000000001"

describe("teaching report query keys", () => {
  it("isolates every cache tree by account and public selector fields", () => {
    const first = reportKeys.list("account-a", {
      enrollmentId: "enrollment-1",
      kind: "daily",
      status: "draft",
      page: 2,
      pageSize: 10,
    })
    const second = reportKeys.list("account-b", {
      enrollmentId: "enrollment-1",
      kind: "daily",
      status: "draft",
      page: 2,
      pageSize: 10,
    })

    expect(first).not.toEqual(second)
    expect(first).toEqual([
      "teaching-reports",
      "account-a",
      "list",
      {
        enrollmentId: "enrollment-1",
        kind: "daily",
        status: "draft",
        page: 2,
        pageSize: 10,
      },
    ])
  })

  it("never accepts report text or review reasons as cache-key inputs", () => {
    const root = reportKeys.root("account-a")
    const review = reportKeys.reviewQueue("account-a", {
      cohortId: "cohort-1",
      projectId: null,
      classId: null,
      page: 1,
      pageSize: 20,
    })
    const published = reportKeys.published("account-a", {
      studentId: "student-1",
      enrollmentId: null,
      page: 1,
      pageSize: 20,
    })
    const serialized = JSON.stringify([root, review, published])

    expect(serialized).not.toContain("body")
    expect(serialized).not.toContain("reason")
    expect(serialized).not.toContain("private-report-text")
  })

  it("normalizes target and version selectors inside the account root", () => {
    const targetFilters = {
      cohortId,
      projectId,
      classId,
      studentId,
      query: "学员_%",
      page: 2,
      pageSize: 10,
    }
    expect(reportKeys.targets("account-a", targetFilters)).toEqual([
      "teaching-reports",
      "account-a",
      "targets",
      normalizeReportTargetFilters(targetFilters),
    ])
    expect(
      reportKeys.versions("account-a", { reportId, page: 1, pageSize: 20 }),
    ).toEqual([
      "teaching-reports",
      "account-a",
      "versions",
      { reportId, page: 1, pageSize: 20 },
    ])
    expect(
      normalizeReportTargetFilters({ query: "   ", cohortId: null }),
    ).toEqual({
      cohortId: null,
      projectId: null,
      classId: null,
      studentId: null,
      query: null,
      page: 1,
      pageSize: 20,
    })
    expect(normalizeReportVersionFilters({ reportId })).toEqual({
      reportId,
      page: 1,
      pageSize: 20,
    })

    const serialized = JSON.stringify([
      reportKeys.targets("account-a", targetFilters),
      reportKeys.versions("account-a", { reportId }),
    ])
    expect(serialized).not.toContain("body")
    expect(serialized).not.toContain("reason")
  })
})
