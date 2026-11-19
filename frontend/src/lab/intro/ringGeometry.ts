/** Geometry of the existing CssRingDemo: sixteen cards, radius 440, -8° tilt, one turn per 40s. */
export const RING_COUNT = 16
export const RING_RADIUS = 440
export const RING_SECONDS = 40
export const ringAngle = (i: number, count = RING_COUNT) => i * 360 / count
