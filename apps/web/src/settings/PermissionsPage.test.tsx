import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { evaluationKeys } from "../evaluations/api"
import { PermissionsPage } from "./PermissionsPage"

afterEach(() => vi.unstubAllGlobals())

it("requires a concrete assigned team and displays its Chinese scope label", async () => {
  const requests: string[] = []
  let assignedManager = false
  let previewPayload: Record<string, unknown> | null = null
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input); requests.push(url)
    if (url.endsWith("/accounts/a1/roles") && init?.method === "POST") {
      expect(JSON.parse(String(init.body))).toEqual({ role_id: "r2" })
      assignedManager = true
      return new Response(null, { status: 204 })
    }
    if (url.endsWith("/accounts/a1/permission-overrides/preview") && init?.method === "POST") {
      previewPayload = JSON.parse(String(init.body)) as Record<string, unknown>
      return new Response(JSON.stringify({ allowed: true, reason: "allowed" }), { headers: { "Content-Type": "application/json" } })
    }
    const body = url.endsWith("/permission-account-directory") ? [{ id: "a1", full_name: "管理账号", status: "active" }]
      : url.endsWith("/roles") ? [{ id: "r1", key: "team_member", name: "队员", permissions: [] }, { id: "r2", key: "team_manager", name: "队伍管理", permissions: [] }]
      : url.endsWith("/permissions") ? [{ id: "perm1", key: "team_memberships.manage", name: "管理队员", sensitive: false }]
      : url.endsWith("/accounts/a1/access") ? { account_id: "a1", roles: [{ id: "r1", key: "team_member", name: "队员", permissions: [] }, ...(assignedManager ? [{ id: "r2", key: "team_manager", name: "队伍管理", permissions: [] }] : [])], permission_overrides: [], effective_permissions: [] }
      : url.includes("/permissions/directory/teams?") ? { items: [{ id: "t1", name: "青训一队", project_id: "p1", project_name: "王者荣耀", is_active: true, label: "王者荣耀 · 青训一队" }], page: 1, page_size: 20, total: 1 }
      : null
    if (body === null) throw new Error(`Unexpected request: ${url}`)
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } })
  }))
  render(<MemoryRouter><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><PermissionsPage canManageGlobalPermissions /></QueryClientProvider></MemoryRouter>)
  const user = userEvent.setup()
  await user.selectOptions(screen.getByLabelText("账号"), await screen.findByRole("option", { name: /管理账号/ }))
  expect(screen.getByText("队员", { selector: "strong" })).toBeVisible()
  await user.selectOptions(screen.getByLabelText("添加角色"), "r2")
  await user.click(screen.getByRole("button", { name: "分配角色" }))
  expect(await screen.findByText("队伍管理", { selector: "strong" })).toBeVisible()
  expect(screen.getByText("队员", { selector: "strong" })).toBeVisible()
  await user.selectOptions(screen.getByLabelText("权限能力"), "perm1")
  await user.selectOptions(screen.getByLabelText("数据范围"), "assigned_team")
  await user.click(screen.getByRole("button", { name: "查看变更摘要" }))
  expect(screen.getByRole("alert")).toHaveTextContent("请补齐账号、权限能力和数据范围")
  await user.type(screen.getByLabelText("查找指定队伍"), "青训")
  await user.click(await screen.findByRole("radio", { name: /王者荣耀 · 青训一队/ }))
  await user.click(screen.getByRole("button", { name: "查看变更摘要" }))
  expect(await screen.findByText("管理队员 · 指定队伍：王者荣耀 · 青训一队")).toBeVisible()
  expect(previewPayload).toMatchObject({ permission_id: "perm1", scope_kind: "assigned_team", scope_id: "t1" })
  expect(requests.some((url) => url.includes("/permissions/directory/teams?query=%E9%9D%92%E8%AE%AD"))).toBe(true)
})

it("shows a selected account's effective permissions, sources, and scopes", async () => {
  const accountId = "10000000-0000-4000-8000-000000000021"
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/permission-account-directory")) {
      return new Response(
        JSON.stringify([
          { id: accountId, full_name: "房态教练", status: "active" },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/roles")) {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/permissions")) {
      return new Response(
        JSON.stringify([
          {
            id: "20000000-0000-4000-8000-000000000037",
            key: "housing.read",
            name: "查看房态",
            sensitive: false,
          },
          {
            id: "20000000-0000-4000-8000-000000000002",
            key: "people.read",
            name: "查看人员档案",
            sensitive: false,
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith(`/accounts/${accountId}/access`)) {
      return new Response(
        JSON.stringify({
          account_id: accountId,
          roles: [],
          permission_overrides: [],
          effective_permissions: [
            {
              id: "20000000-0000-4000-8000-000000000037",
              key: "housing.read",
              name: "查看房态",
              sensitive: false,
              scope_kinds: ["global"],
              sources: ["role"],
            },
            {
              id: "20000000-0000-4000-8000-000000000002",
              key: "people.read",
              name: "查看人员档案",
              sensitive: false,
              scope_kinds: ["person"],
              sources: ["account_override"],
            },
          ],
        }),
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
        <PermissionsPage canManageGlobalPermissions={false} />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.selectOptions(
    screen.getByLabelText("账号"),
    await screen.findByRole("option", {
      name: `房态教练 · 已启用 · 账号 ${accountId}`,
    }),
  )

  const card = screen.getByRole("heading", { name: "当前拥有权限" }).closest("section")
  expect(card).not.toBeNull()
  expect(within(card!).getByText("查看房态", { selector: "strong" })).toBeVisible()
  expect(within(card!).getByText("查看人员档案", { selector: "strong" })).toBeVisible()
  expect(within(card!).getByText("角色继承")).toBeVisible()
  expect(within(card!).getByText("账号覆盖")).toBeVisible()
  expect(within(card!).getByText("全部数据")).toBeVisible()
  expect(within(card!).getByText("指定人员")).toBeVisible()
})

it("keeps global role controls and organization directories unreachable for a scoped-only manager", async () => {
  const roleId = "20000000-0000-4000-8000-000000000099"
  const directoryCalls: string[] = []
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/permission-account-directory")) {
      return new Response(
        JSON.stringify([
          {
            id: "10000000-0000-4000-8000-000000000099",
            full_name: "范围权限管理员",
            status: "active",
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/roles")) {
      return new Response(
        JSON.stringify([
          {
            id: roleId,
            key: "staff",
            name: "员工",
            permissions: [
              {
                id: "20000000-0000-4000-8000-000000000055",
                key: "people.read",
                name: "查看人员档案",
                sensitive: false,
                scope_kind: "global",
              },
            ],
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/permissions")) {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/accounts/10000000-0000-4000-8000-000000000099/access")) {
      return new Response(
        JSON.stringify({
          account_id: "10000000-0000-4000-8000-000000000099",
          roles: [{ id: roleId, key: "staff", name: "员工", permissions: [] }],
          permission_overrides: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.includes("permission-") && url.includes("-directory?")) {
      directoryCalls.push(url)
      return new Response(JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <PermissionsPage canManageGlobalPermissions={false} />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await screen.findByRole("heading", { name: "权限管理" })
  await user.selectOptions(
    screen.getByLabelText("账号"),
    await screen.findByRole("option", {
      name: "范围权限管理员 · 已启用 · 账号 10000000-0000-4000-8000-000000000099",
    }),
  )
  const rolesCard = screen.getByRole("heading", { name: "当前角色" }).closest("section")
  expect(rolesCard).not.toBeNull()
  expect(within(rolesCard!).getByText("员工", { selector: "strong" })).toBeVisible()
  expect(within(rolesCard!).getByText("staff")).toBeVisible()
  expect(within(rolesCard!).getByText("拥有权限 1 项")).toBeVisible()
  expect(within(rolesCard!).getByText("查看人员档案", { selector: "strong" })).toBeVisible()
  expect(within(rolesCard!).getByText("people.read")).toBeVisible()
  expect(within(rolesCard!).getByText("全部数据")).toBeVisible()
  expect(screen.queryByRole("button", { name: "移除 员工" })).not.toBeInTheDocument()
  expect(screen.queryByLabelText("添加角色")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "分配角色" })).not.toBeInTheDocument()
  const scope = screen.getByLabelText("数据范围")
  expect(within(scope).getByRole("option", { name: "全部数据" })).toBeVisible()
  expect(within(scope).getByRole("option", { name: "本人" })).toBeVisible()
  expect(within(scope).getByRole("option", { name: "本人家庭" })).toBeVisible()
  expect(within(scope).getByRole("option", { name: "指定人员" })).toBeVisible()
  expect(within(scope).queryByRole("option", { name: "指定班级" })).not.toBeInTheDocument()
  expect(within(scope).queryByRole("option", { name: "指定营期" })).not.toBeInTheDocument()
  expect(within(scope).queryByRole("option", { name: "指定项目" })).not.toBeInTheDocument()
  expect(within(scope).queryByRole("option", { name: "指定报名" })).not.toBeInTheDocument()
  expect(within(scope).queryByRole("option", { name: "指定楼栋" })).not.toBeInTheDocument()
  expect(within(scope).queryByRole("option", { name: "指定楼层" })).not.toBeInTheDocument()

  fireEvent.change(scope, { target: { value: "cohort" } })
  expect(scope).toHaveValue("global")
  expect(screen.queryByLabelText("查找指定营期")).not.toBeInTheDocument()
  expect(directoryCalls).toEqual([])
})

it("limits talent take to self and talent read to its five supported scopes", async () => {
  const accountId = "10000000-0000-4000-8000-000000000151"
  const takeId = "20000000-0000-4000-8000-000000000151"
  const readId = "20000000-0000-4000-8000-000000000152"
  vi.stubGlobal("fetch", permissionTalentFetch({ accountId, takeId, readId }))
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <PermissionsPage canManageGlobalPermissions canManageTalentPermissions />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.selectOptions(
    screen.getByLabelText("账号"),
    await screen.findByRole("option", { name: `天赋权限账号 · 已启用 · 账号 ${accountId}` }),
  )
  await user.selectOptions(screen.getByLabelText("权限能力"), takeId)
  const scope = screen.getByLabelText("数据范围")
  expect(scope).toHaveValue("self")
  expect(within(scope).getAllByRole("option").map((option) => option.textContent)).toEqual(["本人"])
  expect(screen.getByText(/参加天赋测试还必须同时具备查看天赋测试报告权限/)).toBeVisible()

  await user.selectOptions(screen.getByLabelText("权限能力"), readId)
  expect(within(scope).getAllByRole("option").map((option) => option.textContent)).toEqual([
    "全部数据",
    "本人",
    "本人家庭",
    "指定人员",
    "指定班级",
  ])
})

it("labels independent assessment permissions and applies their separate scope whitelist", async () => {
  const accountId = "10000000-0000-4000-8000-000000000155"
  const takeId = "20000000-0000-4000-8000-000000000155"
  const readId = "20000000-0000-4000-8000-000000000156"
  vi.stubGlobal("fetch", permissionTalentFetch({ accountId, takeId, readId }))
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <PermissionsPage canManageGlobalPermissions canManageTalentPermissions />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.selectOptions(
    screen.getByLabelText("账号"),
    await screen.findByRole("option", { name: `天赋权限账号 · 已启用 · 账号 ${accountId}` }),
  )
  await user.selectOptions(screen.getByLabelText("权限能力"), "permission-esports-take")
  expect(within(screen.getByLabelText("数据范围")).getAllByRole("option").map((item) => item.textContent))
    .toEqual(["本人"])
  expect(screen.getByText(/参加天赋评测 2 还必须同时具备查看天赋评测 2 报告权限/)).toBeVisible()

  await user.selectOptions(screen.getByLabelText("权限能力"), "permission-esports-read")
  expect(within(screen.getByLabelText("数据范围")).getAllByRole("option").map((item) => item.textContent))
    .toEqual(["全部数据", "本人", "本人家庭", "指定人员", "指定班级"])
})

it("keeps talent access readable but hides its mutation controls from an ordinary permission manager", async () => {
  const accountId = "10000000-0000-4000-8000-000000000153"
  const takeId = "20000000-0000-4000-8000-000000000153"
  const readId = "20000000-0000-4000-8000-000000000154"
  vi.stubGlobal("fetch", permissionTalentFetch({
    accountId,
    takeId,
    readId,
    existingTalentAccess: true,
  }))
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <PermissionsPage canManageGlobalPermissions canManageTalentPermissions={false} />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.selectOptions(
    screen.getByLabelText("账号"),
    await screen.findByRole("option", { name: `天赋权限账号 · 已启用 · 账号 ${accountId}` }),
  )
  const current = screen.getByRole("heading", { name: "当前拥有权限" }).closest("section")!
  expect(within(current).getByText("参加天赋测试", { selector: "strong" })).toBeVisible()
  const overrides = screen.getByRole("heading", { name: "账号覆盖规则" }).closest("section")!
  expect(within(overrides).getByText("查看天赋测试报告", { selector: "strong" })).toBeVisible()
  expect(within(overrides).queryByRole("button", { name: /移除规则/ })).not.toBeInTheDocument()
  expect(within(screen.getByLabelText("权限能力")).queryByRole("option", { name: "参加天赋测试" })).not.toBeInTheDocument()
  expect(within(screen.getByLabelText("权限能力")).queryByRole("option", { name: "查看天赋测试报告" })).not.toBeInTheDocument()
  expect(within(screen.getByLabelText("权限能力")).queryByRole("option", { name: "参加天赋评测 2" })).not.toBeInTheDocument()
  expect(within(screen.getByLabelText("权限能力")).queryByRole("option", { name: "查看天赋评测 2 报告" })).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "分配角色" })).toBeVisible()
  await user.selectOptions(screen.getByLabelText("权限能力"), "permission-people")
  expect(within(screen.getByLabelText("数据范围")).getByRole("option", { name: "指定项目" })).toBeInTheDocument()
})

function permissionTalentFetch({
  accountId,
  takeId,
  readId,
  existingTalentAccess = false,
}: {
  accountId: string
  takeId: string
  readId: string
  existingTalentAccess?: boolean
}) {
  return vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/permission-account-directory")) {
      return new Response(JSON.stringify([{ id: accountId, full_name: "天赋权限账号", status: "active" }]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/roles")) return new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } })
    if (url.endsWith("/permissions")) {
      return new Response(JSON.stringify([
        { id: takeId, key: "talent_tests.take", name: "参加天赋测试", sensitive: true },
        { id: readId, key: "talent_tests.read", name: "查看天赋测试报告", sensitive: true },
        { id: "permission-esports-take", key: "esports_assessments.take", name: "esports take", sensitive: true },
        { id: "permission-esports-read", key: "esports_assessments.read", name: "esports read", sensitive: true },
        { id: "permission-people", key: "people.read", name: "查看人员", sensitive: false },
      ]), { status: 200, headers: { "Content-Type": "application/json" } })
    }
    if (url.endsWith(`/accounts/${accountId}/access`)) {
      return new Response(JSON.stringify({
        account_id: accountId,
        roles: [],
        permission_overrides: existingTalentAccess ? [{
          id: "override-talent-read",
          user_id: accountId,
          permission_id: readId,
          effect: "allow",
          scope_kind: "family",
          scope_id: null,
        }] : [],
        effective_permissions: existingTalentAccess ? [{
          id: takeId,
          key: "talent_tests.take",
          name: "参加天赋测试",
          sensitive: true,
          scope_kinds: ["self"],
          sources: ["role"],
        }] : [],
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
}

it("previews deny precedence before saving an account override", async () => {
  const accountId = "10000000-0000-4000-8000-000000000001"
  const permissionId = "20000000-0000-4000-8000-000000000002"
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/permission-account-directory") && !init?.method) {
      return new Response(
        JSON.stringify([
          {
            id: accountId,
            full_name: "权限测试账号",
            status: "active",
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/roles")) {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/permissions")) {
      return new Response(
        JSON.stringify([
          { id: permissionId, key: "people.read", name: "查看人员", sensitive: false },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith(`/accounts/${accountId}/access`) && !init?.method) {
      return new Response(
        JSON.stringify({ account_id: accountId, roles: [], permission_overrides: [] }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (
      url.endsWith(`/accounts/${accountId}/permission-overrides/preview`) &&
      init?.method === "POST"
    ) {
      return new Response(JSON.stringify({ allowed: false, reason: "explicit_deny" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith(`/accounts/${accountId}/permission-overrides`) && init?.method === "POST") {
      return new Response(
        JSON.stringify({
          id: "40000000-0000-4000-8000-000000000001",
          user_id: accountId,
          permission_id: permissionId,
          effect: "deny",
          scope_kind: "global",
          scope_id: null,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()
  const refreshCurrentUser = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <PermissionsPage
          canManageGlobalPermissions
          currentAccountId={accountId}
          onCurrentAccountAccessChanged={refreshCurrentUser}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(screen.getByText("显式拒绝优先于角色和账号允许")).toBeVisible()
  await screen.findByRole("option", {
    name: `权限测试账号 · 已启用 · 账号 ${accountId}`,
  })
  await user.selectOptions(screen.getByLabelText("账号"), accountId)
  await user.selectOptions(screen.getByLabelText("允许或拒绝"), "deny")
  await user.selectOptions(screen.getByLabelText("权限能力"), permissionId)
  await user.selectOptions(screen.getByLabelText("数据范围"), "global")
  await user.click(screen.getByRole("button", { name: "查看变更摘要" }))

  expect(screen.getByText("这条显式拒绝将覆盖角色允许和账号允许")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "确认保存" }))

  const createCall = fetchMock.mock.calls.find(
    ([input, init]) =>
      String(input).endsWith(`/accounts/${accountId}/permission-overrides`) &&
      init?.method === "POST",
  )
  expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({
    permission_id: permissionId,
    effect: "deny",
    scope_kind: "global",
    scope_id: null,
  })
  expect(refreshCurrentUser).toHaveBeenCalledTimes(1)
})

it("selects a named person scope and includes the name in the final effect summary", async () => {
  const accountId = "10000000-0000-4000-8000-000000000001"
  const permissionId = "20000000-0000-4000-8000-000000000002"
  const personId = "30000000-0000-4000-8000-000000000003"
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/permission-account-directory")) {
      return new Response(
        JSON.stringify([
          { id: accountId, full_name: "权限测试账号", status: "active" },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/roles")) {
      return new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } })
    }
    if (url.endsWith("/permissions")) {
      return new Response(
        JSON.stringify([{ id: permissionId, key: "people.read", name: "查看人员", sensitive: false }]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith(`/accounts/${accountId}/access`)) {
      return new Response(
        JSON.stringify({ account_id: accountId, roles: [], permission_overrides: [] }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.includes("/permission-person-directory?name=")) {
      return new Response(JSON.stringify([{ id: personId, full_name: "指定学员" }]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (
      url.endsWith(`/accounts/${accountId}/permission-overrides/preview`) &&
      init?.method === "POST"
    ) {
      return new Response(JSON.stringify({ allowed: true, reason: "account_allow" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith(`/accounts/${accountId}/permission-overrides`) && init?.method === "POST") {
      return new Response(
        JSON.stringify({
          id: "40000000-0000-4000-8000-000000000001",
          user_id: accountId,
          permission_id: permissionId,
          effect: "allow",
          scope_kind: "person",
          scope_id: personId,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <PermissionsPage canManageGlobalPermissions />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await screen.findByRole("option", {
    name: `权限测试账号 · 已启用 · 账号 ${accountId}`,
  })
  await user.selectOptions(screen.getByLabelText("账号"), accountId)
  await user.selectOptions(screen.getByLabelText("权限能力"), permissionId)
  await user.selectOptions(screen.getByLabelText("数据范围"), "person")
  expect(screen.queryByLabelText("指定人员 ID")).not.toBeInTheDocument()
  await user.type(screen.getByLabelText("查找指定人员"), "指定学员")
  await user.click(screen.getByRole("button", { name: "查找人员" }))
  await user.click(
    await screen.findByRole("radio", { name: `指定学员 · 档案 ${personId}` }),
  )
  await user.click(screen.getByRole("button", { name: "查看变更摘要" }))

  expect(
    screen.getByText(`查看人员 · 指定人员：指定学员 · 档案 ${personId}`),
  ).toBeVisible()
  await user.click(screen.getByRole("button", { name: "确认保存" }))
  const createCall = fetchMock.mock.calls.find(
    ([input, init]) => String(input).endsWith(`/accounts/${accountId}/permission-overrides`) && init?.method === "POST",
  )
  expect(JSON.parse(String(createCall?.[1]?.body)).scope_id).toBe(personId)
})

it("prevents self lockout and reports an existing deny as the final effect", async () => {
  const accountId = "10000000-0000-4000-8000-000000000001"
  const superAdminRoleId = "10000000-0000-4000-8000-000000000009"
  const peopleReadId = "20000000-0000-4000-8000-000000000002"
  const permissionsManageId = "20000000-0000-4000-8000-000000000008"
  const ownAllowId = "40000000-0000-4000-8000-000000000001"
  const existingDenyId = "40000000-0000-4000-8000-000000000002"
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/permission-account-directory")) {
      return new Response(
        JSON.stringify([{ id: accountId, full_name: "当前管理员", status: "active" }]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/roles")) {
      return new Response(
        JSON.stringify([
          { id: superAdminRoleId, key: "super_admin", name: "超级管理员" },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/permissions")) {
      return new Response(
        JSON.stringify([
          { id: peopleReadId, key: "people.read", name: "查看人员", sensitive: false },
          {
            id: permissionsManageId,
            key: "permissions.manage",
            name: "管理角色与权限",
            sensitive: true,
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith(`/accounts/${accountId}/access`)) {
      return new Response(
        JSON.stringify({
          account_id: accountId,
          roles: [
            { id: superAdminRoleId, key: "super_admin", name: "超级管理员" },
          ],
          permission_overrides: [
            {
              id: ownAllowId,
              user_id: accountId,
              permission_id: permissionsManageId,
              effect: "allow",
              scope_kind: "global",
              scope_id: null,
              scope_label: null,
            },
            {
              id: existingDenyId,
              user_id: accountId,
              permission_id: peopleReadId,
              effect: "deny",
              scope_kind: "global",
              scope_id: null,
              scope_label: null,
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (
      url.endsWith(`/accounts/${accountId}/permission-overrides/preview`) &&
      init?.method === "POST"
    ) {
      return new Response(JSON.stringify({ allowed: false, reason: "explicit_deny" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <PermissionsPage canManageGlobalPermissions currentAccountId={accountId} />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await screen.findByRole("option", {
    name: `当前管理员 · 已启用 · 账号 ${accountId}`,
  })
  await user.selectOptions(screen.getByLabelText("账号"), accountId)
  expect(
    await screen.findByRole("button", { name: "超级管理员角色受保护" }),
  ).toBeDisabled()
  expect(
    screen.getByRole("button", { name: "自己的权限管理授权受保护" }),
  ).toBeDisabled()

  await user.selectOptions(screen.getByLabelText("允许或拒绝"), "allow")
  await user.selectOptions(screen.getByLabelText("权限能力"), peopleReadId)
  await user.selectOptions(screen.getByLabelText("数据范围"), "global")
  await user.click(screen.getByRole("button", { name: "查看变更摘要" }))
  expect(screen.getByText("最终结果：拒绝（匹配的显式拒绝优先）")).toBeVisible()

  await user.click(screen.getByRole("button", { name: "返回修改" }))
  await user.selectOptions(screen.getByLabelText("允许或拒绝"), "deny")
  await user.selectOptions(screen.getByLabelText("权限能力"), permissionsManageId)
  await user.click(screen.getByRole("button", { name: "查看变更摘要" }))
  expect(screen.getByRole("alert")).toHaveTextContent("不能拒绝自己的权限管理能力")
})

it("disambiguates duplicate person names and existing person scopes", async () => {
  const accountId = "10000000-0000-4000-8000-000000000001"
  const secondAccountId = "10000000-0000-4000-8000-000000000002"
  const permissionId = "20000000-0000-4000-8000-000000000002"
  const firstPersonId = "30000000-0000-4000-8000-000000000003"
  const secondPersonId = "30000000-0000-4000-8000-000000000004"
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/permission-account-directory")) {
      return new Response(
        JSON.stringify([
          { id: accountId, full_name: "权限账号", status: "active" },
          { id: secondAccountId, full_name: "权限账号", status: "active" },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/roles")) {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/permissions")) {
      return new Response(
        JSON.stringify([
          { id: permissionId, key: "people.read", name: "查看人员", sensitive: false },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith(`/accounts/${accountId}/access`)) {
      return new Response(
        JSON.stringify({
          account_id: accountId,
          roles: [],
          permission_overrides: [
            {
              id: "40000000-0000-4000-8000-000000000004",
              user_id: accountId,
              permission_id: permissionId,
              effect: "allow",
              scope_kind: "person",
              scope_id: firstPersonId,
              scope_label: "同名学员",
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.includes("/permission-person-directory?name=")) {
      return new Response(
        JSON.stringify([
          { id: firstPersonId, full_name: "同名学员" },
          { id: secondPersonId, full_name: "同名学员" },
        ]),
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
        <PermissionsPage canManageGlobalPermissions />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(
    await screen.findByRole("option", {
      name: `权限账号 · 已启用 · 账号 ${accountId}`,
    }),
  ).toBeVisible()
  expect(
    screen.getByRole("option", {
      name: `权限账号 · 已启用 · 账号 ${secondAccountId}`,
    }),
  ).toBeVisible()
  await user.selectOptions(screen.getByLabelText("账号"), accountId)
  expect(
    await screen.findByText(`允许 · 指定人员：同名学员 · 档案 ${firstPersonId}`),
  ).toBeVisible()
  await user.selectOptions(screen.getByLabelText("权限能力"), permissionId)
  await user.selectOptions(screen.getByLabelText("数据范围"), "person")
  await user.type(screen.getByLabelText("查找指定人员"), "同名学员")
  await user.click(screen.getByRole("button", { name: "查找人员" }))
  expect(
    await screen.findByRole("radio", { name: `同名学员 · 档案 ${firstPersonId}` }),
  ).toBeVisible()
  expect(
    screen.getByRole("radio", { name: `同名学员 · 档案 ${secondPersonId}` }),
  ).toBeVisible()
})

it("searches paginated class scopes and preserves inactive historical labels", async () => {
  const accountId = "10000000-0000-4000-8000-000000000001"
  const permissionId = "20000000-0000-4000-8000-000000000010"
  const activeClassId = "50000000-0000-4000-8000-000000000001"
  const inactiveClassId = "50000000-0000-4000-8000-000000000002"
  const secondPageClassId = "50000000-0000-4000-8000-000000000003"
  const classCalls: string[] = []
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/permission-account-directory")) {
      return new Response(
        JSON.stringify([{ id: accountId, full_name: "班级权限管理员", status: "active" }]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/roles")) {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/permissions")) {
      return new Response(
        JSON.stringify([
          { id: permissionId, key: "teaching.read", name: "查看教学配置", sensitive: false },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith(`/accounts/${accountId}/access`)) {
      return new Response(
        JSON.stringify({
          account_id: accountId,
          roles: [],
          permission_overrides: [
            {
              id: "40000000-0000-4000-8000-000000000001",
              user_id: accountId,
              permission_id: permissionId,
              effect: "allow",
              scope_kind: "assigned_class",
              scope_id: inactiveClassId,
              scope_label: "历史营期 · 停用班 · 英雄联盟",
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.includes("/permission-class-directory?")) {
      classCalls.push(url)
      const page = new URL(url, "http://localhost").searchParams.get("page")
      return new Response(
        JSON.stringify(
          page === "2"
            ? {
                items: [
                  {
                    id: secondPageClassId,
                    class_name: "进阶班",
                    cohort_name: "八月营期",
                    project_name: "无畏契约",
                    label: "八月营期 · 进阶班 · 无畏契约",
                    is_active: true,
                  },
                ],
                page: 2,
                page_size: 2,
                total: 3,
              }
            : {
                items: [
                  {
                    id: activeClassId,
                    class_name: "基础班",
                    cohort_name: "七月营期",
                    project_name: "英雄联盟",
                    label: "七月营期 · 基础班 · 英雄联盟",
                    is_active: true,
                  },
                  {
                    id: inactiveClassId,
                    class_name: "停用班",
                    cohort_name: "历史营期",
                    project_name: "英雄联盟",
                    label: "历史营期 · 停用班 · 英雄联盟",
                    is_active: false,
                  },
                ],
                page: 1,
                page_size: 2,
                total: 3,
              },
        ),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (
      url.endsWith(`/accounts/${accountId}/permission-overrides/preview`) &&
      init?.method === "POST"
    ) {
      return new Response(JSON.stringify({ allowed: true, reason: "account_allow" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <PermissionsPage canManageGlobalPermissions />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.selectOptions(
    screen.getByLabelText("账号"),
    await screen.findByRole("option", {
      name: `班级权限管理员 · 已启用 · 账号 ${accountId}`,
    }),
  )
  expect(
    await screen.findByText("允许 · 指定班级：历史营期 · 停用班 · 英雄联盟"),
  ).toBeVisible()
  await user.selectOptions(screen.getByLabelText("权限能力"), permissionId)
  await user.selectOptions(screen.getByLabelText("数据范围"), "assigned_class")
  await user.type(screen.getByLabelText("查找指定班级"), "训练")
  expect(classCalls).toHaveLength(0)

  expect(
    await screen.findByRole("radio", { name: "七月营期 · 基础班 · 英雄联盟" }),
  ).toBeVisible()
  expect(classCalls).toHaveLength(1)
  expect(classCalls[0]).toContain("query=%E8%AE%AD%E7%BB%83")
  expect(
    screen.getByRole("radio", { name: "历史营期 · 停用班 · 英雄联盟（已停用）" }),
  ).toBeDisabled()

  await user.click(screen.getByRole("button", { name: "下一页" }))
  const secondPage = await screen.findByRole("radio", {
    name: "八月营期 · 进阶班 · 无畏契约",
  })
  await waitFor(() => expect(classCalls).toHaveLength(2))
  await user.click(secondPage)
  await user.click(screen.getByRole("button", { name: "查看变更摘要" }))

  expect(screen.getByText("查看教学配置 · 指定班级：八月营期 · 进阶班 · 无畏契约")).toBeVisible()
  const previewCall = fetchMock.mock.calls.find(
    ([input, callInit]) =>
      String(input).endsWith(`/accounts/${accountId}/permission-overrides/preview`) &&
      callInit?.method === "POST",
  )
  expect(JSON.parse(String(previewCall?.[1]?.body))).toEqual({
    permission_id: permissionId,
    effect: "allow",
    scope_kind: "assigned_class",
    scope_id: secondPageClassId,
  })
})

it("offers every supported scope directory, labels finite rules, and hides deferred evaluation permissions", async () => {
  const accountId = "10000000-0000-4000-8000-000000000071"
  const projectPermissionId = "20000000-0000-4000-8000-000000000031"
  const cohortId = "70000000-0000-4000-8000-000000000071"
  const projectId = "30000000-0000-4000-8000-000000000071"
  const inactiveProjectId = "30000000-0000-4000-8000-000000000072"
  const enrollmentId = "a0000000-0000-4000-8000-000000000071"
  const directoryCalls: string[] = []
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/permission-account-directory")) {
      return new Response(
        JSON.stringify([{ id: accountId, full_name: "评价权限管理员", status: "active" }]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/roles")) {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/permissions")) {
      return new Response(
        JSON.stringify([
          { id: "permission-upload", key: "evaluation_media.upload", name: "上传评价媒体", sensitive: true },
          { id: "permission-original", key: "evaluation_media.original.read", name: "查看评价媒体原图", sensitive: true },
          { id: "permission-analysis", key: "analysis_profiles.manage", name: "管理分析配置", sensitive: true },
          { id: projectPermissionId, key: "student_game_accounts.manage", name: "管理学员游戏账号", sensitive: true },
          { id: "permission-staff", key: "cohort_staff.manage", name: "管理届次员工分配", sensitive: true },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith(`/accounts/${accountId}/access`)) {
      return new Response(
        JSON.stringify({
          account_id: accountId,
          roles: [],
          permission_overrides: [
            {
              id: "40000000-0000-4000-8000-000000000071",
              user_id: accountId,
              permission_id: projectPermissionId,
              effect: "allow",
              scope_kind: "project",
              scope_id: projectId,
              scope_label: "英雄联盟",
              valid_from: "2026-08-01T00:00:00+08:00",
              valid_until: "2026-08-22T18:00:00+08:00",
              grant_reason: "营期临时账号支持",
            },
            {
              id: "40000000-0000-4000-8000-000000000072",
              user_id: accountId,
              permission_id: projectPermissionId,
              effect: "allow",
              scope_kind: "project",
              scope_id: projectId,
              scope_label: "无期限结束项目",
              valid_from: "2026-08-01T00:00:00+08:00",
              valid_until: null,
              grant_reason: null,
            },
            {
              id: "40000000-0000-4000-8000-000000000073",
              user_id: accountId,
              permission_id: projectPermissionId,
              effect: "allow",
              scope_kind: "project",
              scope_id: projectId,
              scope_label: "仅设置结束时间项目",
              valid_from: null,
              valid_until: "2026-08-25T18:30:00+08:00",
              grant_reason: null,
            },
            {
              id: "40000000-0000-4000-8000-000000000074",
              user_id: accountId,
              permission_id: projectPermissionId,
              effect: "allow",
              scope_kind: "project",
              scope_id: projectId,
              scope_label: "历史长期项目",
              valid_from: null,
              valid_until: null,
              grant_reason: null,
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    const directories: Record<string, unknown> = {
      "/permission-cohort-directory?": {
        items: [{ id: cohortId, name: "八月营期", status: "open", label: "八月营期" }],
        page: 1,
        page_size: 20,
        total: 1,
      },
      "/permission-project-directory?": {
        items: [
          { id: projectId, name: "英雄联盟", is_active: true, label: "英雄联盟" },
          {
            id: inactiveProjectId,
            name: "星际争霸",
            is_active: false,
            label: "星际争霸",
          },
        ],
        page: 1,
        page_size: 20,
        total: 2,
      },
      "/permission-enrollment-directory?": {
        items: [
          {
            id: enrollmentId,
            student_name: "张同学",
            cohort_name: "八月营期",
            class_name: "英雄联盟一班",
            project_name: "英雄联盟",
            status: "active",
            label: "张同学 · 八月营期 · 英雄联盟一班 · 英雄联盟",
          },
        ],
        page: 1,
        page_size: 20,
        total: 1,
      },
    }
    const directory = Object.entries(directories).find(([path]) => url.includes(path))
    if (directory) {
      directoryCalls.push(url)
      return new Response(JSON.stringify(directory[1]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <PermissionsPage canManageGlobalPermissions />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.selectOptions(
    screen.getByLabelText("账号"),
    await screen.findByRole("option", { name: `评价权限管理员 · 已启用 · 账号 ${accountId}` }),
  )
  const scope = screen.getByLabelText("数据范围")
  expect(within(scope).getByRole("option", { name: "本人" })).toHaveValue("self")
  expect(within(scope).getByRole("option", { name: "本人家庭" })).toHaveValue("family")
  expect(within(scope).getByRole("option", { name: "指定人员" })).toHaveValue("person")
  expect(within(scope).getByRole("option", { name: "指定班级" })).toHaveValue("assigned_class")
  expect(within(scope).getByRole("option", { name: "指定营期" })).toHaveValue("cohort")
  expect(within(scope).getByRole("option", { name: "指定项目" })).toHaveValue("project")
  expect(within(scope).getByRole("option", { name: "指定报名" })).toHaveValue("enrollment")

  expect(screen.getByText("允许 · 指定项目：英雄联盟")).toBeVisible()
  expect(screen.getByText("有效期：2026-08-01 至 2026-08-22")).toBeVisible()
  expect(screen.getByText("有效期：自 2026-08-01 起")).toBeVisible()
  expect(screen.getByText("有效期：截至 2026-08-25")).toBeVisible()
  expect(screen.getByText("有效期：长期有效")).toBeVisible()
  expect(screen.getByText("授权原因：营期临时账号支持")).toBeVisible()
  expect(screen.queryByRole("option", { name: "上传评价媒体" })).not.toBeInTheDocument()
  expect(screen.getByRole("option", { name: "查看评价媒体原图" })).toBeVisible()
  expect(screen.getByRole("option", { name: "管理分析配置" })).toBeVisible()
  expect(screen.getByRole("option", { name: "管理学员游戏账号" })).toBeVisible()
  expect(screen.getByRole("option", { name: "管理届次员工分配" })).toBeVisible()

  await user.selectOptions(scope, "cohort")
  await user.type(screen.getByLabelText("查找指定营期"), "八月")
  expect(await screen.findByRole("radio", { name: `八月营期 · ${cohortId}` })).toBeVisible()
  await user.selectOptions(scope, "project")
  await user.type(screen.getByLabelText("查找指定项目"), "英雄")
  expect(await screen.findByRole("radio", { name: `英雄联盟 · ${projectId}` })).toBeVisible()
  const inactiveProject = screen.getByRole("radio", {
    name: `星际争霸 · ${inactiveProjectId}`,
  })
  expect(inactiveProject).toBeDisabled()
  await user.selectOptions(screen.getByLabelText("允许或拒绝"), "deny")
  expect(inactiveProject).toBeEnabled()
  await user.click(inactiveProject)
  expect(inactiveProject).toBeChecked()
  await user.selectOptions(scope, "enrollment")
  await user.type(screen.getByLabelText("查找指定报名"), "张同学")
  expect(await screen.findByRole("radio", {
    name: `张同学 · 八月营期 · 英雄联盟一班 · 英雄联盟 · ${enrollmentId}`,
  })).toBeVisible()
  expect(directoryCalls).toEqual([
    expect.stringContaining("/permission-cohort-directory?query=%E5%85%AB%E6%9C%88&page=1&page_size=20"),
    expect.stringContaining("/permission-project-directory?query=%E8%8B%B1%E9%9B%84&page=1&page_size=20"),
    expect.stringContaining("/permission-enrollment-directory?query=%E5%BC%A0%E5%90%8C%E5%AD%A6&page=1&page_size=20"),
  ])
})

it("requires a finite period and reason for sensitive evaluation allows and preserves them from preview to create", async () => {
  const accountId = "10000000-0000-4000-8000-000000000081"
  const permissionId = "20000000-0000-4000-8000-000000000031"
  const projectId = "30000000-0000-4000-8000-000000000081"
  const writeBodies: unknown[] = []
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/permission-account-directory")) {
      return new Response(
        JSON.stringify([{ id: accountId, full_name: "临时授权管理员", status: "active" }]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/roles")) {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/permissions")) {
      return new Response(
        JSON.stringify([
          {
            id: permissionId,
            key: "student_game_accounts.manage",
            name: "管理学员游戏账号",
            sensitive: true,
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith(`/accounts/${accountId}/access`)) {
      return new Response(
        JSON.stringify({ account_id: accountId, roles: [], permission_overrides: [] }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.includes("/permission-project-directory?")) {
      return new Response(
        JSON.stringify({
          items: [{ id: projectId, name: "无畏契约", is_active: true, label: "无畏契约" }],
          page: 1,
          page_size: 20,
          total: 1,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (
      url.endsWith(`/accounts/${accountId}/permission-overrides/preview`) &&
      init?.method === "POST"
    ) {
      writeBodies.push(JSON.parse(String(init.body)))
      return new Response(JSON.stringify({ allowed: true, reason: "account_allow" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith(`/accounts/${accountId}/permission-overrides`) && init?.method === "POST") {
      const body = JSON.parse(String(init.body))
      writeBodies.push(body)
      return new Response(
        JSON.stringify({
          id: "40000000-0000-4000-8000-000000000081",
          user_id: accountId,
          scope_label: "无畏契约",
          ...body,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <PermissionsPage canManageGlobalPermissions />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.selectOptions(
    screen.getByLabelText("账号"),
    await screen.findByRole("option", { name: `临时授权管理员 · 已启用 · 账号 ${accountId}` }),
  )
  await user.selectOptions(screen.getByLabelText("权限能力"), permissionId)
  await user.click(screen.getByRole("button", { name: "查看变更摘要" }))

  expect(screen.getByRole("alert")).toHaveTextContent(
    "敏感评价允许不能使用全部数据范围",
  )
  expect(writeBodies).toHaveLength(0)

  await user.selectOptions(screen.getByLabelText("数据范围"), "project")
  await user.type(screen.getByLabelText("查找指定项目"), "无畏")
  await user.click(await screen.findByRole("radio", { name: `无畏契约 · ${projectId}` }))
  await user.click(screen.getByRole("button", { name: "查看变更摘要" }))

  expect(screen.getByRole("alert")).toHaveTextContent(
    "敏感评价允许必须填写有效起止时间和授权原因",
  )
  expect(writeBodies).toHaveLength(0)

  await user.type(screen.getByLabelText("授权开始日期"), "2026-08-01")
  await user.type(screen.getByLabelText("授权结束日期"), "2026-08-22")
  await user.type(screen.getByLabelText("授权原因"), "营期项目账号支持")
  await user.click(screen.getByRole("button", { name: "查看变更摘要" }))
  await user.click(await screen.findByRole("button", { name: "确认保存" }))

  expect(writeBodies).toHaveLength(2)
  expect(writeBodies[1]).toEqual(writeBodies[0])
  expect(writeBodies[0]).toMatchObject({
    permission_id: permissionId,
    effect: "allow",
    scope_kind: "project",
    scope_id: projectId,
    grant_reason: "营期项目账号支持",
  })
  const body = writeBodies[0] as { valid_from: string; valid_until: string }
  expect(new Date(body.valid_from).toISOString()).toBe("2026-07-31T16:00:00.000Z")
  expect(new Date(body.valid_until).toISOString()).toBe("2026-08-21T16:00:00.000Z")
})

it("clears every private root before a current-account auth refresh can fail", async () => {
  const accountId = "10000000-0000-4000-8000-000000000091"
  const roleId = "20000000-0000-4000-8000-000000000091"
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const evaluationKey = [...evaluationKeys.root(accountId), "private-role-cache"] as const
  const personKey = ["person-detail", accountId] as const
  let accessCalls = 0
  let privateDataPresentWhenAccessRefreshStarted: boolean | undefined
  let privateDataPresentWhenAuthRefreshStarted: boolean | undefined
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/permission-account-directory")) {
      return new Response(
        JSON.stringify([{ id: accountId, full_name: "当前角色管理员", status: "active" }]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/roles") && !init?.method) {
      return new Response(
        JSON.stringify([{ id: roleId, key: "operator", name: "运营管理员" }]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/permissions")) {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith(`/accounts/${accountId}/access`) && !init?.method) {
      accessCalls += 1
      if (accessCalls === 2) {
        privateDataPresentWhenAccessRefreshStarted =
          queryClient.getQueryData(evaluationKey) !== undefined ||
          queryClient.getQueryData(personKey) !== undefined
        throw new Error("access refresh failed")
      }
      return new Response(
        JSON.stringify({ account_id: accountId, roles: [], permission_overrides: [] }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith(`/accounts/${accountId}/roles`) && init?.method === "POST") {
      return new Response(null, { status: 204 })
    }
    throw new Error(`Unexpected request: ${url} ${init?.method ?? "GET"}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const refreshCurrentUser = vi.fn(async () => {
    queryClient.removeQueries({ queryKey: evaluationKey, exact: true })
    queryClient.removeQueries({ queryKey: personKey, exact: true })
    privateDataPresentWhenAuthRefreshStarted =
      queryClient.getQueryData(evaluationKey) !== undefined ||
      queryClient.getQueryData(personKey) !== undefined
    throw new Error("auth refresh failed")
  })
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <PermissionsPage
          canManageGlobalPermissions
          currentAccountId={accountId}
          onCurrentAccountAccessChanged={refreshCurrentUser}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await screen.findByRole("option", {
    name: `当前角色管理员 · 已启用 · 账号 ${accountId}`,
  })
  await user.selectOptions(screen.getByLabelText("账号"), accountId)
  await screen.findByText("当前没有分配角色。")
  queryClient.setQueryData(evaluationKey, { game_id: "PrivateRole#ID" })
  queryClient.setQueryData(personKey, { full_name: "Private Person" })
  await user.selectOptions(screen.getByLabelText("添加角色"), roleId)
  await user.click(screen.getByRole("button", { name: "分配角色" }))

  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法分配角色")
  expect(privateDataPresentWhenAuthRefreshStarted).toBe(false)
  expect(privateDataPresentWhenAccessRefreshStarted).toBeUndefined()
  expect(queryClient.getQueryData(evaluationKey)).toBeUndefined()
  expect(queryClient.getQueryData(personKey)).toBeUndefined()
  expect(refreshCurrentUser).toHaveBeenCalledTimes(1)
  expect(accessCalls).toBe(1)
  expect(screen.getByRole("heading", { name: "权限管理" })).toBeVisible()
})

it("keeps every private root cleared when current-account access refresh fails", async () => {
  const accountId = "10000000-0000-4000-8000-000000000092"
  const permissionId = "20000000-0000-4000-8000-000000000092"
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const evaluationKey = [...evaluationKeys.root(accountId), "private-override-cache"] as const
  const personKey = ["person-detail", accountId] as const
  let accessCalls = 0
  let privateDataPresentWhenAccessRefreshStarted: boolean | undefined
  let privateDataPresentWhenAuthRefreshStarted: boolean | undefined
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/permission-account-directory")) {
      return new Response(
        JSON.stringify([{ id: accountId, full_name: "当前规则管理员", status: "active" }]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/roles")) {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/permissions")) {
      return new Response(
        JSON.stringify([
          { id: permissionId, key: "people.read", name: "查看人员", sensitive: false },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith(`/accounts/${accountId}/access`) && !init?.method) {
      accessCalls += 1
      if (accessCalls === 2) {
        privateDataPresentWhenAccessRefreshStarted =
          queryClient.getQueryData(evaluationKey) !== undefined ||
          queryClient.getQueryData(personKey) !== undefined
        throw new Error("access refresh failed")
      }
      return new Response(
        JSON.stringify({ account_id: accountId, roles: [], permission_overrides: [] }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (
      url.endsWith(`/accounts/${accountId}/permission-overrides/preview`) &&
      init?.method === "POST"
    ) {
      return new Response(JSON.stringify({ allowed: true, reason: "account_allow" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith(`/accounts/${accountId}/permission-overrides`) && init?.method === "POST") {
      return new Response(
        JSON.stringify({
          id: "40000000-0000-4000-8000-000000000092",
          user_id: accountId,
          permission_id: permissionId,
          effect: "allow",
          scope_kind: "global",
          scope_id: null,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url} ${init?.method ?? "GET"}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const refreshCurrentUser = vi.fn(async () => {
    queryClient.removeQueries({ queryKey: evaluationKey, exact: true })
    queryClient.removeQueries({ queryKey: personKey, exact: true })
    privateDataPresentWhenAuthRefreshStarted =
      queryClient.getQueryData(evaluationKey) !== undefined ||
      queryClient.getQueryData(personKey) !== undefined
  })
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <PermissionsPage
          canManageGlobalPermissions
          currentAccountId={accountId}
          onCurrentAccountAccessChanged={refreshCurrentUser}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await screen.findByRole("option", {
    name: `当前规则管理员 · 已启用 · 账号 ${accountId}`,
  })
  await user.selectOptions(screen.getByLabelText("账号"), accountId)
  await screen.findByText("当前没有账号覆盖规则。")
  queryClient.setQueryData(evaluationKey, { game_id: "PrivateOverride#ID" })
  queryClient.setQueryData(personKey, { full_name: "Private Person" })
  await user.selectOptions(screen.getByLabelText("权限能力"), permissionId)
  await user.click(screen.getByRole("button", { name: "查看变更摘要" }))
  await user.click(await screen.findByRole("button", { name: "确认保存" }))

  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法保存权限规则")
  expect(privateDataPresentWhenAuthRefreshStarted).toBe(false)
  expect(privateDataPresentWhenAccessRefreshStarted).toBe(false)
  expect(queryClient.getQueryData(evaluationKey)).toBeUndefined()
  expect(queryClient.getQueryData(personKey)).toBeUndefined()
  expect(refreshCurrentUser).toHaveBeenCalledTimes(1)
  expect(screen.getByRole("heading", { name: "权限管理" })).toBeVisible()
})

it.each([
  {
    scopeKind: "building",
    scopeLabel: "指定楼栋",
    searchLabel: "查找指定楼栋",
    directoryPath: "/permission-building-directory?",
    resourceId: "53011fd7-1aae-5380-b028-23ad5a2aacdf",
    resourceLabel: "4幢",
    item: {
      id: "53011fd7-1aae-5380-b028-23ad5a2aacdf",
      code: "4",
      name: "4幢",
      is_active: true,
      label: "4幢",
    },
  },
  {
    scopeKind: "floor",
    scopeLabel: "指定楼层",
    searchLabel: "查找指定楼层",
    directoryPath: "/permission-floor-directory?",
    resourceId: "ff0b41d6-d1be-578f-9186-acd45d3081ed",
    resourceLabel: "4幢 · 二楼",
    item: {
      id: "ff0b41d6-d1be-578f-9186-acd45d3081ed",
      building_id: "53011fd7-1aae-5380-b028-23ad5a2aacdf",
      building_name: "4幢",
      number: 2,
      name: "二楼",
      building_is_active: true,
      is_active: true,
      label: "4幢 · 二楼",
    },
  },
])(
  "selects, previews, and saves a $scopeKind housing permission scope",
  async ({
    scopeKind,
    scopeLabel,
    searchLabel,
    directoryPath,
    resourceId,
    resourceLabel,
    item,
  }) => {
    const accountId = "10000000-0000-4000-8000-000000000120"
    const permissionId = "20000000-0000-4000-8000-000000000037"
    const writes: Array<Record<string, unknown>> = []
    let saved = false
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      if (url.endsWith("/permission-account-directory")) {
        return new Response(
          JSON.stringify([
            { id: accountId, full_name: "房态权限账号", status: "active" },
          ]),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith("/roles")) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.endsWith("/permissions")) {
        return new Response(
          JSON.stringify([
            {
              id: permissionId,
              key: "housing.read",
              name: "查看房态",
              sensitive: false,
            },
          ]),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith(`/accounts/${accountId}/access`) && !init?.method) {
        return new Response(
          JSON.stringify({
            account_id: accountId,
            roles: [],
            permission_overrides: saved
              ? [
                  {
                    id: "40000000-0000-4000-8000-000000000120",
                    user_id: accountId,
                    permission_id: permissionId,
                    effect: "allow",
                    scope_kind: scopeKind,
                    scope_id: resourceId,
                    scope_label: resourceLabel,
                  },
                ]
              : [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.includes(directoryPath)) {
        return new Response(
          JSON.stringify({ items: [item], page: 1, page_size: 20, total: 1 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (
        url.endsWith(`/accounts/${accountId}/permission-overrides/preview`) &&
        init?.method === "POST"
      ) {
        writes.push(JSON.parse(String(init.body)))
        return new Response(JSON.stringify({ allowed: true, reason: "account_allow" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      if (
        url.endsWith(`/accounts/${accountId}/permission-overrides`) &&
        init?.method === "POST"
      ) {
        const body = JSON.parse(String(init.body))
        writes.push(body)
        saved = true
        return new Response(
          JSON.stringify({
            id: "40000000-0000-4000-8000-000000000120",
            user_id: accountId,
            scope_label: resourceLabel,
            ...body,
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        )
      }
      throw new Error(`Unexpected request: ${url} ${init?.method ?? "GET"}`)
    })
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()

    render(
      <MemoryRouter>
        <QueryClientProvider client={new QueryClient()}>
          <PermissionsPage canManageGlobalPermissions />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    await user.selectOptions(
      screen.getByLabelText("账号"),
      await screen.findByRole("option", {
        name: `房态权限账号 · 已启用 · 账号 ${accountId}`,
      }),
    )
    await user.selectOptions(screen.getByLabelText("权限能力"), permissionId)
    await user.selectOptions(screen.getByLabelText("数据范围"), scopeKind)
    await user.type(screen.getByLabelText(searchLabel), "4幢")
    await user.click(
      await screen.findByRole("radio", { name: `${resourceLabel} · ${resourceId}` }),
    )
    await user.click(screen.getByRole("button", { name: "查看变更摘要" }))
    expect(screen.getByText(`查看房态 · ${scopeLabel}：${resourceLabel}`)).toBeVisible()
    await user.click(screen.getByRole("button", { name: "确认保存" }))

    await waitFor(() => {
      expect(screen.getByText(`允许 · ${scopeLabel}：${resourceLabel}`)).toBeVisible()
    })
    expect(writes).toEqual([
      {
        permission_id: permissionId,
        effect: "allow",
        scope_kind: scopeKind,
        scope_id: resourceId,
      },
      {
        permission_id: permissionId,
        effect: "allow",
        scope_kind: scopeKind,
        scope_id: resourceId,
      },
    ])
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(`${directoryPath}query=4%E5%B9%A2&page=1&page_size=20`),
      expect.anything(),
    )
  },
)

it("blocks inactive housing allows, permits denies, and clears selections on effect changes", async () => {
  const accountId = "10000000-0000-4000-8000-000000000121"
  const permissionId = "20000000-0000-4000-8000-000000000037"
  const inactiveBuildingId = "53011fd7-1aae-5380-b028-23ad5a2a1211"
  const inactiveFloorId = "ff0b41d6-d1be-578f-9186-acd45d301211"
  const inactiveParentFloorId = "ff0b41d6-d1be-578f-9186-acd45d301212"
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/permission-account-directory")) {
      return new Response(
        JSON.stringify([{ id: accountId, full_name: "停用范围账号", status: "active" }]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/roles")) {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/permissions")) {
      return new Response(
        JSON.stringify([
          {
            id: permissionId,
            key: "housing.read",
            name: "查看房态",
            sensitive: false,
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith(`/accounts/${accountId}/access`)) {
      return new Response(
        JSON.stringify({ account_id: accountId, roles: [], permission_overrides: [] }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.includes("/permission-building-directory?")) {
      return new Response(
        JSON.stringify({
          items: [
            {
              id: inactiveBuildingId,
              code: "X",
              name: "停用楼栋",
              is_active: false,
              label: "停用楼栋",
            },
          ],
          page: 1,
          page_size: 20,
          total: 1,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.includes("/permission-floor-directory?")) {
      return new Response(
        JSON.stringify({
          items: [
            {
              id: inactiveFloorId,
              building_id: "53011fd7-1aae-5380-b028-23ad5a2a1212",
              building_name: "在用楼栋",
              number: 2,
              name: "停用楼层",
              building_is_active: true,
              is_active: false,
              label: "在用楼栋 · 停用楼层",
            },
            {
              id: inactiveParentFloorId,
              building_id: inactiveBuildingId,
              building_name: "停用楼栋",
              number: 2,
              name: "二楼",
              building_is_active: false,
              is_active: true,
              label: "停用楼栋 · 二楼",
            },
          ],
          page: 1,
          page_size: 20,
          total: 2,
        }),
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
        <PermissionsPage canManageGlobalPermissions />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.selectOptions(
    screen.getByLabelText("账号"),
    await screen.findByRole("option", {
      name: `停用范围账号 · 已启用 · 账号 ${accountId}`,
    }),
  )
  await user.selectOptions(screen.getByLabelText("权限能力"), permissionId)
  await user.selectOptions(screen.getByLabelText("数据范围"), "building")
  await user.type(screen.getByLabelText("查找指定楼栋"), "停用")
  const building = await screen.findByRole("radio", {
    name: `停用楼栋 · ${inactiveBuildingId}`,
  })
  expect(building).toBeDisabled()
  await user.selectOptions(screen.getByLabelText("允许或拒绝"), "deny")
  expect(building).toBeEnabled()
  await user.click(building)
  expect(building).toBeChecked()
  await user.selectOptions(screen.getByLabelText("允许或拒绝"), "allow")
  expect(building).not.toBeChecked()
  expect(building).toBeDisabled()

  await user.selectOptions(screen.getByLabelText("数据范围"), "floor")
  await user.type(screen.getByLabelText("查找指定楼层"), "停用")
  const inactiveFloor = await screen.findByRole("radio", {
    name: `在用楼栋 · 停用楼层 · ${inactiveFloorId}`,
  })
  const inactiveParentFloor = screen.getByRole("radio", {
    name: `停用楼栋 · 二楼 · ${inactiveParentFloorId}`,
  })
  expect(inactiveFloor).toBeDisabled()
  expect(inactiveParentFloor).toBeDisabled()
  await user.selectOptions(screen.getByLabelText("允许或拒绝"), "deny")
  expect(inactiveFloor).toBeEnabled()
  expect(inactiveParentFloor).toBeEnabled()
})
