import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AuthProvider } from "../auth/AuthProvider"
import { AuditPage } from "./AuditPage"

afterEach(() => vi.unstubAllGlobals())

it("shows typed daily-cleaning before and after values in Chinese", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
    items: [{ id: "daily-change", actor_user_id: "actor-1", action: "housing.room.update", resource_type: "housing_room", resource_id: "room-1", purpose: null, outcome: "allowed", request_id: "daily-change", metadata: { previous_daily_cleaning_enabled: false, daily_cleaning_enabled: true, request_body: { secret: "private-value" } }, created_at: "2026-10-23T02:35:00Z" }], total: 1, page: 1, page_size: 20,
  }), { headers: { "Content-Type": "application/json" } })))
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><AuditPage /></QueryClientProvider></MemoryRouter>)
  expect(await screen.findByText("修改房间")).toBeVisible()
  expect(screen.getByText("原每日保洁：否")).toBeVisible()
  expect(screen.getByText("新每日保洁：是")).toBeVisible()
  expect(document.body.textContent).not.toContain("private-value")
})

it("labels cleaning and photo actions without exposing binary or raw request metadata", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
    items: ["housing.room.cleaning.complete", "housing.room_photo.put", "housing.room_photo.delete"].map((action, i) => ({
      id: `care-${i}`, actor_user_id: "actor-1", actor_name: "管理员", action, resource_type: "housing_room", resource_id: "room-1",
      purpose: null, outcome: "allowed", request_id: `care-${i}`, metadata: { binary: "private-image-bytes", request_body: { token: "private-token-value" } }, created_at: "2026-10-23T02:35:00Z",
    })), total: 3, page: 1, page_size: 20,
  }), { headers: { "Content-Type": "application/json" } })))
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><AuditPage /></QueryClientProvider></MemoryRouter>)
  expect(await screen.findByText("完成房间保洁")).toBeVisible()
  expect(screen.getByText("上传房间照片")).toBeVisible()
  expect(screen.getByText("删除房间照片")).toBeVisible()
  expect(document.body.textContent).not.toContain("private-image-bytes")
  expect(document.body.textContent).not.toContain("private-token-value")
})

it("explains profile and departure changes in Chinese without exposing note text", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
    items: [
      {
        id: "profile-change", actor_user_id: "actor-1", actor_name: "管理人员",
        action: "person.profile.update", resource_type: "person", resource_id: "person-1",
        purpose: null, outcome: "allowed", request_id: "profile-change",
        metadata: { changed_fields: ["nickname", "profile_notes"], old_nickname: null, new_nickname: "小星", profile_notes: "不应公开的档案备注" },
        created_at: "2026-10-20T02:35:00Z",
      },
      {
        id: "departure-change", actor_user_id: "actor-1", actor_name: "管理人员",
        action: "housing.stay.update_planned_departure", resource_type: "housing_stay", resource_id: "stay-1",
        purpose: null, outcome: "allowed", request_id: "departure-change",
        metadata: { previous_duration_mode: "fixed_term", duration_mode: "long_term", previous_planned_check_out_at: "2026-10-24T16:00:00Z", planned_check_out_at: null },
        created_at: "2026-10-20T02:40:00Z",
      },
    ], total: 2, page: 1, page_size: 20,
  }), { headers: { "Content-Type": "application/json" } })))
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><AuditPage /></QueryClientProvider></MemoryRouter>)
  expect(await screen.findByText("修改人员昵称与备注")).toBeVisible()
  expect(screen.getByText("修改计划退宿日期")).toBeVisible()
  expect(screen.getByText("修改字段：昵称、个人档案备注")).toBeVisible()
  expect(screen.getByText("原昵称：未设置")).toBeVisible()
  expect(screen.getByText("新昵称：小星")).toBeVisible()
  expect(screen.getByText("原住宿期限：固定期限")).toBeVisible()
  expect(screen.getByText("新住宿期限：长住")).toBeVisible()
  expect(screen.getByText("原计划退宿日期：2026-10-25")).toBeVisible()
  expect(screen.getByText("新计划退宿日期：未设置")).toBeVisible()
  expect(screen.getByText("发生时间：2026-10-20 10:40:00")).toBeVisible()
  expect(document.body.textContent).not.toContain("不应公开的档案备注")
})

it("renders invitation and organization-manager audit actions with safe Chinese fields and time", async () => {
  const actions = ["referral_organization_manager.assign", "referral_organization_manager.revoke", "referral_organization_manager.list", "referral_organization_manager.candidates", "referrals.housing.read", "referrals.sources.read"]
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ items: actions.map((action, i) => ({ id: `invite-${i}`, actor_user_id: "actor-1", action, resource_type: i < 4 ? "referral_organization_manager" : i === 4 ? "invited_housing" : "invitation_source", resource_id: null, purpose: null, outcome: "allowed", request_id: `request-${i}`, metadata: { organization_id: "30000000-0000-4000-8000-000000000001", target_account_id: "10000000-0000-4000-8000-000000000001", source_kind: "organization", result_count: 1 }, created_at: "2026-10-10T02:35:00Z" })), total: 6, page: 1, page_size: 20 }))))
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><AuditPage /></QueryClientProvider></MemoryRouter>)
  expect(await screen.findByText("分配邀请机构负责人")).toBeVisible()
  for (const label of ["移除邀请机构负责人", "查看邀请机构负责人", "查询负责人候选账号", "查看受邀人员住宿", "查看邀请来源"]) expect(screen.getByText(label)).toBeVisible()
  expect(screen.getAllByText(/邀请机构编号：/)).toHaveLength(6)
  expect(screen.getAllByText(/目标账号编号：/)).toHaveLength(6)
  expect(screen.getAllByText("邀请来源类型：机构")).toHaveLength(6)
  expect(screen.getAllByText(/10:35/)).toHaveLength(6)
  expect(document.body.textContent).not.toContain("referral_organization_manager")
  expect(screen.getByText("对象：邀请来源")).toBeVisible()
})

it.each([
  ["baidu", "百度网盘"], ["115", "115 网盘"], ["quark", "夸克网盘"],
])("renders safe cloud backup actions for %s", async (provider, name) => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
    items: ["update", "test", "copy"].map((action, i) => ({ id: `cloud-${i}`, actor_user_id: "actor-1", action: `operations.cloud_backup.${provider}.${action}`, resource_type: "cloud_backup", resource_id: action === "update" ? null : "30000000-0000-4000-8000-000000000001", purpose: null, outcome: "allowed", request_id: `request-${i}`, metadata: {}, created_at: "2026-10-02T00:00:00Z" })),
    page: 1, page_size: 20, total: 3,
  }), { headers: { "Content-Type": "application/json" } })))
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><AuditPage /></QueryClientProvider></MemoryRouter>)
  expect(await screen.findByText(`${name}：更新云备份配置`)).toBeVisible()
  expect(screen.getByText(`${name}：测试云备份连接`)).toBeVisible()
  expect(screen.getByText(`${name}：复制云备份`)).toBeVisible()
  expect(screen.getAllByText("对象：云备份")).toHaveLength(3)
  expect(document.body.textContent).not.toContain("operations.cloud_backup")
})

it("renders team lifecycle audit actions and identifiers without raw codes or transfer reason", async () => {
  const actions = ["team_memberships.join", "team_memberships.leave", "team_memberships.transfer", "team_managers.assign", "team_managers.unassign"]
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
    items: actions.map((action, index) => ({ id: `audit-${index}`, actor_user_id: "actor-1", actor_name: "安全管理员", action, resource_type: index < 3 ? "team_membership" : "team_manager_assignment", resource_id: "30000000-0000-4000-8000-000000000001", purpose: index === 2 ? "绝不能展示的调队原因全文" : null, outcome: "allowed", request_id: `request-${index}`, metadata: { operation_id: "10000000-0000-4000-8000-000000000001", membership_id: "20000000-0000-4000-8000-000000000001", closed_membership_id: "20000000-0000-4000-8000-000000000002", assignment_id: "40000000-0000-4000-8000-000000000001", source_team_id: "50000000-0000-4000-8000-000000000001", target_team_id: "50000000-0000-4000-8000-000000000002", transfer_reason: "绝不能展示的调队原因全文" }, created_at: "2026-09-29T10:00:00+08:00" })),
    page: 1, page_size: 20, total: 5,
  }), { headers: { "Content-Type": "application/json" } })))
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><AuditPage /></QueryClientProvider></MemoryRouter>)
  expect(await screen.findByText("加入队伍")).toBeVisible()
  expect(screen.getByText("调动队伍")).toBeVisible()
  expect(screen.getByText("分配队伍管理人员")).toBeVisible()
  expect(screen.getAllByText("安全管理员").length).toBe(5)
  expect(screen.getAllByText(/2026/).length).toBeGreaterThan(0)
  const visibleRawActionCodes = actions.filter((action) => document.body.textContent?.includes(action))
  expect(visibleRawActionCodes).toEqual([])
  expect(document.body.textContent).not.toContain("绝不能展示的调队原因全文")
})

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

  await user.type(screen.getByLabelText("操作人账号编号"), "10000000-0000-4000-8000-000000000001")
  await user.type(screen.getByLabelText("操作类型"), "person.identity.reveal")
  await user.type(screen.getByLabelText("对象类型"), "person")
  await user.type(screen.getByLabelText("开始日期"), "2026-07-26")
  await user.type(screen.getByLabelText("结束日期"), "2026-07-26")
  await user.click(screen.getByRole("button", { name: "查询审计" }))

  expect(await screen.findByText(/字段：身份证号/)).toBeVisible()
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
            actor_name: "安全管理员",
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
            actor_name: "安全管理员",
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
            actor_name: "安全管理员",
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

  expect(await screen.findByText("重置账号密码")).toBeVisible()
  expect(screen.getAllByText("安全管理员")).toHaveLength(3)
  expect(screen.getByText(/权限能力：查看人员档案/)).toBeVisible()
  expect(screen.getByText(/权限结果：拒绝/)).toBeVisible()
  expect(screen.getByText(/数据范围：指定人员/)).toBeVisible()
  expect(screen.getByText(`范围编号：${scopeId}`)).toBeVisible()
  expect(screen.getByText("已填写用途")).toBeVisible()
  expect(screen.getByText("发生时间：2026-07-26 10:02:00")).toBeVisible()
})

it("renders procurement audit actions and resources in Chinese", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          items: [{
            id: "50000000-0000-4000-8000-000000000005",
            actor_user_id: "10000000-0000-4000-8000-000000000001",
            actor_name: "行政人员",
            action: "procurement.purchase.settlement",
            resource_type: "daily_purchase_record",
            resource_id: "60000000-0000-4000-8000-000000000001",
            purpose: null,
            outcome: "allowed",
            request_id: "audit-procurement-settlement",
            metadata: { permission_key: "procurement.settle" },
            created_at: "2026-08-28T18:30:00+08:00",
          }],
          page: 1,
          page_size: 20,
          total: 1,
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

  expect(await screen.findByText("变更采购结算状态")).toBeVisible()
  expect(screen.getByText("对象：日常采购记录")).toBeVisible()
  expect(screen.getByText("权限能力：管理采购结算")).toBeVisible()
  expect(screen.getByText("发生时间：2026-08-28 18:30:00")).toBeVisible()
})

it("removes a previously rendered audit snapshot after an explicit 403", async () => {
  const staleEvent = {
    id: "audit-stale",
    actor_user_id: "account-stale",
    actor_name: "权限撤销前操作人",
    action: "person.update",
    resource_type: "person",
    resource_id: "person-stale",
    purpose: "权限撤销前用途",
    outcome: "allowed",
    request_id: "audit-stale-request",
    metadata: { field: "phone" },
    created_at: "2026-07-26T10:00:00+08:00",
  }
  let meCalls = 0
  let auditCalls = 0
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      meCalls += 1
      return new Response(JSON.stringify({
        account_id: "account-1",
        person_id: "operator-1",
        full_name: "超级管理员",
        email: "admin@example.com",
        force_password_change: false,
        permissions: meCalls === 1 ? ["audit.read"] : [],
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    }
    if (url.endsWith("/auth/csrf")) {
      return new Response(JSON.stringify({ csrf_token: "audit-csrf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.includes("/audit-events?")) {
      auditCalls += 1
      if (auditCalls === 1) {
        return new Response(
          JSON.stringify({ items: [staleEvent], page: 1, page_size: 20, total: 1 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      return new Response(JSON.stringify({
        type: "about:blank",
        title: "Forbidden",
        status: 403,
        detail: "没有安全审计权限",
        code: "forbidden",
        request_id: "audit-revoked",
      }), { status: 403, headers: { "Content-Type": "application/problem+json" } })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <MemoryRouter initialEntries={["/settings/audit"]}>
      <QueryClientProvider client={queryClient}>
        <AuthProvider><AuditPage /></AuthProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
  expect(await screen.findByText("权限撤销前操作人")).toBeVisible()
  await act(async () => {
    await queryClient.invalidateQueries({ queryKey: ["audit-events"] })
  })
  expect(await screen.findByRole("alert")).toHaveTextContent("安全审计权限已撤销")
  expect(screen.queryByText("权限撤销前操作人")).not.toBeInTheDocument()
  expect(document.body.textContent).not.toContain("权限撤销前用途")
  await waitFor(() => expect(
    queryClient.getQueriesData({ queryKey: ["audit-events"] }).every(([, data]) => data === undefined),
  ).toBe(true))
  await waitFor(() => expect(meCalls).toBe(2))
  expect(auditCalls).toBe(2)
})

it("labels historical housing voids without exposing their reason in generic audit", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
    items: [{ id: "void-audit", actor_user_id: "admin", action: "housing.stay.void_history", resource_type: "housing_stay", resource_id: "stay-1", purpose: "不得公开的历史作废原因", outcome: "allowed", request_id: "req", metadata: { reason: "不得公开的历史作废原因" }, created_at: "2026-10-21T00:00:00Z" }], total: 1, page: 1, page_size: 20,
  }))))
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><AuditPage /></QueryClientProvider></MemoryRouter>)
  expect(await screen.findByText("作废历史住宿")).toBeVisible()
  expect(document.body.textContent).not.toContain("不得公开的历史作废原因")
})

it("keeps the prior audit snapshot available after an ordinary network error", async () => {
  const staleEvent = {
    id: "audit-network",
    actor_user_id: "account-network",
    actor_name: "网络错误前操作人",
    action: "person.update",
    resource_type: "person",
    resource_id: "person-network",
    purpose: "网络错误前用途",
    outcome: "allowed",
    request_id: "audit-network-request",
    metadata: {},
    created_at: "2026-07-26T10:00:00+08:00",
  }
  let auditCalls = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.includes("/audit-events?")) {
      auditCalls += 1
      if (auditCalls === 1) {
        return new Response(
          JSON.stringify({ items: [staleEvent], page: 1, page_size: 20, total: 1 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      throw new TypeError("Failed to fetch")
    }
    throw new Error(`Unexpected request: ${url}`)
  }))
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}><AuditPage /></QueryClientProvider>
    </MemoryRouter>,
  )
  expect(await screen.findByText("网络错误前操作人")).toBeVisible()
  await act(async () => {
    await queryClient.invalidateQueries({ queryKey: ["audit-events"] })
  })
  expect(await screen.findByText("暂时无法读取审计记录。")).toBeVisible()
  expect(screen.getByText("网络错误前操作人")).toBeVisible()
  expect(queryClient.getQueriesData({ queryKey: ["audit-events"] })[0]?.[1]).toEqual({
    items: [staleEvent], page: 1, page_size: 20, total: 1,
  })
})
