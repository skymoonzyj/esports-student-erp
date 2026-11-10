import { useState } from "react"
import { fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it } from "vitest"
import { IntakeIdentitySection, identityValidation } from "./IntakeIdentitySection"
import { emptyIdentityDraft } from "./intakeIdentity"
import type { IntakeIdentityDraft } from "./intakeIdentity"
import type { ManagementIdentityContext } from "./api"

const context: ManagementIdentityContext = { role_options: [
  { key: "student", name: "学员", assignable: true, reason: null }, { key: "team_member", name: "队员", assignable: true, reason: null },
  { key: "coach", name: "教练", assignable: true, reason: null }, { key: "assistant_coach", name: "助教", assignable: true, reason: null },
  { key: "finance", name: "财务", assignable: false, reason: "仅超级管理员可批准" }, { key: "registered_user", name: "其他人员（基础账号）", assignable: true, reason: null },
], can_assign_staff_teams: true, staff_team_reason: null, staff_team_options: [{ id: "a", project_id: "p", project_name: "项目甲", name: "一队" }, { id: "b", project_id: "p", project_name: "项目乙", name: "一队" }] }
function Harness({ initial = emptyIdentityDraft(), denied = false }: { initial?: IntakeIdentityDraft; denied?: boolean }) {
  const [value, setValue] = useState(initial)
  const [coaches, setCoaches] = useState(initial.primary_identity === "student" ? ["teacher"] : [])
  return <><IntakeIdentitySection value={value} onChange={(next, nextCoaches) => { setValue(next); setCoaches(nextCoaches) }} context={{ ...context, can_assign_staff_teams: !denied, staff_team_reason: denied ? "需有权人员分配" : null }} coachPersonIds={coaches} coachOptions={[{ id: "teacher", label: "负责教练甲" }]} teamOptions={[{ id: "player", label: "队员甲队" }]} mode="pending" /><output role="note">{JSON.stringify({ value, coaches })}</output></>
}
it("requires confirmation before removing a forbidden primary identity's saved team assignment", async () => {
  const value = { ...emptyIdentityDraft(), primary_identity: "coach" as const, staff_team_ids: { manager: [], coach: ["a"], assistant_coach: [] } }
  let last = value
  render(<IntakeIdentitySection value={value} onChange={next => { last = next as typeof value }} context={{ ...context, role_options: context.role_options.map(option => ({ ...option, assignable: false, reason: "权限已失效" })) }} coachPersonIds={[]} coachOptions={[]} teamOptions={[]} mode="pending" />)
  await userEvent.click(screen.getByRole("button", { name: "移除不可用身份：教练" }))
  expect(screen.getByRole("dialog")).toHaveTextContent("教练负责队伍")
  expect(last.staff_team_ids.coach).toEqual(["a"])
  await userEvent.click(screen.getByRole("button", { name: "确认移除归属并更改身份" }))
  expect(last.primary_identity).toBeNull()
  expect(last.staff_team_ids.coach).toEqual([])
})
it("qualifies same-name staff teams by project in the removal preview, not by player-team labels", () => {
  const value = { ...emptyIdentityDraft(), primary_identity: "coach" as const, staff_team_ids: { manager: [], coach: ["a", "b"], assistant_coach: [] } }
  render(<IntakeIdentitySection value={value} onChange={() => undefined} context={context} coachPersonIds={[]} coachOptions={[]} teamOptions={[{ id: "a", label: "队员归属别名" }]} mode="pending" />)
  fireEvent.change(screen.getByLabelText("主要身份"), { target: { value: "registered_user" } })
  expect(screen.getByRole("dialog")).toHaveTextContent("教练负责队伍：项目甲 / 一队")
  expect(screen.getByRole("dialog")).toHaveTextContent("教练负责队伍：项目乙 / 一队")
  expect(screen.getByRole("dialog")).not.toHaveTextContent("队员归属别名")
})
it("shows qualified multi-team staff choices and a single player team only for selected identities", async () => {
  render(<Harness />)
  fireEvent.change(screen.getByLabelText("主要身份"), { target: { value: "coach" } })
  expect(screen.queryByLabelText("队员所属队伍（可暂缺）")).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole("checkbox", { name: "项目甲 / 一队" }))
  await userEvent.click(screen.getByRole("checkbox", { name: "项目乙 / 一队" }))
  await userEvent.click(screen.getByText("兼任身份"))
  await userEvent.click(screen.getByRole("checkbox", { name: "队员" }))
  expect(screen.getByLabelText("队员所属队伍（可暂缺）")).toBeVisible()
  expect(screen.getByRole("checkbox", { name: /财务/ })).toBeDisabled()
  expect(screen.getByRole("status")).toHaveTextContent("教练、队员")
  expect(screen.queryByText("管理员")).not.toBeInTheDocument()
})
it("cancelling removal retains hidden team and coach facts, confirming clears them together", async () => {
  render(<Harness initial={{ ...emptyIdentityDraft(), primary_identity: "student", concurrent_identities: ["coach", "team_member"], player_team_id: "player", staff_team_ids: { manager: [], coach: ["a"], assistant_coach: [] } }} />)
  fireEvent.change(screen.getByLabelText("主要身份"), { target: { value: "registered_user" } })
  expect(screen.getByRole("dialog")).toHaveTextContent("负责教练甲")
  await userEvent.click(screen.getByRole("button", { name: "取消身份更改" }))
  expect(screen.getByLabelText("主要身份")).toHaveValue("student")
  expect(screen.getByRole("checkbox", { name: "负责教练甲" })).toBeChecked()
  fireEvent.change(screen.getByLabelText("主要身份"), { target: { value: "registered_user" } })
  await userEvent.click(screen.getByRole("button", { name: "确认移除归属并更改身份" }))
  expect(screen.queryByRole("checkbox", { name: "负责教练甲" })).not.toBeInTheDocument()
  expect(screen.getByRole("status")).toHaveTextContent("其他人员（基础账号）")
  expect(screen.getByText(/"coaches":\[\]/)).toBeInTheDocument()
})
it("explains denied team allocation without prohibiting a staff identity with no team", () => {
  render(<Harness denied initial={{ ...emptyIdentityDraft(), primary_identity: "coach" }} />)
  expect(screen.getByRole("checkbox", { name: "项目甲 / 一队" })).toBeDisabled()
  expect(screen.getByText("需有权人员分配")).toBeVisible()
  expect(screen.getByLabelText("主要身份")).toBeEnabled()
})
it("allows requestable coach identity without granting it and still blocks finance", () => {
  const pendingContext = { ...context, role_options: context.role_options.map(option => ({ ...option, assignable: false, requestable: option.key === "coach" })) }
  const draft = { ...emptyIdentityDraft(), primary_identity: "coach" as const }
  expect(identityValidation(draft, pendingContext)).toEqual({})
  render(<IntakeIdentitySection value={draft} onChange={() => undefined} context={pendingContext} coachPersonIds={[]} coachOptions={[]} teamOptions={[]} mode="pending" />)
  expect(screen.getByRole("option", { name: "教练" })).toBeEnabled()
  expect(screen.getByRole("option", { name: /财务/ })).toBeDisabled()
  expect(screen.getByText(/待超级管理员批准/)).toBeVisible()
})
it("keeps automatic baseline roles available without delegation but never exempts extra registered_user", () => {
  const restricted = { ...context, role_options: context.role_options.map(option => ({ ...option, assignable: false, reason: "无角色委派权限" })) }
  expect(identityValidation({ ...emptyIdentityDraft(), primary_identity: "student" }, restricted)).toEqual({})
  expect(identityValidation({ ...emptyIdentityDraft(), primary_identity: "team_member", concurrent_identities: ["student"] }, restricted)).toEqual({})
  expect(identityValidation({ ...emptyIdentityDraft(), primary_identity: "registered_user" }, restricted)).toEqual({})
  expect(identityValidation({ ...emptyIdentityDraft(), primary_identity: "student", concurrent_identities: ["registered_user"] }, restricted)).toHaveProperty("identity")
  expect(identityValidation({ ...emptyIdentityDraft(), primary_identity: "coach" }, restricted)).toHaveProperty("identity")
})
