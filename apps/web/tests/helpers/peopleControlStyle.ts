import type { Locator } from '@playwright/test'

export async function observePeopleControlStyle(input: Locator) {
  // Observe the immediate non-focused state in one browser task: RHF can
  // schedule another focus after invalid submit. Keep focused styling separate.
  return input.evaluate(element => {
    (element as HTMLInputElement).blur()
    const s = getComputedStyle(element)
    const px = (value: string) => /^\d{1,4}(?:\.\d{1,4})?px$/.test(value) ? value : null
    const color = /^rgba?\([\d., ]{1,48}\)$/.test(s.borderTopColor) ? s.borderTopColor : null
    const loaded = (domain: string) => [...document.styleSheets].some(sheet => !sheet.disabled
      && sheet.href !== null && new RegExp(`/assets/${domain}-[^/]+\\.css$`).test(new URL(sheet.href).pathname))
    return {
      before: { minHeight: s.minHeight, border: s.borderTopColor, borderWidth: s.borderTopWidth },
      diagnostic: { minHeight: px(s.minHeight), borderTopColor: color, borderTopWidth: px(s.borderTopWidth),
        inputActive: document.activeElement === element, ariaInvalid: element.getAttribute('aria-invalid') === 'true',
        peopleCssLoaded: loaded('people'), procurementCssLoaded: loaded('procurement'), assetsCssLoaded: loaded('assets'),
        inlineCssLoaded: [...document.styleSheets].some(sheet => !sheet.disabled && sheet.href === null) },
    }
  })
}
