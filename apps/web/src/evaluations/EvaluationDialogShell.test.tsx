import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useRef, useState } from "react"
import { afterEach, expect, it } from "vitest"

import { EvaluationDialogShell } from "./EvaluationDialogShell"

function DialogHarness({
  pending = false,
  autofocus = false,
}: {
  pending?: boolean
  autofocus?: boolean
}) {
  const [open, setOpen] = useState(false)
  return (
    <main>
      <button type="button" onClick={() => setOpen(true)}>
        打开审核
      </button>
      {open ? (
        <EvaluationDialogShell
          title="审核系统评价"
          closeLabel="关闭系统评价审核"
          pending={pending}
          onClose={() => setOpen(false)}
        >
          <p>只读审核内容</p>
          {autofocus ? <input data-autofocus aria-label="驳回原因" /> : null}
          <button type="button">确认审核</button>
          <button type="button" onClick={() => setOpen(false)}>
            取消
          </button>
        </EvaluationDialogShell>
      ) : null}
    </main>
  )
}

function FallbackHarness() {
  const [open, setOpen] = useState(false)
  const [showOpener, setShowOpener] = useState(true)
  const ownerRef = useRef<HTMLElement | null>(null)
  return (
    <section ref={ownerRef} tabIndex={-1} data-evaluation-dialog-owner>
      {showOpener ? (
        <button type="button" onClick={() => setOpen(true)}>
          打开会被撤权的操作
        </button>
      ) : null}
      {open ? (
        <EvaluationDialogShell
          title="撤权测试"
          closeLabel="关闭撤权测试"
          pending={false}
          focusFallbackRef={ownerRef}
          onClose={() => {
            setOpen(false)
            setShowOpener(false)
          }}
        >
          <button type="button">确认</button>
        </EvaluationDialogShell>
      ) : null}
    </section>
  )
}

afterEach(() => {
  document.body.removeAttribute("inert")
  document.body.style.overflow = ""
})

it("locks body scrolling while a dialog is mounted and restores the prior value", async () => {
  document.body.style.overflow = "clip"
  const user = userEvent.setup()
  const view = render(<DialogHarness />)

  await user.click(screen.getByRole("button", { name: "打开审核" }))
  expect(document.body.style.overflow).toBe("hidden")

  view.unmount()
  expect(document.body.style.overflow).toBe("clip")
})

it("keeps body scrolling locked until the last simultaneous dialog unmounts", () => {
  const background = document.createElement("aside")
  document.body.append(background)
  const first = render(
    <EvaluationDialogShell
      title="第一个审核"
      closeLabel="关闭第一个审核"
      pending={false}
      onClose={() => undefined}
    >
      <button type="button">确认第一个审核</button>
    </EvaluationDialogShell>,
  )
  const second = render(
    <EvaluationDialogShell
      title="第二个审核"
      closeLabel="关闭第二个审核"
      pending={false}
      onClose={() => undefined}
    >
      <button type="button">确认第二个审核</button>
    </EvaluationDialogShell>,
  )

  expect(document.body.style.overflow).toBe("hidden")
  expect(background).toHaveAttribute("inert")
  first.unmount()
  expect(document.body.style.overflow).toBe("hidden")
  expect(background).toHaveAttribute("inert")
  second.unmount()
  expect(document.body.style.overflow).toBe("")
  expect(background).not.toHaveAttribute("inert")
  background.remove()
})

it("moves focus into the dialog, traps Tab, closes with Escape, and restores focus", async () => {
  const user = userEvent.setup()
  const view = render(<DialogHarness />)
  const opener = screen.getByRole("button", { name: "打开审核" })

  await user.click(opener)

  const dialog = screen.getByRole("dialog", { name: "审核系统评价" })
  const close = within(dialog).getByRole("button", { name: "关闭系统评价审核" })
  await waitFor(() => expect(close).toHaveFocus())
  expect(view.container).toHaveAttribute("inert")

  await user.tab({ shift: true })
  expect(within(dialog).getByRole("button", { name: "取消" })).toHaveFocus()
  await user.tab()
  expect(close).toHaveFocus()

  await user.keyboard("{Escape}")
  expect(screen.queryByRole("dialog", { name: "审核系统评价" })).not.toBeInTheDocument()
  expect(opener).toHaveFocus()
  expect(view.container).not.toHaveAttribute("inert")
})

it("supports explicit cancel and backdrop close while restoring the opener", async () => {
  const user = userEvent.setup()
  render(<DialogHarness />)
  const opener = screen.getByRole("button", { name: "打开审核" })

  await user.click(opener)
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "取消" }))
  expect(opener).toHaveFocus()

  await user.click(opener)
  const dialog = screen.getByRole("dialog", { name: "审核系统评价" })
  fireEvent.mouseDown(dialog.parentElement as HTMLElement)
  expect(screen.queryByRole("dialog", { name: "审核系统评价" })).not.toBeInTheDocument()
  await waitFor(() => expect(opener).toHaveFocus())
})

it("prevents Escape, backdrop, and close-button dismissal while pending", async () => {
  const user = userEvent.setup()
  render(<DialogHarness pending />)
  await user.click(screen.getByRole("button", { name: "打开审核" }))

  const dialog = screen.getByRole("dialog", { name: "审核系统评价" })
  const close = within(dialog).getByRole("button", { name: "关闭系统评价审核" })
  expect(close).toBeDisabled()
  await waitFor(() =>
    expect(within(dialog).getByRole("button", { name: "确认审核" })).toHaveFocus(),
  )

  await user.keyboard("{Escape}")
  fireEvent.mouseDown(dialog.parentElement as HTMLElement)
  expect(screen.getByRole("dialog", { name: "审核系统评价" })).toBeVisible()
})

it("preserves pre-existing inert state when the dialog closes", async () => {
  const user = userEvent.setup()
  const persistentBackground = document.createElement("aside")
  persistentBackground.setAttribute("inert", "")
  document.body.append(persistentBackground)

  const view = render(<DialogHarness />)
  const opener = screen.getByRole("button", { name: "打开审核" })
  await user.click(opener)
  await user.keyboard("{Escape}")

  expect(view.container).not.toHaveAttribute("inert")
  expect(persistentBackground).toHaveAttribute("inert")
  persistentBackground.remove()
})

it("preserves an explicit consumer autofocus target", async () => {
  const user = userEvent.setup()
  render(<DialogHarness autofocus />)

  await user.click(screen.getByRole("button", { name: "打开审核" }))

  await waitFor(() => expect(screen.getByLabelText("驳回原因")).toHaveFocus())
})

it("focuses a stable owner when closing also removes the opener", async () => {
  const user = userEvent.setup()
  render(<FallbackHarness />)
  await user.click(screen.getByRole("button", { name: "打开会被撤权的操作" }))

  await user.keyboard("{Escape}")

  const owner = document.querySelector<HTMLElement>("[data-evaluation-dialog-owner]")
  await waitFor(() => expect(owner).toHaveFocus())
})
