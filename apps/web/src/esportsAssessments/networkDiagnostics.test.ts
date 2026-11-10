// @vitest-environment node
import { expect, it } from 'vitest'
import { BoundedNetworkDiagnostics } from '../../tests/helpers/esportsNetworkDiagnostics'

it('classifies unauthenticated probes and aborted navigation without private URL, ID, query or error text', () => {
  const observer = new BoundedNetworkDiagnostics()
  observer.recordResponse('https://private-host.example/api/v1/auth/me?token=private-value', 'GET', 401)
  observer.recordConsole('https://private-host.example/api/v1/auth/me?token=private-value')
  observer.recordFailure('https://private-host.example/api/v1/esports-assessments/attempts/private-identifier', 'POST', 'net::ERR_ABORTED private-sensitive-details')
  const output = observer.snapshot()
  expect(output.events).toContainEqual({ kind: 'http_error', path_category: 'auth_probe', method: 'GET', status: 401, failure_category: null, count: 1 })
  expect(output.events).toContainEqual({ kind: 'console_error', path_category: 'auth_probe', method: 'NONE', status: null, failure_category: null, count: 1 })
  expect(output.events).toContainEqual({ kind: 'request_failed', path_category: 'assessment', method: 'POST', status: null, failure_category: 'aborted_navigation', count: 1 })
  expect(JSON.stringify(output)).not.toMatch(/private|https|token|ERR_ABORTED/)
})

it('bounds category bins and per-bin counters without dropping the original error booleans elsewhere', () => {
  const observer = new BoundedNetworkDiagnostics()
  for (let status = 400; status < 500; status++) observer.recordResponse('https://private.example/unknown', 'GET', status)
  expect(observer.snapshot().events).toHaveLength(24)
  expect(observer.snapshot().dropped_count).toBe(76)
  const repeated = new BoundedNetworkDiagnostics()
  for (let i = 0; i < 1200; i++) repeated.recordResponse('https://private.example/api/v1/auth/me', 'GET', 401)
  expect(repeated.snapshot().events[0].count).toBe(999)
  expect(repeated.snapshot().dropped_count).toBe(201)
})

it('ignores non-error statuses and normalizes unrecognized values to fixed enums', () => {
  const observer = new BoundedNetworkDiagnostics()
  for (const status of [200, 302, NaN, 600, 401.5]) observer.recordResponse('https://private.example/', 'GET', status)
  expect(observer.snapshot().events).toEqual([])
  observer.recordFailure('not-a-url-with-private-data', 'PRIVATE_METHOD', 'private error details')
  expect(observer.snapshot().events).toEqual([{ kind: 'request_failed', path_category: 'other', method: 'OTHER', status: null, failure_category: 'other', count: 1 }])
})
