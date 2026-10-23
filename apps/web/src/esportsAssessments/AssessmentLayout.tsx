import { Outlet } from 'react-router-dom'
import type { ReactNode } from 'react'

import './assessment.css'

export function AssessmentLayout({ children }: { children?: ReactNode }) {
  return (
    <main className="assessmentLayout">
      {children ?? <Outlet />}
    </main>
  )
}

export default AssessmentLayout
