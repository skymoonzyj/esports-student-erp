import { useEffect, useMemo, useState } from "react"
import { Link } from "react-router-dom"

import { AnalysisProfilesPanel } from "./AnalysisProfilesPanel"
import { CohortStaffPanel } from "./CohortStaffPanel"
import { StudentMatchingPanel } from "./StudentMatchingPanel"
import type { EvaluationFoundationContext } from "./foundationApi"
import { useEvaluationAccessError } from "./useEvaluationAccessError"
import { useEvaluationContext } from "./useEvaluationContext"

import "./evaluation-foundation.css"

type AdminSection = keyof EvaluationFoundationContext["admin_sections"]

const sectionLabels: Record<AdminSection, string> = {
  cohort_staff: "工作人员",
  student_matching: "学员匹配",
  analysis_profiles: "分析配置",
}

export function EvaluationAdminPage() {
  const context = useEvaluationContext({ refetchOnMount: false })
  const accessError = useEvaluationAccessError(context.error)
  const enabled = useMemo(
    () =>
      (Object.keys(sectionLabels) as AdminSection[]).filter(
        (section) => context.data?.admin_sections[section],
      ),
    [context.data],
  )
  const [section, setSection] = useState<AdminSection>("cohort_staff")
  useEffect(() => {
    if (enabled.length && !enabled.includes(section)) setSection(enabled[0])
  }, [enabled, section])

  if (context.isPending || accessError.permissionRevoked) {
    return <div className="foundationState">正在更新基础数据权限...</div>
  }
  if (context.isError) {
    return <div className="foundationState" role="alert">{accessError.message}</div>
  }

  return (
    <div className="foundationPage evaluationAdminPage">
      <header className="foundationPageHeader">
        <div><Link to="/evaluations">返回评价中心</Link><p className="pageEyebrow">FOUNDATION ADMIN</p><h1>基础数据管理</h1></div>
      </header>
      <div className="foundationTabs" role="tablist" aria-label="基础数据管理模块">
        {enabled.map((item) => (
          <button key={item} type="button" role="tab" aria-selected={section === item} onClick={() => setSection(item)}>{sectionLabels[item]}</button>
        ))}
      </div>
      {section === "cohort_staff" && enabled.includes("cohort_staff") ? <CohortStaffPanel /> : null}
      {section === "student_matching" && enabled.includes("student_matching") ? <StudentMatchingPanel /> : null}
      {section === "analysis_profiles" && enabled.includes("analysis_profiles") ? <AnalysisProfilesPanel /> : null}
    </div>
  )
}
