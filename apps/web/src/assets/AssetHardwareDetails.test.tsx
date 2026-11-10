import { render, screen } from "@testing-library/react"
import { expect, it } from "vitest"
import { AssetHardwareDetails } from "./AssetHardwareDetails"

// Catches false missing-configuration warnings after switching away from a computer category.
it("does not ask noncomputers to complete unrelated hardware fields", () => {
  render(<AssetHardwareDetails asset={{ category: "显示器", cpu_model: "历史记录" }} />)
  expect(screen.getByText("CPU 型号：历史记录")).toBeVisible()
  expect(screen.queryAllByText(/待补充/)).toHaveLength(0)
})
