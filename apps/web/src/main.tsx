import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import { AppProviders } from "./app/providers"
import { AppRouter } from "./app/router"
import { AppearanceProvider } from "./appearance/AppearanceProvider"
import { appearanceStore } from "./appearance/store"
import "./styles/tokens.css"
import "./styles/global.css"

const root = document.getElementById("root")
if (!root) throw new Error("Application root was not found")
appearanceStore.apply()

createRoot(root).render(
  <StrictMode>
    <AppearanceProvider>
      <AppProviders>
        <AppRouter />
      </AppProviders>
    </AppearanceProvider>
  </StrictMode>,
)
