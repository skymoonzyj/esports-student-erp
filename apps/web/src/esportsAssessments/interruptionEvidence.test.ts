import { afterEach, expect, it } from 'vitest'
import { readHostInterruption } from '../../tests/helpers/esportsInterruption'

afterEach(() => { document.body.replaceChildren() })
function alert(text: string) {
  const host = document.createElement('section')
  host.setAttribute('aria-label', '测试场景')
  const notice = document.createElement('p')
  notice.setAttribute('role', 'alert')
  notice.textContent = text
  host.append(notice)
  document.body.append(host)
}
it('identifies the observed interruption without exporting alert content', () => {
  alert('倒计时帧间隙超过 500ms，本项已中断。')
  const evidence = readHostInterruption()
  expect(evidence.interruption_countdown_gap).toBe(true)
  expect(Object.entries(evidence).filter(([key, value]) => key.startsWith('interruption_') && value)).toHaveLength(1)
  expect(Object.values(evidence).every(value => typeof value === 'boolean')).toBe(true)
})
it('does not include unknown sensitive-looking alerts or nested alerts', () => {
  alert('private-identity@example.test result=123')
  const nested = document.createElement('section')
  nested.innerHTML = '<p role="alert">窗口失去焦点，本项已中断。</p>'
  document.querySelector('[aria-label="测试场景"]')!.append(nested)
  const evidence = readHostInterruption()
  expect(Object.entries(evidence).filter(([key]) => key.startsWith('interruption_')).every(([, value]) => value === false)).toBe(true)
  expect(JSON.stringify(evidence)).not.toContain('private-identity')
})
it('reports fixed page milestones without retaining headings or private paths', () => {
  document.body.innerHTML = '<h2>自由练习</h2><section aria-label="测试场景" data-host-phase="intro"><p role="alert">private-account@example.test</p></section>'
  const evidence = readHostInterruption()
  expect(evidence.context_host_present).toBe(true)
  expect(evidence.context_host_intro).toBe(true)
  expect(evidence.context_practice_heading).toBe(true)
  expect(evidence.context_login_heading).toBe(false)
  expect(Object.values(evidence).every(value => typeof value === 'boolean')).toBe(true)
  expect(JSON.stringify(evidence)).not.toContain('private-account')
})
