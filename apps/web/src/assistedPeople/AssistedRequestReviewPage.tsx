import { AssistedRequestsPage } from "./MyAssistedRequestsPage"
import { lazy, Suspense } from "react"
const PendingRoleIntentQueue = lazy(() => import("./PendingRoleIntentPanel").then(module => ({ default: module.PendingRoleIntentQueue })))

export function AssistedRequestReviewPage() { return <><Suspense fallback={null}><PendingRoleIntentQueue /></Suspense><AssistedRequestsPage view="review" /></> }
