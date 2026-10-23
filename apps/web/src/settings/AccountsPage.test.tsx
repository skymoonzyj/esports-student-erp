import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AccountsPage } from "./AccountsPage"

afterEach(() => vi.unstubAllGlobals())

it("shows a temporary password once and never places it in query cache", async () => {
  const temporaryPassword = "One-time-password-2026"
  const candidate = {
    id: "30000000-0000-4000-8000-000000000001",
    full_name: "待开户教练",
    person_types: ["staff"],
    identity_number: "110105********002X",
    phone: "138****8000",
    email: "coach@example.com",
    sex: "male",
    date_of_birth: "1990-01-02",
    created_at: "2026-07-26T10:00:00+08:00",
  }
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/accounts?") && !init?.method) {
      return new Response(JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.includes("/account-candidates?") && !init?.method) {
      return new Response(JSON.stringify([candidate]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/accounts") && init?.method === "POST") {
      return new Response(
        JSON.stringify({
          account: {
            id: "10000000-0000-4000-8000-000000000001",
            person_id: candidate.id,
            full_name: candidate.full_name,
            phone: candidate.phone,
            email: candidate.email,
            status: "active",
            force_password_change: true,
            created_at: "2026-07-26T10:10:00+08:00",
          },
          temporary_password: temporaryPassword,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient()
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <AccountsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.click(await screen.findByRole("button", { name: "开通账号" }))
  await user.type(screen.getByLabelText("查找人员"), "待开户教练")
  await user.click(screen.getByRole("button", { name: "查找" }))
  await user.click(
    await screen.findByRole("radio", {
      name: `待开户教练 138****8000 · 档案 ${candidate.id}`,
    }),
  )
  await user.click(screen.getByRole("button", { name: "确认开通" }))

  expect(await screen.findByText("请立即安全交付临时密码")).toBeVisible()
  expect(screen.getByText(temporaryPassword)).toBeVisible()
  expect(JSON.stringify(queryClient.getQueryCache().getAll())).not.toContain(temporaryPassword)
  expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/people?"))).toBe(false)

  await user.click(screen.getByRole("button", { name: "我已保存并关闭" }))
  expect(screen.queryByText(temporaryPassword)).not.toBeInTheDocument()
  expect(screen.queryByText("请立即安全交付临时密码")).not.toBeInTheDocument()
})

it("disambiguates candidates with the same name and masked phone", async () => {
  const candidates = [
    {
      id: "30000000-0000-4000-8000-000000000031",
      full_name: "同名待开户人员",
      person_types: ["staff"],
      phone: "138****8000",
    },
    {
      id: "30000000-0000-4000-8000-000000000032",
      full_name: "同名待开户人员",
      person_types: ["staff"],
      phone: "138****8000",
    },
  ]
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.includes("/accounts?")) {
        return new Response(JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.includes("/account-candidates?")) {
        return new Response(JSON.stringify(candidates), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      throw new Error(`Unexpected request: ${url}`)
    }),
  )
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <AccountsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.click(await screen.findByRole("button", { name: "开通账号" }))
  await user.type(screen.getByLabelText("查找人员"), "同名待开户人员")
  await user.click(screen.getByRole("button", { name: "查找" }))

  for (const candidate of candidates) {
    expect(
      await screen.findByRole("radio", {
        name: `${candidate.full_name} ${candidate.phone} · 档案 ${candidate.id}`,
      }),
    ).toBeVisible()
  }
})

it("disables, enables, and resets an account while keeping the reset secret ephemeral", async () => {
  const accountId = "10000000-0000-4000-8000-000000000002"
  const resetPassword = "Reset-one-time-password-2026"
  let status: "active" | "disabled" = "active"
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/accounts?") && !init?.method) {
      return new Response(
        JSON.stringify({
          items: [
            {
              id: accountId,
              person_id: "30000000-0000-4000-8000-000000000002",
              full_name: "生命周期账号",
              phone: "139****9000",
              email: "lifecycle@example.com",
              status,
              force_password_change: false,
              created_at: "2026-07-26T10:00:00+08:00",
            },
          ],
          page: 1,
          page_size: 20,
          total: 1,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith(`/accounts/${accountId}/disable`) && init?.method === "POST") {
      status = "disabled"
      return new Response(null, { status: 204 })
    }
    if (url.endsWith(`/accounts/${accountId}/enable`) && init?.method === "POST") {
      status = "active"
      return new Response(null, { status: 204 })
    }
    if (url.endsWith(`/accounts/${accountId}/reset-password`) && init?.method === "POST") {
      return new Response(
        JSON.stringify({ account_id: accountId, temporary_password: resetPassword }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient()
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <AccountsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.click(await screen.findByRole("button", { name: "停用" }))
  await user.click(await screen.findByRole("button", { name: "启用" }))
  await user.click(await screen.findByRole("button", { name: "重置密码" }))

  expect(await screen.findByText(resetPassword)).toBeVisible()
  expect(JSON.stringify(queryClient.getQueryCache().getAll())).not.toContain(resetPassword)
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/disable"))).toBe(true)
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/enable"))).toBe(true)
  await user.click(screen.getByRole("button", { name: "我已保存并关闭" }))
  expect(screen.queryByText(resetPassword)).not.toBeInTheDocument()
})

it("links to the person record when account opening needs a valid guardian", async () => {
  const candidate = {
    id: "30000000-0000-4000-8000-000000000041",
    full_name: "待绑定未成年学员",
    person_types: ["student"],
    phone: "138****8041",
  }
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      if (url.includes("/accounts?") && !init?.method) {
        return new Response(JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.includes("/account-candidates?")) {
        return new Response(JSON.stringify([candidate]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.endsWith("/accounts") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Conflict",
            status: 409,
            detail: "minor student requires a guardian",
            code: "minor_guardian_required",
            request_id: "accounts-guardian-error",
            errors: [{ field: "guardian", message: "required" }],
          }),
          { status: 409, headers: { "Content-Type": "application/problem+json" } },
        )
      }
      throw new Error(`Unexpected request: ${url}`)
    }),
  )
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <AccountsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.click(await screen.findByRole("button", { name: "开通账号" }))
  await user.type(screen.getByLabelText("查找人员"), "待绑定未成年学员")
  await user.click(screen.getByRole("button", { name: "查找" }))
  await user.click(
    await screen.findByRole("radio", {
      name: `${candidate.full_name} ${candidate.phone} · 档案 ${candidate.id}`,
    }),
  )
  await user.click(screen.getByRole("button", { name: "确认开通" }))

  expect(
    await screen.findByText("请先绑定已成年、已实名且账号启用的监护人"),
  ).toBeVisible()
  expect(screen.getByRole("link", { name: "返回人员档案" })).toHaveAttribute(
    "href",
    `/people/${candidate.id}`,
  )
})
