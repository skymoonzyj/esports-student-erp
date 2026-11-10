import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"
import { PersonAssignmentsPanel } from "./PersonAssignmentsPanel"

const auth = vi.hoisted(() => ({ user: { account_id: "admin", is_super_admin: true, permissions: ["people.update"], scoped_permissions: [] as string[] }, refreshUser: vi.fn(async () => undefined) }))
vi.mock("../auth/AuthProvider", () => ({ useOptionalAuth: () => ({ status: "authenticated", ...auth }) }))
afterEach(() => { vi.unstubAllGlobals(); auth.user = { account_id: "admin", is_super_admin: true, permissions: ["people.update"], scoped_permissions: [] } })
const state = {
  person_id: "p1", version: "v1", business_kind: null, source_intake_kind: "staff",
  student_project_ids: [], coach_person_ids: [],
  capabilities: { can_edit: true, can_manage_account_roles: false, can_remove_identity: false },
  eligible_projects: [{ id: "project1", name: "王者荣耀" }, { id: "project2", name: "英雄联盟" }],
  eligible_coaches: [{ id: "coach1", full_name: "王者教练", project_ids: ["project1"] }, { id: "coach2", full_name: "联盟教练", project_ids: ["project2"] }],
  current_projects: [], current_coaches: [], account_id: null, account_status: "not_activated",
}
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }) }
function mount(personId = "p1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onSaved = vi.fn()
  const ui = (id = personId) => <QueryClientProvider client={client}><PersonAssignmentsPanel personId={id} onSaved={onSaved} /></QueryClientProvider>
  return { ...render(ui()), client, onSaved, ui }
}

// Catches accidental activation/RBAC/team writes and missing staff-to-student support.
it("adds student identity/projects/coaches to an unactivated staff profile without changing account or team", async () => {
  const writes: Array<{ url: string; input: Record<string, unknown> }> = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === "PUT") { const input = JSON.parse(String(init.body)); writes.push({ url: String(url), input }); return json({ ...state, ...input, version: "v2" }) }
    return json(state)
  }))
  const { onSaved } = mount()
  await userEvent.click(await screen.findByLabelText("学员身份"))
  await userEvent.click(screen.getByLabelText("王者荣耀"))
  await userEvent.click(screen.getByLabelText("王者教练"))
  fireEvent.change(screen.getByLabelText("身份与归属变更原因"), { target: { value: "  审批后补充培训归属  " } })
  await userEvent.click(screen.getByRole("button", { name: "保存身份与归属" }))
  expect(await screen.findByRole("status")).toHaveTextContent("身份与归属已保存")
  expect(writes).toEqual([{ url: "/api/v1/people/p1/assignments", input: { business_kind: "student", student_project_ids: ["project1"], coach_person_ids: ["coach1"], expected_version: "v1", idempotency_key: expect.stringMatching(/^[0-9a-f-]{36}$/), reason: "审批后补充培训归属" } }])
  expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ version: "v2" }))
  expect(screen.getByText(/新增业务身份不会自动授予登录权限/)).toBeVisible()
  expect(screen.getByText(/本档案尚未激活账号/)).toBeVisible()
})

// Catches identity removal and clearing a stale coach without the operator seeing it.
it("locks existing identity and retains unavailable legacy labels until explicitly removed", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => json({ ...state, business_kind: "student", student_project_ids: ["legacy"], coach_person_ids: ["coach1"], current_projects: [{ id: "legacy", name: "停用项目" }], current_coaches: [state.eligible_coaches[0]] })))
  mount()
  expect(await screen.findByLabelText("学员身份")).toBeDisabled()
  expect(screen.getByLabelText(/停用项目.*不可用/)).toBeChecked()
  expect(screen.getByLabelText(/王者教练.*不适用于所选项目/)).toBeChecked()
  expect(screen.getByLabelText("队员身份")).toBeEnabled()
  expect(screen.getByText(/已有身份不可删除/)).toBeVisible()
})

it("filters coach union by selected projects and permits pending project/coach assignment", async () => {
  const writes: Record<string, unknown>[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (_url, init) => {
    if (init?.method === "PUT") { const input = JSON.parse(String(init.body)); writes.push(input); return json({ ...state, ...input }) }
    return json({ ...state, business_kind: "student" })
  }))
  mount()
  await screen.findByLabelText("学员身份")
  expect(screen.queryByLabelText("联盟教练")).not.toBeInTheDocument()
  await userEvent.click(screen.getByLabelText("英雄联盟"))
  expect(screen.getByLabelText("联盟教练")).toBeEnabled()
  expect(screen.queryByLabelText("王者教练")).not.toBeInTheDocument()
  await userEvent.click(screen.getByLabelText("英雄联盟"))
  fireEvent.change(screen.getByLabelText("身份与归属变更原因"), { target: { value: "等待分配" } })
  await userEvent.click(screen.getByRole("button", { name: "保存身份与归属" }))
  await screen.findByRole("status")
  expect(writes[0]).toMatchObject({ business_kind: "student", student_project_ids: [], coach_person_ids: [] })
})

// Catches operation-key churn on an unchanged retry and key reuse after changing intent.
it("retains validation/network drafts, reuses unchanged retry UUID and replaces it for changed payload", async () => {
  const writes: Record<string, unknown>[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (_url, init) => {
    if (init?.method !== "PUT") return json(state)
    writes.push(JSON.parse(String(init.body)))
    if (writes.length === 1) return json({ status: 422, detail: "请核对项目", errors: [{ field: "student_project_ids", message: "项目已停用" }] }, 422)
    throw new TypeError("offline")
  }))
  mount()
  await userEvent.click(await screen.findByLabelText("学员身份"))
  fireEvent.change(screen.getByLabelText("身份与归属变更原因"), { target: { value: "保留原因" } })
  const save = screen.getByRole("button", { name: "保存身份与归属" })
  await userEvent.click(save)
  expect(await screen.findByRole("alert")).toHaveTextContent("项目已停用")
  expect(screen.getByLabelText("身份与归属变更原因")).toHaveValue("保留原因")
  await userEvent.click(save)
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[1]).toEqual(writes[0])
  fireEvent.change(screen.getByLabelText("身份与归属变更原因"), { target: { value: "新的原因" } })
  await userEvent.click(save)
  await waitFor(() => expect(writes).toHaveLength(3))
  expect(writes[2].idempotency_key).not.toBe(writes[0].idempotency_key)
})

it("preserves stale draft, refreshes only by explicit review and never auto-resubmits", async () => {
  let gets = 0
  const writes: Record<string, unknown>[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (_url, init) => {
    if (init?.method === "PUT") { writes.push(JSON.parse(String(init.body))); return json({ status: 409, code: "assignments_stale", detail: "归属信息已变化" }, 409) }
    gets += 1
    return json({ ...state, version: gets === 1 ? "v1" : "v2" })
  }))
  mount()
  await userEvent.click(await screen.findByLabelText("队员身份"))
  fireEvent.change(screen.getByLabelText("身份与归属变更原因"), { target: { value: "保留草稿" } })
  await userEvent.click(screen.getByRole("button", { name: "保存身份与归属" }))
  await screen.findByRole("alert")
  expect(screen.getByRole("button", { name: "保存身份与归属" })).toBeDisabled()
  expect(screen.getByLabelText("身份与归属变更原因")).toHaveValue("保留草稿")
  await userEvent.click(screen.getByRole("button", { name: "重新读取最新归属" }))
  await screen.findByRole("button", { name: "已核对，采用最新版本" })
  expect(writes).toHaveLength(1)
  await userEvent.click(screen.getByRole("button", { name: "已核对，采用最新版本" }))
  expect(screen.getByLabelText("队员身份")).toBeChecked()
  expect(writes).toHaveLength(1)
  await userEvent.click(screen.getByRole("button", { name: "保存身份与归属" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[1].expected_version).toBe("v2")
  expect(writes[1].idempotency_key).not.toBe(writes[0].idempotency_key)
})

it.each([401, 403, 404])("clears choices and drafts immediately after denied save (%s)", async status => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (_url, init) => init?.method === "PUT" ? json({ status, detail: "无权访问" }, status) : json(state)))
  const { client } = mount()
  await userEvent.click(await screen.findByLabelText("学员身份"))
  fireEvent.change(screen.getByLabelText("身份与归属变更原因"), { target: { value: "敏感草稿" } })
  await userEvent.click(screen.getByRole("button", { name: "保存身份与归属" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("权限已失效")
  expect(screen.queryByText("王者荣耀")).not.toBeInTheDocument()
  expect(screen.queryByDisplayValue("敏感草稿")).not.toBeInTheDocument()
  expect(client.getQueriesData({ queryKey: ["person-assignments"] }).every(([, value]) => !value)).toBe(true)
})

it.each(["target", "actor", "scope"])("ignores late old responses and clears drafts when %s changes", async boundary => {
  let finish!: (value: Response) => void
  let requests = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (_url, init) => {
    if (init?.method === "PUT") return new Promise<Response>(resolve => { finish = resolve })
    requests += 1
    return json({ ...state, person_id: boundary === "target" && requests > 1 ? "p2" : "p1" })
  }))
  const { rerender, ui } = mount()
  await userEvent.click(await screen.findByLabelText("队员身份"))
  fireEvent.change(screen.getByLabelText("身份与归属变更原因"), { target: { value: "旧草稿" } })
  await userEvent.click(screen.getByRole("button", { name: "保存身份与归属" }))
  if (boundary === "actor") auth.user.account_id = "other-admin"
  if (boundary === "scope") auth.user.scoped_permissions = ["people.update:person:p1"]
  rerender(ui(boundary === "target" ? "p2" : "p1"))
  await screen.findByLabelText("队员身份")
  expect(screen.queryByDisplayValue("旧草稿")).not.toBeInTheDocument()
  await act(async () => finish(json({ ...state, business_kind: "player", version: "old-success" })))
  expect(screen.queryByText("身份与归属已保存")).not.toBeInTheDocument()
  expect(screen.getByLabelText("队员身份")).not.toBeChecked()
})

it("shows existing assignments read-only without edit controls or candidate catalogs", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => json({ ...state, business_kind: "student", student_project_ids: ["old-project"], coach_person_ids: ["old-coach"], capabilities: { ...state.capabilities, can_edit: false }, eligible_projects: [], eligible_coaches: [], current_projects: [{ id: "old-project", name: "当前项目" }], current_coaches: [{ id: "old-coach", full_name: "现任教练", project_ids: ["old-project"] }] })))
  mount()
  expect(await screen.findByText(/当前项目/)).toBeVisible()
  expect(screen.getByText(/现任教练/)).toBeVisible()
  expect(screen.queryByRole("button", { name: "保存身份与归属" })).not.toBeInTheDocument()
  expect(screen.queryByLabelText("身份与归属变更原因")).not.toBeInTheDocument()
})

// Catches forced removal of historic coach-only relations merely to add a player identity.
it.each([null, "student"])("preserves unchanged legacy coaches with empty projects when original identity is %s", async businessKind => {
  const writes: Record<string, unknown>[] = []
  const legacy = { ...state, business_kind: businessKind, coach_person_ids: ["legacy-coach"], current_coaches: [{ id: "legacy-coach", full_name: "历史负责教练", project_ids: [] }] }
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (_url, init) => {
    if (init?.method !== "PUT") return json(legacy)
    const input = JSON.parse(String(init.body)); writes.push(input)
    return json({ ...legacy, ...input, version: "v2" })
  }))
  mount()
  await userEvent.click(await screen.findByLabelText("队员身份"))
  fireEvent.change(screen.getByLabelText("身份与归属变更原因"), { target: { value: "仅新增队员业务身份，保留历史负责关系" } })
  expect(screen.getByRole("button", { name: "保存身份与归属" })).toBeEnabled()
  await userEvent.click(screen.getByRole("button", { name: "保存身份与归属" }))
  await screen.findByRole("status")
  expect(writes[0]).toMatchObject({ business_kind: businessKind === "student" ? "both" : "player", student_project_ids: [], coach_person_ids: ["legacy-coach"] })
})

it("ignores delayed old GET candidates after switching target", async () => {
  let finish!: (value: Response) => void
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async url => String(url).includes("/p1/") ? new Promise<Response>(resolve => { finish = resolve }) : json({ ...state, person_id: "p2", eligible_projects: [{ id: "new-project", name: "新目标项目" }] })))
  const { rerender, ui } = mount()
  rerender(ui("p2"))
  await userEvent.click(await screen.findByLabelText("学员身份"))
  expect(screen.getByLabelText("新目标项目")).toBeVisible()
  await act(async () => finish(json(state)))
  expect(screen.queryByLabelText("王者荣耀")).not.toBeInTheDocument()
  expect(screen.getByLabelText("新目标项目")).toBeVisible()
})

it.each([401, 403, 404])("clears denied initial GET data without offering candidate access (%s)", async status => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => json({ status, detail: "无权访问" }, status)))
  const { client } = mount()
  expect(await screen.findByRole("alert")).toHaveTextContent("权限已失效")
  expect(screen.queryByLabelText("学员身份")).not.toBeInTheDocument()
  expect(screen.queryByText("王者荣耀")).not.toBeInTheDocument()
  expect(client.getQueriesData({ queryKey: ["person-assignments"] }).every(([, value]) => !value)).toBe(true)
})

it("does not allow changed project sets to retain a now ineligible legacy coach", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => json({ ...state, business_kind: "student", coach_person_ids: ["coach1"], current_coaches: [state.eligible_coaches[0]] })))
  mount()
  await screen.findByLabelText("学员身份")
  fireEvent.change(screen.getByLabelText("身份与归属变更原因"), { target: { value: "切换项目" } })
  expect(screen.getByRole("button", { name: "保存身份与归属" })).toBeEnabled()
  await userEvent.click(screen.getByLabelText("英雄联盟"))
  expect(screen.getByLabelText(/王者教练.*不适用于所选项目/)).toBeChecked()
  expect(screen.getByRole("button", { name: "保存身份与归属" })).toBeDisabled()
  await userEvent.click(screen.getByLabelText(/王者教练.*不适用于所选项目/))
  expect(screen.getByRole("button", { name: "保存身份与归属" })).toBeEnabled()
})
