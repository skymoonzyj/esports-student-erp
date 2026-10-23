import { useCallback, useEffect, useMemo, useRef, useState } from "react"

export type HousingMutationCoordinator = {
  pending: boolean
  isPending: () => boolean
  acquire: () => symbol | null
  release: (token: symbol) => void
}

export function useHousingMutationCoordinator(): HousingMutationCoordinator {
  const activeTokenRef = useRef<symbol | null>(null)
  const mountedRef = useRef(false)
  const [pending, setPending] = useState(false)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  const acquire = useCallback((): symbol | null => {
    if (activeTokenRef.current) return null
    const token = Symbol("housing-mutation")
    activeTokenRef.current = token
    if (mountedRef.current) setPending(true)
    return token
  }, [])

  const isPending = useCallback((): boolean => activeTokenRef.current !== null, [])

  const release = useCallback((token: symbol): void => {
    if (activeTokenRef.current !== token) return
    activeTokenRef.current = null
    if (mountedRef.current) setPending(false)
  }, [])

  return useMemo(() => ({ pending, isPending, acquire, release }), [acquire, isPending, pending, release])
}
