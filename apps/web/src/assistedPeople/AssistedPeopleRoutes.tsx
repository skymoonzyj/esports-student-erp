import { AssistedDirectCreatePage } from "./AssistedDirectCreatePage"
import { AssistedRequestReviewPage } from "./AssistedRequestReviewPage"
import { ManagedPeoplePage } from "./ManagedPeoplePage"
import { MyAssistedRequestsPage } from "./MyAssistedRequestsPage"

/** Each page reuses the live account-keyed context, independently of people.list. */
export function AssistedPeopleRoutes({ view }: { view: "mine" | "review" | "create" | "managed" }) {
  switch (view) {
    case "mine": return <MyAssistedRequestsPage />
    case "review": return <AssistedRequestReviewPage />
    case "create": return <AssistedDirectCreatePage />
    case "managed": return <ManagedPeoplePage />
  }
}
