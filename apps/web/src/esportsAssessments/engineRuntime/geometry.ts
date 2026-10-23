import type { Rect, Size, Vector2 } from './types'

export function containsRect(outer: Rect, inner: Rect): boolean {
  return inner.x >= outer.x
    && inner.y >= outer.y
    && inner.x + inner.width <= outer.x + outer.width
    && inner.y + inner.height <= outer.y + outer.height
}

export function overlaps(left: Rect, right: Rect): boolean {
  return left.x < right.x + right.width
    && left.x + left.width > right.x
    && left.y < right.y + right.height
    && left.y + left.height > right.y
}

export function containsPoint(rect: Rect, point: Vector2): boolean {
  return point.x >= rect.x
    && point.x <= rect.x + rect.width
    && point.y >= rect.y
    && point.y <= rect.y + rect.height
}

export function clientToLogical(point: Vector2, stage: Rect, logicalSize: Size): Vector2 {
  if (stage.width <= 0 || stage.height <= 0 || logicalSize.width <= 0 || logicalSize.height <= 0) {
    throw new Error('geometry dimensions must be positive')
  }
  return {
    x: (point.x - stage.x) * logicalSize.width / stage.width,
    y: (point.y - stage.y) * logicalSize.height / stage.height,
  }
}

export function sameStageGeometry(previous: Rect, current: Rect, tolerancePx = 0.01): boolean {
  return Math.abs(previous.width - current.width) <= tolerancePx
    && Math.abs(previous.height - current.height) <= tolerancePx
}
