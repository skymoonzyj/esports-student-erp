import { act, fireEvent, render, screen } from '@testing-library/react'
import { createContext, StrictMode, Suspense, useContext, useEffect, useState, type ComponentType } from 'react'
import { expect, it, vi } from 'vitest'
import { createLazyModule } from './lazyModule'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

it('controlled pending commits normally without suspending or mounting/querying the page before CSS and import finish', async () => {
  const css = deferred<void>()
  const code = deferred<{ default: ComponentType<object> }>()
  const pageImport = vi.fn(() => code.promise)
  const query = vi.fn()
  const module = createLazyModule('controlled-css', async () => { await css.promise; return pageImport() }, 'controlled')
  function Page() { useEffect(() => { query() }, []); return <p>真实页面</p> }
  const committed = vi.fn()
  function Sibling() { useEffect(() => { committed() }, []); return <p>保留内容</p> }
  render(<Suspense fallback={<p>外层挂起</p>}><Sibling /><module.Component /></Suspense>)
  expect(screen.getByRole('status')).toHaveTextContent('正在加载页面…')
  expect(screen.queryByText('外层挂起')).not.toBeInTheDocument()
  expect(screen.getByText('保留内容')).toBeVisible()
  expect(committed).toHaveBeenCalledOnce()
  expect(pageImport).not.toHaveBeenCalled()
  expect(query).not.toHaveBeenCalled()
  await act(async () => css.resolve())
  expect(pageImport).toHaveBeenCalledOnce()
  expect(query).not.toHaveBeenCalled()
  await act(async () => code.resolve({ default: Page }))
  expect(screen.getByText('真实页面')).toBeVisible()
  expect(query).toHaveBeenCalledOnce()
})

it('StrictMode and simultaneous subscribers share one attempt and use current props/context with stable loaded identity', async () => {
  const code = deferred<{ default: ComponentType<{ label: string }> }>()
  const loader = vi.fn(() => code.promise)
  const module = createLazyModule('current', loader, 'controlled')
  const Identity = createContext('old')
  function Page({ label }: { label: string }) {
    const account = useContext(Identity)
    const [draft, setDraft] = useState('')
    return <input aria-label={`${account}:${label}`} value={draft} onChange={e => setDraft(e.target.value)} />
  }
  function Tree({ value }: { value: string }) { return <StrictMode><Identity value={value}><module.Component label={value} /><module.Component label="second" /></Identity></StrictMode> }
  module.prepare!()
  module.prepare!()
  expect(loader).toHaveBeenCalledOnce()
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  const view = render(<Tree value="old" />)
  expect(loader).toHaveBeenCalledOnce()
  expect(screen.getAllByRole('status')).toHaveLength(2)
  view.rerender(<Tree value="current" />)
  await act(async () => code.resolve({ default: Page }))
  const input = screen.getByLabelText('current:current')
  expect(screen.queryByLabelText('old:old')).not.toBeInTheDocument()
  fireEvent.change(input, { target: { value: '保留草稿' } })
  act(() => module.retry())
  view.rerender(<Tree value="next" />)
  expect(screen.getByLabelText('next:next')).toBe(input)
  expect(input).toHaveValue('保留草稿')
  expect(loader).toHaveBeenCalledOnce()
  module.prepare!()
  expect(loader).toHaveBeenCalledOnce()
})

it.each(['unmount', 'revocation'] as const)('denied guards do not load and late completion after %s cannot mount private content', async reason => {
  const code = deferred<{ default: ComponentType<object> }>()
  const loader = vi.fn(() => code.promise)
  const mounted = vi.fn()
  const module = createLazyModule('guard', loader, 'controlled')
  function Private() { useEffect(() => { mounted() }, []); return <p>私密页面</p> }
  function Guard({ allowed }: { allowed: boolean }) { return allowed ? <module.Component /> : <p>没有访问权限</p> }
  const view = render(<Guard allowed={false} />)
  expect(loader).not.toHaveBeenCalled()
  view.rerender(<Guard allowed />)
  expect(loader).toHaveBeenCalledOnce()
  if (reason === 'unmount') view.unmount()
  else view.rerender(<Guard allowed={false} />)
  await act(async () => code.resolve({ default: Private }))
  expect(mounted).not.toHaveBeenCalled()
  expect(screen.queryByText('私密页面')).not.toBeInTheDocument()
})
