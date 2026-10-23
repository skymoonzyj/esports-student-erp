import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it, vi } from "vitest"

import type { HousingRoomIssue } from "./api"
import { ReportIssueDialog, ResolveIssueDialog } from "./HousingIssueDialogs"

const issue: HousingRoomIssue = {
  id: "issue-1",
  room_id: "room-409",
  description: "空调无法制冷",
  previous_service_status: "available",
  reported_at: "2026-08-06T09:00:00+08:00",
  status: "open",
  resolved_at: null,
  resolution_note: null,
  allowed_actions: { resolve: true },
  created_at: "2026-08-06T09:00:00+08:00",
  updated_at: "2026-08-06T09:00:00+08:00",
}

it("requires and trims a problem description before reporting", async () => {
  const user = userEvent.setup()
  const onSubmit = vi.fn()
  render(
    <ReportIssueDialog
      roomNumber="409"
      roomVersion={8}
      pending={false}
      error={null}
      onClose={vi.fn()}
      onSubmit={onSubmit}
    />,
  )

  const description = screen.getByLabelText("问题描述")
  const submit = screen.getByRole("button", { name: "确认上报" })
  expect(description).toBeRequired()
  expect(description).toHaveAttribute("maxlength", "1000")
  expect(submit).toBeDisabled()

  await user.type(description, "   ")
  expect(submit).toBeDisabled()
  await user.type(description, " 空调故障 ")
  await user.click(submit)

  expect(onSubmit).toHaveBeenCalledWith({ room_version: 8, description: "空调故障" })
})

it("shows the original issue and trims the resolution note", async () => {
  const user = userEvent.setup()
  const onSubmit = vi.fn()
  render(
    <ResolveIssueDialog
      roomNumber="409"
      roomVersion={9}
      issue={issue}
      pending={false}
      error={null}
      onClose={vi.fn()}
      onSubmit={onSubmit}
    />,
  )

  expect(screen.getByText("空调无法制冷")).toBeVisible()
  const note = screen.getByLabelText("处理说明")
  expect(note).toBeRequired()
  expect(note).toHaveAttribute("maxlength", "1000")
  expect(screen.getByRole("button", { name: "确认解决" })).toBeDisabled()

  await user.type(note, " 已完成维修 ")
  await user.click(screen.getByRole("button", { name: "确认解决" }))

  expect(onSubmit).toHaveBeenCalledWith({ room_version: 9, resolution_note: "已完成维修" })
})

it("keeps the opening room version and draft after an error refresh", async () => {
  const user = userEvent.setup()
  const onSubmit = vi.fn()
  const props = {
    roomNumber: "409",
    pending: false,
    onClose: vi.fn(),
    onSubmit,
  }
  const view = render(<ReportIssueDialog {...props} roomVersion={8} error={null} />)

  await user.type(screen.getByLabelText("问题描述"), "窗户损坏")
  view.rerender(<ReportIssueDialog {...props} roomVersion={11} error="数据已更新，请查看当前数据后再重试。" />)

  expect(screen.getByRole("alert")).toHaveTextContent("数据已更新")
  expect(screen.getByLabelText("问题描述")).toHaveValue("窗户损坏")
  await user.click(screen.getByRole("button", { name: "确认上报" }))
  expect(onSubmit).toHaveBeenCalledWith({ room_version: 8, description: "窗户损坏" })
})

it("locks report interactions while a mutation is pending", async () => {
  const onClose = vi.fn()
  const onSubmit = vi.fn()
  render(
    <ReportIssueDialog
      roomNumber="409"
      roomVersion={8}
      pending
      error={null}
      onClose={onClose}
      onSubmit={onSubmit}
    />,
  )

  expect(screen.getByRole("group")).toBeDisabled()
  expect(screen.getByRole("button", { name: "关闭上报问题 · 房间 409" })).toBeDisabled()
  expect(onClose).not.toHaveBeenCalled()
  expect(onSubmit).not.toHaveBeenCalled()
})
