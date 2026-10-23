import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./styles/tokens.css"
import "./styles/global.css"
import "./styles/shared-controls.css"

import { AppProviders } from "./app/providers"
import { AppRouter } from "./app/router"
import { installModuleLoadEvents } from "./app/loading/moduleLoadEvents"

const root = document.getElementById("root")
if (!root) throw new Error("Application root was not found")

const uninstallModuleLoadEvents = installModuleLoadEvents()
if (import.meta.hot) import.meta.hot.dispose(uninstallModuleLoadEvents)

createRoot(root).render(
  <StrictMode>
    <AppProviders>
      <AppRouter />
    </AppProviders>
  </StrictMode>,
)
