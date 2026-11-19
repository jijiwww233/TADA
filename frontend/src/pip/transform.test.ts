import { describe, expect, it } from 'vitest'
import { commitTransform, TRANSFORMER_SCALE_PROPS } from './transform'

interface FakeState {
  width: number
  height: number
  scaleX: number
  scaleY: number
  x: number
  y: number
  rotation: number
}

function fakeNode(overrides: Partial<FakeState> = {}) {
  const state: FakeState = { width: 150, height: 100, scaleX: 1, scaleY: 1, x: 30, y: 40, rotation: 0, ...overrides }
  const node = {
    state,
    width: () => state.width,
    height: () => state.height,
    scaleX: () => state.scaleX,
    scaleY: () => state.scaleY,
    x: () => state.x,
    y: () => state.y,
    rotation: () => state.rotation,
    setAttrs: (attrs: Partial<FakeState>) => { Object.assign(state, attrs) },
  }
  return node
}

describe('transform commit', () => {
  it('bakes the transformer scale into width/height and resets the node scale', () => {
    const node = fakeNode({ scaleX: 2, scaleY: 1.5 })
    const patch = commitTransform(node)
    expect(patch).toMatchObject({ x: 30, y: 40, width: 300, height: 150, rotation: 0 })
    // The leftover imperative scale must be consumed here: react-konva compares against the
    // last rendered props, so an un-reset scale would multiply every later width/height commit.
    expect(node.state.scaleX).toBe(1)
    expect(node.state.scaleY).toBe(1)
    expect(node.state.width).toBe(300)
    expect(node.state.height).toBe(150)
  })

  it('keeps position and rotation from the transformer and never re-applies a crop offset', () => {
    // For a cropped item the node already renders only the cropped face: node.x/y is the
    // visible top-left, so commit must not add crop.x * width again.
    const node = fakeNode({ width: 75, height: 50, scaleX: 1.2, scaleY: 1.2, x: 120, y: 60, rotation: 90 })
    const patch = commitTransform(node)
    expect(patch).toMatchObject({ x: 120, y: 60, width: 90, height: 60, rotation: 90 })
  })

  it('clamps degenerate scales to the minimum item size', () => {
    const node = fakeNode({ scaleX: -0.01, scaleY: 0.0001 })
    const patch = commitTransform(node)
    expect(patch.width).toBeGreaterThanOrEqual(4)
    expect(patch.height).toBeGreaterThanOrEqual(4)
    expect(node.state.scaleX).toBe(1)
  })

  it('does not compound across consecutive transforms', () => {
    const node = fakeNode({ width: 100, height: 50 })
    node.state.scaleX = 1.5
    node.state.scaleY = 1.5
    commitTransform(node)
    expect(node.state.width).toBe(150)
    node.state.scaleX = 1.5
    node.state.scaleY = 1.5
    commitTransform(node)
    // A stale scale would give 150 * 1.5 * 1.5 = 337.5 instead of 225.
    expect(node.state.width).toBe(225)
    expect(node.state.scaleX).toBe(1)
  })

  it('configures the transformer for free default scaling and Shift to keep the aspect ratio', () => {
    expect(TRANSFORMER_SCALE_PROPS).toEqual({ keepRatio: false, shiftBehavior: 'default', flipEnabled: false })
  })
})
describe('crop / rotate / flip combinations', () => {
  it('crop then scale keeps the visible face exactly at the dragged size', () => {
    const node = fakeNode({ width: 300, height: 150 })
    node.state.scaleX = 1.5
    node.state.scaleY = 1.5
    const first = commitTransform(node)
    expect(first).toMatchObject({ width: 450, height: 225, rotation: 0 })
    // Simulate the board applying the commit, then cropping the scaled item in half.
    const croppedWidth = first.width * 0.5
    const croppedHeight = first.height
    node.state.width = croppedWidth
    node.state.height = croppedHeight
    node.state.x = first.x + first.width * 0.5
    node.state.scaleX = 2
    node.state.scaleY = 2
    const second = commitTransform(node)
    expect(second.width).toBe(450)
    expect(second.height).toBe(450)
    // No extra crop offset is added: x stays where the transformed node really is.
    expect(second.x).toBe(node.state.x)
    expect(node.state.scaleX).toBe(1)
  })

  it('rotation is preserved after a scaled commit on a cropped item', () => {
    const node = fakeNode({ width: 75, height: 50, scaleX: 1.5, scaleY: 1.5, x: 200, y: 100, rotation: 90 })
    const patch = commitTransform(node)
    expect(patch).toMatchObject({ x: 200, y: 100, width: 112.5, height: 75, rotation: 90 })
    expect(node.state.width).toBe(112.5)
    expect(node.state.scaleY).toBe(1)
  })

  it('flip is a scene flag, not a negative node scale', () => {
    expect(TRANSFORMER_SCALE_PROPS.flipEnabled).toBe(false)
    const node = fakeNode({ width: 100, height: 60, scaleX: 1.25, scaleY: 1.25 })
    const patch = commitTransform(node)
    expect(patch.width).toBeGreaterThan(0)
    expect(patch.height).toBeGreaterThan(0)
  })
})