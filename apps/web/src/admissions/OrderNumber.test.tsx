import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import { OrderNumber } from "./OrderNumber"

afterEach(() => vi.restoreAllMocks())

it("copies the complete order number and announces success", async () => {
  const user = userEvent.setup()
  const orderNumber = "BM20260911000001"
  render(<OrderNumber orderNumber={orderNumber} />)

  await user.click(screen.getByRole("button", { name: `复制订单号 ${orderNumber}` }))

  expect(await navigator.clipboard.readText()).toBe(orderNumber)
  expect(screen.getByRole("status")).toHaveTextContent("订单号已复制")
})

it("offers manual copying when browser clipboard permission is denied", async () => {
  const user = userEvent.setup()
  vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("denied"))
  render(<OrderNumber orderNumber="BM20260911000001" />)

  await user.click(screen.getByRole("button", { name: "复制订单号 BM20260911000001" }))

  expect(screen.getByRole("alert")).toHaveTextContent("复制失败，请长按或选中订单号复制")
  expect(screen.getByText("BM20260911000001")).toBeVisible()
  expect(screen.queryByText("已复制")).not.toBeInTheDocument()
})
