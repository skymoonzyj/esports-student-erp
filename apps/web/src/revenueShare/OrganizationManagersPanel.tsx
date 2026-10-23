import { useState } from "react"
import type { RevenueShareOrganization } from "./api"
import { OrganizationManagersDialog } from "./OrganizationManagersDialog"

type Props = { accountId: string; organizations: RevenueShareOrganization[]; canManageManagers: boolean }

export function OrganizationManagersPanel(props: Props) {
  if (!props.canManageManagers) return null
  return <ManagersPanel key={props.accountId} {...props} />
}

function ManagersPanel({ accountId, organizations }: Props) {
  const [organizationId, setOrganizationId] = useState<string | null>(null)
  const organization = organizations.find((item) => item.id === organizationId)
  return (
    <section className="organizationManagersPanel" aria-label="邀请机构负责人">
      <h3>邀请机构负责人</h3>
      <p>负责人只能查看本机构已有受邀人员的住宿，不会获得财务或住房管理权限。</p>
      {organizations.length === 0 ? <p>暂无邀请机构</p> : (
        <ul className="organizationManagersOrganizations">
          {organizations.map((item) => <li key={item.id}><span>{item.name} · {item.is_active ? "使用中" : "已停用"}</span><button className="secondaryButton" type="button" aria-label={`查看负责人：${item.name}`} onClick={() => setOrganizationId(item.id)}>查看负责人</button></li>)}
        </ul>
      )}
      {organization ? <OrganizationManagersDialog accountId={accountId} organization={organization} onClose={() => setOrganizationId(null)} /> : null}
    </section>
  )
}
