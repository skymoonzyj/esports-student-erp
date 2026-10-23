import { describe, expect, it } from "vitest"

describe("system evaluation query keys", () => {
  it("nests every actor projection below the account evaluation root", async () => {
    const { systemEvaluationKeys } = await import("./systemEvaluationKeys")

    expect(systemEvaluationKeys.root("account-1")).toEqual([
      "evaluations",
      "account-1",
      "system",
      "v2",
    ])
    expect(systemEvaluationKeys.projection("account-1", "account")).toEqual([
      "evaluations",
      "account-1",
      "system",
      "v2",
      "account",
    ])
    expect(systemEvaluationKeys.projection("account-1", "admin")).toEqual([
      "evaluations",
      "account-1",
      "system",
      "v2",
      "admin",
    ])

    expect(systemEvaluationKeys.filterOptions("account-1", "account")).toEqual([
      "evaluations",
      "account-1",
      "system",
      "v2",
      "account",
      "filter-options",
    ])
    expect(systemEvaluationKeys.filterOptions("account-1", "admin")).toEqual([
      "evaluations",
      "account-1",
      "system",
      "v2",
      "admin",
      "filter-options",
    ])
    expect(systemEvaluationKeys.filterOptions("account-2", "account")).not.toEqual(
      systemEvaluationKeys.filterOptions("account-1", "account"),
    )
  })

  it("normalizes filters and pagination into deterministic cache keys", async () => {
    const { systemEvaluationKeys } = await import("./systemEvaluationKeys")

    const first = systemEvaluationKeys.list("account-1", "admin", {
      queue: "needs_review",
      cohortId: " cohort-1 ",
      projectId: "project-1",
      classId: "",
      studentId: undefined,
      page: 0,
      pageSize: 500,
    })
    const second = systemEvaluationKeys.list("account-1", "admin", {
      pageSize: 100,
      page: 1,
      projectId: "project-1",
      cohortId: "cohort-1",
      queue: "needs_review",
    })

    expect(first).toEqual(second)
    expect(first).toEqual([
      "evaluations",
      "account-1",
      "system",
      "v2",
      "admin",
      "lists",
      {
        queue: "needs_review",
        cohortId: "cohort-1",
        projectId: "project-1",
        page: 1,
        pageSize: 100,
      },
    ])
  })

  it("keeps account aggregate queues distinct from exact status filters", async () => {
    const { systemEvaluationKeys } = await import("./systemEvaluationKeys")

    const aggregate = systemEvaluationKeys.list("account-1", "account", {
      queue: "processing",
      page: 1,
      pageSize: 20,
    })
    const exact = systemEvaluationKeys.list("account-1", "account", {
      status: "processing",
      page: 1,
      pageSize: 20,
    })

    expect(aggregate).not.toEqual(exact)
    expect(aggregate.at(-1)).toEqual({
      queue: "processing",
      page: 1,
      pageSize: 20,
    })
  })

  it("keeps details, published history and certifications in separate roots", async () => {
    const { systemEvaluationKeys } = await import("./systemEvaluationKeys")

    expect(systemEvaluationKeys.dashboard("account-1")).toEqual([
      "evaluations",
      "account-1",
      "system",
      "v2",
      "dashboard",
    ])
    expect(systemEvaluationKeys.detail("account-1", "account", "task-1")).toEqual([
      "evaluations",
      "account-1",
      "system",
      "v2",
      "account",
      "details",
      "task-1",
    ])
    expect(systemEvaluationKeys.workspace("account-1", "admin", "task-1")).toEqual([
      "evaluations",
      "account-1",
      "system",
      "v2",
      "admin",
      "workspaces",
      "task-1",
    ])
    expect(systemEvaluationKeys.published("account-1", "student-1", 2)).toEqual([
      "evaluations",
      "account-1",
      "system",
      "v2",
      "published",
      "student-1",
      2,
    ])
    expect(systemEvaluationKeys.certifications("account-1")).toEqual([
      "evaluations",
      "account-1",
      "system",
      "v2",
      "certifications",
    ])
  })

  it("never retains free text, advice, reasons or idempotency keys", async () => {
    const { systemEvaluationKeys } = await import("./systemEvaluationKeys")
    const privateReason = "private-reason-13800138000"
    const key = systemEvaluationKeys.list(
      "account-1",
      "account",
      {
        status: "published",
        studentId: "student-1",
        page: 1,
        pageSize: 20,
        reason: privateReason,
        advice: privateReason,
        idempotencyKey: privateReason,
      } as never,
    )

    expect(JSON.stringify(key)).not.toContain(privateReason)
  })
})
