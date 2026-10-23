import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { readFileSync } from "node:fs"
import { MemoryRouter } from "react-router-dom"
import { expect, it } from "vitest"

import { AppShell } from "./AppShell"

it("test_team_navigation_without_global_people_permission", async () => {
  const user = userEvent.setup()
  const context = { can_open_management: true, can_read_self: true, can_manage_catalog: false, can_assign_managers: false, can_transfer: false }
  render(<MemoryRouter><AppShell permissions={new Set()} teamContext={context} /></MemoryRouter>)
  const group = screen.getAllByRole("group", { name: "人员中心菜单" })[0]
  expect(within(group).queryByRole("link", { name: "队伍管理" })).not.toBeInTheDocument()
  await user.click(within(group).getByRole("button", { name: "人员中心" }))
  expect(within(group).getByRole("link", { name: "队伍管理" })).toHaveAttribute("href", "/people/teams")
  expect(within(group).queryByRole("link", { name: "人员中心" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "招生教学" })).not.toBeInTheDocument()
})

it("keeps member self-profile and existing student navigation independent of roster management", () => {
  const context = { can_open_management: false, can_read_self: true, can_manage_catalog: false, can_assign_managers: false, can_transfer: false }
  const portal = { coach_available: false, learning_available: true, payments_available: false, housing_management_available: false, housing_available: false, adult_guardian_invitations_available: false, food_profile_available: false, food_preferences_onboarding_required: false, learning_mode: "self" } as const
  render(<MemoryRouter><AppShell permissions={new Set()} teamContext={context} portalContext={portal} /></MemoryRouter>)
  expect(screen.getAllByRole("link", { name: "个人资料" })).toHaveLength(2)
  expect(screen.getAllByRole("link", { name: "学习信息" })).toHaveLength(2)
  expect(screen.queryByRole("group", { name: "人员中心菜单" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "队伍管理" })).not.toBeInTheDocument()
})

it("does not invent a team management entry for an administrator without live team capability", () => {
  const context = { can_open_management: false, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false }
  render(<MemoryRouter><AppShell permissions={new Set(["people.list"])} isSuperAdmin teamContext={context} /></MemoryRouter>)
  expect(screen.getAllByRole("link", { name: "人员中心" })).toHaveLength(2)
  expect(screen.queryByRole("link", { name: "队伍管理" })).not.toBeInTheDocument()
})

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

it("groups account administration under personnel while preserving child permissions", async () => {
  const user = userEvent.setup()
  render(
    <MemoryRouter>
      <AppShell
        permissions={new Set([
          "accounts.read",
          "registration.review",
          "staff_identity_applications.review",
          "permissions.manage",
          "mail.settings.manage",
          "audit.read",
        ])}
        isSuperAdmin
        userName="安全管理员"
      />
    </MemoryRouter>,
  )

  const groups = screen.getAllByRole("group", { name: "人员中心菜单" })
  expect(groups).toHaveLength(2)
  expect(within(groups[0]).queryByRole("link", { name: "账号管理" })).not.toBeInTheDocument()
  await user.click(within(groups[0]).getByRole("button", { name: "人员中心" }))
  expect(within(groups[0]).getByRole("link", { name: "账号管理" })).toHaveAttribute(
    "href",
    "/settings/accounts",
  )
  expect(within(groups[0]).getByRole("link", { name: "注册处理" })).toHaveAttribute(
    "href",
    "/settings/registrations",
  )
  expect(within(groups[0]).getByRole("link", { name: "身份申请" })).toHaveAttribute(
    "href",
    "/settings/staff-applications",
  )
  expect(within(groups[0]).getByRole("link", { name: "权限管理" })).toHaveAttribute(
    "href",
    "/settings/permissions",
  )
  expect(screen.getAllByRole("link", { name: "权限管理" })).toHaveLength(2)
  expect(screen.getAllByRole("link", { name: "邮件设置" })).toHaveLength(2)
  expect(screen.getAllByRole("link", { name: "安全审计" })).toHaveLength(2)
})

it("hides security audit navigation from non-super-admins even with the permission string", () => {
  render(
    <MemoryRouter>
      <AppShell permissions={new Set(["audit.read"])} />
    </MemoryRouter>,
  )

  expect(screen.queryByRole("link", { name: "安全审计" })).not.toBeInTheDocument()
})

it("shows operations monitoring only to a super administrator with its permission", () => {
  const view = render(
    <MemoryRouter>
      <AppShell permissions={new Set(["operations.monitor.read"])} />
    </MemoryRouter>,
  )
  expect(screen.queryByRole("link", { name: "运维监控" })).not.toBeInTheDocument()

  view.rerender(
    <MemoryRouter>
      <AppShell
        permissions={new Set(["operations.monitor.read"])}
        isSuperAdmin
      />
    </MemoryRouter>,
  )
  expect(screen.getAllByRole("link", { name: "运维监控" })).toHaveLength(2)
  const desktopNavigation = screen.getByRole("navigation", { name: "主导航" })
  const mobileNavigation = screen.getByRole("navigation", { name: "手机导航" })
  expect(within(desktopNavigation).getAllByRole("link")[0]).toHaveAccessibleName("运维监控")
  expect(within(mobileNavigation).getAllByRole("link")[0]).toHaveAccessibleName("运维监控")
})

it("keeps registration and staff application child entries independently permission-gated", () => {
  render(
    <MemoryRouter>
      <AppShell permissions={new Set(["registration.review"])} />
    </MemoryRouter>,
  )

  const registrationGroups = screen.getAllByRole("group", { name: "人员中心菜单" })
  expect(registrationGroups).toHaveLength(2)
  expect(within(registrationGroups[0]).queryByRole("link", { name: "身份申请" })).not.toBeInTheDocument()
  expect(within(registrationGroups[0]).queryByRole("link", { name: "注册处理" })).not.toBeInTheDocument()

  const user = userEvent.setup()
  return user.click(within(registrationGroups[0]).getByRole("button", { name: "人员中心" })).then(() => {
    expect(within(registrationGroups[0]).getByRole("link", { name: "注册处理" })).toBeVisible()
    expect(within(registrationGroups[0]).queryByRole("link", { name: "身份申请" })).not.toBeInTheDocument()
  })
})

it("shows the staff application child only for its review permission", async () => {
  const user = userEvent.setup()
  render(
    <MemoryRouter>
      <AppShell permissions={new Set(["staff_identity_applications.review"])} />
    </MemoryRouter>,
  )

  const group = screen.getAllByRole("group", { name: "人员中心菜单" })[0]
  await user.click(within(group).getByRole("button", { name: "人员中心" }))
  expect(within(group).getByRole("link", { name: "身份申请" })).toHaveAttribute(
    "href",
    "/settings/staff-applications",
  )
  expect(within(group).queryByRole("link", { name: "注册处理" })).not.toBeInTheDocument()
})

it("shows food administration navigation only for its independent global permissions", async () => {
  const user = userEvent.setup()
  const view = render(
    <MemoryRouter>
      <AppShell permissions={new Set(["food_preferences.export"])} />
    </MemoryRouter>,
  )

  expect(screen.getAllByRole("link", { name: "餐饮统计" })).toHaveLength(2)
  expect(screen.queryByRole("link", { name: "菜系管理" })).not.toBeInTheDocument()

  view.rerender(
    <MemoryRouter>
      <AppShell permissions={new Set(["food_preferences.catalog.manage"])} />
    </MemoryRouter>,
  )
  expect(screen.queryByRole("link", { name: "餐饮统计" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "菜系管理" })).not.toBeInTheDocument()
  await user.click(screen.getAllByRole("button", { name: "餐饮统计" })[0])
  expect(screen.getAllByRole("link", { name: "菜系管理" })).toHaveLength(2)
})

it("nests cuisine management under food statistics when both permissions are granted", async () => {
  const user = userEvent.setup()
  render(
    <MemoryRouter>
      <AppShell
        permissions={new Set([
          "food_preferences.export",
          "food_preferences.catalog.manage",
        ])}
      />
    </MemoryRouter>,
  )

  const groups = screen.getAllByRole("group", { name: "餐饮统计菜单" })
  expect(groups).toHaveLength(2)
  const group = groups[0]
  expect(within(group).getByRole("link", { name: "餐饮统计" })).toHaveAttribute(
    "href",
    "/people/food-preferences",
  )
  expect(within(group).queryByRole("link", { name: "菜系管理" })).not.toBeInTheDocument()
  await user.click(within(group).getByRole("link", { name: "餐饮统计" }))
  expect(within(group).getByRole("link", { name: "菜系管理" })).toHaveAttribute(
    "href",
    "/settings/food-cuisines",
  )
})

it("separates finance settlement access from revenue-share rule management", async () => {
  const user = userEvent.setup()
  const view = render(
    <MemoryRouter>
      <AppShell permissions={new Set(["revenue_share.finance"])} />
    </MemoryRouter>,
  )

  expect(screen.getAllByRole("link", { name: "财务结算" })).toHaveLength(2)
  expect(screen.queryByRole("link", { name: "分成管理" })).not.toBeInTheDocument()

  view.rerender(
    <MemoryRouter>
      <AppShell permissions={new Set(["revenue_share.manage"])} />
    </MemoryRouter>,
  )
  expect(screen.queryByRole("link", { name: "财务结算" })).not.toBeInTheDocument()
  await user.click(screen.getAllByRole("button", { name: "财务结算" })[0])
  expect(screen.getAllByRole("link", { name: "分成管理" })).toHaveLength(2)
})

it("does not keep the people link active on the food statistics route", () => {
  render(
    <MemoryRouter initialEntries={["/people/food-preferences"]}>
      <AppShell permissions={new Set(["people.list", "food_preferences.export"])} />
    </MemoryRouter>,
  )

  const peopleLinks = screen.getAllByRole("link", { name: "人员中心" })
  const foodLinks = screen.getAllByRole("link", { name: "餐饮统计" })
  for (const link of peopleLinks) expect(link).not.toHaveClass("active")
  for (const link of foodLinks) expect(link).toHaveClass("active")
})

it("marks the personnel group active on a direct child route without expanding it", () => {
  render(
    <MemoryRouter initialEntries={["/settings/registrations"]}>
      <AppShell permissions={new Set(["registration.review"])} />
    </MemoryRouter>,
  )

  const group = screen.getAllByRole("group", { name: "人员中心菜单" })[0]
  const parent = within(group).getByRole("button", { name: "人员中心" })
  expect(parent).toHaveClass("active")
  expect(parent).toHaveAttribute("aria-expanded", "false")
  expect(within(group).queryByRole("link", { name: "注册处理" })).not.toBeInTheDocument()
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
          adult_guardian_invitations_available: false,
          food_profile_available: false,
          food_preferences_onboarding_required: false,
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
        permissions={new Set(["housing.read"])}
        portalContext={{
          coach_available: false,
          learning_available: false,
          payments_available: false,
          housing_management_available: true,
          housing_available: false,
          adult_guardian_invitations_available: false,
          food_profile_available: false,
          food_preferences_onboarding_required: false,
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
          adult_guardian_invitations_available: false,
          food_profile_available: false,
          food_preferences_onboarding_required: false,
          learning_mode: null,
        }}
      />
    </MemoryRouter>,
  )
  expect(screen.queryByRole("link", { name: "住宿信息" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "房态管理" })).not.toBeInTheDocument()
})

it("shows the optional personal profile without exposing people management", () => {
  render(
    <MemoryRouter>
      <AppShell
        portalContext={{
          coach_available: false,
          learning_available: false,
          payments_available: false,
          housing_management_available: false,
          housing_available: false,
          adult_guardian_invitations_available: false,
          food_profile_available: true,
          food_preferences_onboarding_required: false,
          learning_mode: null,
        }}
      />
    </MemoryRouter>,
  )

  const profileLinks = screen.getAllByRole("link", { name: "个人资料" })
  expect(profileLinks).toHaveLength(2)
  for (const link of profileLinks) {
    expect(link).toHaveAttribute("href", "/me/profile")
  }
  expect(screen.queryByRole("link", { name: "人员中心" })).not.toBeInTheDocument()
})

it("shows adult guardian invitations independently of food profile access", () => {
  render(
    <MemoryRouter>
      <AppShell
        portalContext={{
          coach_available: false,
          learning_available: true,
          payments_available: false,
          housing_management_available: false,
          housing_available: false,
          adult_guardian_invitations_available: true,
          food_profile_available: false,
          food_preferences_onboarding_required: false,
          learning_mode: "self",
        }}
      />
    </MemoryRouter>,
  )

  const links = screen.getAllByRole("link", { name: "监护邀请" })
  expect(links).toHaveLength(2)
  for (const link of links) {
    expect(link).toHaveAttribute("href", "/me/guardian-invitations")
  }
  expect(screen.queryByRole("link", { name: "个人资料" })).not.toBeInTheDocument()
})

it("shows a server-backed food onboarding task and hides stale entries while reauthorizing", () => {
  const requiredContext = {
    coach_available: false,
    learning_available: false,
    payments_available: false,
    housing_management_available: false,
    housing_available: false,
    adult_guardian_invitations_available: false,
    food_profile_available: true,
    food_preferences_onboarding_required: true,
    learning_mode: null,
  } as const
  const view = render(
    <MemoryRouter>
      <AppShell portalContext={requiredContext}>
        <p>页面正文</p>
      </AppShell>
    </MemoryRouter>,
  )

  const task = screen.getByRole("link", { name: "完善餐饮偏好" })
  expect(task).toHaveAttribute("href", "/onboarding/food-preferences")
  expect(screen.getByText("页面正文")).toBeVisible()

  view.rerender(
    <MemoryRouter>
      <AppShell portalContext={undefined}>
        <p>页面正文</p>
      </AppShell>
    </MemoryRouter>,
  )
  expect(
    screen.queryByRole("link", { name: "完善餐饮偏好" }),
  ).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "个人资料" })).not.toBeInTheDocument()
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
            "accounts.read",
            "permissions.manage",
            "audit.read",
            "mail.settings.manage",
          ])
        }
        isSuperAdmin
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
  expect(within(more).queryByRole("link", { name: "账号管理" })).not.toBeInTheDocument()
  expect(within(more).queryByRole("link", { name: "权限管理" })).not.toBeInTheDocument()
  expect(within(more).getByRole("link", { name: "安全审计" })).toBeVisible()
  expect(within(more).getByRole("link", { name: "邮件设置" })).toBeVisible()
  expect(within(more).queryByRole("link", { name: "招生教学" })).not.toBeInTheDocument()

  await user.click(within(mobileNavigation).getByRole("link", { name: "人员中心" }))
  expect(within(mobileNavigation).getByRole("link", { name: "账号管理" })).toBeVisible()
  expect(within(mobileNavigation).getByRole("link", { name: "权限管理" })).toBeVisible()

  await user.keyboard("{Escape}")
  expect(screen.queryByRole("dialog", { name: "更多导航" })).not.toBeInTheDocument()
})

it("prioritizes coach and learning while keeping payment and back-office links in more", async () => {
  const user = userEvent.setup()
  render(
    <MemoryRouter>
      <AppShell
        permissions={new Set(["people.list", "accounts.read"])}
        portalContext={{
          coach_available: true,
          learning_available: true,
          payments_available: true,
          housing_management_available: false,
          housing_available: true,
          adult_guardian_invitations_available: false,
          food_profile_available: true,
          food_preferences_onboarding_required: false,
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
  expect(within(more).getByRole("link", { name: "个人资料" })).toBeVisible()
  expect(within(more).getByRole("link", { name: "人员中心" })).toBeVisible()
  expect(within(more).queryByRole("link", { name: "账号管理" })).not.toBeInTheDocument()
  await user.click(within(more).getByRole("link", { name: "人员中心" }))
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
          adult_guardian_invitations_available: false,
          food_profile_available: false,
          food_preferences_onboarding_required: false,
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

it("keeps the onboarding task single-column and touchable at 360px", () => {
  const css = readFileSync("src/layout/AppShell.css", "utf8")

  expect(css).toMatch(/\.shellOnboardingTask\s*\{[^}]*min-width:\s*0;/s)
  expect(css).toMatch(
    /\.shellOnboardingTask\s+a\s*\{[^}]*min-height:\s*44px;/s,
  )
  expect(css).toMatch(
    /@media\s*\(max-width:\s*767px\)[\s\S]*\.shellOnboardingTask\s*\{[^}]*grid-template-columns:\s*1fr;/,
  )
  expect(css).not.toMatch(/overflow-x:\s*hidden/)
})

it("keeps tablet navigation bounded behind the shared more menu", () => {
  const css = readFileSync("src/layout/AppShell.css", "utf8")
  const tabletStyles = css.match(
    /@media\s*\(min-width:\s*768px\)\s*and\s*\(max-width:\s*1023px\)\s*\{([\s\S]*?)\n\}/,
  )?.[1]

  expect(tabletStyles).toMatch(/\.shellPrimaryNavigation\s*\{[^}]*display:\s*none;/s)
  expect(tabletStyles).toMatch(
    /\.shellMobileNavigation\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*1fr\)\);/s,
  )
  expect(tabletStyles).toMatch(
    /\.shellMobileNavigation\s+\.shellNavSubLinks\s*\{[^}]*position:\s*absolute;/s,
  )
})

it("keeps an overflowing desktop menu inside the dark navigation rail", () => {
  const css = readFileSync("src/layout/AppShell.css", "utf8")

  expect(css).toMatch(/\.shellRail\s*\{[^}]*overflow:\s*hidden;/s)
  expect(css).toMatch(
    /\.shellPrimaryNavigation\s*\{[^}]*min-height:\s*0;[^}]*flex:\s*1\s+1\s+auto;[^}]*align-content:\s*start;[^}]*overflow-y:\s*auto;/s,
  )
  expect(css).toMatch(/\.shellSecurityNote\s*\{[^}]*flex:\s*0\s+0\s+auto;/s)
})

it("shows daily procurement only to procurement readers", () => {
  const view = render(
    <MemoryRouter>
      <AppShell permissions={new Set(["procurement.read"])} />
    </MemoryRouter>,
  )

  const links = screen.getAllByRole("link", { name: "日常采购" })
  expect(links).toHaveLength(2)
  for (const link of links) expect(link).toHaveAttribute("href", "/procurement")

  view.rerender(
    <MemoryRouter>
      <AppShell permissions={new Set(["procurement.manage"])} />
    </MemoryRouter>,
  )
  expect(screen.queryByRole("link", { name: "日常采购" })).not.toBeInTheDocument()
})

it("shows travel report conversion only to travel report readers", () => {
  const { rerender } = render(
    <MemoryRouter>
      <AppShell permissions={new Set(["travel_reports.read"])} />
    </MemoryRouter>,
  )
  expect(screen.getAllByRole("link", { name: /订票报表/ })[0]).toHaveAttribute(
    "href",
    "/travel-reports",
  )

  rerender(
    <MemoryRouter>
      <AppShell permissions={new Set(["travel_reports.import"])} />
    </MemoryRouter>,
  )
  expect(screen.queryByRole("link", { name: /订票报表/ })).not.toBeInTheDocument()
})

it("天赋菜单不能被教学重定向规则改写", () => {
  render(
    <MemoryRouter>
      <AppShell permissions={new Set(["talent_tests.read"])} />
    </MemoryRouter>,
  )
  const links = screen.getAllByRole("link", { name: "天赋测试" })
  expect(links).toHaveLength(2)
  for (const link of links) expect(link).toHaveAttribute("href", "/talent-tests")
  expect(screen.queryByRole("link", { name: "中文打字" })).not.toBeInTheDocument()
})

it.each(["talent_tests.take", "talent_tests.read"])(
  "shows talent navigation for effective %s permission",
  (permission) => {
    render(
      <MemoryRouter>
        <AppShell permissions={new Set([permission])} />
      </MemoryRouter>,
    )

    expect(screen.getAllByRole("link", { name: "天赋测试" })).toHaveLength(2)
  },
)

it.each(["esports_assessments.take", "esports_assessments.read"])(
  "shows the independent assessment navigation for effective %s permission",
  (permission) => {
    render(
      <MemoryRouter initialEntries={["/esports-assessments/history"]}>
        <AppShell permissions={new Set([permission, "talent_tests.read"])} />
      </MemoryRouter>,
    )

    const assessmentLinks = screen.getAllByRole("link", { name: "天赋评测 2" })
    const legacyLinks = screen.getAllByRole("link", { name: "天赋测试" })
    expect(assessmentLinks).toHaveLength(2)
    for (const link of assessmentLinks) {
      expect(link).toHaveAttribute("href", "/esports-assessments")
      expect(link).toHaveClass("active")
    }
    for (const link of legacyLinks) expect(link).not.toHaveClass("active")
  },
)

it("does not infer the independent assessment entry from administrator identity", () => {
  render(
    <MemoryRouter>
      <AppShell permissions={new Set(["people.list"])} isSuperAdmin />
    </MemoryRouter>,
  )

  expect(screen.queryByRole("link", { name: "天赋评测 2" })).not.toBeInTheDocument()
})
