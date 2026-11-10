import { fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { ModuleErrorBoundary } from "./ModuleErrorBoundary"

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

function Report({ failed }: { failed: boolean }) {
  if (failed) throw new Error("private-stack-and-chunk-path")
  return <p>已授权报告内容</p>
}

it("contains a report render error without exposing details or automatically refreshing", () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined)
  const reload = vi.fn()
  const browserWindow = window
  vi.stubGlobal("window", new Proxy(browserWindow, {
    get: (target, property) => property === "location" ? { reload } : Reflect.get(target, property, target),
  }))
  const view = render(<><h1>人员档案</h1><ModuleErrorBoundary moduleName="天赋评测报告"><Report failed /></ModuleErrorBoundary></>)
  expect(screen.getByRole("heading", { name: "人员档案" })).toBeVisible()
  expect(screen.getByRole("alert")).toHaveTextContent("天赋评测报告暂时无法加载")
  expect(screen.getByRole("alert")).toHaveTextContent("刷新会丢失未保存的内容")
  expect(screen.queryByText(/private-stack-and-chunk-path/)).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /重试/ })).not.toBeInTheDocument()

  expect(reload).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: "刷新页面" }))
  expect(reload).toHaveBeenCalledTimes(1)
  vi.unstubAllGlobals()
  view.unmount()
})

it("does not pretend rerendering a failed boundary retries a rejected module, but clears for a new identity key", () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined)
  const view = render(<ModuleErrorBoundary key="account-a:person-a" moduleName="天赋评测报告"><Report failed /></ModuleErrorBoundary>)
  view.rerender(<ModuleErrorBoundary key="account-a:person-a" moduleName="天赋评测报告"><Report failed={false} /></ModuleErrorBoundary>)
  expect(screen.getByRole("alert")).toBeVisible()
  expect(screen.queryByText("已授权报告内容")).not.toBeInTheDocument()
  view.rerender(<ModuleErrorBoundary key="account-a:person-b" moduleName="天赋评测报告"><Report failed={false} /></ModuleErrorBoundary>)
  expect(screen.getByText("已授权报告内容")).toBeVisible()
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
})
