import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AuditPage } from "./AuditPage"

afterEach(() => vi.unstubAllGlobals())

it("filters audit events while rendering only safe metadata", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () =>
    new Response(
      JSON.stringify({
        items: [
          {
            id: "50000000-0000-4000-8000-000000000001",
            actor_user_id: "10000000-0000-4000-8000-000000000001",
            action: "person.identity.reveal",
            resource_type: "person",
            resource_id: "30000000-0000-4000-8000-000000000001",
            purpose: "误填手机号 138/0013/8000",
            outcome: "allowed",
            request_id: "audit-request",
            metadata: {
              field: "identity_number",
              result_count: 1,
              phone: "13900139000",
              password: "Temporary-password-2026",
              token: "Bearer unsafe-token",
            },
            created_at: "2026-07-26T10:00:00+08:00",
          },
        ],
        page: 1,
        page_size: 20,
        total: 1,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  )
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <AuditPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.type(screen.getByLabelText("操作人账号 ID"), "10000000-0000-4000-8000-000000000001")
  await user.type(screen.getByLabelText("动作"), "person.identity.reveal")
  await user.type(screen.getByLabelText("资源类型"), "person")
  await user.type(screen.getByLabelText("开始日期"), "2026-07-26")
  await user.type(screen.getByLabelText("结束日期"), "2026-07-26")
  await user.click(screen.getByRole("button", { name: "查询审计" }))

  expect(await screen.findByText("身份证字段")).toBeVisible()
  expect(screen.getByText("结果数量：1")).toBeVisible()
  expect(screen.getByText("敏感内容已隐藏")).toBeVisible()
  expect(document.body.innerHTML).not.toContain("138/0013/8000")
  expect(document.body.innerHTML).not.toContain("13900139000")
  expect(document.body.innerHTML).not.toContain("Temporary-password-2026")
  expect(document.body.innerHTML).not.toContain("unsafe-token")
  expect(
    fetchMock.mock.calls.some(([input]) => {
      const url = String(input)
      return (
        url.includes("actor_user_id=10000000-0000-4000-8000-000000000001") &&
        url.includes("action=person.identity.reveal") &&
        url.includes("resource_type=person") &&
        url.includes("date_from=2026-07-26T00%3A00%3A00%2B08%3A00") &&
        url.includes("date_to=2026-07-27T00%3A00%3A00%2B08%3A00")
      )
    }),
  ).toBe(true)
})

it("shows the actor and reconstructable permission change without hiding password-reset actions", async () => {
  const actorId = "10000000-0000-4000-8000-000000000001"
  const scopeId = "30000000-0000-4000-8000-000000000003"
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          items: [
            {
              id: "50000000-0000-4000-8000-000000000002",
              actor_user_id: actorId,
              action: "account.password.reset",
              resource_type: "account",
              resource_id: "10000000-0000-4000-8000-000000000002",
              purpose: null,
              outcome: "allowed",
              request_id: "audit-password-reset",
              metadata: {},
              created_at: "2026-07-26T10:00:00+08:00",
            },
            {
              id: "50000000-0000-4000-8000-000000000003",
              actor_user_id: actorId,
              action: "account.permission_override.add",
              resource_type: "account",
              resource_id: "10000000-0000-4000-8000-000000000003",
              purpose: null,
              outcome: "allowed",
              request_id: "audit-permission-change",
              metadata: {
                permission_key: "people.read",
                effect: "deny",
                scope_kind: "person",
                scope_id: scopeId,
              },
              created_at: "2026-07-26T10:01:00+08:00",
            },
            {
              id: "50000000-0000-4000-8000-000000000004",
              actor_user_id: actorId,
              action: "person.identity.reveal",
              resource_type: "person",
              resource_id: scopeId,
              purpose: "security-incident-investigation",
              outcome: "allowed",
              request_id: "audit-safe-purpose",
              metadata: {},
              created_at: "2026-07-26T10:02:00+08:00",
            },
          ],
          page: 1,
          page_size: 20,
          total: 3,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  )

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <AuditPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("account.password.reset")).toBeVisible()
  expect(screen.getAllByText(actorId)).toHaveLength(3)
  expect(screen.getByText("权限：people.read")).toBeVisible()
  expect(screen.getByText("结果：拒绝")).toBeVisible()
  expect(screen.getByText(`范围：指定人员 · ${scopeId}`)).toBeVisible()
  expect(screen.getByText("security-incident-investigation")).toBeVisible()
})
