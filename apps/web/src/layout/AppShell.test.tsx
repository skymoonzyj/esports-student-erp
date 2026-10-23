import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { expect, it } from "vitest"

import { AppShell } from "./AppShell"

it("shows desktop and mobile navigation from the same permitted route model", () => {
  render(
    <MemoryRouter>
      <AppShell permissions={new Set(["people.list"])} userName="平台管理员" />
    </MemoryRouter>,
  )

  expect(screen.getAllByRole("navigation")).toHaveLength(2)
  expect(screen.getAllByRole("link", { name: "人员中心" })).toHaveLength(2)
  expect(screen.queryByRole("link", { name: "账号管理" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "权限管理" })).not.toBeInTheDocument()
})

it("shows evaluation navigation only with permission and keeps nested routes active", () => {
  const view = render(
    <MemoryRouter initialEntries={["/evaluations/system"]}>
      <AppShell permissions={new Set(["evaluation_center.access"])} />
    </MemoryRouter>,
  )

  const evaluationLinks = screen.getAllByRole("link", { name: "评价中心" })
  expect(evaluationLinks).toHaveLength(2)
  for (const link of evaluationLinks) expect(link).toHaveClass("active")

  view.rerender(
    <MemoryRouter initialEntries={["/evaluations/system"]}>
      <AppShell permissions={new Set(["people.list"])} />
    </MemoryRouter>,
  )
  expect(
    screen.queryByRole("link", { name: "评价中心" }),
  ).not.toBeInTheDocument()
})

it("shows account, permission, and audit navigation as independent permission-gated items", () => {
  render(
    <MemoryRouter>
      <AppShell
        permissions={new Set(["accounts.manage", "permissions.manage", "audit.read"])}
        userName="安全管理员"
      />
    </MemoryRouter>,
  )

  expect(screen.getAllByRole("link", { name: "账号管理" })).toHaveLength(2)
  expect(screen.getAllByRole("link", { name: "权限管理" })).toHaveLength(2)
  // Adding the administrator-only mail entry moves the lower-priority audit
  // link into the closed mobile overflow sheet.
  expect(screen.getAllByRole("link", { name: "邮件设置" })).toHaveLength(1)
  expect(screen.getAllByRole("link", { name: "安全审计" })).toHaveLength(1)
})

it("keeps mail credentials out of scoped permission navigation", () => {
  const view = render(
    <MemoryRouter>
      <AppShell
        permissions={new Set(["permissions.manage"])}
        globalPermissions={new Set()}
      />
    </MemoryRouter>,
  )

  expect(screen.queryByRole("link", { name: "邮件设置" })).not.toBeInTheDocument()

  view.rerender(
    <MemoryRouter>
      <AppShell
        permissions={new Set(["permissions.manage"])}
        globalPermissions={new Set(["permissions.manage"])}
      />
    </MemoryRouter>,
  )
  expect(screen.getAllByRole("link", { name: "邮件设置" })).toHaveLength(2)
})

it("shows personal housing and housing management independently on desktop and mobile", () => {
  const view = render(
    <MemoryRouter>
      <AppShell
        permissions={new Set(["housing.read"])}
        portalContext={{
          coach_available: false,
          learning_available: false,
          payments_available: false,
          housing_management_available: false,
          housing_available: true,
          learning_mode: null,
        }}
      />
    </MemoryRouter>,
  )
  expect(screen.getAllByRole("link", { name: "住宿信息" })).toHaveLength(2)
  expect(screen.queryByRole("link", { name: "房态管理" })).not.toBeInTheDocument()

  view.rerender(
    <MemoryRouter>
      <AppShell
        portalContext={{
          coach_available: false,
          learning_available: false,
          payments_available: false,
          housing_management_available: true,
          housing_available: false,
          learning_mode: null,
        }}
      />
    </MemoryRouter>,
  )
  expect(screen.getAllByRole("link", { name: "房态管理" })).toHaveLength(2)
  expect(screen.queryByRole("link", { name: "住宿信息" })).not.toBeInTheDocument()

  view.rerender(
    <MemoryRouter>
      <AppShell
        permissions={new Set(["housing.read"])}
        portalContext={{
          coach_available: false,
          learning_available: false,
          payments_available: false,
          housing_management_available: false,
          housing_available: false,
          learning_mode: null,
        }}
      />
    </MemoryRouter>,
  )
  expect(screen.queryByRole("link", { name: "住宿信息" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "房态管理" })).not.toBeInTheDocument()
})

it("opens the first permitted admissions workspace without adding a new top-level item", () => {
  render(
    <MemoryRouter>
      <AppShell
        permissions={new Set(["admission_cases.read"])}
        userName="招生客服"
      />
    </MemoryRouter>,
  )

  const teachingLinks = screen.getAllByRole("link", { name: "招生教学" })
  expect(teachingLinks).toHaveLength(2)
  expect(teachingLinks[0]).toHaveAttribute("href", "/teaching/admission-cases")
  expect(screen.queryByRole("link", { name: "待分配" })).not.toBeInTheDocument()
})

it("keeps the teaching top-level item active across admissions subpages", () => {
  render(
    <MemoryRouter initialEntries={["/teaching/payments"]}>
      <AppShell
        permissions={new Set(["teaching.read", "payments.read"])}
        userName="招生管理员"
      />
    </MemoryRouter>,
  )

  for (const link of screen.getAllByRole("link", { name: "招生教学" })) {
    expect(link).toHaveClass("active")
  }
})

it("keeps four stable admin mobile tracks and closes permission-filtered more with Escape", async () => {
  const user = userEvent.setup()
  render(
    <MemoryRouter>
      <AppShell
        permissions={
          new Set([
            "people.list",
            "teaching.read",
            "accounts.manage",
            "permissions.manage",
            "audit.read",
          ])
        }
        userName="平台管理员"
        onLogout={() => undefined}
      />
    </MemoryRouter>,
  )

  const mobileNavigation = screen.getByRole("navigation", { name: "手机导航" })
  expect(within(mobileNavigation).getAllByRole("link")).toHaveLength(3)
  expect(within(mobileNavigation).getByRole("link", { name: "工作台" })).toHaveTextContent(
    "首页",
  )
  expect(
    within(mobileNavigation).getByRole("link", { name: "人员中心" }),
  ).toHaveTextContent("人员")
  expect(
    within(mobileNavigation).getByRole("link", { name: "招生教学" }),
  ).toHaveTextContent("招生教学")
  expect(
    within(mobileNavigation).getByRole("link", { name: "招生教学" }),
  ).toHaveAttribute("href", "/teaching/cohorts")
  await user.click(within(mobileNavigation).getByRole("button", { name: "更多" }))

  const more = screen.getByRole("dialog", { name: "更多导航" })
  expect(within(more).getByRole("link", { name: "账号管理" })).toBeVisible()
  expect(within(more).getByRole("link", { name: "权限管理" })).toBeVisible()
  expect(within(more).getByRole("link", { name: "安全审计" })).toBeVisible()
  expect(within(more).queryByRole("link", { name: "招生教学" })).not.toBeInTheDocument()

  await user.keyboard("{Escape}")
  expect(screen.queryByRole("dialog", { name: "更多导航" })).not.toBeInTheDocument()
})

it("prioritizes coach and learning while keeping payment and back-office links in more", async () => {
  const user = userEvent.setup()
  render(
    <MemoryRouter>
      <AppShell
        permissions={new Set(["people.list", "accounts.manage"])}
        portalContext={{
          coach_available: true,
          learning_available: true,
          payments_available: true,
          housing_management_available: false,
          housing_available: true,
          learning_mode: "family",
        }}
        userName="多身份账号"
      />
    </MemoryRouter>,
  )

  const mobileNavigation = screen.getByRole("navigation", { name: "手机导航" })
  expect(
    within(mobileNavigation)
      .getAllByRole("link")
      .map((link) => link.getAttribute("aria-label")),
  ).toEqual(["工作台", "教练工作", "学习信息"])
  expect(within(mobileNavigation).getByRole("button", { name: "更多" })).toBeVisible()
  expect(screen.getAllByRole("link", { name: "教练工作" })).toHaveLength(2)
  expect(screen.getAllByRole("link", { name: "学习信息" })).toHaveLength(2)

  await user.click(within(mobileNavigation).getByRole("button", { name: "更多" }))
  const more = screen.getByRole("dialog", { name: "更多导航" })
  expect(within(more).getByRole("link", { name: "本人付款" })).toBeVisible()
  expect(within(more).getByRole("link", { name: "住宿信息" })).toBeVisible()
  expect(within(more).getByRole("link", { name: "人员中心" })).toBeVisible()
  expect(within(more).getByRole("link", { name: "账号管理" })).toBeVisible()
})

it("traps focus, locks scrolling and restores the more trigger on close", async () => {
  const user = userEvent.setup()
  render(
    <MemoryRouter>
      <AppShell
        permissions={new Set(["people.list"])}
        portalContext={{
          coach_available: true,
          learning_available: true,
          payments_available: true,
          housing_management_available: false,
          housing_available: false,
          learning_mode: "self_and_family",
        }}
        userName="键盘用户"
        onLogout={() => undefined}
      />
    </MemoryRouter>,
  )

  const mobileNavigation = screen.getByRole("navigation", { name: "手机导航" })
  const trigger = within(mobileNavigation).getByRole("button", { name: "更多" })
  await user.click(trigger)

  const dialog = screen.getByRole("dialog", { name: "更多导航" })
  const close = within(dialog).getByRole("button", { name: "关闭更多导航" })
  const logout = within(dialog).getByRole("button", { name: "退出登录" })
  expect(close).toHaveFocus()
  expect(document.body.style.overflow).toBe("hidden")

  await user.keyboard("{Shift>}{Tab}{/Shift}")
  expect(logout).toHaveFocus()
  await user.keyboard("{Tab}")
  expect(close).toHaveFocus()

  await user.keyboard("{Escape}")
  expect(screen.queryByRole("dialog", { name: "更多导航" })).not.toBeInTheDocument()
  expect(trigger).toHaveFocus()
  expect(document.body.style.overflow).toBe("")
})
