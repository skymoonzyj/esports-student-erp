import {
  Activity,
  ArrowRight,
  BedDouble,
  BadgeCheck,
  BrainCircuit,
  BookOpenCheck,
  Building2,
  CalendarDays,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  ClipboardPenLine,
  ChartNoAxesCombined,
  FileSpreadsheet,
  Gamepad2,
  Home,
  HandCoins,
  Landmark,
  LogOut,
  Mail,
  MessageSquareText,
  MoreHorizontal,
  ShieldCheck,
  ShoppingCart,
  SlidersHorizontal,
  Utensils,
  UserCog,
  UserPlus,
  UserRound,
  Users,
  WalletCards,
  Package,
  PlaneTakeoff,
  X,
} from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"
import type { LucideIcon } from "lucide-react"
import { Link, NavLink, Outlet, useLocation } from "react-router-dom"

import { foodOnboardingPath } from "../foodPreferences/FoodOnboardingPage"
import type { PortalCapability, PortalContext } from "../portal/api"
import type { TeamContext } from "../teams/api"
import type { AssistedContext } from "../assistedPeople/api"
import "./AppShell.css"

export type NavigationItem = {
  to: string
  label: string
  shortLabel: string
  icon: LucideIcon
  mobilePriority: number
  permission?: string
  sectionPermissions?: readonly string[]
  activePrefix?: string
  activeExclusions?: readonly string[]
  end?: boolean
  portalCapability?: PortalCapability
  teamCapability?: keyof TeamContext
  assistedCapability?: "can_submit" | "can_review" | "can_direct_create" | "can_read_managed"
  superAdminOnly?: boolean
  children?: readonly NavigationItem[]
}

export const navigationItems: NavigationItem[] = [
  {
    to: "/settings/operations",
    label: "运维监控",
    shortLabel: "运维",
    icon: Activity,
    mobilePriority: -1,
    permission: "operations.monitor.read",
    superAdminOnly: true,
  },
  {
    to: "/",
    label: "工作台",
    shortLabel: "首页",
    icon: Home,
    mobilePriority: 0,
    end: true,
  },
  {
    to: "/evaluations",
    label: "评价中心",
    shortLabel: "评价",
    icon: MessageSquareText,
    mobilePriority: 4,
    permission: "evaluation_center.access",
    activePrefix: "/evaluations",
  },
  {
    to: "/talent-tests",
    label: "天赋测试",
    shortLabel: "天赋",
    icon: BrainCircuit,
    mobilePriority: 4,
    sectionPermissions: ["talent_tests.take", "talent_tests.read"],
    activePrefix: "/talent-tests",
  },
  {
    to: "/esports-assessments",
    label: "天赋评测 2",
    shortLabel: "评测 2",
    icon: Gamepad2,
    mobilePriority: 4,
    sectionPermissions: ["esports_assessments.take", "esports_assessments.read"],
    activePrefix: "/esports-assessments",
  },
  {
    to: "/coach/next-cohorts",
    label: "教练工作",
    shortLabel: "教练",
    icon: ClipboardList,
    mobilePriority: 1,
    portalCapability: "coach_available",
  },
  {
    to: "/learning",
    label: "学习信息",
    shortLabel: "学习",
    icon: BookOpenCheck,
    mobilePriority: 2,
    portalCapability: "learning_available",
  },
  {
    to: "/enrollment",
    label: "学员报名",
    shortLabel: "报名",
    icon: ClipboardPenLine,
    mobilePriority: 3,
    permission: "enrollment_applications.create",
    end: true,
  },
  {
    to: "/me/housing",
    label: "住宿信息",
    shortLabel: "住宿",
    icon: BedDouble,
    mobilePriority: 3,
    portalCapability: "housing_available",
  },
  {
    to: "/me/payments",
    label: "本人付款",
    shortLabel: "付款",
    icon: WalletCards,
    mobilePriority: 4,
    portalCapability: "payments_available",
  },
  {
    to: "/me/assets",
    label: "我的资产",
    shortLabel: "资产",
    icon: Package,
    mobilePriority: 5,
    permission: "assets.read_self",
  },
  {
    to: "/me/revenue-share",
    label: "我的分成",
    shortLabel: "分成",
    icon: HandCoins,
    mobilePriority: 6,
    permission: "revenue_share.read_self",
  },
  {
    to: "/me/invitations", label: "我的邀请", shortLabel: "邀请", icon: UserPlus,
    mobilePriority: 6, portalCapability: "invitations_available",
  },
  {
    to: "/me/profile",
    label: "个人资料",
    shortLabel: "资料",
    icon: UserRound,
    mobilePriority: 5,
    portalCapability: "food_profile_available",
  },
  {
    to: "/me/guardian-invitations",
    label: "监护邀请",
    shortLabel: "监护邀请",
    icon: UserPlus,
    mobilePriority: 6,
    portalCapability: "adult_guardian_invitations_available",
  },
  {
    to: "/my-people", label: "我的学员／队员", shortLabel: "所属人员", icon: Users,
    mobilePriority: 6, assistedCapability: "can_read_managed",
  },
  {
    to: "/people",
    label: "人员中心",
    shortLabel: "人员",
    icon: Users,
    mobilePriority: 10,
    permission: "people.list",
    activeExclusions: ["/people/food-preferences"],
    children: [
      {
        to: "/people",
        label: "人员名单",
        shortLabel: "人员名单",
        icon: Users,
        mobilePriority: 11,
        permission: "people.list",
        end: true,
      },
      { to: "/people/assisted-requests", label: "我的建档申请", shortLabel: "建档申请", icon: ClipboardPenLine, mobilePriority: 13, assistedCapability: "can_submit" },
      { to: "/people/assisted-review", label: "建档审核", shortLabel: "建档审核", icon: ClipboardList, mobilePriority: 13, assistedCapability: "can_review" },
      { to: "/people/assisted-create", label: "管理建档", shortLabel: "管理建档", icon: UserPlus, mobilePriority: 13, assistedCapability: "can_direct_create" },
      {
        to: "/people/teams",
        label: "队伍管理",
        shortLabel: "队伍",
        icon: Users,
        mobilePriority: 13,
        teamCapability: "can_open_management",
      },
      {
        to: "/settings/accounts",
        label: "账号管理",
        shortLabel: "账号",
        icon: UserCog,
        mobilePriority: 14,
        permission: "accounts.read",
      },
      {
        to: "/settings/registrations",
        label: "注册处理",
        shortLabel: "注册处理",
        icon: UserPlus,
        mobilePriority: 17,
        permission: "registration.review",
      },
      {
        to: "/settings/staff-applications",
        label: "身份申请",
        shortLabel: "身份申请",
        icon: BadgeCheck,
        mobilePriority: 18,
        permission: "staff_identity_applications.review",
      },
      {
        to: "/settings/permissions",
        label: "权限管理",
        shortLabel: "权限",
        icon: SlidersHorizontal,
        mobilePriority: 19,
        permission: "permissions.manage",
      },
    ],
  },
  {
    to: "/people/food-preferences",
    label: "餐饮统计",
    shortLabel: "餐饮统计",
    icon: ChartNoAxesCombined,
    mobilePriority: 17,
    permission: "food_preferences.export",
    activePrefix: "/people/food-preferences",
    children: [
      {
        to: "/settings/food-cuisines",
        label: "菜系管理",
        shortLabel: "菜系",
        icon: Utensils,
        mobilePriority: 18,
        permission: "food_preferences.catalog.manage",
        activePrefix: "/settings/food-cuisines",
      },
    ],
  },
  {
    to: "/teaching/cohorts",
    label: "招生教学",
    shortLabel: "招生教学",
    icon: CalendarDays,
    mobilePriority: 11,
    sectionPermissions: [
      "teaching.read",
      "payments.read",
      "admission_cases.read",
      "enrollments.read",
    ],
    activePrefix: "/teaching/",
  },
  {
    to: "/housing",
    label: "房态管理",
    shortLabel: "房态管理",
    icon: Building2,
    mobilePriority: 12,
    permission: "housing.read",
    portalCapability: "housing_management_available",
    activePrefix: "/housing/rooms/",
    end: true,
  },
  {
    to: "/assets",
    label: "资产管理",
    shortLabel: "资产管理",
    icon: Package,
    mobilePriority: 13,
    permission: "assets.read",
    activePrefix: "/assets",
    activeExclusions: ["/asset-scan"],
  },
  {
    to: "/procurement",
    label: "日常采购",
    shortLabel: "采购",
    icon: ShoppingCart,
    mobilePriority: 14,
    permission: "procurement.read",
    end: true,
  },
  {
    to: "/travel-reports",
    label: "订票报表",
    shortLabel: "订票",
    icon: PlaneTakeoff,
    mobilePriority: 15,
    permission: "travel_reports.read",
    end: true,
  },
  {
    to: "/finance/revenue-share",
    label: "财务结算",
    shortLabel: "财务",
    icon: Landmark,
    mobilePriority: 14,
    permission: "revenue_share.finance",
    activePrefix: "/finance/revenue-share",
    children: [
      {
        to: "/settings/revenue-share",
        label: "分成管理",
        shortLabel: "分成管理",
        icon: SlidersHorizontal,
        mobilePriority: 15,
        permission: "revenue_share.manage",
      },
    ],
  },
  {
    to: "/housing/reports",
    label: "实名报送",
    shortLabel: "实名报送",
    icon: FileSpreadsheet,
    mobilePriority: 13,
    permission: "housing.reports.export_sensitive",
    end: true,
  },
  {
    to: "/settings/mail",
    label: "邮件设置",
    shortLabel: "邮件",
    icon: Mail,
    mobilePriority: 19,
    permission: "mail.settings.manage",
    activePrefix: "/settings/mail",
  },
  {
    to: "/settings/audit",
    label: "安全审计",
    shortLabel: "审计",
    icon: ShieldCheck,
    mobilePriority: 16,
    permission: "audit.read",
    superAdminOnly: true,
  },
]

type AppShellProps = {
  claimReviewAvailable?: boolean
  permissions?: ReadonlySet<string>
  isSuperAdmin?: boolean
  portalContext?: PortalContext
  teamContext?: TeamContext
  assistedContext?: AssistedContext
  userName?: string
  onLogout?: () => void
  children?: ReactNode
}

function hasNavigationAccess(
  item: NavigationItem,
  permissions: ReadonlySet<string>,
  isSuperAdmin: boolean,
  portalContext?: PortalContext,
  teamContext?: TeamContext,
  assistedContext?: AssistedContext,
): boolean {
  return (
    (!item.superAdminOnly || isSuperAdmin) &&
    (!item.permission || permissions.has(item.permission)) &&
    (!item.sectionPermissions ||
      item.sectionPermissions.some((permission) => permissions.has(permission))) &&
    (!item.portalCapability || portalContext?.[item.portalCapability] === true ||
      (item.to === "/me/profile" && teamContext?.can_read_self === true)) &&
    (!item.teamCapability || teamContext?.[item.teamCapability] === true) &&
    (!item.assistedCapability || assistedContext?.[item.assistedCapability] === true)
  )
}

function hasVisibleNavigationItem(
  item: NavigationItem,
  permissions: ReadonlySet<string>,
  isSuperAdmin: boolean,
  portalContext?: PortalContext,
  teamContext?: TeamContext,
  assistedContext?: AssistedContext,
): boolean {
  return (
    hasNavigationAccess(item, permissions, isSuperAdmin, portalContext, teamContext, assistedContext) ||
    item.children?.some((child) =>
      hasVisibleNavigationItem(child, permissions, isSuperAdmin, portalContext, teamContext, assistedContext),
    ) === true
  )
}

function NavigationLinks({
  items,
  mobile = false,
  onNavigate,
  permissions,
  isSuperAdmin,
  portalContext,
  teamContext,
  assistedContext,
  expandedNavigationGroups,
  onToggleGroup,
}: {
  items: readonly NavigationItem[]
  mobile?: boolean
  onNavigate?: () => void
  permissions: ReadonlySet<string>
  isSuperAdmin: boolean
  portalContext?: PortalContext
  teamContext?: TeamContext
  assistedContext?: AssistedContext
  expandedNavigationGroups: ReadonlySet<string>
  onToggleGroup: (to: string) => void
}) {
  const location = useLocation()
  function renderItem(item: NavigationItem): ReactNode {
    const ownAccess = hasNavigationAccess(item, permissions, isSuperAdmin, portalContext, teamContext, assistedContext)
    const children = (item.children ?? []).filter((child) =>
      hasVisibleNavigationItem(child, permissions, isSuperAdmin, portalContext, teamContext, assistedContext),
    )
    if (!ownAccess && children.length === 0) return null
    const Icon = item.icon
    const expanded = expandedNavigationGroups.has(item.to)
    const childActive = children.some((child) => {
      const prefix = child.activePrefix ?? child.to
      return location.pathname === prefix || location.pathname.startsWith(`${prefix}/`)
    })
    const toggleGroup = () => onToggleGroup(item.to)
    const link = ownAccess ? (
      <NavLink
        key={item.to}
        to={item.to}
        end={item.end}
        className={({ isActive }) => {
          const sectionActive =
            item.activePrefix !== undefined &&
            location.pathname.startsWith(item.activePrefix)
          const excluded = item.activeExclusions?.some(
            (prefix) =>
              location.pathname === prefix ||
              location.pathname.startsWith(`${prefix}/`),
          )
          return `shellNavLink${!excluded && (isActive || sectionActive || childActive) ? " active" : ""}`
        }}
        aria-label={item.label}
        aria-expanded={children.length > 0 ? expanded : undefined}
        onClick={() => {
          if (children.length > 0) toggleGroup()
          else onNavigate?.()
        }}
      >
        <span className="shellNavIcon">
          <Icon aria-hidden="true" />
        </span>
        <span className="shellNavLabel">{mobile ? item.shortLabel : item.label}</span>
        {children.length > 0 ? (
          <span className="shellNavGroupChevron" aria-hidden="true">
            <ChevronDown />
          </span>
        ) : null}
      </NavLink>
    ) : (
      <button
        type="button"
        className={`shellNavGroupLabel${childActive ? " active" : ""}`}
        key={item.to}
        aria-label={item.label}
        aria-expanded={expanded}
        onClick={toggleGroup}
      >
        <span className="shellNavIcon">
          <Icon aria-hidden="true" />
        </span>
        <span className="shellNavLabel">{item.label}</span>
        <span className="shellNavGroupChevron" aria-hidden="true">
          <ChevronDown />
        </span>
      </button>
    )
    if (children.length === 0) return link
    return (
      <div
        className={`shellNavGroup${expanded ? " expanded" : ""}`}
        role="group"
        aria-label={`${item.label}菜单`}
        key={item.to}
      >
        {link}
        {expanded ? (
          <div className="shellNavSubLinks">
            {children.map((child) => renderItem(child))}
          </div>
        ) : null}
      </div>
    )
  }

  return items.map((item) => renderItem(item))
}

export function AppShell({
  claimReviewAvailable = false,
  permissions = new Set<string>(),
  isSuperAdmin = false,
  portalContext,
  teamContext,
  assistedContext,
  userName = "当前用户",
  onLogout,
  children,
}: AppShellProps) {
  const location = useLocation()
  const [moreOpen, setMoreOpen] = useState(false)
  const [expandedNavigationGroups, setExpandedNavigationGroups] = useState<Set<string>>(
    () => new Set(location.pathname.startsWith("/people/assisted-") ? ["/people"] : []),
  )
  const moreTriggerRef = useRef<HTMLButtonElement>(null)
  const moreSheetRef = useRef<HTMLElement>(null)
  const teachingDestination = permissions.has("teaching.read")
    ? "/teaching/cohorts"
    : permissions.has("payments.read")
      ? "/teaching/payments"
      : permissions.has("admission_cases.read")
        ? "/teaching/admission-cases"
        : "/teaching/enrollments"
  // Only the combined navigation entry is widened; route guards stay independent.
  const visibleItems = navigationItems.map((item) => ({ ...item, children: item.children?.map((child) =>
    child.to === "/settings/registrations" && claimReviewAvailable ? { ...child, permission: undefined } : child,
  ) }))
    .filter(
      (item) => hasVisibleNavigationItem(item, permissions, isSuperAdmin, portalContext, teamContext, assistedContext),
    )
    .map((item) =>
      item.to === "/teaching/cohorts" && item.sectionPermissions
        ? { ...item, to: teachingDestination }
        : item,
    )
  const mobileItems = [...visibleItems].sort(
    (left, right) => left.mobilePriority - right.mobilePriority,
  )
  const hasMobileOverflow = mobileItems.length > 4
  const mobilePrimaryItems = hasMobileOverflow ? mobileItems.slice(0, 3) : mobileItems
  const mobileOverflowItems = hasMobileOverflow ? mobileItems.slice(3) : []
  const initial = userName.trim().slice(0, 1) || "用"
  const currentItem = visibleItems.flatMap((item) => [item, ...(item.children ?? []).filter((child) =>
    hasVisibleNavigationItem(child, permissions, isSuperAdmin, portalContext, teamContext, assistedContext),
  )]).filter((item) => item.to === location.pathname || (item.to !== "/" && location.pathname.startsWith(`${item.to}/`)))
    .sort((left, right) => right.to.length - left.to.length)[0]

  const toggleNavigationGroup = useCallback((to: string) => {
    setExpandedNavigationGroups((current) => {
      const next = new Set(current)
      if (next.has(to)) next.delete(to)
      else next.add(to)
      return next
    })
  }, [])

  const closeMobileNavigationGroups = useCallback(() => {
    // Hidden phone navigation shares desktop expansion state; only a real phone
    // leaf action should dismiss its overlay, not a desktop child navigation.
    if (window.matchMedia("(max-width: 767px)").matches) {
      setExpandedNavigationGroups(new Set())
    }
  }, [])

  const closeMore = useCallback(() => {
    setMoreOpen(false)
    queueMicrotask(() => moreTriggerRef.current?.focus())
  }, [])

  useEffect(() => {
    if (!moreOpen) return
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = "hidden"
    const sheet = moreSheetRef.current
    const focusable = () =>
      Array.from(
        sheet?.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      )
    focusable()[0]?.focus()

    function handleKeyboard(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        event.preventDefault()
        closeMore()
        return
      }
      if (event.key !== "Tab") return
      const items = focusable()
      const first = items[0]
      const last = items.at(-1)
      if (!first || !last) return
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    window.addEventListener("keydown", handleKeyboard)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener("keydown", handleKeyboard)
    }
  }, [closeMore, moreOpen])

  return (
    <div className="appShell">
      <aside className="shellRail">
        <div className="shellBrand">
          <span className="shellBrandMark">竞</span>
          <span className="shellBrandText">
            <strong>竞学 ERP</strong>
            <small>教育运营平台</small>
          </span>
        </div>
        <p className="shellNavigationCaption">工作空间</p>
        <nav className="shellPrimaryNavigation" aria-label="主导航">
          <NavigationLinks
            items={visibleItems}
            permissions={permissions}
            isSuperAdmin={isSuperAdmin}
            portalContext={portalContext}
            teamContext={teamContext}
            assistedContext={assistedContext}
            expandedNavigationGroups={expandedNavigationGroups}
            onToggleGroup={toggleNavigationGroup}
          />
        </nav>
        <div className="shellSecurityNote">
          <span aria-hidden="true">✓</span>
          <p>
            <strong>权限与隐私保护</strong>
            <small>敏感操作按授权范围开放</small>
          </p>
        </div>
      </aside>

      <div className="shellWorkspace">
        <header className="shellHeader">
          <div className="shellMobileBrand">
            <span className="shellBrandMark compact">竞</span>
            <strong>竞学 ERP</strong>
          </div>
          <div className="shellHeaderContext" aria-label="当前位置">
            <span>工作空间</span>
            <ChevronRight aria-hidden="true" />
            <strong>{currentItem?.label ?? "竞学 ERP"}</strong>
          </div>
          <div className="shellUser">
            <span className="shellAvatar" aria-hidden="true">
              {initial}
            </span>
            <span className="shellUserIdentity">
              <span className="shellUserName">{userName}</span>
              <small>{isSuperAdmin ? "超级管理员" : "已登录账号"}</small>
            </span>
            {onLogout ? (
              <button
                type="button"
                className="shellLogout"
                onClick={onLogout}
                aria-label="退出登录"
                title="退出登录"
              >
                <LogOut aria-hidden="true" />
              </button>
            ) : null}
          </div>
        </header>

        <main className="shellContent">
          {portalContext?.food_preferences_onboarding_required === true ? (
            <section
              className="shellOnboardingTask"
              aria-label="餐饮偏好待办"
            >
              <div>
                <strong>餐饮偏好待完善</strong>
                <p>填写喜欢的菜系和菜品，方便营期餐饮安排。</p>
              </div>
              <Link to={foodOnboardingPath}>完善餐饮偏好</Link>
            </section>
          ) : null}
          {children ?? <Outlet />}
        </main>
      </div>

      <nav className="shellMobileNavigation" aria-label="手机导航">
        <NavigationLinks
          items={mobilePrimaryItems}
          mobile
          onNavigate={closeMobileNavigationGroups}
          permissions={permissions}
          isSuperAdmin={isSuperAdmin}
          portalContext={portalContext}
          teamContext={teamContext}
          assistedContext={assistedContext}
          expandedNavigationGroups={expandedNavigationGroups}
          onToggleGroup={toggleNavigationGroup}
        />
        {hasMobileOverflow ? (
          <button
            ref={moreTriggerRef}
            type="button"
            className={`shellMobileMoreButton${moreOpen ? " active" : ""}`}
            aria-label="更多"
            aria-expanded={moreOpen}
            aria-controls="shell-more-navigation"
            onClick={() => setMoreOpen(true)}
          >
            <span className="shellNavIcon">
              <MoreHorizontal aria-hidden="true" />
            </span>
            <span>更多</span>
          </button>
        ) : null}
      </nav>

      {moreOpen ? (
        <div className="shellMoreBackdrop" onMouseDown={closeMore}>
          <section
            ref={moreSheetRef}
            id="shell-more-navigation"
            className="shellMoreSheet"
            role="dialog"
            aria-modal="true"
            aria-label="更多导航"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header>
              <strong>更多</strong>
              <button
                type="button"
                onClick={closeMore}
                aria-label="关闭更多导航"
                title="关闭"
              >
                <X aria-hidden="true" />
              </button>
            </header>
            <nav aria-label="更多功能">
              <NavigationLinks
                items={mobileOverflowItems}
                onNavigate={closeMore}
                permissions={permissions}
                isSuperAdmin={isSuperAdmin}
                portalContext={portalContext}
                teamContext={teamContext}
                assistedContext={assistedContext}
                expandedNavigationGroups={expandedNavigationGroups}
                onToggleGroup={toggleNavigationGroup}
              />
              {onLogout ? (
                <button type="button" className="shellMoreLogout" onClick={onLogout}>
                  <span className="shellNavIcon">
                    <LogOut aria-hidden="true" />
                  </span>
                  <span>退出登录</span>
                </button>
              ) : null}
            </nav>
          </section>
        </div>
      ) : null}
    </div>
  )
}

export function DashboardPage({ embedded = false, permissions = new Set<string>(), housingAvailable = false, isSuperAdmin = false }: { embedded?: boolean; permissions?: ReadonlySet<string>; housingAvailable?: boolean; isSuperAdmin?: boolean } = {}) {
  const entries = [
    { permission: "people.list", to: "/people", title: "人员与实名档案", action: "进入人员中心", description: "人员档案与业务归属", icon: Users },
    { permission: "housing.read", to: "/housing", title: "住宿管理", action: "进入住宿管理", description: "查看房态、入住与预约", icon: BedDouble },
    { permission: "teaching.read", to: "/teaching/cohorts", title: "招生教学", action: "进入招生教学", description: "营期、班级与教学安排", icon: BookOpenCheck },
    { permission: "admission_cases.read", to: "/teaching/admission-cases", title: "招生线索", action: "进入招生线索", description: "跟进招生与入学进度", icon: ClipboardList },
    { permission: "payments.read", to: "/teaching/payments", title: "收款管理", action: "进入收款管理", description: "查看学费与收款记录", icon: WalletCards },
    { permission: "assets.read", to: "/assets", title: "资产台账", action: "进入资产台账", description: "资产使用、租期与归还", icon: Package },
    { permission: "procurement.read", to: "/procurement", title: "日常采购", action: "进入日常采购", description: "采购申请与日常物资", icon: ShoppingCart },
    { permission: "travel_reports.read", to: "/travel-reports", title: "订票报表", action: "进入订票报表", description: "出行安排与报表查询", icon: PlaneTakeoff },
    { permission: "revenue_share.finance", to: "/finance/revenue-share", title: "财务结算", action: "进入财务结算", description: "查看结算与分成记录", icon: Landmark },
    { permission: "enrollments.read", to: "/teaching/enrollments", title: "学员入学", action: "进入学员入学", description: "查看已授权的入学信息", icon: ClipboardPenLine },
    { permission: "food_preferences.export", to: "/people/food-preferences", title: "餐饮统计", action: "进入餐饮统计", description: "营期餐饮偏好与统计", icon: Utensils },
    { permission: "accounts.read", to: "/settings/accounts", title: "账号与细分权限", action: "进入账号管理", description: "账号状态与访问权限", icon: UserCog },
    { permission: "audit.read", to: "/settings/audit", title: "敏感操作审计", action: "进入安全审计", description: "查询授权范围内的操作记录", icon: ShieldCheck },
  ].filter((entry) => permissions.has(entry.permission) && (entry.permission !== "housing.read" || housingAvailable) && (entry.permission !== "audit.read" || isSuperAdmin))
  return (
    <div className={`dashboardPage${embedded ? " embedded" : ""}`}>
      {!embedded ? (
        <div className="pageHeadingRow">
          <div>
            <p className="pageEyebrow">WORKSPACE / 工作空间</p>
            <h1>工作台</h1>
            <p>从已授权的业务入口开始，处理日常运营工作。</p>
          </div>
          <span className="foundationBadge">按当前权限展示</span>
        </div>
      ) : null}
      <section className="dashboardOperations" aria-label="业务快捷入口">
        <header className="dashboardSectionHeading">
          <h2>业务快捷入口</h2>
          <p>仅展示当前账号可访问的模块</p>
        </header>
        <div className="foundationGrid">
          {entries.map(({ to, title, action, description, icon: Icon }) => (
            <article key={to}>
              <span className="dashboardEntryIcon"><Icon aria-hidden="true" /></span>
              <h3>{title}</h3>
              <p>{description}</p>
              <Link to={to} aria-label={action}>进入模块<ArrowRight aria-hidden="true" /></Link>
            </article>
          ))}
        </div>
      </section>
    </div>
  )
}
