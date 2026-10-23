import type { ReactNode } from "react"

import "./talentTests.css"

type GameChromeProps = {
  title: string
  instructions: string
  children: ReactNode
  statusText: string | null
}

export function GameChrome({
  title,
  instructions,
  children,
  statusText,
}: GameChromeProps) {
  return (
    <section className="talent-game-chrome" aria-labelledby="talent-game-title">
      <header>
        <p className="talent-game-kicker">天赋训练</p>
        <h1 id="talent-game-title">{title}</h1>
        <p>{instructions}</p>
      </header>
      {statusText ? <p className="talent-game-status" role="status">{statusText}</p> : null}
      <div className="talent-game-stage">{children}</div>
    </section>
  )
}
