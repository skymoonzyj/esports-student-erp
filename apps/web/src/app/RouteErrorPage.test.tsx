import { fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { RouteErrorPage } from "./RouteErrorPage"

afterEach(() => vi.unstubAllGlobals())

it("renders independently of authentication and the failed child, and reloads only on user action", () => {
  const reload = vi.fn()
  const browserWindow = window
  vi.stubGlobal("window", new Proxy(browserWindow, {
    get: (target, property) => property === "location" ? { reload } : Reflect.get(target, property, target),
  }))
  const view = render(<RouteErrorPage />)
  expect(screen.getByRole("heading", { name: "页面暂时无法加载" })).toBeVisible()
  expect(screen.getByRole("alert")).toHaveTextContent("刷新会丢失未保存的内容")
  expect(reload).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: "刷新页面" }))
  expect(reload).toHaveBeenCalledTimes(1)
  vi.unstubAllGlobals()
  view.unmount()
})
