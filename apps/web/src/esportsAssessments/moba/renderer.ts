import type { SceneSnapshot } from '../engineRuntime/types'

export interface MobaRenderer {
  render(scene: SceneSnapshot | null): void
  resize(width: number, height: number, dpr: number): void
  dispose(): void
}

/** Rendering only: the scene is never mutated and no gameplay clock is created. */
export function createMobaRenderer(canvas: HTMLCanvasElement): MobaRenderer {
  const context = canvas.getContext('2d')
  if (!context) throw new Error('Canvas 2D is unavailable')
  const ctx = context
  let width = 1000, height = 562.5, dpr = 1, disposed = false

  function circle(x: number, y: number, radius: number, fill: string, dashed = false) {
    ctx.beginPath()
    ctx.setLineDash(dashed ? [5, 4] : [])
    ctx.arc(x, y, radius, 0, Math.PI * 2)
    ctx.fillStyle = fill
    ctx.fill()
    ctx.strokeStyle = '#dce8f4'
    ctx.lineWidth = 2
    ctx.stroke()
    ctx.setLineDash([])
  }

  function clear() {
    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.clearRect(0, 0, canvas.width, canvas.height)
  }

  return {
    resize(nextWidth, nextHeight, nextDpr) {
      if (disposed) return
      if (![nextWidth, nextHeight, nextDpr].every(v => Number.isFinite(v) && v > 0) || nextDpr > 16) throw new Error('invalid Canvas resolution')
      width = nextWidth; height = nextHeight; dpr = nextDpr
      // Assigning either property resets the context; do not recreate the backing store every scene frame.
      const backingWidth = Math.round(width * dpr), backingHeight = Math.round(height * dpr)
      if (canvas.width !== backingWidth) canvas.width = backingWidth
      if (canvas.height !== backingHeight) canvas.height = backingHeight
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
    },
    render(scene) {
      if (disposed) return
      if (scene !== null && scene.kind !== 'moba') throw new Error('MOBA scene required')
      clear()
      ctx.fillStyle = '#0b1420'
      ctx.fillRect(0, 0, canvas.width, canvas.height)
      if (!scene) return
      const scale = Math.min(width / scene.logical_size.width, height / scene.logical_size.height)
      const left = (width - scene.logical_size.width * scale) / 2
      const top = (height - scene.logical_size.height * scale) / 2
      ctx.setTransform(scale * dpr, 0, 0, scale * dpr, left * dpr, top * dpr)
      ctx.save()
      ctx.beginPath()
      ctx.rect(0, 0, scene.logical_size.width, scene.logical_size.height)
      ctx.clip()
      ctx.strokeStyle = '#1b2d3f'
      ctx.lineWidth = 1
      for (let x = 0; x <= scene.logical_size.width; x += 50) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, scene.logical_size.height); ctx.stroke() }
      for (let y = 0; y <= scene.logical_size.height; y += 50) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(scene.logical_size.width, y); ctx.stroke() }
      ctx.font = '15px sans-serif'
      ctx.textAlign = 'center'
      const currentView = scene.feedback?.version === 'v2'
      const danger = scene.danger_choice
      const zone = danger?.safe_zone
      if (danger && zone) {
        ctx.save()
        if (danger.phase === 'warning' || danger.phase === 'active') {
          ctx.beginPath()
          ctx.rect(0, 0, scene.logical_size.width, scene.logical_size.height)
          ctx.moveTo(zone.position.x + zone.radius, zone.position.y)
          ctx.arc(zone.position.x, zone.position.y, zone.radius, 0, Math.PI * 2)
          ctx.fillStyle = danger.phase === 'warning' ? 'rgba(255, 209, 122, 0.22)' : 'rgba(190, 42, 58, 0.32)'
          ctx.fill('evenodd')
          if (danger.phase === 'warning') {
            ctx.save()
            ctx.clip('evenodd')
            ctx.strokeStyle = 'rgba(255, 209, 122, 0.48)'
            ctx.lineWidth = 1
            for (let x = -scene.logical_size.height; x < scene.logical_size.width; x += 32) {
              ctx.beginPath()
              ctx.moveTo(x, 0)
              ctx.lineTo(x + scene.logical_size.height, scene.logical_size.height)
              ctx.stroke()
            }
            ctx.restore()
          }
        }
        ctx.beginPath()
        ctx.setLineDash(danger.phase === 'warning' ? [8, 6] : [])
        ctx.arc(zone.position.x, zone.position.y, zone.radius, 0, Math.PI * 2)
        ctx.strokeStyle = danger.phase === 'active' ? '#ff6777' : danger.phase === 'resolved' ? '#8ea2b5' : '#ffd17a'
        ctx.lineWidth = 3
        ctx.stroke()
        ctx.setLineDash([])
        ctx.fillStyle = '#ffffff'
        ctx.fillText(danger.phase === 'warning' ? '安全圆' : danger.phase === 'active' ? '危险爆发' : '本轮危险已结束', zone.position.x, zone.position.y - zone.radius - 10)
        ctx.restore()
      }
      for (const target of scene.targets) {
        if (target.state === 'hidden') continue
        const checkpoint = target.type === 'checkpoint'
        const done = target.state === 'hit' || target.state === 'resolved'
        circle(target.position.x, target.position.y, target.radius, done ? '#344453' : checkpoint ? '#184c46' : '#873f51', checkpoint)
        ctx.fillStyle = '#ffffff'
        const label = currentView
          ? checkpoint ? '终点' : target.type === 'main' ? '主目标' : target.type === 'minion' ? '补刀目标' : scene.feedback?.task_id === 'skill-aim' ? '移动目标' : '攻击目标'
          : checkpoint ? '到位' : target.type === 'main' ? '点击' : target.type === 'minion' ? '补刀' : '攻击'
        ctx.fillText(label, target.position.x, target.position.y + 5)
        if (target.health_ratio !== undefined) {
          ctx.fillStyle = '#28323e'
          ctx.fillRect(target.position.x - 25, target.position.y - target.radius - 13, 50, 6)
          ctx.fillStyle = '#62d4a2'
          ctx.fillRect(target.position.x - 25, target.position.y - target.radius - 13, 50 * Math.max(0, Math.min(1, target.health_ratio)), 6)
        }
      }
      for (const projectile of scene.projectiles) {
        if (projectile.state === 'expired' || projectile.state === 'hit') continue
        if (projectile.state === 'warning') {
          const guideColor = currentView && projectile.type === 'skill' ? '#8abaff' : '#ffd17a'
          ctx.beginPath()
          ctx.setLineDash([9, 6])
          ctx.moveTo(projectile.origin.x, projectile.origin.y)
          ctx.lineTo(projectile.target.x, projectile.target.y)
          ctx.strokeStyle = guideColor
          ctx.lineWidth = projectile.radius * 2
          ctx.stroke()
          ctx.setLineDash([])
          ctx.fillStyle = guideColor
          ctx.fillText(currentView ? projectile.type === 'skill' ? '技能瞄准' : '敌方预警' : '预警', projectile.target.x, projectile.target.y - 28)
        } else circle(projectile.position.x, projectile.position.y, projectile.radius, projectile.type === 'threat' ? '#ffa665' : '#8abaff')
      }
      const player = scene.player
      circle(player.position.x, player.position.y, player.radius, '#60a8d8')
      ctx.beginPath()
      ctx.moveTo(player.position.x, player.position.y)
      ctx.lineTo(player.position.x + Math.cos(player.facing_rad) * 20, player.position.y + Math.sin(player.facing_rad) * 20)
      ctx.strokeStyle = '#ffffff'
      ctx.stroke()
      if (currentView) {
        ctx.fillStyle = '#ffffff'
        ctx.fillText('你', player.position.x, player.position.y - player.radius - 8)
      }
      if (scene.minimap.visible) {
        const { bounds, event } = scene.minimap
        ctx.fillStyle = '#122638'
        ctx.fillRect(bounds.x, bounds.y, bounds.width, bounds.height)
        ctx.strokeStyle = '#9bb1c5'
        ctx.lineWidth = 1
        ctx.strokeRect(bounds.x, bounds.y, bounds.width, bounds.height)
        ctx.beginPath()
        ctx.moveTo(bounds.x + bounds.width / 2, bounds.y)
        ctx.lineTo(bounds.x + bounds.width / 2, bounds.y + bounds.height)
        ctx.moveTo(bounds.x, bounds.y + bounds.height / 2)
        ctx.lineTo(bounds.x + bounds.width, bounds.y + bounds.height / 2)
        ctx.stroke()
        if (event?.state === 'active') {
          const { x, y } = event.position
          ctx.beginPath(); ctx.moveTo(x, y - 12); ctx.lineTo(x + 12, y); ctx.lineTo(x, y + 12); ctx.lineTo(x - 12, y); ctx.closePath()
          ctx.fillStyle = '#ffe38b'; ctx.fill()
          ctx.fillText(`地图 ${event.quadrant + 1}`, x, y + 26)
        }
      }
      ctx.restore()
    },
    dispose() { if (!disposed) { clear(); disposed = true } },
  }
}
