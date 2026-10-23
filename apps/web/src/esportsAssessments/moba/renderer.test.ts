import { afterEach, expect, it, vi } from 'vitest'
import { fixtureConfig, fixtureCurrentConfig, fixtureSettings, fixtureV3Config } from '../testSupport/fixtures'
import { createMapEngine } from './mapAwareness'
import { createDodgeEngine } from './dodge'
import { createMobaRenderer } from './renderer'
import { createRhythmEngine } from './rhythm'
import { createSkillAimEngine } from './skillAim'
import { pcMobaFactory } from '../suites/pcMoba'

afterEach(() => vi.restoreAllMocks())

function canvasContext() {
  return { setTransform: vi.fn(), clearRect: vi.fn(), fillRect: vi.fn(), strokeRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), arc: vi.fn(), fill: vi.fn(), stroke: vi.fn(), closePath: vi.fn(), fillText: vi.fn(), save: vi.fn(), restore: vi.fn(), rect: vi.fn(), clip: vi.fn(), setLineDash: vi.fn() }
}

it('renders readonly geometry with shape/text markers and keeps DPR out of logical geometry', () => {
  const context = canvasContext()
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D)
  const canvas = document.createElement('canvas')
  const widthSetter = vi.spyOn(canvas, 'width', 'set')
  const renderer = createMobaRenderer(canvas)
  const game = createMapEngine(fixtureConfig('pc-moba', 'map-awareness'), 123, fixtureSettings('pc-moba'))
  game.advanceTo(3000)
  const scene = game.scene()
  const before = structuredClone(scene)
  renderer.resize(800, 450, 1)
  renderer.render(scene)
  expect(canvas.width).toBe(800)
  expect(canvas.height).toBe(450)
  renderer.resize(800, 450, 1)
  expect(widthSetter).toHaveBeenCalledTimes(1)
  const logicalCircles = context.arc.mock.calls.map(call => [...call])
  context.arc.mockClear()
  renderer.resize(800, 450, 2)
  renderer.render(scene)
  expect(canvas.width).toBe(1600)
  expect(canvas.height).toBe(900)
  expect(canvas.style.width).toBe('800px')
  expect(context.arc.mock.calls).toEqual(logicalCircles)
  expect(context.fillText.mock.calls.some(call => String(call[0]).includes('地图'))).toBe(true)
  expect(scene).toEqual(before)
  renderer.dispose()
  const count = context.fillRect.mock.calls.length
  renderer.render(scene)
  expect(context.fillRect).toHaveBeenCalledTimes(count)
})

it('fails clearly when Canvas 2D is unavailable and rejects invalid resolution', () => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
  expect(() => createMobaRenderer(document.createElement('canvas'))).toThrow('Canvas 2D')
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(canvasContext() as unknown as CanvasRenderingContext2D)
  const renderer = createMobaRenderer(document.createElement('canvas'))
  expect(() => renderer.resize(0, 450, 1)).toThrow()
  expect(() => renderer.resize(800, 450, Number.NaN)).toThrow()
})

it('does not paint an unrelated map over a dodge player and warning at a legal world corner', () => {
  const context = canvasContext()
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D)
  const game = createDodgeEngine(fixtureConfig('pc-moba', 'dodge'), 123, fixtureSettings('pc-moba'))
  game.input({ kind: 'point', at_ms: 0, x: 1000, y: 0, button: 2 })
  game.advanceTo(4000)
  const scene = game.scene()
  if (scene.kind !== 'moba') throw new Error('scene')
  expect(scene.player.position).toEqual({ x: 988, y: 12 })
  const renderer = createMobaRenderer(document.createElement('canvas'))
  renderer.resize(1000, 562.5, 1)
  renderer.render(scene)
  expect(context.arc).toHaveBeenCalledWith(988, 12, 12, 0, Math.PI * 2)
  expect(context.fillText.mock.calls.some(call => call[0] === '预警')).toBe(true)
  expect(context.fillRect).not.toHaveBeenCalledWith(820, 0, 180, 110)
  expect(context.strokeRect).not.toHaveBeenCalled()
  expect(context.fillText.mock.calls.some(call => String(call[0]).includes('地图'))).toBe(false)
})

it.each([0, 3000, 4000])('keeps the attention map panel visible at %i ms, including between events', time => {
  const context = canvasContext()
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D)
  const game = createMapEngine(fixtureConfig('pc-moba', 'map-awareness'), 123, fixtureSettings('pc-moba'))
  game.advanceTo(time)
  const renderer = createMobaRenderer(document.createElement('canvas'))
  renderer.resize(1000, 562.5, 1)
  renderer.render(game.scene())
  expect(context.fillRect).toHaveBeenCalledWith(820, 0, 180, 110)
  expect(context.strokeRect).toHaveBeenCalledWith(820, 0, 180, 110)
  expect(context.fillText.mock.calls.some(call => String(call[0]).includes('地图'))).toBe(time === 3000)
})

it('explicit hidden visibility suppresses even a populated map event and label', () => {
  const context = canvasContext()
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D)
  const game = createMapEngine(fixtureConfig('pc-moba', 'map-awareness'), 123, fixtureSettings('pc-moba'))
  game.advanceTo(3000)
  const scene = game.scene()
  if (scene.kind !== 'moba' || scene.minimap.event === null) throw new Error('scene')
  const renderer = createMobaRenderer(document.createElement('canvas'))
  renderer.render({ ...scene, minimap: { ...scene.minimap, visible: false } })
  expect(context.fillRect).not.toHaveBeenCalledWith(820, 0, 180, 110)
  expect(context.strokeRect).not.toHaveBeenCalled()
  expect(context.closePath).not.toHaveBeenCalled()
  expect(context.fillText.mock.calls.some(call => String(call[0]).includes('地图'))).toBe(false)
})

it('uses task-specific v2 labels while leaving frozen v1 labels unchanged', () => {
  const context = canvasContext()
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D)
  const renderer = createMobaRenderer(document.createElement('canvas'))

  const legacy = createRhythmEngine(fixtureConfig('pc-moba', 'orb-walk'), 123, fixtureSettings('pc-moba'))
  renderer.render(legacy.scene())
  expect(context.fillText.mock.calls.map(call => call[0])).toEqual(expect.arrayContaining(['攻击', '到位']))

  context.fillText.mockClear()
  const current = createRhythmEngine(fixtureCurrentConfig('pc-moba', 'orb-walk'), 123, fixtureSettings('pc-moba'))
  renderer.render(current.scene())
  const labels = context.fillText.mock.calls.map(call => call[0])
  expect(labels).toEqual(expect.arrayContaining(['攻击目标', '终点', '你']))
  expect(labels).not.toEqual(expect.arrayContaining(['攻击', '到位']))
})

it('distinguishes a v2 skill aiming guide from a hostile dodge warning', () => {
  const context = canvasContext()
  const strokeColors: string[] = []
  let strokeStyle = ''
  Object.defineProperty(context, 'strokeStyle', { configurable: true, get: () => strokeStyle, set: value => { strokeStyle = String(value); strokeColors.push(strokeStyle) } })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D)
  const renderer = createMobaRenderer(document.createElement('canvas'))

  const skill = createSkillAimEngine(fixtureCurrentConfig('mobile-moba', 'skill-aim'), 123, fixtureSettings('mobile-moba'))
  const skillScene = skill.scene()
  if (skillScene.kind !== 'moba') throw new Error('scene')
  skill.input({ kind: 'skill', at_ms: 0, ...skillScene.targets[0].position, release: false })
  renderer.render(skill.scene())
  expect(context.fillText.mock.calls.some(call => call[0] === '技能瞄准')).toBe(true)
  expect(context.fillText.mock.calls.some(call => call[0] === '敌方预警')).toBe(false)
  expect(strokeColors).toContain('#8abaff')
  expect(strokeColors).not.toContain('#ffd17a')

  context.fillText.mockClear()
  strokeColors.length = 0
  const dodge = createDodgeEngine(fixtureCurrentConfig('pc-moba', 'dodge'), 123, fixtureSettings('pc-moba'))
  renderer.render(dodge.scene())
  expect(context.fillText.mock.calls.some(call => call[0] === '敌方预警')).toBe(true)
  expect(context.fillText.mock.calls.some(call => call[0] === '技能瞄准')).toBe(false)
  expect(strokeColors).toContain('#ffd17a')
  expect(strokeColors).not.toContain('#8abaff')

  context.fillText.mockClear()
  strokeColors.length = 0
  const legacy = createSkillAimEngine(fixtureConfig('mobile-moba', 'skill-aim'), 123, fixtureSettings('mobile-moba'))
  const legacyScene = legacy.scene()
  if (legacyScene.kind !== 'moba') throw new Error('scene')
  legacy.input({ kind: 'skill', at_ms: 0, ...legacyScene.targets[0].position, release: false })
  renderer.render(legacy.scene())
  expect(context.fillText.mock.calls.some(call => call[0] === '预警')).toBe(true)
  expect(strokeColors).toContain('#ffd17a')
})

it('renders v3 danger phases behind the player without fabricating a waiting safe zone', () => {
  const context = canvasContext()
  let fillStyle = ''
  const areaFills: Array<{ rule: unknown; style: string }> = []
  Object.defineProperty(context, 'fillStyle', { configurable: true, get: () => fillStyle, set: value => { fillStyle = String(value) } })
  context.fill.mockImplementation(rule => { areaFills.push({ rule, style: fillStyle }) })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D)
  const renderer = createMobaRenderer(document.createElement('canvas'))
  const game = pcMobaFactory.create(fixtureV3Config('pc-moba', 'dodge'), 123, fixtureSettings('pc-moba'), { width: 1000, height: 562.5 })

  renderer.render(game.scene())
  expect(context.arc.mock.calls.some(call => call[2] === 32)).toBe(false)
  expect(areaFills.some(call => call.rule === 'evenodd')).toBe(false)

  game.advanceTo(418)
  context.moveTo.mockClear(); context.arc.mockClear(); context.fill.mockClear(); context.setLineDash.mockClear(); context.fillText.mockClear(); areaFills.length = 0
  renderer.render(game.scene())
  const warningArc = context.arc.mock.calls.find(call => call[2] === 32)
  expect(warningArc).toEqual([570, 387.85254037844385, 32, 0, Math.PI * 2])
  expect(context.moveTo).toHaveBeenCalledWith(602, 387.85254037844385)
  expect(context.moveTo.mock.invocationCallOrder.find(order => order < context.arc.mock.invocationCallOrder[context.arc.mock.calls.indexOf(warningArc!)]!)).toBeDefined()
  expect(areaFills).toContainEqual({ rule: 'evenodd', style: 'rgba(255, 209, 122, 0.22)' })
  expect(context.clip).toHaveBeenCalledWith('evenodd')
  expect(context.setLineDash).toHaveBeenCalledWith([8, 6])

  game.advanceTo(1418)
  context.arc.mockClear(); context.fill.mockClear(); context.fillText.mockClear(); areaFills.length = 0
  renderer.render(game.scene())
  expect(areaFills).toContainEqual({ rule: 'evenodd', style: 'rgba(190, 42, 58, 0.32)' })
  const zoneOrder = context.arc.mock.invocationCallOrder[context.arc.mock.calls.findIndex(call => call[2] === 32)]
  const playerOrder = context.arc.mock.invocationCallOrder[context.arc.mock.calls.findIndex(call => call[2] === 12)]
  expect(zoneOrder).toBeLessThan(playerOrder)

  game.advanceTo(1568)
  context.fill.mockClear(); context.fillText.mockClear(); areaFills.length = 0
  renderer.render(game.scene())
  expect(areaFills.some(call => call.rule === 'evenodd')).toBe(false)
  expect(context.fillText.mock.calls.some(call => call[0] === '本轮危险已结束')).toBe(true)
})

it('leaves legacy scenes without danger_choice on the frozen rendering branch', () => {
  const context = canvasContext()
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D)
  const renderer = createMobaRenderer(document.createElement('canvas'))
  const game = createDodgeEngine(fixtureCurrentConfig('pc-moba', 'dodge'), 123, fixtureSettings('pc-moba'))
  const scene = game.scene()
  expect(scene).not.toHaveProperty('danger_choice')
  renderer.render(scene)
  expect(context.fill).not.toHaveBeenCalledWith('evenodd')
  expect(context.arc.mock.calls.some(call => call[2] === 32)).toBe(false)
})
