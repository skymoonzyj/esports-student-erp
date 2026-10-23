import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, expect, it, vi } from "vitest"

import { ReferralQrDialog } from "./ReferralQrDialog"

const referralCode = {
  id: "referral-1",
  code: "COACH-2026",
  label: "张教练专属邀请",
  source_kind: "person" as const,
  source_name: "张教练",
  is_active: true,
  registration_url: "/register?invite=COACH-2026",
}

beforeEach(() => {
  vi.stubGlobal("print", vi.fn())
})

it("renders the complete registration URL and supports copy and print", async () => {
  const user = userEvent.setup()
  const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined)
  render(<ReferralQrDialog referralCode={referralCode} onClose={vi.fn()} />)

  const completeUrl = "http://localhost:3000/register?invite=COACH-2026"
  expect(screen.getByRole("img", { name: `邀请注册二维码：${completeUrl}` })).toBeVisible()
  expect(screen.getByText(completeUrl)).toBeVisible()
  expect(screen.getByText("张教练")).toBeVisible()

  await user.click(screen.getByRole("button", { name: "复制链接" }))
  expect(writeText).toHaveBeenCalledWith(completeUrl)
  expect(screen.getByText("注册链接已复制")).toBeVisible()

  await user.click(screen.getByRole("button", { name: "打印" }))
  expect(window.print).toHaveBeenCalledTimes(1)
})
