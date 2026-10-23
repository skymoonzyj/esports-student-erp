import { afterEach, describe, expect, it, vi } from "vitest"

import { setCsrfToken } from "../api/client"
import {
  confirmWithdrawal,
  createManualPayment,
  getEnrollmentActionContext,
  listAdmissionEnrollmentOptions,
  listAdmissionCases,
  listAdmissionStudentCandidates,
  listEnrollmentTransferOptions,
  listEnrollments,
  listPayments,
  previewWithdrawal,
} from "./api"

afterEach(() => {
  setCsrfToken(null)
  vi.unstubAllGlobals()
})

function successfulJson(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

describe("admissions API", () => {
  it("posts the exact withdrawal preview contract", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        successfulJson({
          withdrawal_date: "2026-08-06",
          policy: "prorated_remaining_days",
          total_days: 10,
          remaining_days: 4,
          agreed_tuition_cents: 10_001,
          available_paid_cents: 20_000,
          refund_cents: 4_000,
          allocations: [{ payment_id: "payment-1", amount_cents: 4_000 }],
        }),
      )
    vi.stubGlobal("fetch", fetchMock)
    setCsrfToken("admissions-csrf")

    await previewWithdrawal("enrollment-1", {
      withdrawal_date: "2026-08-06",
      policy: "prorated_remaining_days",
    })

    const [input, init] = fetchMock.mock.calls[0] ?? []
    expect(input).toBe(
      "/api/v1/enrollments/enrollment-1/withdrawal-refund-preview",
    )
    expect(init?.method).toBe("POST")
    expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("admissions-csrf")
    expect(JSON.parse(String(init?.body))).toEqual({
      withdrawal_date: "2026-08-06",
      policy: "prorated_remaining_days",
    })
  })

  it("sends the server preview snapshot unchanged for final confirmation", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(successfulJson({}))
    vi.stubGlobal("fetch", fetchMock)
    const preview = {
      withdrawal_date: "2026-08-06",
      policy: "full_remaining_paid" as const,
      total_days: 10,
      remaining_days: 4,
      agreed_tuition_cents: 1_280_000,
      available_paid_cents: 960_000,
      refund_cents: 960_000,
      allocations: [{ payment_id: "payment-2", amount_cents: 960_000 }],
    }

    await confirmWithdrawal("enrollment-1", {
      ...preview,
      reason: "家庭安排变化",
    })

    const [input, init] = fetchMock.mock.calls[0] ?? []
    expect(input).toBe(
      "/api/v1/enrollments/enrollment-1/withdrawal-refund-confirm",
    )
    expect(JSON.parse(String(init?.body))).toEqual({
      ...preview,
      reason: "家庭安排变化",
    })
  })

  it("keeps decimal yuan input as a string when recording a payment", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(successfulJson({}))
    vi.stubGlobal("fetch", fetchMock)

    await createManualPayment({
      idempotency_key: "00000000-0000-4000-8000-000000000001",
      student_person_id: "student-1",
      admission_case_id: null,
      amount: "36800.05",
      paid_at: "2026-08-01T10:00:00+08:00",
      note: "线下转账",
    })

    const [, init] = fetchMock.mock.calls[0] ?? []
    expect(JSON.parse(String(init?.body)).amount).toBe("36800.05")
  })

  it("maps list filters to the backend's stable pagination contract", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation(async () =>
        successfulJson({ items: [], page: 2, page_size: 50, total: 0 }),
      )
    vi.stubGlobal("fetch", fetchMock)

    await listPayments({
      orderNumber: " bm20260911000001 ",
      sourceKind: "manual",
      sourceSystem: "offline",
      payerPhone: "13800138000",
      dateFrom: "2026-08-01T00:00:00+08:00",
      dateTo: "2026-09-01T00:00:00+08:00",
      page: 2,
      pageSize: 50,
    })
    await listAdmissionCases({
      orderNumber: " bm20260911000001\n",
      status: "ready",
      applicationSource: "self_service",
      studentPhone: "13800138000",
      page: 2,
      pageSize: 50,
    })
    await listEnrollments({
      status: "active",
      cohortId: "cohort-1",
      classId: "class-1",
      page: 2,
      pageSize: 50,
    })

    const urls = fetchMock.mock.calls.map(([input]) =>
      new URL(String(input), "http://local.test"),
    )
    expect(urls[0]?.pathname).toBe("/api/v1/payments")
    expect(Object.fromEntries(urls[0]?.searchParams ?? [])).toMatchObject({
      order_number: "BM20260911000001",
      source_kind: "manual",
      source_system: "offline",
      payer_phone: "13800138000",
      date_from: "2026-08-01T00:00:00+08:00",
      date_to: "2026-09-01T00:00:00+08:00",
      page: "2",
      page_size: "50",
    })
    expect(Object.fromEntries(urls[1]?.searchParams ?? [])).toMatchObject({
      order_number: "BM20260911000001",
      status: "ready",
      application_source: "self_service",
      student_phone: "13800138000",
      page: "2",
      page_size: "50",
    })
    expect(Object.fromEntries(urls[2]?.searchParams ?? [])).toMatchObject({
      status: "active",
      cohort_id: "cohort-1",
      class_id: "class-1",
      page: "2",
      page_size: "50",
    })
  })

  it("uses case-scoped candidate and enrollment-option endpoints", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation(async () =>
        successfulJson({ items: [], page: 2, page_size: 10, total: 0 }),
      )
    vi.stubGlobal("fetch", fetchMock)

    await listAdmissionStudentCandidates("case-1", {
      query: "S20260001",
      page: 2,
      pageSize: 10,
    })
    await listAdmissionEnrollmentOptions("case-1", {
      page: 2,
      pageSize: 10,
    })

    const urls = fetchMock.mock.calls.map(([input]) =>
      new URL(String(input), "http://local.test"),
    )
    expect(urls[0]?.pathname).toBe(
      "/api/v1/admission-cases/case-1/student-candidates",
    )
    expect(Object.fromEntries(urls[0]?.searchParams ?? [])).toEqual({
      query: "S20260001",
      page: "2",
      page_size: "10",
    })
    expect(urls[1]?.pathname).toBe(
      "/api/v1/admission-cases/case-1/enrollment-options",
    )
    expect(Object.fromEntries(urls[1]?.searchParams ?? [])).toEqual({
      page: "2",
      page_size: "10",
    })
  })

  it("reads server-projected enrollment actions and transfer options", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation(async () =>
        successfulJson({ items: [], page: 2, page_size: 10, total: 0 }),
      )
    vi.stubGlobal("fetch", fetchMock)

    await getEnrollmentActionContext("enrollment-1")
    await listEnrollmentTransferOptions("enrollment-1", {
      page: 2,
      pageSize: 10,
    })

    const urls = fetchMock.mock.calls.map(([input]) =>
      new URL(String(input), "http://local.test"),
    )
    expect(urls[0]?.pathname).toBe(
      "/api/v1/enrollments/enrollment-1/action-context",
    )
    expect(urls[1]?.pathname).toBe(
      "/api/v1/enrollments/enrollment-1/transfer-options",
    )
    expect(Object.fromEntries(urls[1]?.searchParams ?? [])).toEqual({
      page: "2",
      page_size: "10",
    })
  })
})
