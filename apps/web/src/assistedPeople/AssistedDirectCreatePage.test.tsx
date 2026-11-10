import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import { AssistedDirectCreatePage } from "./AssistedDirectCreatePage"
import { identityKeys } from "./types"
import { identityNames } from "./intakeIdentity"

const auth = vi.hoisted(() => ({ account: "10000000-0000-4000-8000-000000000001" }))
vi.mock("../auth/AuthProvider", () => ({ useAuth: () => ({ status: "authenticated", user: { account_id: auth.account, permissions: ["people.create"] } }) }))
const person = "30000000-0000-4000-8000-000000000001"
const context = { can_submit: false, can_review: false, can_direct_create: true, can_read_managed: false, allowed_team_ids: [], eligible_coaches: [], eligible_teams: [] }
let allowed: boolean
let otherAccess: boolean
let pendingCapability: boolean | undefined
let postStatus: number
let contextFailure: "network" | number | null
let writes: { url: string; body: Record<string, unknown> }[]
let reads: string[]
let roleAssignable: boolean
let optionsFailure: boolean
let optionsStatus: number
let staffTeamsAllowed: boolean
let extraRolesAllowed: boolean
const teamA = "40000000-0000-4000-8000-000000000001"
const teamB = "40000000-0000-4000-8000-000000000002"
const coachId = "50000000-0000-4000-8000-000000000001"
beforeEach(() => {
  allowed = true; otherAccess = false; pendingCapability = true; postStatus = 500; writes = []; reads = []
  contextFailure = null
  roleAssignable = true; optionsFailure = false
  optionsStatus = 200
  staffTeamsAllowed = false; extraRolesAllowed = true
  auth.account = "10000000-0000-4000-8000-000000000001"
  setCsrfToken("test")
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === "POST") {
      writes.push({ url: String(url), body: JSON.parse(String(init.body)) })
      return Response.json(postStatus === 200 ? { request_id: null, person_id: person, approval_id: person } : { code: "private", detail: "raw-identity-must-not-leak" }, { status: postStatus })
    }
    reads.push(String(url))
    if (String(url).endsWith("/management-identity-context") && optionsStatus !== 200) return Response.json({ detail: "private" }, { status: optionsStatus })
    if (String(url).endsWith("/management-identity-context")) return Response.json(optionsFailure ? {} : { role_options: identityKeys.map(key => ({ key, name: identityNames[key], assignable: extraRolesAllowed && (key === "finance" ? false : key === "coach" ? roleAssignable : true), reason: !extraRolesAllowed ? "无角色委派权限" : key === "finance" ? "仅超级管理员可批准" : key === "coach" && !roleAssignable ? "当前授权范围不足" : null })), can_assign_staff_teams: staffTeamsAllowed, staff_team_reason: staffTeamsAllowed ? null : "须由有权人员分配", staff_team_options: [{ id: teamA, project_id: person, project_name: "项目甲", name: "合成一队" }, { id: teamB, project_id: person, project_name: "项目乙", name: "合成一队" }] })
    if (String(url).endsWith("/activation-role-options")) return Response.json(optionsFailure ? {} : { items: [
      { key: "coach", name: "教练", assignable: roleAssignable, reason: roleAssignable ? null : "当前授权范围不足" },
      { key: "guardian", name: "监护人", assignable: true, reason: null },
      { key: "finance", name: "财务", assignable: false, reason: "仅超级管理员可批准" },
    ] })
    if (String(url).endsWith("/context") && contextFailure) {
      if (contextFailure === "network") throw new TypeError("offline")
      return Response.json({ detail: "unavailable" }, { status: contextFailure })
    }
    if (String(url).endsWith("/readiness")) return Response.json({ approval_valid: true, housing_ready: false, completion_allowed: true, activation_profile_ready: false,
      approval_id: person, version: 1, kind: "staff", profile_missing: ["sex", "date_of_birth"], housing_missing: ["sex"], activation_missing: ["sex", "date_of_birth", "email"] })
    return Response.json({ ...context, can_direct_create: allowed, can_submit: otherAccess, can_review: otherAccess,
      eligible_teams: [{ id: teamA, label: "队员所属甲队" }],
      eligible_coaches: [{ id: coachId, label: "旧负责教练" }],
      ...(pendingCapability === undefined ? {} : { can_direct_create_pending: pendingCapability }) })
  }))
})
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null); focusManager.setFocused(undefined) })
function show(saved?: unknown) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  if (saved) client.setQueryData(["assisted-people", auth.account, "pending-create-draft"], saved)
  const tree = (visible = true) => <QueryClientProvider client={client}>{visible ? <AssistedDirectCreatePage /> : null}</QueryClientProvider>
  return { client, tree, ...render(tree()) }
}
it("opens the lazy quick intake editor from management intake", async () => {
  show()
  fireEvent.click(await screen.findByRole("button", { name: "一键导入建档及入住" }))
  expect(await screen.findByLabelText("粘贴人员表格")).toBeInTheDocument()
  expect(writes).toHaveLength(0)
})
it.each(["pending", "unknown"])("collapsing quick import preserves %s operation and original replay", async (state) => {
  show()
  fireEvent.click(await screen.findByRole("button", { name: "一键导入建档及入住" }))
  await screen.findByLabelText("粘贴人员表格")
  const normalFetch = globalThis.fetch
  let resolve!: (response: Response) => void
  const pending = new Promise<Response>(done => { resolve = done })
  const commitBodies: unknown[] = []
  const result = { person_id: person, stay_id: teamA, stay_status: "checked_in", role_pending: false }
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (String(url) === "/api/v1/personnel-intake/quick-import/preview") return Response.json({ import_date: "2026-11-07", rows: [{
      row_number: 1, full_name: "批次隔离人员", room_label: "4-101", arrival_date: "2026-11-07", departure_date: null,
      identity_label: "助教", project_label: "", team_options: [], selected_team_id: null, needs_team_choice: false,
      birth_date: "1990-01-01", sex: "male", masked_identity: "110***********0015", masked_phone: "138****0721",
      errors: [], warnings: [], ticket: "private-test-ticket",
    }] })
    if (String(url) === "/api/v1/personnel-intake/quick-import/commit") {
      commitBodies.push(JSON.parse(String(init?.body)))
      if (commitBodies.length === 1) {
        if (state === "pending") return pending
        throw new TypeError("isolated network loss")
      }
      return Response.json(result)
    }
    return normalFetch(url, init)
  }))
  fireEvent.change(screen.getByLabelText("粘贴人员表格"), { target: { value: "隔离批次原文" } })
  fireEvent.click(screen.getByRole("button", { name: "识别并预览" }))
  await screen.findByText("批次隔离人员")
  fireEvent.click(screen.getByRole("checkbox", { name: /已核对预览/ }))
  fireEvent.click(screen.getByRole("button", { name: "确认建档并安排入住" }))
  await waitFor(() => expect(commitBodies).toHaveLength(1))
  if (state === "unknown") await screen.findByText(/结果尚未确认/)
  fireEvent.click(screen.getByRole("button", { name: "收起一键导入" }))
  fireEvent.click(screen.getByRole("button", { name: "一键导入建档及入住" }))
  expect(await screen.findByLabelText("粘贴人员表格")).toHaveValue("隔离批次原文")
  if (state === "pending") await act(async () => { resolve(Response.json(result)) })
  else {
    fireEvent.click(screen.getByRole("button", { name: "重试未完成行" }))
    await waitFor(() => expect(commitBodies).toHaveLength(2))
    expect(commitBodies[0]).toEqual(commitBodies[1])
  }
  expect(await screen.findByText("已建档并入住")).toBeInTheDocument()
})
it("restores legacy both plus coach facts and retry evidence without guessing its primary identity", async () => {
  const saved = { draft: { kind: "both", full_name: "旧合成档案", identity_number: "AB123456", document_type: "passport", document_issuing_country: null, sex: null, date_of_birth: null, phone: null, email: null, guardians: [], coach_person_ids: [], team_id: null, team_allocation_status: "pending" }, roleKeys: ["coach"], attempt: { fingerprint: "legacy", key: person } }
  const view = show(saved)
  expect(await screen.findByLabelText("主要身份")).toHaveValue("")
  expect(screen.getByRole("checkbox", { name: "学员" })).toBeChecked()
  expect(screen.getByRole("checkbox", { name: "队员" })).toBeChecked()
  expect(screen.getByRole("checkbox", { name: "教练" })).toBeChecked()
  expect(screen.getByLabelText("姓名")).toHaveValue("旧合成档案")
  expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toMatchObject({ attempt: saved.attempt })
  await userEvent.selectOptions(screen.getByLabelText("主要身份"), "student")
  await userEvent.click(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" }))
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0].body.idempotency_key).not.toBe(person)
  expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toMatchObject({ schemaVersion: 2, identity: { primary_identity: "student", concurrent_identities: ["team_member", "coach"] } })
})
it("preserves an untouched legacy draft across pending-strict-pending navigation before any primary confirmation", async () => {
  const saved = { draft: { kind: "both", full_name: "旧多身份档案", identity_number: "AB123456", document_type: "passport", document_issuing_country: null, sex: null, date_of_birth: null, phone: null, email: null, guardians: [], coach_person_ids: [coachId], team_id: teamA, team_allocation_status: "pending" }, roleKeys: ["coach"], attempt: { fingerprint: "legacy-intent", key: person } }
  const view = show(saved)
  expect(await screen.findByLabelText("主要身份")).toHaveValue("")
  await userEvent.click(screen.getByRole("button", { name: "普通严格建档" }))
  await userEvent.click(screen.getByRole("button", { name: "资料待补建档" }))
  expect(screen.getByLabelText("主要身份")).toHaveValue("")
  expect(screen.getByRole("checkbox", { name: "学员" })).toBeChecked()
  expect(screen.getByRole("checkbox", { name: "队员" })).toBeChecked()
  expect(screen.getByRole("checkbox", { name: "教练" })).toBeChecked()
  expect(screen.getByLabelText("队员所属队伍（可暂缺）")).toHaveValue(teamA)
  expect(screen.getByRole("checkbox", { name: "旧负责教练" })).toBeChecked()
  expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toEqual(saved)
  fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "已核对的旧多身份档案" } })
  expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toMatchObject({ schemaVersion: 2, identity: { primary_identity: null, concurrent_identities: ["student", "team_member", "coach"], player_team_id: teamA }, draft: { coach_person_ids: [coachId], team_id: teamA }, attempt: saved.attempt })
  await userEvent.selectOptions(screen.getByLabelText("主要身份"), "student")
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(screen.getByRole("checkbox", { name: "旧负责教练" })).toBeChecked()
  expect(screen.getByLabelText("队员所属队伍（可暂缺）")).toHaveValue(teamA)
})
it("restores a baseline-only legacy staff draft without inventing a delegated staff role", async () => {
  extraRolesAllowed = false
  show({ draft: { kind: "staff", full_name: "旧基础档案", identity_number: "AB123456", document_type: "passport", document_issuing_country: null, sex: null, date_of_birth: null, phone: null, email: null, guardians: [], coach_person_ids: [], team_id: null, team_allocation_status: "pending" }, roleKeys: [], attempt: { fingerprint: "legacy-staff", key: person } })
  expect(await screen.findByLabelText("主要身份")).toHaveValue("")
  expect(screen.getByRole("checkbox", { name: "其他人员（基础账号）" })).toBeChecked()
  expect(screen.getByRole("checkbox", { name: "其他员工" })).not.toBeChecked()
  await userEvent.selectOptions(screen.getByLabelText("主要身份"), "registered_user")
  expect(screen.getByRole("button", { name: "建立待补档案" })).toBeEnabled()
  await userEvent.click(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" }))
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0].body.identity_selection).toEqual({ primary_identity: "registered_user", concurrent_identities: [], primary_staff_identity: null })
  expect(writes[0].body.idempotency_key).not.toBe(person)
})
it("preserves an explicitly saved legacy coach role under lost delegation without inventing an extra staff role", async () => {
  extraRolesAllowed = false
  const view = show({ draft: { kind: "staff", full_name: "旧教练档案", identity_number: "AB123456", document_type: "passport", document_issuing_country: null, sex: null, date_of_birth: null, phone: null, email: null, guardians: [], coach_person_ids: [], team_id: null, team_allocation_status: "pending" }, roleKeys: ["coach"], attempt: { fingerprint: "legacy-coach", key: person } })
  expect(await screen.findByLabelText("主要身份")).toHaveValue("")
  expect(screen.getByRole("checkbox", { name: "其他人员（基础账号）" })).toBeChecked()
  expect(screen.getByRole("checkbox", { name: "教练" })).toBeChecked()
  expect(screen.getByRole("checkbox", { name: "其他员工" })).not.toBeChecked()
  await userEvent.selectOptions(screen.getByLabelText("主要身份"), "registered_user")
  expect(screen.getByRole("button", { name: "建立待补档案" })).toBeDisabled()
  expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toMatchObject({ identity: { primary_identity: "registered_user", concurrent_identities: ["coach"] }, attempt: { fingerprint: "legacy-coach", key: person } })
  expect(writes).toHaveLength(0)
})
it("keeps unrecognized legacy role evidence until explicit discard instead of silently narrowing it", async () => {
  const saved = { draft: { kind: "staff", full_name: "旧草稿", identity_number: "", document_type: "cn_id", document_issuing_country: null, sex: null, date_of_birth: null, phone: null, email: null, guardians: [], coach_person_ids: [], team_id: null, team_allocation_status: "pending" }, roleKeys: ["unknown-role"] }
  const view = show(saved)
  expect(await screen.findByText(/旧草稿含未识别身份/)).toBeVisible()
  expect(await screen.findByRole("button", { name: "建立待补档案" })).toBeDisabled()
  expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toEqual(saved)
  await userEvent.click(screen.getByRole("button", { name: "普通严格建档" }))
  await userEvent.click(screen.getByRole("button", { name: "资料待补建档" }))
  expect(screen.getByText(/旧草稿含未识别身份/)).toBeVisible()
  expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toEqual(saved)
  await userEvent.click(screen.getByRole("button", { name: "清除待补草稿" }))
  expect(screen.getByLabelText("姓名")).toHaveValue("")
  expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toBeUndefined()
})
it("requires explicit correction of a saved not-required allocation before assigning real staff teams", async () => {
  staffTeamsAllowed = true
  show({ schemaVersion: 2, identity: { primary_identity: "coach", concurrent_identities: [], primary_staff_identity: null, player_team_id: null, staff_team_ids: { manager: [], coach: [], assistant_coach: [] } }, draft: { kind: "staff", full_name: "合成教练", identity_number: "AB123456", document_type: "passport", document_issuing_country: null, sex: null, date_of_birth: null, phone: null, email: null, guardians: [], coach_person_ids: [], team_id: null, team_allocation_status: "not_required" } })
  await userEvent.click(await screen.findByRole("checkbox", { name: "项目甲 / 合成一队" }))
  expect(screen.getByLabelText("队伍归属状态")).toHaveValue("not_required")
  await userEvent.click(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" }))
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  expect(writes).toHaveLength(0)
  expect(screen.getByLabelText("队伍归属状态")).toHaveAttribute("aria-invalid", "true")
  await userEvent.selectOptions(screen.getByLabelText("队伍归属状态"), "pending")
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0].body.team_allocation_status).toBe("pending")
  expect(writes[0].body.staff_teams).toEqual([{ staff_role: "coach", team_ids: [teamA] }])
})
it("keeps a pending identity draft separate from edits in strict mode", async () => {
  show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "普通严格建档" }))
  await userEvent.selectOptions(screen.getByLabelText("主要身份"), "coach")
  await userEvent.click(screen.getByRole("button", { name: "资料待补建档" }))
  expect(screen.getByLabelText("主要身份")).toHaveValue("staff")
  expect(screen.getByLabelText("护照号码")).toHaveValue("AB123456")
})
it("a private identity-context denial discards both pending facts and the separate strict identity selection", async () => {
  const view = show()
  await userEvent.selectOptions(await screen.findByLabelText("主要身份"), "coach")
  await fillPending()
  optionsStatus = 403
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await screen.findByText(/当前无权设置激活角色，建档草稿已清除/)
  expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toBeUndefined()
  optionsStatus = 200
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  expect(await screen.findByLabelText("姓名")).toHaveValue("")
  await userEvent.click(screen.getByRole("button", { name: "普通严格建档" }))
  expect(screen.getByLabelText("主要身份")).toHaveValue("")
})
it("submits a single player team with independently grouped multi-team staff roles and an explicit primary staff", async () => {
  staffTeamsAllowed = true
  show(); await fillPending()
  await userEvent.selectOptions(screen.getByLabelText("主要身份"), "team_member")
  await userEvent.click(screen.getByText("兼任身份"))
  await userEvent.click(screen.getByRole("checkbox", { name: "教练" }))
  await userEvent.click(screen.getByRole("checkbox", { name: "队伍经理" }))
  expect(screen.getByRole("button", { name: "建立待补档案" })).toBeDisabled()
  await userEvent.selectOptions(screen.getByLabelText("主要工作人员岗位"), "coach")
  await userEvent.selectOptions(screen.getByLabelText("队员所属队伍（可暂缺）"), teamA)
  const coachTeams = within(screen.getByRole("group", { name: "教练负责队伍（可暂不分配）" }))
  const managerTeams = within(screen.getByRole("group", { name: "队伍经理负责队伍（可暂不分配）" }))
  await userEvent.click(coachTeams.getByRole("checkbox", { name: "项目乙 / 合成一队" }))
  await userEvent.click(coachTeams.getByRole("checkbox", { name: "项目甲 / 合成一队" }))
  await userEvent.click(managerTeams.getByRole("checkbox", { name: "项目乙 / 合成一队" }))
  const summary = within(screen.getByRole("region", { name: "提交管理建档确认" }))
  expect(summary.getByText(/教练：项目甲 \/ 合成一队、项目乙 \/ 合成一队/)).toBeVisible()
  await userEvent.click(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" }))
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0].body).toMatchObject({ identity_selection: { primary_identity: "team_member", concurrent_identities: ["coach", "team_manager"], primary_staff_identity: "coach" }, team_id: teamA, staff_teams: [{ staff_role: "coach", team_ids: [teamA, teamB] }, { staff_role: "manager", team_ids: [teamB] }] })
})
it("permits baseline-only pending intake without delegation and preserves no extra registered_user exemption", async () => {
  extraRolesAllowed = false
  show()
  await userEvent.click(await screen.findByRole("button", { name: "资料待补建档" }))
  await userEvent.selectOptions(screen.getByLabelText("主要身份"), "student")
  await userEvent.selectOptions(screen.getByLabelText("证件类型"), "passport")
  fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "基线学员" } })
  fireEvent.change(screen.getByLabelText("护照号码"), { target: { value: "BASE123" } })
  await userEvent.click(screen.getByText("兼任身份"))
  expect(screen.getByRole("checkbox", { name: "其他人员（基础账号）" })).toBeDisabled()
  await userEvent.click(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" }))
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0].body.identity_selection).toEqual({ primary_identity: "student", concurrent_identities: [], primary_staff_identity: null })
})
it("keeps strict student and player assignment requirements while allowing them to be pending", async () => {
  show()
  await userEvent.selectOptions(await screen.findByLabelText("主要身份"), "student")
  await userEvent.click(screen.getByText("兼任身份"))
  await userEvent.click(screen.getByRole("checkbox", { name: "队员" }))
  fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "严格合成" } })
  fireEvent.change(screen.getByLabelText("身份证"), { target: { value: "110105199001021238" } })
  fireEvent.change(screen.getByLabelText("手机号"), { target: { value: "13800000000" } })
  await userEvent.click(screen.getByRole("button", { name: "提交申请" }))
  expect(screen.getByText("请至少选择一名可负责的教练")).toBeVisible()
  expect(screen.getByLabelText("队员所属队伍")).toHaveAttribute("aria-invalid", "true")
  expect(writes).toHaveLength(0)
})
async function fillPending() {
  await userEvent.click(await screen.findByRole("button", { name: "资料待补建档" }))
  await userEvent.selectOptions(await screen.findByLabelText("主要身份"), "staff")
  await userEvent.selectOptions(screen.getByLabelText("证件类型"), "passport")
  fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "Passport Person" } })
  fireEvent.change(screen.getByLabelText("护照号码"), { target: { value: "AB123456" } })
  await userEvent.click(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" }))
}

// Catches UI-only grants, stale role submissions, and losing the role portion of uncertain retries.
it("uses server role decisions, preserves selected roles with the pending draft and saves them explicitly", async () => {
  const view = show(); await fillPending()
  expect(screen.queryByText("账号激活角色（激活后生效）")).not.toBeInTheDocument()
  await userEvent.click(screen.getByText("兼任身份"))
  await userEvent.click(await screen.findByRole("checkbox", { name: "教练" }))
  expect(screen.getByRole("checkbox", { name: /财务/ })).toBeDisabled()
  expect(screen.getByText("仅超级管理员可批准")).toBeVisible()
  await userEvent.click(screen.getByRole("checkbox", { name: "监护人" }))
  expect(screen.getByText(/角色不会自动建立监护关系/)).toBeVisible()
  await userEvent.click(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" }))
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await screen.findByText(/操作未成功/)
  expect(writes[0].body.identity_selection).toEqual({ primary_identity: "staff", concurrent_identities: ["coach", "guardian"], primary_staff_identity: null })
  expect(writes[0].body).not.toHaveProperty("activation_role_keys")
  view.rerender(view.tree(false)); view.rerender(view.tree())
  expect(await screen.findByRole("checkbox", { name: "教练" })).toBeChecked()
  await userEvent.click(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" }))
  postStatus = 200
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await screen.findByText("管理建档成功。")
  expect(writes[1]).toEqual(writes[0])
  expect(screen.getByText(/超出建档人授权范围的角色需超级管理员单独批准/)).toBeVisible()
})

it("blocks role operations on failed catalog validation and requires removing newly forbidden selections", async () => {
  show(); await fillPending()
  await userEvent.click(screen.getByText("兼任身份"))
  await userEvent.click(await screen.findByRole("checkbox", { name: "教练" }))
  roleAssignable = false
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await screen.findByText("当前授权范围不足")
  expect(screen.getByRole("button", { name: "建立待补档案" })).toBeDisabled()
  await userEvent.click(screen.getByRole("button", { name: /移除不可用身份/ }))
  optionsFailure = true
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  expect(await screen.findByText(/暂时无法核实人员身份目录/)).toBeVisible()
  expect(screen.getByRole("button", { name: "建立待补档案" })).toBeDisabled()
  expect(writes).toHaveLength(0)
})

it("offers strict staff creation without inventing coach or team assignments and still requires complete facts", async () => {
  show()
  await screen.findByRole("heading", { name: "管理建档" })
  await userEvent.selectOptions(await screen.findByLabelText("主要身份"), "coach")
  fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "合成工作人员" } })
  fireEvent.change(screen.getByLabelText("身份证"), { target: { value: "110105199001021238" } })
  await userEvent.click(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" }))
  await userEvent.click(screen.getByRole("button", { name: "提交申请" }))
  expect(writes).toHaveLength(0)
  expect(screen.getByLabelText("手机号")).toHaveAttribute("aria-invalid", "true")
  fireEvent.change(screen.getByLabelText("手机号"), { target: { value: "13800000000" } })
  await userEvent.click(screen.getByRole("button", { name: "提交申请" }))
  await waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0]).toMatchObject({ url: "/api/v1/personnel-intake/direct-create", body: { identity_selection: { primary_identity: "coach", concurrent_identities: [], primary_staff_identity: null }, staff_teams: [], coach_person_ids: [], team_id: null, guardians: [] } })
})

it.each([500, "network"] as const)("retains draft and uncertain operation key through focus refresh %s and revalidates before retry", async (failure) => {
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await screen.findByText(/操作未成功/)
  const initialReads = reads.length
  contextFailure = failure
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(reads.length).toBeGreaterThan(initialReads))
  await waitFor(() => expect(screen.queryByLabelText("护照号码")).not.toBeInTheDocument())
  expect(writes).toHaveLength(1)
  contextFailure = null
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  expect(await screen.findByLabelText("护照号码")).toHaveValue("AB123456")
  expect(screen.getByLabelText("姓名")).toHaveValue("Passport Person")
  await userEvent.click(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" }))
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[1]).toEqual(writes[0])
  expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toBeDefined()
})

it("explicit discard clears the pending facts and uncertain key", async () => {
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await screen.findByText(/操作未成功/)
  const old = writes[0].body.idempotency_key
  await userEvent.click(screen.getByRole("button", { name: "清除待补草稿" }))
  expect(await screen.findByLabelText("身份证")).toHaveValue("")
  expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toBeUndefined()
  await userEvent.selectOptions(screen.getByLabelText("证件类型"), "passport")
  await userEvent.selectOptions(screen.getByLabelText("主要身份"), "staff")
  fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "Passport Person" } })
  fireEvent.change(screen.getByLabelText("护照号码"), { target: { value: "AB123456" } })
  await userEvent.click(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" }))
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[1].body.idempotency_key).not.toBe(old)
})

it("a late old-account focus response cannot restore its draft or overwrite new-account facts", async () => {
  const view = show(); await fillPending()
  const firstAccount = auth.account
  const original = vi.mocked(fetch).getMockImplementation()!
  let resolveOld!: (value: Response) => void
  let pause = true
  vi.mocked(fetch).mockImplementation((url, init) => {
    if (pause && String(url).endsWith("/context")) return new Promise(resolve => { resolveOld = resolve })
    return original(url, init)
  })
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(resolveOld).toBeTypeOf("function"))
  pause = false; auth.account = "10000000-0000-4000-8000-000000000002"
  view.rerender(view.tree())
  await fillPending()
  fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "New Account Person" } })
  await act(async () => resolveOld(Response.json({ ...context, can_direct_create_pending: true })))
  expect(screen.getByLabelText("姓名")).toHaveValue("New Account Person")
  expect(view.client.getQueryData(["assisted-people", firstAccount, "pending-create-draft"])).toBeUndefined()
  expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toMatchObject({ draft: { full_name: "New Account Person" } })
})

it("does not expose pending mode merely because the user has global create permission", async () => {
  allowed = false
  show()
  expect(await screen.findByRole("alert")).toHaveTextContent("无管理建档权限")
  expect(screen.queryByRole("button", { name: "资料待补建档" })).not.toBeInTheDocument()
  expect(reads.some((url) => url.endsWith("/readiness"))).toBe(false)
})

it.each([false, undefined])("ordinary create remains available without pending capability %s, and pending mode is never inferred", async (capability) => {
  pendingCapability = capability
  show()
  expect(await screen.findByRole("heading", { name: "管理建档" })).toBeVisible()
  expect(screen.getByRole("button", { name: "普通严格建档" })).toBeVisible()
  expect(await screen.findByLabelText("身份证")).toHaveValue("")
  expect(screen.queryByRole("button", { name: "资料待补建档" })).not.toBeInTheDocument()
  expect(writes).toHaveLength(0)
})

it("retries the dedicated pending create with one UUID and keeps unknown facts null", async () => {
  show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await screen.findByText(/操作未成功/)
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[0].url).toBe("/api/v1/personnel-intake/direct-create-pending")
  expect(writes[0].body).toEqual({ full_name: "Passport Person", identity_number: "AB123456", identity_selection: { primary_identity: "staff", concurrent_identities: [], primary_staff_identity: null }, staff_teams: [], document_type: "passport", document_issuing_country: null,
    sex: null, date_of_birth: null, phone: null, email: null, guardians: [], coach_person_ids: [], team_id: null, team_allocation_status: "pending", idempotency_key: expect.any(String) })
  expect(writes[1]).toEqual(writes[0])
  expect(screen.queryByText("raw-identity-must-not-leak")).not.toBeInTheDocument()
})

it("revoking direct creation purges its sensitive pending draft even when submit/review remain authorized", async () => {
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await screen.findByText(/操作未成功/)
  expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toBeDefined()
  allowed = false; pendingCapability = false; otherAccess = true
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-people", auth.account, "context"] }) })
  await screen.findByText("无管理建档权限。")
  await waitFor(() => expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toBeUndefined())
  expect(screen.queryByDisplayValue("AB123456")).not.toBeInTheDocument()
  expect(writes).toHaveLength(1)
})

it("revoking only pending creation clears its draft/key/confirmation while preserving ordinary create", async () => {
  otherAccess = true
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await screen.findByText(/操作未成功/)
  pendingCapability = false
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-people", auth.account, "context"] }) })
  await waitFor(() => expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toBeUndefined())
  expect(screen.getByRole("button", { name: "普通严格建档" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "资料待补建档" })).not.toBeInTheDocument()
  expect(screen.queryByDisplayValue("AB123456")).not.toBeInTheDocument()
  expect(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" })).not.toBeChecked()
  pendingCapability = true
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-people", auth.account, "context"] }) })
  await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[1].body.idempotency_key).not.toBe(writes[0].body.idempotency_key)
})

it("a pending retry key is discarded on capability revocation even after switching to strict mode", async () => {
  otherAccess = true
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await screen.findByText(/操作未成功/)
  await userEvent.click(screen.getByRole("button", { name: "普通严格建档" }))
  expect(screen.getByLabelText("身份证")).toHaveValue("")
  pendingCapability = false
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-people", auth.account, "context"] }) })
  await waitFor(() => expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toBeUndefined())
  expect(screen.getByRole("button", { name: "普通严格建档" })).toBeVisible()
  pendingCapability = true
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-people", auth.account, "context"] }) })
  await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  const { idempotency_key: oldKey, ...oldFacts } = writes[0].body
  const { idempotency_key: newKey, ...newFacts } = writes[1].body
  expect(newFacts).toEqual(oldFacts)
  expect(newKey).not.toBe(oldKey)
})

it("retains a draft and retry UUID on route return and refocus without browser persistence", async () => {
  const local = vi.spyOn(Storage.prototype, "setItem")
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await screen.findByText(/操作未成功/)
  view.rerender(view.tree(false))
  view.rerender(view.tree())
  expect(await screen.findByLabelText("护照号码")).toHaveValue("AB123456")
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-people"] }) })
  expect(screen.getByLabelText("姓名")).toHaveValue("Passport Person")
  await userEvent.click(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" }))
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[1]).toEqual(writes[0])
  expect(local).not.toHaveBeenCalled()
  expect(window.location.href).not.toContain("AB123456")
  local.mockRestore()
})

it("success displays actual server missing facts and stays explicitly unactivated", async () => {
  postStatus = 200
  show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  expect(await screen.findByText(/资料待补：性别、出生日期/)).toBeVisible()
  expect(screen.getByText(/账号未激活/)).toBeVisible()
  expect(screen.queryByLabelText("护照号码")).not.toBeInTheDocument()
  expect(reads.filter((url) => url.endsWith("/readiness"))).toEqual([`/api/v1/personnel-intake/people/${person}/readiness`])
})

it.each([403, 404])("private denial %s clears the pending draft and prevents another write", async (status) => {
  postStatus = status
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(screen.queryByDisplayValue("AB123456")).not.toBeInTheDocument())
  expect(writes).toHaveLength(1)
  view.rerender(view.tree(false)); view.rerender(view.tree())
  await screen.findByRole("heading", { name: "管理建档" })
  await userEvent.click(screen.getByRole("button", { name: "资料待补建档" }))
  expect(screen.getByLabelText("身份证")).toHaveValue("")
})
