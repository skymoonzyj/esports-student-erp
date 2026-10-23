import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it, vi } from "vitest"

import {
  MarkHousingReportSubmittedDialog,
  VoidHousingReportDialog,
} from "./HousingReportDialogs"

it("requires an exact submitted time and returns a versioned action", async () => {
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
  await user.type(within(dialog).getByLabelText("报送时间"), "2026-08-06T20:30")
  await user.type(within(dialog).getByLabelText("报送备注（选填）"), "窗口报送")
  await user.click(within(dialog).getByRole("button", { name: "确认已报送" }))

  await waitFor(() => expect(submit).toHaveBeenCalledWith({
    submitted_at: "2026-08-06T20:30:00+08:00",
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
