import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { fileURLToPath, URL } from 'node:url'
import { chromium } from '@playwright/test'
import { rolldown } from 'rolldown'
import { observePeopleControlStyle } from '../tests/helpers/peopleControlStyle.ts'

/* global document, getComputedStyle */

const expected = { minHeight: '44px', border: 'rgb(180, 35, 61)', borderWidth: '1px' }
const css = ['styles/tokens.css', 'styles/global.css', 'styles/shared-controls.css', 'people/people.css']
  .map(file => readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8')).join('\n')

test('real RHF deferred focus cannot change an immediate unfocused style observation', { timeout: 30000 }, async t => {
  const bundle = await rolldown({ input: fileURLToPath(import.meta.resolve('react-hook-form')),
    platform: 'browser', transform: { define: { 'process.env.NODE_ENV': '"production"' } } })
  let script
  try {
    const { output } = await bundle.generate({ format: 'iife', name: 'RHF' })
    assert.equal(output.length, 1)
    script = output[0].code
  } finally { await bundle.close() }
  const browser = await chromium.launch({ headless: true, executablePath: chromium.executablePath() })
  const context = await browser.newContext({ serviceWorkers: 'block' })
  let requests = 0
  context.on('request', () => requests++)
  await context.route('**/*', route => route.abort())
  const fixture = async () => {
    const page = await context.newPage()
    await page.setContent('<main class="personFormPage"><form><div class="formGrid"><div class="formField"><label for="person-name">姓名</label><input id="person-name" name="full_name" /></div></div></form></main>')
    await page.addStyleTag({ content: css })
    await page.addScriptTag({ content: script })
    await page.evaluate(async () => {
      const input = document.querySelector('#person-name')
      const events = []
      input.addEventListener('focus', () => {
        if (events.length >= 2) return
        events.push({ ordinal: events.length + 1, active: document.activeElement === input,
          accent: getComputedStyle(input).borderTopColor === 'rgb(122, 115, 255)' })
      })
      const form = globalThis.RHF.createFormControl()
      form.register('full_name', { required: true }).ref(input)
      // The real invalid-submit callback and both real focus calls run.
      // Only delivery of its one zero-delay task is controlled, not its body.
      const original = globalThis.setTimeout
      let pending, scheduled = 0
      globalThis.setTimeout = (callback, delay, ...args) => {
        if (delay === undefined || delay === 0) {
          if (++scheduled !== 1 || typeof callback !== 'function') throw new Error('Unexpected deferred task')
          pending = () => callback(...args)
          return 1
        }
        return original(callback, delay, ...args)
      }
      try {
        await form.handleSubmit(() => { throw new Error('Expected invalid synthetic form') },
          () => input.setAttribute('aria-invalid', 'true'))()
      } finally { globalThis.setTimeout = original }
      if (scheduled !== 1 || events.length !== 1 || document.activeElement !== input) throw new Error('Missing real RHF focus scheduling')
      globalThis.__styleFocusCharacterization = {
        events,
        release() {
          if (!pending) throw new Error('Deferred task already delivered')
          const callback = pending; pending = undefined; callback()
        },
      }
    })
    return page
  }
  try {
    await t.test('controlled delivery between browser tasks exposes split observation but not atomic observation', async () => {
      const page = await fixture()
      try {
        const input = page.locator('#person-name')
        let evaluations = 0
        // Exercise the SAME observer used by frontend-style-order.spec.ts.
        // With split blur/read this runs the real deferred focus in the gap;
        // with one atomic callback it runs only after the captured observation.
        const observed = await observePeopleControlStyle({ evaluate: async (...args) => {
          const result = await input.evaluate(...args)
          if (++evaluations === 1) await page.evaluate(() => globalThis.__styleFocusCharacterization.release())
          return result
        } })
        const focusEvents = await page.evaluate(() => globalThis.__styleFocusCharacterization.events)
        assert.deepEqual(focusEvents, [{ ordinal: 1, active: true, accent: true }, { ordinal: 2, active: true, accent: true }])
        t.diagnostic(JSON.stringify({ controlledInterleaving: true, focusEvents, observation: observed.diagnostic }))
        assert.deepEqual(observed.before, expected)
        assert.equal(observed.diagnostic.inputActive, false)
        assert.equal(observed.diagnostic.ariaInvalid, true)
        assert.doesNotMatch(JSON.stringify(observed.diagnostic), /person-name|full_name|http|姓名/)
        // Keep the independent keyboard/accent contract, not just blur styling.
        await input.blur()
        await page.keyboard.press('Tab')
        await input.focus()
        const focused = await input.evaluate(element => {
          const style = getComputedStyle(element)
          return { border: style.borderTopColor, width: parseFloat(style.outlineWidth),
            outline: style.outlineStyle, shadow: style.boxShadow }
        })
        assert.equal(focused.border, 'rgb(122, 115, 255)')
        assert.equal(focused.width >= 2 && focused.outline !== 'none' || focused.shadow !== 'none', true)
      } finally { await page.close() }
    })
    for (const [property, value, field, actual] of [
      ['border-top-color', 'rgb(1, 2, 3)', 'border', 'rgb(1, 2, 3)'],
      ['min-height', '45px', 'minHeight', '45px'],
      ['border-top-width', '2px', 'borderWidth', '2px'],
    ]) await t.test(`negative control retains an actually wrong unfocused ${property}`, async () => {
      const page = await fixture()
      try {
        await page.addStyleTag({ content: `.formField [aria-invalid="true"] { ${property}: ${value} !important; }` })
        const observed = await observePeopleControlStyle(page.locator('#person-name'))
        assert.equal(observed.diagnostic.inputActive, false)
        assert.equal(observed.before[field], actual)
        assert.throws(() => assert.deepEqual(observed.before, expected), assert.AssertionError)
        t.diagnostic(JSON.stringify({ negativeControl: property, observation: observed.diagnostic, exactTupleRejected: true }))
      } finally { await page.close() }
    })
    assert.equal(requests, 0)
  } finally { await context.close(); await browser.close() }
})
