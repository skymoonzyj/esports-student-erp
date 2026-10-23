import { Suspense } from "react"
import { createLazyModule } from "../app/loading/lazyModule"
import { ModuleBoundary } from "../app/loading/ModuleBoundary"
import { loadRouteStyles } from "../app/routeStyles"

const Requests = createLazyModule("assisted-requests", async () => {
  await loadRouteStyles("AssistedRequestsPage")
  return { default: (await import("./MyAssistedRequestsPage")).AssistedRequestsPage }
})
const Create = createLazyModule("assisted-create", async () => {
  await loadRouteStyles("AssistedDirectCreatePage")
  return { default: (await import("./AssistedDirectCreatePage")).AssistedDirectCreatePage }
})
const Managed = createLazyModule("assisted-managed", async () => {
  await loadRouteStyles("ManagedPeoplePage")
  return { default: (await import("./ManagedPeoplePage")).ManagedPeoplePage }
})

/** Each page reuses the live account-keyed context, independently of people.list. */
export function AssistedPeopleRoutes({ view }: { view: "mine" | "review" | "create" | "managed" }) {
  switch (view) {
    case "mine":
    case "review": return <ModuleBoundary moduleId="assisted-requests" retry={Requests.retry}><Suspense fallback={<p role="status">正在加载申请工作区…</p>}><Requests.Component view={view} /></Suspense></ModuleBoundary>
    case "create": return <ModuleBoundary moduleId="assisted-create" retry={Create.retry}><Suspense fallback={<p role="status">正在加载建档表单…</p>}><Create.Component /></Suspense></ModuleBoundary>
    case "managed": return <ModuleBoundary moduleId="assisted-managed" retry={Managed.retry}><Suspense fallback={<p role="status">正在加载所属人员…</p>}><Managed.Component /></Suspense></ModuleBoundary>
  }
}
