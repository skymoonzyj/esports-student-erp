import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { GuardianPanel } from "./GuardianPanel"

afterEach(() => vi.unstubAllGlobals())

it("binds an existing primary guardian and refreshes readiness", async () => {
  let linked = false
  const guardian = {
    id: "40000000-0000-4000-8000-000000000001",
    full_name: "测试家长",
    person_types: ["guardian"],
    identity_number: "110105********002X",
    phone: "139****9000",
    email: "guardian@example.com",
    sex: "female",
    date_of_birth: "1988-01-02",
    created_at: "2026-07-26T10:00:00+08:00",
    age_status: "adult",
    guardian_status: "not_applicable",
    relationship_id: "50000000-0000-4000-8000-000000000001",
    relationship: "母亲",
    is_primary: true,
    valid_from: "2026-07-26",
    valid_until: null,
    status: "active",
    ended_at: null,
    end_reason: null,
  }
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/readiness")) {
      return new Response(
        JSON.stringify(
          linked
            ? {
                ready: true,
                missing: [],
                age_status: "minor",
                guardian_status: "ready",
              }
            : {
                ready: false,
                missing: ["guardian"],
                age_status: "minor",
                guardian_status: "pending",
                guardian_issues: [],
              },
        ),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/guardians") && init?.method === "POST") {
      linked = true
      return new Response(JSON.stringify(guardian), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.includes("/guardians")) {
      return new Response(JSON.stringify(linked ? [guardian] : []), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.includes("/people?") && url.includes("name=")) {
      return new Response(
        JSON.stringify({ items: [guardian], page: 1, page_size: 10, total: 1 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <GuardianPanel
          studentId="30000000-0000-4000-8000-000000000001"
          canManage
        />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("未成年 · 待绑定监护人")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "绑定监护人" }))
  await user.type(screen.getByLabelText("查找已有监护人"), "测试家长")
  await user.click(screen.getByRole("button", { name: "搜索" }))
  await user.click(await screen.findByRole("radio", { name: "测试家长 139****9000" }))
  await user.clear(screen.getByLabelText("关系"))
  await user.type(screen.getByLabelText("关系"), "母亲")
  await user.click(screen.getByLabelText("设为主要监护人"))
  await user.click(screen.getByRole("button", { name: "确认绑定" }))

  expect(await screen.findByText("未成年 · 监护人已绑定")).toBeVisible()
  expect(screen.getByText("测试家长")).toBeVisible()
  expect(screen.getByText("主要监护人")).toBeVisible()
  expect(
    fetchMock.mock.calls.some(([input]) =>
      String(input).includes("/guardians?include_inactive=true"),
    ),
  ).toBe(true)
})

it("shows administrator diagnostics and repair links without exposing account ids", async () => {
  const studentId = "30000000-0000-4000-8000-000000000021"
  const guardianId = "40000000-0000-4000-8000-000000000021"
  const relationshipId = "50000000-0000-4000-8000-000000000021"
  const readiness = {
    ready: false,
    missing: ["guardian"],
    age_status: "minor",
    guardian_status: "pending",
    guardian_issues: [
      {
        relationship_id: relationshipId,
        guardian_person_id: guardianId,
        account_id: null,
        reason_codes: [
          "guardian_account_missing",
          "guardian_role_missing",
          "relationship_verification_required",
        ],
      },
    ],
  }
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.includes("/readiness")) {
        return new Response(JSON.stringify(readiness), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.includes("/guardians")) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      throw new Error(`Unexpected request: ${url}`)
    }),
  )

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <GuardianPanel studentId={studentId} canManage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("尚未开通独立账号")).toBeVisible()
  expect(screen.getByText("缺少监护人角色")).toBeVisible()
  expect(screen.getByText("监护关系待验证")).toBeVisible()
  expect(screen.getByRole("link", { name: "前往账号管理" })).toHaveAttribute(
    "href",
    "/settings/accounts",
  )
  expect(screen.getByRole("link", { name: "前往权限管理" })).toHaveAttribute(
    "href",
    "/settings/permissions",
  )
  expect(document.body.textContent).not.toContain(relationshipId)
  expect(document.body.textContent).not.toContain(guardianId)
})

it("hides internal guardian diagnostics when the API omits them", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      const body = url.includes("/readiness")
        ? {
            ready: false,
            missing: ["guardian"],
            age_status: "minor",
            guardian_status: "pending",
          }
        : []
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }),
  )

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <GuardianPanel studentId="30000000-0000-4000-8000-000000000022" />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("未成年 · 待绑定监护人")).toBeVisible()
  expect(screen.queryByText("尚未开通独立账号")).not.toBeInTheDocument()
  expect(screen.queryByText("缺少监护人角色")).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "前往账号管理" })).not.toBeInTheDocument()
})

it("keeps adult guardian relationships optional and manageable", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      const body = url.includes("/readiness")
        ? {
            ready: true,
            missing: [],
            age_status: "adult",
            guardian_status: "not_applicable",
            guardian_issues: [],
          }
        : []
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }),
  )

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <GuardianPanel studentId="30000000-0000-4000-8000-000000000023" canManage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("成年 · 监护关系非强制")).toBeVisible()
  expect(screen.getByRole("button", { name: "绑定监护人" })).toBeVisible()
})

it("ends a relationship with a reason and retains the history row", async () => {
  const studentId = "30000000-0000-4000-8000-000000000024"
  const relationshipId = "50000000-0000-4000-8000-000000000024"
  let ended = false
  const guardian = {
    id: "40000000-0000-4000-8000-000000000024",
    full_name: "待结束监护人",
    person_types: ["guardian"],
    identity_number: "110105********024X",
    phone: "139****9024",
    email: "guardian-24@example.com",
    sex: "female",
    date_of_birth: "1988-01-02",
    created_at: "2026-07-26T10:00:00+08:00",
    age_status: "adult",
    guardian_status: "not_applicable",
    relationship_id: relationshipId,
    relationship: "母亲",
    is_primary: true,
    valid_from: "2026-01-01",
    valid_until: null,
    status: ended ? "ended" : "active",
    ended_at: ended ? "2026-07-27T12:00:00+08:00" : null,
    end_reason: ended ? "监护安排调整" : null,
  }
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/readiness")) {
      return new Response(
        JSON.stringify({
          ready: !ended,
          missing: ended ? ["guardian"] : [],
          age_status: "minor",
          guardian_status: ended ? "pending" : "ready",
          guardian_issues: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith(`/guardians/${relationshipId}/end`) && init?.method === "POST") {
      ended = true
      return new Response(JSON.stringify({ ...guardian, status: "ended", end_reason: "监护安排调整" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.includes("/guardians")) {
      return new Response(
        JSON.stringify([
          {
            ...guardian,
            status: ended ? "ended" : "active",
            ended_at: ended ? "2026-07-27T12:00:00+08:00" : null,
            end_reason: ended ? "监护安排调整" : null,
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient()
  const invalidate = vi.spyOn(queryClient, "invalidateQueries")
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <GuardianPanel studentId={studentId} canManage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.click(await screen.findByRole("button", { name: "结束监护关系" }))
  expect(screen.getByRole("dialog", { name: "结束监护关系" })).toBeVisible()
  expect(screen.getByLabelText("结束原因")).toHaveAttribute("minlength", "2")
  await user.type(screen.getByLabelText("结束原因"), "  ")
  await user.click(screen.getByRole("button", { name: "确认结束" }))
  expect(await screen.findByText("结束原因至少需要 2 个字符")).toBeVisible()
  expect(
    fetchMock.mock.calls.some(
      ([input, init]) =>
        String(input).endsWith(`/guardians/${relationshipId}/end`) &&
        init?.method === "POST",
    ),
  ).toBe(false)
  await user.clear(screen.getByLabelText("结束原因"))
  await user.type(screen.getByLabelText("结束原因"), "监护安排调整")
  await user.click(screen.getByRole("button", { name: "确认结束" }))

  expect(await screen.findByText("已结束")).toBeVisible()
  expect(screen.getByText("结束原因：监护安排调整")).toBeVisible()
  expect(screen.getByText("待结束监护人")).toBeVisible()
  expect(
    fetchMock.mock.calls.some(
      ([input, init]) =>
        String(input).endsWith(`/guardians/${relationshipId}/end`) &&
        init?.method === "POST" &&
        JSON.parse(String(init.body)).reason === "监护安排调整",
    ),
  ).toBe(true)
  await waitFor(() => {
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["guardians", studentId] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["readiness", studentId] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["people"] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["person", studentId] })
  })
})
