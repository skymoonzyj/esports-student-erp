import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { readFileSync } from "node:fs"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AccountsPage } from "./AccountsPage"

afterEach(() => vi.unstubAllGlobals())

it("renders an email-only account without synthesizing a phone", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.includes("/accounts?")) {
        return new Response(
          JSON.stringify({
            items: [
              {
                id: "10000000-0000-4000-8000-000000000091",
                person_id: "30000000-0000-4000-8000-000000000091",
                full_name: "邮箱账号",
                nickname: "小邮",
                masked_primary_identifier: "e***@example.com",
                primary_identifier_kind: "email",
                masked_phone: null,
                masked_email: "e***@example.com",
                verified_identifier_kinds: ["email"],
                status: "active",
                force_password_change: false,
                created_at: "2026-08-16T10:00:00+08:00",
              },
            ],
            page: 1,
            page_size: 20,
            total: 1,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      throw new Error(`Unexpected request: ${url}`)
    }),
  )

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <AccountsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("e***@example.com")).toBeVisible()
  const nicknameCell = screen.getByRole("cell", { name: "小邮" })
  expect(nicknameCell).toHaveAttribute("data-label", "昵称")
  expect(nicknameCell).toBeVisible()
  expect(screen.queryByText(/000\*{4}0000/)).not.toBeInTheDocument()
  expect(screen.getByRole("heading", { name: "账号查看" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "开通账号" })).not.toBeInTheDocument()
})

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
            masked_primary_identifier: candidate.phone,
            primary_identifier_kind: "phone",
            masked_phone: candidate.phone,
            masked_email: candidate.email,
            verified_identifier_kinds: ["phone", "email"],
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
        <AccountsPage canManage />
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
        <AccountsPage canManage />
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
              masked_primary_identifier: "139****9000",
              primary_identifier_kind: "phone",
              masked_phone: "139****9000",
              masked_email: "l***@example.com",
              verified_identifier_kinds: ["phone", "email"],
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
        <AccountsPage canManage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("139****9000、l***@example.com")).toBeVisible()
  expect(screen.getByText("均可登录：手机号、邮箱")).toBeVisible()
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

it("links to the person record when account opening needs a valid guardian and preserves styles during People preparation", async () => {
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
        <AccountsPage canManage />
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
  const styles = ["styles/tokens.css", "styles/global.css", "styles/shared-controls.css", "layout/AppShell.css", "settings/settings.css"]
    .map(path => {
      const style = document.createElement("style")
      style.textContent = readFileSync(`src/${path}`, "utf8")
      document.head.append(style)
      return style
    })
  const prepared = document.createElement("style")
  try {
    const alert = screen.getByRole("alert")
    const input = screen.getByLabelText("查找人员")
    input.focus()
    const appearance = () => {
      const css = getComputedStyle(alert)
      return { fontSize: css.fontSize, padding: css.padding, margin: css.margin,
        borderRadius: css.borderRadius, color: css.color, background: css.backgroundColor }
    }
    const before = appearance()
    prepared.textContent = readFileSync("src/people/people.css", "utf8")
    document.head.append(prepared)
    expect(screen.getByRole("alert")).toBe(alert)
    expect(input).toHaveValue("待绑定未成年学员")
    expect(input).toHaveFocus()
    expect(appearance()).toEqual(before)
  } finally {
    prepared.remove()
    styles.forEach(style => style.remove())
  }
})

it("shows the user count and requires two confirmations before deleting an account", async () => {
  const accountId = "10000000-0000-4000-8000-000000000061"
  let deleted = false
  const account = {
    id: accountId,
    person_id: "30000000-0000-4000-8000-000000000061",
    full_name: "待删除账号",
    masked_primary_identifier: "138****0061",
    primary_identifier_kind: "phone",
    masked_phone: "138****0061",
    masked_email: null,
    verified_identifier_kinds: ["phone"],
    status: "active",
    force_password_change: false,
    created_at: "2026-08-20T10:00:00+08:00",
  }
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/accounts?") && !init?.method) {
      return new Response(
        JSON.stringify({
          items: deleted ? [] : [account],
          page: 1,
          page_size: 20,
          total: deleted ? 0 : 1,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith(`/accounts/${accountId}`) && init?.method === "DELETE") {
      deleted = true
      return new Response(null, { status: 204 })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <AccountsPage canManage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("待删除账号")).toBeVisible()
  expect(screen.getByRole("status")).toHaveTextContent("用户数量：1")

  await user.click(screen.getByRole("button", { name: "删除账号" }))
  expect(screen.getByRole("alertdialog")).toBeVisible()
  expect(screen.getByRole("heading", { name: "确认删除账号" })).toBeVisible()
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false)

  await user.click(screen.getByRole("button", { name: "继续删除" }))
  expect(screen.getByRole("heading", { name: "再次确认删除账号" })).toBeVisible()
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false)

  await user.click(screen.getByRole("button", { name: "确认删除" }))
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("用户数量：0"))
  expect(
    fetchMock.mock.calls.filter(([, init]) => init?.method === "DELETE"),
  ).toHaveLength(1)
  expect(screen.queryByText("待删除账号")).not.toBeInTheDocument()
})
