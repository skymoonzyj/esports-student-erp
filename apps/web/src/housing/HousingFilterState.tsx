import { createContext, useContext, useRef } from "react"
import type { PropsWithChildren, RefObject } from "react"

import type { HousingFiltersValue } from "./api"

type HousingFilterSnapshot = {
  filters: HousingFiltersValue
  accessRevision: string
}

const HousingFilterStateContext = createContext<RefObject<HousingFilterSnapshot | null> | null>(null)

// Keep only applied UI choices above the capability guard. Private room data,
// selections and dialogs must still disappear while permission is rechecked.
// The route/account owns this memory; nothing is written to browser storage.
export function HousingFilterStateProvider({ children }: PropsWithChildren) {
  const snapshot = useRef<HousingFilterSnapshot | null>(null)
  return (
    <HousingFilterStateContext.Provider value={snapshot}>
      {children}
    </HousingFilterStateContext.Provider>
  )
}

export function useHousingFilterState() {
  return useContext(HousingFilterStateContext)
}
