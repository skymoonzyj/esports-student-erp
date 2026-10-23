import {
  BedDouble,
  BookOpenCheck,
  Building2,
  CalendarDays,
  ClipboardList,
  FileSpreadsheet,
  Home,
  LogOut,
  Mail,
  MessageSquareText,
  MoreHorizontal,
  ShieldCheck,
  SlidersHorizontal,
  UserCog,
  Users,
  WalletCards,
  X,
} from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"
import type { LucideIcon } from "lucide-react"
import { NavLink, Outlet, useLocation } from "react-router-dom"

import type { PortalCapability, PortalContext } from "../portal/api"
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
  end?: boolean
  portalCapability?: PortalCapability
  globalPermission?: string
}

export const navigationItems: NavigationItem[] = [
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
    to: "/people",
    label: "人员中心",
    shortLabel: "人员",
    icon: Users,
    mobilePriority: 10,
    permission: "people.list",
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
    portalCapability: "housing_management_available",
    activePrefix: "/housing/rooms/",
    end: true,
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
    to: "/settings/accounts",
    label: "账号管理",
    shortLabel: "账号",
    icon: UserCog,
    mobilePriority: 14,
    permission: "accounts.manage",
  },
  {
    to: "/settings/permissions",
    label: "权限管理",
    shortLabel: "权限",
    icon: SlidersHorizontal,
    mobilePriority: 15,
    permission: "permissions.manage",
  },
  {
    to: "/settings/mail",
    label: "邮件设置",
    shortLabel: "邮件",
    icon: Mail,
    mobilePriority: 16,
    permission: "permissions.manage",
    globalPermission: "permissions.manage",
    activePrefix: "/settings/mail",
  },
  {
    to: "/settings/audit",
    label: "安全审计",
    shortLabel: "审计",
    icon: ShieldCheck,
    mobilePriority: 16,
    permission: "audit.read",
  },
]

type AppShellProps = {
  permissions?: ReadonlySet<string>
  globalPermissions?: ReadonlySet<string>
  portalContext?: PortalContext
  userName?: string
  onLogout?: () => void
  children?: ReactNode
}

function NavigationLinks({
  items,
  mobile = false,
  onNavigate,
}: {
  items: readonly NavigationItem[]
  mobile?: boolean
  onNavigate?: () => void
}) {
  const location = useLocation()
  return items.map((item) => {
    const Icon = item.icon
    return (
      <NavLink
        key={item.to}
        to={item.to}
        end={item.end}
        className={({ isActive }) => {
          const sectionActive =
            item.activePrefix !== undefined &&
            location.pathname.startsWith(item.activePrefix)
          return `shellNavLink${isActive || sectionActive ? " active" : ""}`
        }}
        aria-label={item.label}
        onClick={onNavigate}
      >
        <span className="shellNavIcon">
          <Icon aria-hidden="true" />
        </span>
        <span>{mobile ? item.shortLabel : item.label}</span>
      </NavLink>
    )
  })
}

export function AppShell({
  permissions = new Set<string>(),
  globalPermissions = permissions,
  portalContext,
  userName = "当前用户",
  onLogout,
  children,
}: AppShellProps) {
  const [moreOpen, setMoreOpen] = useState(false)
  const moreTriggerRef = useRef<HTMLButtonElement>(null)
  const moreSheetRef = useRef<HTMLElement>(null)
  const teachingDestination = permissions.has("teaching.read")
    ? "/teaching/cohorts"
    : permissions.has("payments.read")
      ? "/teaching/payments"
      : permissions.has("admission_cases.read")
        ? "/teaching/admission-cases"
        : "/teaching/enrollments"
  const visibleItems = navigationItems
    .filter(
      (item) =>
      (!item.permission || permissions.has(item.permission)) &&
        (!item.globalPermission || globalPermissions.has(item.globalPermission)) &&
        (!item.sectionPermissions ||
          item.sectionPermissions.some((permission) => permissions.has(permission))) &&
        (!item.portalCapability || portalContext?.[item.portalCapability] === true),
    )
    .map((item) =>
      item.sectionPermissions ? { ...item, to: teachingDestination } : item,
    )
  const mobileItems = [...visibleItems].sort(
    (left, right) => left.mobilePriority - right.mobilePriority,
  )
  const hasMobileOverflow = mobileItems.length > 4
  const mobilePrimaryItems = hasMobileOverflow ? mobileItems.slice(0, 3) : mobileItems
  const mobileOverflowItems = hasMobileOverflow ? mobileItems.slice(3) : []
  const initial = userName.trim().slice(0, 1) || "用"

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
        <nav className="shellPrimaryNavigation" aria-label="主导航">
          <NavigationLinks items={visibleItems} />
        </nav>
        <div className="shellSecurityNote">
          <span aria-hidden="true">✓</span>
          <p>
            <strong>资料安全保护中</strong>
            <small>敏感操作均留有审计记录</small>
          </p>
        </div>
      </aside>

      <div className="shellWorkspace">
        <header className="shellHeader">
          <div className="shellMobileBrand">
            <span className="shellBrandMark compact">竞</span>
            <strong>竞学 ERP</strong>
          </div>
          <div className="shellHeaderContext">
            <span className="shellStatusDot" aria-hidden="true" />
            <span>系统运行正常</span>
          </div>
          <div className="shellUser">
            <span className="shellAvatar" aria-hidden="true">
              {initial}
            </span>
            <span className="shellUserName">{userName}</span>
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

        <main className="shellContent">{children ?? <Outlet />}</main>
      </div>

      <nav className="shellMobileNavigation" aria-label="手机导航">
        <NavigationLinks items={mobilePrimaryItems} mobile />
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

export function DashboardPage({ embedded = false }: { embedded?: boolean } = {}) {
  return (
    <div className={`dashboardPage${embedded ? " embedded" : ""}`}>
      {!embedded ? (
        <div className="pageHeadingRow">
          <div>
            <p className="pageEyebrow">PLATFORM FOUNDATION</p>
            <h1>工作台</h1>
            <p>账号、人员与安全能力已经接入，业务模块将按阶段逐步开放。</p>
          </div>
          <span className="foundationBadge">平台基础阶段</span>
        </div>
      ) : null}
      <section className="foundationGrid" aria-label="平台能力概览">
        <article>
          <span className="foundationNumber">01</span>
          <h2>人员与实名档案</h2>
          <p>统一维护学员、家长、教练、生活老师和其他工作人员。</p>
        </article>
        <article>
          <span className="foundationNumber">02</span>
          <h2>账号与细分权限</h2>
          <p>角色默认权限与账号级授权并行，显式拒绝始终优先。</p>
        </article>
        <article>
          <span className="foundationNumber">03</span>
          <h2>敏感操作审计</h2>
          <p>完整手机号和身份证默认隐藏，查看时需要用途并记录日志。</p>
        </article>
      </section>
    </div>
  )
}
