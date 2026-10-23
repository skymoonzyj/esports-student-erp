import { useRef, useState } from "react"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it } from "vitest"
import { HousingDialogShell } from "./HousingDialogShell"

function FocusRestoreHarness() {
  const [open, setOpen] = useState(false)
  const ownerRef = useRef<HTMLElement>(null)
  const unrelatedRef = useRef<HTMLButtonElement>(null)

  return (
    <>
      <section ref={ownerRef} data-housing-dialog-owner="true" tabIndex={-1}>
        <button type="button" onClick={() => setOpen(true)}>打开操作</button>
      </section>
      <button ref={unrelatedRef} type="button">页面外操作</button>
      {open ? (
        <HousingDialogShell
          title="测试操作"
          pending={false}
          error={null}
          focusFallbackRef={ownerRef}
          onClose={() => setOpen(false)}
        >
          <button
            type="button"
            data-autofocus
            onClick={() => {
              unrelatedRef.current?.focus()
              setOpen(false)
            }}
          >
            完成操作
          </button>
        </HousingDialogShell>
      ) : null}
    </>
  )
}

function FallbackRestoreHarness() {
  const [open, setOpen] = useState(true)
  const ownerRef = useRef<HTMLElement>(null)

  return (
    <section ref={ownerRef} data-housing-dialog-owner="true" tabIndex={-1}>
      <span>稳定页面区域</span>
      {open ? (
        <HousingDialogShell
          title="直接打开的操作"
          pending={false}
          error={null}
          focusFallbackRef={ownerRef}
          onClose={() => setOpen(false)}
        >
          <button type="button" data-autofocus onClick={() => setOpen(false)}>完成操作</button>
        </HousingDialogShell>
      ) : null}
    </section>
  )
}

it("restores the opener when a non-modal page control has focus during dialog cleanup", async () => {
  const user = userEvent.setup()
  render(<FocusRestoreHarness />)
  const opener = screen.getByRole("button", { name: "打开操作" })

  await user.click(opener)
  const complete = screen.getByRole("button", { name: "完成操作" })
  expect(complete).toHaveFocus()
  await user.click(complete)

  await waitFor(() => expect(opener).toHaveFocus())
})

it("uses the explicit fallback when the dialog opened while the body had focus", async () => {
  const user = userEvent.setup()
  render(<FallbackRestoreHarness />)
  const owner = screen.getByText("稳定页面区域").closest("section")
  expect(owner).not.toBeNull()

  await user.click(screen.getByRole("button", { name: "完成操作" }))

  await waitFor(() => expect(owner).toHaveFocus())
})
