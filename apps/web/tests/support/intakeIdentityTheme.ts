/** Visual-only fixture serialized by Playwright into every new document.
 * Never replaces fetch, API responses, AppearanceStore or persistent settings.
 * The real authoritative store still loads/refreshed its own value; only the
 * root CSS theme selector is kept at the case's requested A/B value.
 */
export function installIdentityTheme(theme: "clear" | "command") {
  const document = globalThis.document
  const apply = () => {
    const root = document.documentElement
    if (root && root.dataset.uiTheme !== theme) root.dataset.uiTheme = theme
  }
  // Observe the document rather than an old root, including HTML creation and
  // root replacement. The equality guard prevents our own mutation looping.
  const observer = new globalThis.MutationObserver(apply)
  observer.observe(document, { subtree: true, childList: true, attributes: true,
    attributeFilter: ["data-ui-theme"] })
  apply()
}
