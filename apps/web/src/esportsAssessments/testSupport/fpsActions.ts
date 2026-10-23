import type { GameEngine } from '../engineRuntime/types'
import { vectorToDirection } from '../fps/math'

export function aimHighlighted(game: GameEngine, at_ms: number): void {
  game.input({ kind: 'look', at_ms, dx: 0, dy: 0 })
  const scene = game.scene()
  if (scene.kind !== 'fps') throw new Error('expected fps')
  const target = scene.targets.find(candidate => candidate.state === 'highlighted')
  if (!target) throw new Error('missing target')
  const aim = vectorToDirection({
    x: target.position.x - scene.camera.position.x,
    y: target.position.y - scene.camera.position.y,
    z: target.position.z - scene.camera.position.z,
  })
  game.input({
    kind: 'look', at_ms,
    dx: aim.yaw_deg - scene.camera.yaw_deg,
    dy: scene.camera.pitch_deg - aim.pitch_deg,
  })
}

export function pulse(game: GameEngine, at_ms: number): void {
  game.input({ kind: 'fire', at_ms, pressed: true })
  game.input({ kind: 'fire', at_ms: at_ms + 1, pressed: false })
}
