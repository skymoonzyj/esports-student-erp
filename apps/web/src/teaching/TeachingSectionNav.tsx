import { NavLink } from "react-router-dom"

type SectionLink = {
  to: string
  label: string
  permission: string
  globalOnly?: boolean
}

const sectionLinks: readonly SectionLink[] = [
  { to: "/teaching/cohorts", label: "营期", permission: "teaching.read" },
  {
    to: "/teaching/catalog",
    label: "项目与队伍",
    permission: "teaching.read",
    globalOnly: true,
  },
  {
    to: "/teaching/payments",
    label: "付款与退款",
    permission: "payments.read",
    globalOnly: true,
  },
  {
    to: "/teaching/admission-cases",
    label: "待分配",
    permission: "admission_cases.read",
  },
  {
    to: "/teaching/enrollments",
    label: "正式报名",
    permission: "enrollments.read",
  },
]

export function TeachingSectionNav({
  permissions,
  scopedPermissions = new Set<string>(),
}: {
  permissions: ReadonlySet<string>
  scopedPermissions?: ReadonlySet<string>
}) {
  const visibleLinks = sectionLinks.filter(
    (item) =>
      permissions.has(item.permission) ||
      (!item.globalOnly && scopedPermissions.has(item.permission)),
  )

  if (visibleLinks.length === 0) return null

  return (
    <nav className="teachingSectionNav" aria-label="招生教学二级导航">
      {visibleLinks.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) =>
            `teachingSectionNavLink${isActive ? " active" : ""}`
          }
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  )
}
