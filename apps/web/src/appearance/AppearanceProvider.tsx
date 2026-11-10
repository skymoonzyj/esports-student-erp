import { createContext, useContext, useEffect, useSyncExternalStore } from "react"
import type { PropsWithChildren } from "react"
import { appearanceStore } from "./store"
import type { AppearanceStore } from "./store"

const AppearanceContext = createContext(appearanceStore)

export function AppearanceProvider({ children, store = appearanceStore }: PropsWithChildren<{ store?: AppearanceStore }>) {
  useEffect(() => store.start(), [store])
  return <AppearanceContext.Provider value={store}>{children}</AppearanceContext.Provider>
}

export function useAppearance() {
  const store = useContext(AppearanceContext)
  const appearance = useSyncExternalStore(store.subscribe, store.getSnapshot)
  const authoritativeAppearance = useSyncExternalStore(store.subscribe, store.getAuthoritativeSnapshot)
  return { appearance, authoritativeAppearance, store }
}
