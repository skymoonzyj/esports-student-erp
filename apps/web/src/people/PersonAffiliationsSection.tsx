import { lazy, Suspense, useCallback, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"

import { ModuleErrorBoundary } from "../shared/ModuleErrorBoundary"
import type { PersonDetail } from "./api"
import type { PersonAssignmentsView } from "./personAssignmentsApi"
import "./personAffiliations.css"

const PersonAssignmentsPanel = lazy(() => import("./PersonAssignmentsPanel").then(module => ({ default: module.PersonAssignmentsPanel })))
const PersonTeamMembershipPanel = lazy(() => import("./PersonTeamMembershipPanel").then(module => ({ default: module.PersonTeamMembershipPanel })))

export function PersonAffiliationsSection({ detail }: { detail: PersonDetail }) {
  const client = useQueryClient()
  const [expanded, setExpanded] = useState(false)
  const [businessKind, setBusinessKind] = useState<PersonAssignmentsView["business_kind"]>(null)
  const loaded = useCallback((view: PersonAssignmentsView) => setBusinessKind(view.business_kind), [])
  const saved = useCallback((view: PersonAssignmentsView) => {
    setBusinessKind(view.business_kind)
    void client.invalidateQueries({ queryKey: ["person", detail.id] })
    void client.invalidateQueries({ queryKey: ["people"] })
    void client.invalidateQueries({ queryKey: ["people-counts"] })
  }, [client, detail.id])
  const teamSaved = useCallback(() => {
    void client.invalidateQueries({ queryKey: ["person", detail.id] })
    void client.invalidateQueries({ queryKey: ["people"] })
    void client.invalidateQueries({ queryKey: ["people-counts"] })
  }, [client, detail.id])
  const hasPlayer = businessKind === "player" || businessKind === "both" ||
    detail.assisted_kind === "player" || detail.assisted_kind === "both" ||
    detail.roles?.some(role => role.key === "team_member") || Boolean(detail.team?.current)

  return <section className="detailCard personAffiliations" aria-label="人员身份与归属设置">
    <div className="detailCardHeader">
      <button type="button" className="secondaryButton" aria-expanded={expanded} aria-controls="person-affiliations-content" onClick={() => setExpanded(value => !value)}>身份与归属</button>
      <span className="formHint">按权限维护学员项目、负责教练、队伍及账号角色</span>
    </div>
    {expanded ? <div id="person-affiliations-content">
      <p className="formHint">各部分独立保存。收起此区域会放弃尚未保存的修改，不影响已保存记录。</p>
      <ModuleErrorBoundary moduleName="身份与归属">
        <Suspense fallback={<p role="status">正在加载身份与归属…</p>}>
          <PersonAssignmentsPanel personId={detail.id} onLoaded={loaded} onSaved={saved} />
          {hasPlayer && detail.team ? <PersonTeamMembershipPanel personId={detail.id} projection={detail.team} onSaved={teamSaved} /> : null}
        </Suspense>
      </ModuleErrorBoundary>
    </div> : null}
  </section>
}
