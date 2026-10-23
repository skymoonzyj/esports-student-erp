import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import { AssistedPersonForm } from "./AssistedPersonForm"
import type { IntakeInput } from "./types"

const coach = { id: "30000000-0000-4000-8000-000000000001", label: "测试教练" }
const team = { id: "40000000-0000-4000-8000-000000000001", label: "测试队伍" }
const adult: IntakeInput = {
  kind: "student", full_name: "测试成人", identity_number: "110105199001021238",
  sex: "male", date_of_birth: "1990-01-02", phone: "13800000000",
  email: "adult@example.test", guardians: [], coach_person_ids: [coach.id], team_id: null,
}
const minor: IntakeInput = {
  ...adult, full_name: "测试未成年", identity_number: "110105201201020029",
  sex: "female", date_of_birth: "2012-01-02", email: "child@example.test",
}
const guardian = {
  full_name: "测试监护", identity_number: "110105199001021238", sex: "male" as const,
  date_of_birth: "1990-01-02", phone: "13800000000", email: null,
  relationship: "父亲", is_primary: true,
}
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

it.each(["2026-02-27", "2026-02-28", "2026-03-01"])("uses the canonical leap-day eighteenth birthday for subject and guardian on %s", async (today) => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(`${today}T04:00:00Z`))
  const leap = { ...adult, identity_number: "110105200802291233", date_of_birth: "2008-02-29" }
  const onSubmit = vi.fn().mockResolvedValue(undefined)
  const view = render(<AssistedPersonForm initialValue={leap} onSubmit={onSubmit} submitting={false} coachOptions={[coach]} />)
  fireEvent.click(screen.getByRole("button", { name: "提交申请" }))
  if (today === "2026-02-27") {
    expect(screen.getByText("未成年人至少需要一名成年监护人")).toBeVisible()
    expect(onSubmit).not.toHaveBeenCalled()
  } else await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
  view.unmount(); onSubmit.mockClear()
  render(<AssistedPersonForm initialValue={{ ...minor, guardians: [{ ...guardian,
    identity_number: leap.identity_number, date_of_birth: leap.date_of_birth }] }} onSubmit={onSubmit} submitting={false} coachOptions={[coach]} />)
  fireEvent.click(screen.getByRole("button", { name: "提交申请" }))
  if (today === "2026-02-27") {
    expect(screen.getByText("监护人必须年满 18 周岁")).toBeVisible()
    expect(onSubmit).not.toHaveBeenCalled()
  } else await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
})

it.each(["母", " 母 ", "   "])("rejects trimmed short guardian relationship %s before submission", async (relationship) => {
  const { onSubmit } = show({ ...minor, guardians: [{ ...guardian, relationship }] })
  fireEvent.click(screen.getByRole("button", { name: "提交申请" }))
  expect(screen.getByLabelText("与本人关系")).toHaveAttribute("aria-invalid", "true")
  expect(onSubmit).not.toHaveBeenCalled()
})

it.each([" 母亲 ", "关".repeat(40)])("accepts trimmed 2–40 character guardian relationship", async (relationship) => {
  const { onSubmit } = show({ ...minor, guardians: [{ ...guardian, relationship }] })
  fireEvent.click(screen.getByRole("button", { name: "提交申请" }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ ...minor, guardians: [{ ...guardian, relationship: relationship.trim() }] }))
})

it("identity-only correction hides immutable affiliations but keeps full identity and minor guardian validation", async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined)
  render(<AssistedPersonForm identityOnlyKind="both" initialValue={{ ...minor, kind: "both", coach_person_ids: [], team_id: null }} onSubmit={onSubmit} submitting={false} />)
  expect(screen.queryByLabelText("人员身份")).not.toBeInTheDocument()
  expect(screen.queryByText("负责教练")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("所属队伍")).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole("button", { name: "提交更正" }))
  expect(screen.getByText("未成年人至少需要一名成年监护人")).toBeVisible()
  expect(onSubmit).not.toHaveBeenCalled()
})

it("identity-only correction skips only assignment validation", async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined)
  render(<AssistedPersonForm identityOnlyKind="both" initialValue={{ ...adult, kind: "both", coach_person_ids: [], team_id: null }} onSubmit={onSubmit} submitting={false} />)
  await userEvent.click(screen.getByRole("button", { name: "提交更正" }))
  expect(onSubmit).toHaveBeenCalledExactlyOnceWith({ ...adult, kind: "both", coach_person_ids: [], team_id: null })
})

function show(initialValue?: IntakeInput) {
  const onSubmit = vi.fn<(input: IntakeInput) => Promise<void>>().mockResolvedValue(undefined)
  return { onSubmit, ...render(<AssistedPersonForm initialValue={initialValue} onSubmit={onSubmit}
    submitting={false} coachOptions={[coach]} teamOptions={[team]} />) }
}

it("submits a complete adult with string transport fields and selected authorized coach", async () => {
  const user = userEvent.setup()
  const { onSubmit } = show()
  await user.type(screen.getByLabelText("姓名"), adult.full_name)
  await user.type(screen.getByLabelText("身份证"), adult.identity_number)
  await user.selectOptions(screen.getByLabelText("性别"), "male")
  fireEvent.change(screen.getByLabelText("出生日期"), { target: { value: adult.date_of_birth } })
  await user.type(screen.getByLabelText("手机号"), adult.phone)
  await user.type(screen.getByLabelText("邮箱"), adult.email)
  await user.click(screen.getByLabelText(coach.label))
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(onSubmit).toHaveBeenCalledExactlyOnceWith(adult)
  expect(screen.queryByRole("textbox", { name: /UUID|人员编号|教练编号|队伍编号/i })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /自动通过|管理建档/ })).not.toBeInTheDocument()
})

it("rejects missing required person fields without submitting", async () => {
  const user = userEvent.setup()
  const { onSubmit } = show()
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  for (const label of ["姓名", "身份证", "性别", "出生日期", "手机号", "邮箱"]) {
    expect(screen.getByLabelText(label)).toHaveAttribute("aria-invalid", "true")
  }
  expect(onSubmit).not.toHaveBeenCalled()
})

it("requires a guardian for minors and accepts an adult guardian with shared phone and no email", async () => {
  const user = userEvent.setup()
  const { onSubmit } = show(minor)
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(screen.getByText("未成年人至少需要一名成年监护人")).toBeVisible()
  expect(onSubmit).not.toHaveBeenCalled()
  await user.click(screen.getByRole("button", { name: "添加监护人" }))
  const section = within(screen.getByRole("group", { name: "监护人 1" }))
  await user.type(section.getByLabelText("监护人姓名"), guardian.full_name)
  await user.type(section.getByLabelText("监护人身份证"), guardian.identity_number)
  await user.selectOptions(section.getByLabelText("监护人性别"), "male")
  fireEvent.change(section.getByLabelText("监护人出生日期"), { target: { value: guardian.date_of_birth } })
  await user.type(section.getByLabelText("监护人手机号"), guardian.phone)
  await user.type(section.getByLabelText("与本人关系"), guardian.relationship)
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(onSubmit).toHaveBeenCalledExactlyOnceWith({ ...minor, guardians: [guardian] })
})

it.each([
  ["sex", "female", "性别"],
  ["date_of_birth", "1990-01-03", "出生日期"],
  ["identity_number", "110105199001021239", "身份证"],
  ["phone", "12345", "手机号"],
  ["email", "invalid-email", "邮箱"],
] as const)("rejects inconsistent or malformed %s beside its field", async (field, value, label) => {
  const user = userEvent.setup()
  const { onSubmit } = show({ ...adult, [field]: value })
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(screen.getByLabelText(label)).toHaveAttribute("aria-invalid", "true")
  expect(onSubmit).not.toHaveBeenCalled()
})

it.each([
  ["student", [], null, "请至少选择一名可负责的教练"],
  ["player", [], null, "请选择可加入的队伍"],
  ["both", [], team.id, "请至少选择一名可负责的教练"],
  ["both", [coach.id], null, "请选择可加入的队伍"],
] as const)("requires kind-specific assignments for %s", async (kind, coaches, teamId, message) => {
  const user = userEvent.setup()
  const { onSubmit } = show({ ...adult, kind, coach_person_ids: [...coaches], team_id: teamId })
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(screen.getByText(message)).toBeVisible()
  expect(onSubmit).not.toHaveBeenCalled()
})

it.each(["player", "both"] as const)("submits valid %s assignments", async (kind) => {
  const user = userEvent.setup()
  const value = { ...adult, kind, coach_person_ids: kind === "player" ? [] : [coach.id], team_id: team.id }
  const { onSubmit } = show(value)
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(onSubmit).toHaveBeenCalledExactlyOnceWith(value)
})

it.each([
  [{ ...guardian, email: minor.email }, "监护人邮箱不能与本人邮箱相同"],
  [{ ...guardian, identity_number: minor.identity_number, sex: "female" as const, date_of_birth: minor.date_of_birth }, "监护人必须年满 18 周岁"],
  [{ ...guardian, full_name: "", phone: "", relationship: "" }, "请填写监护人姓名"],
] as const)("rejects invalid guardian facts without submission", async (value, message) => {
  const user = userEvent.setup()
  const { onSubmit } = show({ ...minor, guardians: [value] })
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(screen.getByText(message)).toBeVisible()
  expect(onSubmit).not.toHaveBeenCalled()
})

it("keeps edits across focus and initialValue refresh without persisting sensitive data", async () => {
  const user = userEvent.setup()
  const storageWrite = vi.spyOn(Storage.prototype, "setItem")
  const url = window.location.href
  const { onSubmit, rerender } = show(adult)
  await user.clear(screen.getByLabelText("姓名"))
  await user.type(screen.getByLabelText("姓名"), "未提交草稿")
  fireEvent(window, new Event("blur")); fireEvent(window, new Event("focus"))
  rerender(<AssistedPersonForm initialValue={{ ...adult, full_name: "刷新数据" }} onSubmit={onSubmit}
    submitting={false} coachOptions={[coach]} teamOptions={[team]} />)
  expect(screen.getByLabelText("姓名")).toHaveValue("未提交草稿")
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(onSubmit).toHaveBeenCalledExactlyOnceWith({ ...adult, full_name: "未提交草稿" })
  expect(storageWrite).not.toHaveBeenCalled()
  expect(window.location.href).toBe(url)
})

it("blocks duplicate submission while its promise is pending", async () => {
  const user = userEvent.setup()
  let finish!: () => void
  const onSubmit = vi.fn(() => new Promise<void>((resolve) => { finish = resolve }))
  render(<AssistedPersonForm initialValue={adult} onSubmit={onSubmit} submitting={false} coachOptions={[coach]} />)
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(screen.getByRole("button", { name: "正在提交…" })).toBeDisabled()
  await user.click(screen.getByRole("button", { name: "正在提交…" }))
  expect(onSubmit).toHaveBeenCalledTimes(1)
  finish()
  await waitFor(() => expect(screen.getByRole("button", { name: "提交申请" })).toBeEnabled())
})

it("does not submit disabled or missing assignment options", async () => {
  const user = userEvent.setup()
  const onSubmit = vi.fn().mockResolvedValue(undefined)
  render(<AssistedPersonForm initialValue={adult} onSubmit={onSubmit} submitting={false}
    coachOptions={[{ ...coach, disabled: true }]} />)
  expect(screen.getByLabelText(coach.label)).toBeDisabled()
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(screen.getByText("请至少选择一名可负责的教练")).toBeVisible()
  expect(onSubmit).not.toHaveBeenCalled()
})

it("keeps its draft and permits retry after the server rejects submission", async () => {
  const user = userEvent.setup()
  const onSubmit = vi.fn<(input: IntakeInput) => Promise<void>>()
    .mockRejectedValueOnce(new Error("private server diagnostics"))
    .mockResolvedValueOnce(undefined)
  render(<AssistedPersonForm initialValue={adult} onSubmit={onSubmit} submitting={false} coachOptions={[coach]} />)
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(screen.getByRole("alert")).toHaveTextContent("当前草稿已保留")
  expect(screen.queryByText("private server diagnostics")).not.toBeInTheDocument()
  expect(screen.getByLabelText("姓名")).toHaveValue(adult.full_name)
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(onSubmit).toHaveBeenCalledTimes(2)
})

it.each([{ submitting: true }, { submitting: false, readOnly: true }])("disables editing and submission for external busy or read-only state", async (state) => {
  const user = userEvent.setup()
  const onSubmit = vi.fn().mockResolvedValue(undefined)
  render(<AssistedPersonForm initialValue={adult} onSubmit={onSubmit} coachOptions={[coach]} {...state} />)
  expect(screen.getByLabelText("姓名")).toBeDisabled()
  const button = screen.getByRole("button", { name: /提交申请|正在提交/ })
  expect(button).toBeDisabled()
  await user.click(button)
  expect(onSubmit).not.toHaveBeenCalled()
})

it.each([
  ["110105200810191232", "2008-10-19", true],
  ["110105200810201234", "2008-10-20", false],
] as const)("uses China's calendar day for the eighteenth birthday %s", async (identity, birth, accepted) => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date("2026-10-18T16:00:00Z"))
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
  const { onSubmit } = show({ ...adult, identity_number: identity, date_of_birth: birth })
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  if (accepted) expect(onSubmit).toHaveBeenCalledTimes(1)
  else {
    expect(screen.getByText("未成年人至少需要一名成年监护人")).toBeVisible()
    expect(onSubmit).not.toHaveBeenCalled()
  }
})

it("removes a guardian without submitting the removed private data", async () => {
  const user = userEvent.setup()
  const { onSubmit } = show({ ...minor, guardians: [guardian, { ...guardian, full_name: "应移除监护" }] })
  await user.click(screen.getByRole("button", { name: "移除监护人 2" }))
  expect(screen.queryByDisplayValue("应移除监护")).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(onSubmit).toHaveBeenCalledExactlyOnceWith({ ...minor, guardians: [guardian] })
})

it("retains the authorized coach draft while changing kind and clears irrelevant payload assignments", async () => {
  const user = userEvent.setup()
  const { onSubmit } = show(adult)
  await user.selectOptions(screen.getByLabelText("人员身份"), "both")
  await user.selectOptions(screen.getByLabelText("所属队伍"), team.id)
  await user.selectOptions(screen.getByLabelText("人员身份"), "player")
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(onSubmit).toHaveBeenLastCalledWith({ ...adult, kind: "player", coach_person_ids: [], team_id: team.id })
  await user.selectOptions(screen.getByLabelText("人员身份"), "student")
  expect(screen.getByLabelText(coach.label)).toBeChecked()
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(onSubmit).toHaveBeenLastCalledWith(adult)
})

it.each(["missing", "disabled"] as const)("repairs a selected coach becoming %s without losing the edited draft or re-adding it", async (unavailable) => {
  const user = userEvent.setup()
  const replacement = { id: "30000000-0000-4000-8000-000000000002", label: "替换教练" }
  const { onSubmit, rerender } = show(adult)
  await user.clear(screen.getByLabelText("姓名"))
  await user.type(screen.getByLabelText("姓名"), "保留姓名草稿")
  const options = unavailable === "missing" ? [replacement] : [{ ...coach, disabled: true }, replacement]
  rerender(<AssistedPersonForm initialValue={adult} onSubmit={onSubmit} submitting={false} coachOptions={options} />)
  await user.click(screen.getByLabelText(replacement.label))
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(onSubmit).not.toHaveBeenCalled()
  expect(screen.getByText("请至少选择一名可负责的教练")).toBeVisible()
  await user.click(screen.getByRole("button", { name: /移除不可用教练/ }))
  expect(screen.queryByRole("button", { name: /移除不可用教练/ })).not.toBeInTheDocument()
  expect(screen.getByLabelText("姓名")).toHaveValue("保留姓名草稿")
  if (unavailable === "missing") expect(screen.queryByLabelText(coach.label)).not.toBeInTheDocument()
  else {
    const staleCoach = screen.getByLabelText(coach.label)
    expect(staleCoach).toBeDisabled()
    expect(staleCoach).not.toBeChecked()
    await user.click(staleCoach)
    expect(staleCoach).not.toBeChecked()
  }
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(onSubmit).toHaveBeenCalledExactlyOnceWith({ ...adult, full_name: "保留姓名草稿", coach_person_ids: [replacement.id] })
})

it.each([
  ["missing", "readOnly"], ["disabled", "readOnly"],
  ["missing", "submitting"], ["disabled", "submitting"],
] as const)("blocks removal of %s selected coaches while %s", async (unavailable, lockedBy) => {
  const user = userEvent.setup()
  const { onSubmit, rerender } = show(adult)
  const options = unavailable === "missing" ? [] : [{ ...coach, disabled: true }]
  rerender(<AssistedPersonForm initialValue={adult} onSubmit={onSubmit}
    submitting={lockedBy === "submitting"} readOnly={lockedBy === "readOnly"} coachOptions={options} />)
  const remove = screen.getByRole("button", { name: /移除不可用教练/ })
  expect(remove).toBeDisabled()
  await user.click(remove)
  expect(onSubmit).not.toHaveBeenCalled()
  rerender(<AssistedPersonForm initialValue={adult} onSubmit={onSubmit} submitting={false} coachOptions={options} />)
  const enabledRemove = screen.getByRole("button", { name: /移除不可用教练/ })
  expect(enabledRemove).toBeEnabled()
  await user.click(enabledRemove)
  expect(screen.queryByRole("button", { name: /移除不可用教练/ })).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(onSubmit).not.toHaveBeenCalled()
})
