import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it, vi } from "vitest"
import { PendingPersonForm, PendingReadinessSummary } from "./PendingPersonForm"
import type { PendingIntakeInput } from "./types"

const passport: PendingIntakeInput = { kind: "staff", full_name: "Passport Person", identity_number: "AB123456", document_type: "passport",
  document_issuing_country: null, sex: null, date_of_birth: null, phone: null, email: null, guardians: [], coach_person_ids: [], team_id: null, team_allocation_status: "pending" }
function show(initialValue?: PendingIntakeInput) {
  const onSubmit = vi.fn<(value: PendingIntakeInput) => Promise<void>>().mockResolvedValue(undefined)
  return { onSubmit, ...render(<PendingPersonForm initialValue={initialValue} submitting={false} onSubmit={onSubmit} />) }
}

it("submits pending return permits with nullable facts and a complete adult guardian", async () => {
  const guardian = { full_name: "合成监护", document_type: "hk_mo_permit" as const, identity_number: "m8765432103", phone: "13800000000", email: null,
    relationship: "母亲", sex: "female" as const, date_of_birth: "1990-01-02", is_primary: true }
  const { onSubmit } = show({ ...passport, document_type: "hk_mo_permit", identity_number: "h1234567801", guardians: [guardian] })
  fireEvent.change(screen.getByLabelText("监护人回乡证号码"), { target: { value: "m8765432104" } })
  expect(screen.getByLabelText("监护人出生日期")).toHaveValue("1990-01-02")
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledExactlyOnceWith({ ...passport, document_type: "hk_mo_permit", identity_number: "H12345678", guardians: [{ ...guardian, identity_number: "M87654321" }] }))
  fireEvent.change(screen.getByLabelText("出生日期"), { target: { value: "1990-01-02" } })
  fireEvent.change(screen.getByLabelText("性别"), { target: { value: "female" } })
  fireEvent.change(screen.getByLabelText("回乡证号码"), { target: { value: "H1234 5678" } })
  expect(screen.getByLabelText("出生日期")).toHaveValue("1990-01-02")
  expect(screen.getByLabelText("性别")).toHaveValue("female")
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  expect(screen.getByLabelText("回乡证号码")).toHaveAttribute("aria-invalid", "true")
  expect(onSubmit).toHaveBeenCalledTimes(1)
})

it("accepts pending HKID with unknown demographics, then shows age for a manually entered birthday", async () => {
  const { onSubmit } = show(passport)
  fireEvent.change(screen.getByLabelText("证件类型"), { target: { value: "hk_id" } })
  fireEvent.change(screen.getByLabelText("香港身份证号码"), { target: { value: "a1234563" } })
  expect(screen.getByLabelText("出生日期")).toHaveValue("")
  expect(screen.getByLabelText("性别")).toHaveValue("")
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledExactlyOnceWith({ ...passport, document_type: "hk_id", identity_number: "A1234563" }))
  fireEvent.change(screen.getByLabelText("出生日期"), { target: { value: "1990-01-02" } })
  expect(screen.getByText(/年龄：\d+ 周岁/)).toBeVisible()
})

it("removes CN-derived pending facts when the document number becomes invalid", () => {
  show()
  fireEvent.change(screen.getByLabelText("身份证"), { target: { value: "110105201201020029" } })
  fireEvent.change(screen.getByLabelText("身份证"), { target: { value: "110105201201020020" } })
  expect(screen.getByLabelText("性别")).toHaveValue("")
  expect(screen.getByLabelText("出生日期")).toHaveValue("")
})

it("does not broaden mainland contact validation for a pending HK document", async () => {
  const { onSubmit } = show({ ...passport, document_type: "hk_id", identity_number: "A1234563", phone: "not-a-phone" })
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  expect(screen.getByLabelText("联系电话（可暂缺）")).toHaveAttribute("aria-invalid", "true")
  expect(onSubmit).not.toHaveBeenCalled()
})

it("submits manually completed HK guardians with strict adult and phone checks in pending mode", async () => {
  const guardian = { full_name: "合成监护", document_type: "hk_id" as const, identity_number: "AB123456(9)", phone: "13800000000", email: null,
    relationship: "母亲", sex: "female" as const, date_of_birth: "1990-01-02", is_primary: true }
  const { onSubmit } = show({ ...passport, guardians: [guardian] })
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledExactlyOnceWith({ ...passport, guardians: [{ ...guardian, identity_number: "AB1234569" }] }))
})

it("requires real name and typed document rather than creating a placeholder person", async () => {
  const { onSubmit } = show()
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  expect(screen.getByLabelText("姓名")).toHaveAttribute("aria-invalid", "true")
  expect(screen.getByLabelText("身份证")).toHaveAttribute("aria-invalid", "true")
  expect(onSubmit).not.toHaveBeenCalled()
})

it("submits an unknown-age passport without invented contacts, guardian, sex, birthday or roles", async () => {
  const { onSubmit } = show(passport)
  expect(screen.getByText("年龄待补")).toBeVisible()
  expect(screen.getByText(/性别待补，暂不能办理实际入住/)).toBeVisible()
  expect(screen.getByLabelText("人员身份")).toHaveDisplayValue("其他工作人员")
  expect(screen.getByLabelText("队伍归属状态")).toHaveDisplayValue("待划分队伍")
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledExactlyOnceWith(passport))
})

it("validates CN checksum and derives only CN sex and birthday, while minor guardian remains optional in pending mode", async () => {
  const { onSubmit } = show()
  fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "真实未成年" } })
  fireEvent.change(screen.getByLabelText("身份证"), { target: { value: "110105201201020020" } })
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  expect(screen.getByLabelText("身份证")).toHaveAttribute("aria-invalid", "true")
  expect(onSubmit).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText("身份证"), { target: { value: "110105201201020029" } })
  expect(screen.getByLabelText("性别")).toHaveValue("female")
  expect(screen.getByLabelText("出生日期")).toHaveValue("2012-01-02")
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledExactlyOnceWith({ ...passport, kind: "staff", full_name: "真实未成年", identity_number: "110105201201020029", document_type: "cn_id", sex: "female", date_of_birth: "2012-01-02" }))
})

it("switching document type never reuses CN-derived passport facts", async () => {
  show()
  fireEvent.change(screen.getByLabelText("身份证"), { target: { value: "110105201201020029" } })
  await userEvent.selectOptions(screen.getByLabelText("证件类型"), "passport")
  expect(screen.getByLabelText("护照号码")).toHaveValue("")
  expect(screen.getByLabelText("性别")).toHaveValue("")
  expect(screen.getByLabelText("出生日期")).toHaveValue("")
})

it("preserves the passport spelling and accepts foreign contact information without making it a verified login", async () => {
  const { onSubmit } = show({ ...passport, identity_number: "  Ab-123 45  ", phone: "+55 11 1234 5678" })
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledExactlyOnceWith({ ...passport, identity_number: "  Ab-123 45  ", phone: "+55 11 1234 5678" }))
})

it("a provided CN contact must remain valid even though an absent contact is allowed", async () => {
  const { onSubmit } = show({ ...passport, document_type: "cn_id", identity_number: "110105199001021238", sex: "male", date_of_birth: "1990-01-02", phone: "not-a-phone" })
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  expect(screen.getByLabelText("联系电话（可暂缺）")).toHaveAttribute("aria-invalid", "true")
  expect(onSubmit).not.toHaveBeenCalled()
})

it("accepts only explicitly selected real affiliations", async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined)
  render(<PendingPersonForm initialValue={passport} submitting={false} onSubmit={onSubmit}
    coachOptions={[{ id: "coach", label: "实际教练" }]} teamOptions={[{ id: "team", label: "实际队伍" }]} />)
  await userEvent.selectOptions(screen.getByLabelText("人员身份"), "both")
  await userEvent.click(screen.getByLabelText("实际教练"))
  await userEvent.selectOptions(screen.getByLabelText("所属队伍（可暂缺）"), "team")
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledExactlyOnceWith({ ...passport, kind: "both", coach_person_ids: ["coach"], team_id: "team" }))
})

it("not-required allocation cannot send a contradictory team relationship", async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined)
  render(<PendingPersonForm initialValue={{ ...passport, kind: "player", team_id: "team", team_allocation_status: "not_required" }} submitting={false} onSubmit={onSubmit} teamOptions={[{ id: "team", label: "实际队伍" }]} />)
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  expect(screen.getByLabelText("所属队伍（可暂缺）")).toHaveAttribute("aria-invalid", "true")
  expect(onSubmit).not.toHaveBeenCalled()
})

it("staff classification cannot retain coach or player-team relationships", async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined)
  render(<PendingPersonForm initialValue={{ ...passport, kind: "both", coach_person_ids: ["coach"], team_id: "team" }} submitting={false} onSubmit={onSubmit}
    coachOptions={[{ id: "coach", label: "实际教练" }]} teamOptions={[{ id: "team", label: "实际队伍" }]} />)
  await userEvent.selectOptions(screen.getByLabelText("人员身份"), "staff")
  expect(screen.queryByLabelText("实际教练")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("所属队伍（可暂缺）")).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledExactlyOnceWith(passport))
})

it("an optional guardian is not synthesized; a manually added incomplete guardian cannot be submitted", async () => {
  const { onSubmit } = show(passport)
  await userEvent.click(screen.getByRole("button", { name: "添加监护人" }))
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  expect(screen.getByLabelText("监护人姓名")).toHaveAttribute("aria-invalid", "true")
  expect(screen.getByLabelText("监护人身份证")).toHaveAttribute("aria-invalid", "true")
  expect(onSubmit).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole("button", { name: "移除监护人 1" }))
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledExactlyOnceWith(passport))
})

it("provided guardians retain strict adult identity and valid mainland-phone checks", async () => {
  const guardian = { full_name: "真实监护人", identity_number: "110105199001021238", phone: "12000000000", email: null,
    relationship: "父亲", sex: "male" as const, date_of_birth: "1990-01-02", is_primary: true }
  const { onSubmit } = show({ ...passport, guardians: [guardian] })
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  expect(screen.getByLabelText("监护人手机号")).toHaveAttribute("aria-invalid", "true")
  expect(onSubmit).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText("监护人手机号"), { target: { value: "13800000000" } })
  fireEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledExactlyOnceWith({ ...passport, guardians: [{ ...guardian, phone: "13800000000" }] }))
})

it("pending correction hides classification, roles and affiliations and protects bound email", async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined)
  render(<PendingPersonForm initialValue={passport} identityOnlyKind="staff" canEditActivationEmail={false} submitting={false} onSubmit={onSubmit} />)
  expect(screen.queryByLabelText("人员身份")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("队伍归属状态")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("所属队伍（可暂缺）")).not.toBeInTheDocument()
  expect(screen.getByLabelText("邮箱")).toBeDisabled()
  fireEvent.click(screen.getByRole("button", { name: "提交待补更正" }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce())
})

it("renders server missing facts independently from housing and unactivated account", () => {
  render(<PendingReadinessSummary value={{ approval_valid: true, housing_ready: true, completion_allowed: true, activation_profile_ready: false,
    approval_id: null, version: 3, kind: "staff", profile_missing: ["date_of_birth", "guardian_age"], housing_missing: [], activation_missing: ["date_of_birth", "guardian_age", "email"] }} />)
  expect(screen.getByText(/^资料待补：出生日期、监护年龄待核对$/)).toBeVisible()
  expect(screen.getByText(/可办理住宿/)).toBeVisible()
  expect(screen.getByText(/未激活/)).toBeVisible()
  expect(screen.getByText(/预填邮箱也须本人验证并经管理员核对认领/)).toBeVisible()
})
