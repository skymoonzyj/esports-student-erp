import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it, vi } from "vitest"

import {
  GenerateHousingReportDialog,
  MarkHousingReportSubmittedDialog,
  VoidHousingReportDialog,
} from "./HousingReportDialogs"

const REPORT_OPTIONS = {
  buildings: [{ id: "building-1", name: "1号楼" }],
  floors: [{ id: "floor-1", building_id: "building-1", name: "二层" }],
  cohorts: [{ id: "cohort-1", name: "英雄联盟一期" }],
}

it("submits all eleven personnel categories including team staff", async () => {
  const user = userEvent.setup()
  let submitted: unknown
  render(
    <GenerateHousingReportDialog
      options={REPORT_OPTIONS}
      pending={false}
      error={null}
      onClose={() => undefined}
      onSubmit={async (input) => { submitted = input }}
    />,
  )
  const dialog = screen.getByRole("dialog", { name: "导出实名名单" })
  for (const label of ["学员", "队员", "学员兼队员", "战队经理", "教练", "助教", "生活老师", "客服", "行政", "其他工作人员", "临时访客"]) {
    await user.click(within(dialog).getByLabelText(label, { exact: true }))
  }
  await user.type(within(dialog).getByLabelText("导出用途（必填）"), "工作人员住宿核对")
  await user.click(within(dialog).getByRole("button", { name: "确认导出" }))

  await waitFor(() => expect(submitted).toEqual(expect.objectContaining({
    person_types: ["student", "player", "student_player", "team_manager", "coach", "assistant_coach", "life_teacher", "customer_service", "administration", "other", "visitor"],
  })))
})

it("uses an explicit all-person option and clears specific person filters", async () => {
  const user = userEvent.setup()
  const submit = vi.fn().mockResolvedValue(undefined)
  render(
    <GenerateHousingReportDialog
      options={REPORT_OPTIONS}
      pending={false}
      error={null}
      onClose={() => undefined}
      onSubmit={submit}
    />,
  )
  const dialog = screen.getByRole("dialog", { name: "导出实名名单" })
  const allPeople = within(dialog).getByLabelText("全部", { exact: true })
  const students = within(dialog).getByLabelText("学员", { exact: true })

  expect(allPeople).toBeChecked()
  await user.click(students)
  expect(allPeople).not.toBeChecked()
  expect(students).toBeChecked()

  await user.type(within(dialog).getByLabelText("导出用途（必填）"), "公安入住报送")
  await user.click(within(dialog).getByRole("button", { name: "确认导出" }))
  await waitFor(() => expect(submit).toHaveBeenLastCalledWith(
    expect.objectContaining({ person_types: ["student"] }),
  ))

  await user.click(allPeople)
  expect(allPeople).toBeChecked()
  expect(students).not.toBeChecked()
  await user.click(within(dialog).getByRole("button", { name: "确认导出" }))
  await waitFor(() => expect(submit).toHaveBeenLastCalledWith(
    expect.objectContaining({ person_types: [] }),
  ))
})

it("keeps export available and explains the required purpose", async () => {
  const user = userEvent.setup()
  const submit = vi.fn().mockResolvedValue(undefined)
  render(
    <GenerateHousingReportDialog
      options={REPORT_OPTIONS}
      pending={false}
      error={null}
      onClose={() => undefined}
      onSubmit={submit}
    />,
  )
  const dialog = screen.getByRole("dialog", { name: "导出实名名单" })
  const exportButton = within(dialog).getByRole("button", { name: "确认导出" })

  expect(exportButton).toBeEnabled()
  expect(within(dialog).getByText("请填写至少 2 个字的导出用途")).toBeVisible()
  await user.click(exportButton)
  expect(submit).not.toHaveBeenCalled()
  await user.type(within(dialog).getByLabelText("导出用途（必填）"), "公安报送")
  expect(within(dialog).queryByText("请填写至少 2 个字的导出用途")).not.toBeInTheDocument()
  await user.click(exportButton)
  await waitFor(() => expect(submit).toHaveBeenCalledOnce())
})

it("requires a submitted date and returns a versioned action", async () => {
  const user = userEvent.setup()
  const submit = vi.fn().mockResolvedValue(undefined)
  render(
    <MarkHousingReportSubmittedDialog
      batchNumber="HR-20260806-000001"
      version={3}
      pending={false}
      error={null}
      onClose={() => undefined}
      onSubmit={submit}
    />,
  )
  const dialog = screen.getByRole("dialog", { name: "标记人工报送" })

  expect(within(dialog).getByRole("button", { name: "确认已报送" })).toBeDisabled()
  await user.type(within(dialog).getByLabelText("报送日期"), "2026-08-06")
  await user.type(within(dialog).getByLabelText("报送备注（选填）"), "窗口报送")
  await user.click(within(dialog).getByRole("button", { name: "确认已报送" }))

  await waitFor(() => expect(submit).toHaveBeenCalledWith({
    submitted_at: "2026-08-06T00:00:00+08:00",
    note: "窗口报送",
    version: 3,
  }))
})

it("requires a void reason and warns that downloads will stop", async () => {
  const user = userEvent.setup()
  const submit = vi.fn().mockResolvedValue(undefined)
  render(
    <VoidHousingReportDialog
      batchNumber="HR-20260806-000001"
      version={4}
      pending={false}
      error={null}
      onClose={() => undefined}
      onSubmit={submit}
    />,
  )
  const dialog = screen.getByRole("dialog", { name: "作废实名批次" })

  expect(within(dialog).getByText("作废后原批次不能再次下载，只能创建关联更正批次。")).toBeVisible()
  expect(within(dialog).getByRole("button", { name: "确认作废" })).toBeDisabled()
  await user.type(within(dialog).getByLabelText("作废原因"), "发现房号错误")
  await user.click(within(dialog).getByRole("button", { name: "确认作废" }))

  await waitFor(() => expect(submit).toHaveBeenCalledWith({
    reason: "发现房号错误",
    version: 4,
  }))
})
