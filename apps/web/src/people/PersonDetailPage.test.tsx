import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Link, MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import {
  PersonDetailPage,
  SENSITIVE_REVEAL_DURATION_MS,
} from "./PersonDetailPage"

vi.mock("../auth/AuthProvider", () => {
  const refreshUser = vi.fn(async () => undefined)
  return {
    useAuth: () => ({
      user: { account_id: "10000000-0000-4000-8000-000000000001" },
      refreshUser,
    }),
  }
})

const personId = "30000000-0000-4000-8000-000000000001"
const otherPersonId = "30000000-0000-4000-8000-000000000002"

function personResponse(id = personId) {
  return {
    id,
    full_name: id === personId ? "测试学员" : "另一学员",
    person_types: ["student"],
    identity_number: "110105********002X",
    phone: "138****8000",
    email: "student@example.com",
    sex: "female",
    date_of_birth: "2012-01-02",
    created_at: "2026-07-26T10:00:00+08:00",
    age_status: "minor",
    guardian_status: "pending",
    student_number: "S-2026-001",
    student_status: "active",
    staff_type: null,
    visit_reason: null,
    linked_student_id: null,
  }
}

function renderDetail(revealDurationMs?: number) {
  const queryClient = new QueryClient()
  const result = render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route
            path="/people/:personId"
            element={
              <PersonDetailPage
                permissions={new Set([
                  "people.identity.reveal",
                  "people.phone.reveal",
                  "people.update",
                ])}
                revealDurationMs={revealDurationMs}
              />
            }
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return { ...result, queryClient }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

it("requires a purpose before revealing identity", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(personResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  )
  const user = userEvent.setup()
  renderDetail()

  await user.click(await screen.findByRole("button", { name: "查看完整身份证" }))

  expect(screen.getByLabelText("查看用途")).toBeRequired()
  expect(screen.getByRole("dialog", { name: "查看完整身份证" })).toBeVisible()
})

it("keeps a revealed identity only in temporary page state", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (...args) => {
    const init = args[1]
    if (init?.method === "POST") {
      return new Response(JSON.stringify({ value: "11010520120102002X" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    return new Response(JSON.stringify(personResponse()), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  })
  vi.stubGlobal("fetch", fetchMock)
  const { queryClient } = renderDetail(20)
  const revealButton = await screen.findByRole("button", { name: "查看完整身份证" })
  const user = userEvent.setup()

  await user.click(revealButton)
  await user.type(screen.getByLabelText("查看用途"), "入住资料核对")
  await user.click(screen.getByRole("button", { name: "确认查看" }))

  expect(await screen.findByText("11010520120102002X")).toBeVisible()
  expect(queryClient.getMutationCache().getAll()).toHaveLength(0)
  expect(SENSITIVE_REVEAL_DURATION_MS).toBe(60_000)
  await waitFor(() => {
    expect(screen.queryByText("11010520120102002X")).not.toBeInTheDocument()
  })
})

it("expires identity and phone reveals on independent timers", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (...args) => {
    const init = args[1]
    if (init?.method === "POST") {
      const field = JSON.parse(String(init.body)).field as "identity_number" | "phone"
      return new Response(
        JSON.stringify({
          value: field === "identity_number" ? "11010520120102002X" : "13800138000",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    return new Response(JSON.stringify(personResponse()), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  })
  vi.stubGlobal("fetch", fetchMock)
  renderDetail(160)
  const user = userEvent.setup()

  await user.click(await screen.findByRole("button", { name: "查看完整身份证" }))
  await user.type(screen.getByLabelText("查看用途"), "入住资料核对")
  await user.click(screen.getByRole("button", { name: "确认查看" }))
  expect(await screen.findByText("11010520120102002X")).toBeVisible()

  await new Promise((resolve) => setTimeout(resolve, 100))
  await user.click(screen.getByRole("button", { name: "查看完整手机号" }))
  await user.type(screen.getByLabelText("查看用途"), "账号资料核对")
  await user.click(screen.getByRole("button", { name: "确认查看" }))
  expect(await screen.findByText("13800138000")).toBeVisible()

  await waitFor(
    () => expect(screen.queryByText("11010520120102002X")).not.toBeInTheDocument(),
    { timeout: 110 },
  )
  expect(screen.getByText("13800138000")).toBeVisible()
  await waitFor(() => expect(screen.queryByText("13800138000")).not.toBeInTheDocument())
})

it("clears a revealed value when the detail route changes person", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (init?.method === "POST") {
      return new Response(JSON.stringify({ value: "11010520120102002X" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    const id = String(input).endsWith(otherPersonId) ? otherPersonId : personId
    return new Response(JSON.stringify(personResponse(id)), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={new QueryClient()}>
        <Link to={`/people/${otherPersonId}`}>切换人员</Link>
        <Routes>
          <Route
            path="/people/:personId"
            element={
              <PersonDetailPage permissions={new Set(["people.identity.reveal"])} />
            }
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.click(await screen.findByRole("button", { name: "查看完整身份证" }))
  await user.type(screen.getByLabelText("查看用途"), "入住资料核对")
  await user.click(screen.getByRole("button", { name: "确认查看" }))
  expect(await screen.findByText("11010520120102002X")).toBeVisible()

  await user.click(screen.getByRole("link", { name: "切换人员" }))
  expect(await screen.findByRole("heading", { name: "另一学员" })).toBeVisible()
  expect(screen.queryByText("11010520120102002X")).not.toBeInTheDocument()
  expect(screen.getByText("110105********002X")).toBeVisible()
})

it("only shows account opening to an accounts manager", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(personResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  )

  render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={new QueryClient()}>
        <Routes>
          <Route path="/people/:personId" element={<PersonDetailPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByRole("heading", { name: "测试学员" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "开通账号" })).not.toBeInTheDocument()
})

it("shows account opening when the detail API grants object-scoped management", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({ ...personResponse(), account_management_allowed: true }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  )

  render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={new QueryClient()}>
        <Routes>
          <Route path="/people/:personId" element={<PersonDetailPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByRole("button", { name: "开通账号" })).toBeVisible()
})

it("honors an object-level denial even when global auth permissions are stale", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({ ...personResponse(), account_management_allowed: false }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  )

  render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={new QueryClient()}>
        <Routes>
          <Route
            path="/people/:personId"
            element={<PersonDetailPage permissions={new Set(["accounts.manage"])} />}
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByRole("heading", { name: "测试学员" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "开通账号" })).not.toBeInTheDocument()
})

it("requires confirmation and clears the one-time password after opening an account", async () => {
  const temporaryPassword = "Person-detail-one-time-2026"
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith("/accounts") && init?.method === "POST") {
      return new Response(
        JSON.stringify({
          account: {
            id: "10000000-0000-4000-8000-000000000009",
            person_id: personId,
            full_name: "测试学员",
            phone: "138****8000",
            email: "student@example.com",
            status: "active",
            force_password_change: true,
            created_at: "2026-07-26T10:10:00+08:00",
          },
          temporary_password: temporaryPassword,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      )
    }
    return new Response(
      JSON.stringify({ ...personResponse(), account_management_allowed: true }),
      {
      status: 200,
      headers: { "Content-Type": "application/json" },
      },
    )
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient()
  const user = userEvent.setup()

  render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route
            path="/people/:personId"
            element={<PersonDetailPage permissions={new Set(["accounts.manage"])} />}
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.click(await screen.findByRole("button", { name: "开通账号" }))
  expect(screen.getByRole("dialog", { name: "确认开通账号" })).toBeVisible()
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false)

  await user.click(screen.getByRole("button", { name: "确认开通" }))
  expect(await screen.findByText(temporaryPassword)).toBeVisible()
  expect(JSON.stringify(queryClient.getQueryCache().getAll())).not.toContain(temporaryPassword)

  await user.click(screen.getByRole("button", { name: "我已保存并关闭" }))
  expect(screen.queryByText(temporaryPassword)).not.toBeInTheDocument()
})

it("links back to guardian management when account opening is blocked", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith("/accounts") && init?.method === "POST") {
      return new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Conflict",
          status: 409,
          detail: "minor student requires a guardian",
          code: "minor_guardian_required",
          request_id: "detail-guardian-error",
          errors: [{ field: "guardian", message: "required" }],
        }),
        { status: 409, headers: { "Content-Type": "application/problem+json" } },
      )
    }
    return new Response(
      JSON.stringify({ ...personResponse(), account_management_allowed: true }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()
  renderDetail()

  await user.click(await screen.findByRole("button", { name: "开通账号" }))
  await user.click(screen.getByRole("button", { name: "确认开通" }))

  expect(
    await screen.findByText("请先绑定已成年、已实名且账号启用的监护人"),
  ).toBeVisible()
  expect(screen.getByRole("link", { name: "返回人员档案" })).toHaveAttribute(
    "href",
    `/people/${personId}`,
  )
})

it("reuses the student matching panel for an administrator viewing a student", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith(`/people/${personId}`)) {
      return new Response(JSON.stringify(personResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith(`/evaluations/admin/students/${personId}/matching-profile`)) {
      return new Response(
        JSON.stringify({
          student_person_id: personId,
          student_name: "测试学员",
          student_number: "S-2026-001",
          active_count: 1,
          active_limit: 3,
          game_accounts: [
            {
              id: "40000000-0000-4000-8000-000000000001",
              project_id: "30000000-0000-4000-8000-000000000001",
              project_name: "英雄联盟",
              game_id: "Authorized#PersonDetail",
              status: "active",
              valid_from: "2026-08-01T09:30:00+08:00",
              unbound_at: null,
              unbind_reason: null,
            },
          ],
          game_accounts_total: 1,
          game_accounts_truncated: false,
          auxiliary_phones: [
            {
              id: "60000000-0000-4000-8000-000000000001",
              masked_phone: "139****9000",
              status: "active",
              created_at: "2026-08-01T09:30:00+08:00",
              disabled_at: null,
            },
          ],
          auxiliary_phones_total: 1,
          auxiliary_phones_truncated: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)

  render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={new QueryClient()}>
        <Routes>
          <Route
            path="/people/:personId"
            element={
              <PersonDetailPage
                permissions={new Set([
                  "people.read",
                  "evaluation_center.access",
                  "student_game_accounts.manage",
                ])}
              />
            }
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByRole("heading", { name: "评价匹配资料" })).toBeVisible()
  expect(screen.getByText("Authorized#PersonDetail")).toBeVisible()
  expect(screen.getByText("139****9000")).toBeVisible()
  expect(fetchMock).toHaveBeenCalledWith(
    `/api/v1/evaluations/admin/students/${personId}/matching-profile`,
    expect.objectContaining({ credentials: "include" }),
  )
})

it("does not mount the embedded evaluation panel without center access", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith(`/people/${personId}`)) {
      return new Response(JSON.stringify(personResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)

  render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={new QueryClient()}>
        <Routes>
          <Route
            path="/people/:personId"
            element={
              <PersonDetailPage
                permissions={new Set(["people.read", "student_game_accounts.manage"])}
              />
            }
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByRole("heading", { name: "测试学员" })).toBeVisible()
  expect(screen.queryByRole("heading", { name: "评价匹配资料" })).not.toBeInTheDocument()
  expect(
    fetchMock.mock.calls.some(([input]) => String(input).includes("/evaluations/")),
  ).toBe(false)
})

it("removes a revoked evaluation panel without leaving the person page", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith(`/people/${personId}`)) {
      return new Response(JSON.stringify(personResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith(`/evaluations/admin/students/${personId}/matching-profile`)) {
      return new Response(
        JSON.stringify({
          student_person_id: personId,
          student_name: "测试学员",
          student_number: "S-2026-001",
          active_count: 1,
          active_limit: 3,
          game_accounts: [
            {
              id: "40000000-0000-4000-8000-000000000001",
              project_id: "30000000-0000-4000-8000-000000000001",
              project_name: "英雄联盟",
              game_id: "Authorized#RevokedPerson",
              status: "active",
              valid_from: "2026-08-01T09:30:00+08:00",
              unbound_at: null,
              unbind_reason: null,
            },
          ],
          game_accounts_total: 1,
          game_accounts_truncated: false,
          auxiliary_phones: [],
          auxiliary_phones_total: 0,
          auxiliary_phones_truncated: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (
      url.endsWith(`/evaluations/admin/students/${personId}/game-account-limit`) &&
      init?.method === "PUT"
    ) {
      return new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "评价中心权限已收回",
          code: "evaluation_access_denied",
          request_id: "person-evaluation-revoked",
        }),
        { status: 403, headers: { "Content-Type": "application/problem+json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Routes>
          <Route
            path="/people/:personId"
            element={
              <PersonDetailPage
                permissions={new Set([
                  "people.read",
                  "evaluation_center.access",
                  "student_game_accounts.manage",
                ])}
              />
            }
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("Authorized#RevokedPerson")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "调整账号上限" }))
  await user.type(screen.getByLabelText("调整原因"), "整中心撤权")
  await user.click(screen.getByRole("button", { name: "确认调整" }))

  await waitFor(() => {
    expect(screen.queryByRole("heading", { name: "评价匹配资料" })).not.toBeInTheDocument()
    expect(screen.queryByText("正在更新基础数据权限...")).not.toBeInTheDocument()
  })
  expect(screen.getByRole("heading", { name: "测试学员" })).toBeVisible()
  expect(screen.queryByText("Authorized#RevokedPerson")).not.toBeInTheDocument()
})
