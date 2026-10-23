import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it, vi } from "vitest"
import { HousingExportDialog } from "./HousingExportDialog"

it("defaults to current snapshots and keeps historical export an explicit separate action", async () => {
  const current = vi.fn().mockResolvedValue(undefined)
  const history = vi.fn().mockResolvedValue(undefined)
  render(<HousingExportDialog catalog={{ access_revision: "r", buildings: [], floors: [], can_create_building: false }} filters={{ occupancy: "unoccupied" }} pending={false} error={null} onClose={vi.fn()} onSnapshot={current} onHistory={history} />)
  const user = userEvent.setup()
  expect(screen.getByLabelText("导出类型")).toHaveValue("snapshot")
  await user.type(screen.getByLabelText("导出用途（必填）"), "台账核对")
  await user.click(screen.getByRole("button", { name: "开始导出" }))
  expect(current).toHaveBeenCalledWith(expect.objectContaining({ occupancy: "unoccupied", purpose: "台账核对" }))
  expect(history).not.toHaveBeenCalled()
  await user.selectOptions(screen.getByLabelText("导出类型"), "history")
  await user.selectOptions(screen.getByLabelText("历史导出入住状态"), "occupied")
  await user.type(screen.getByLabelText("导出用途（必填）"), "历史核对")
  await user.click(screen.getByRole("button", { name: "开始导出" }))
  expect(history).toHaveBeenCalledWith(expect.objectContaining({ occupancy: "occupied", purpose: "历史核对" }))
})
