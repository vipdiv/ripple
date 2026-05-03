/**
 * Ripple Field — 2D Wave Height Field Simulation
 * 
 * Each cell stores height + velocity. On each step:
 * 1. Average the 4 neighbors
 * 2. Accelerate toward that average
 * 3. Damp velocity
 * 4. Update height
 * 
 * Particles sample the gradient (slope) of the field
 * to get a directional force.
 */

export interface RippleConfig {
  cellSize: number
  speed: number
  damping: number
  clickRadius: number
  clickStrength: number
  dragRadius: number
  dragStrength: number
  dragSpacing: number
}

export const DEFAULT_CONFIG: RippleConfig = {
  cellSize: 8,
  speed: 0.38,
  damping: 0.985,
  clickRadius: 6,
  clickStrength: 12,
  dragRadius: 4,
  dragStrength: 5,
  dragSpacing: 12,
}

export class RippleField {
  cols: number = 0
  rows: number = 0
  field: Float32Array = new Float32Array(0)
  velocity: Float32Array = new Float32Array(0)
  config: RippleConfig

  constructor(config: Partial<RippleConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config }
  }

  resize(width: number, height: number) {
    this.cols = Math.ceil(width / this.config.cellSize) + 1
    this.rows = Math.ceil(height / this.config.cellSize) + 1
    const n = this.cols * this.rows
    this.field = new Float32Array(n)
    this.velocity = new Float32Array(n)
  }

  private idx(c: number, r: number): number {
    return r * this.cols + c
  }

  /** Inject energy at a screen position */
  disturb(px: number, py: number, radius: number, strength: number) {
    const fc = px / this.config.cellSize
    const fr = py / this.config.cellSize

    for (let dr = -radius; dr <= radius; dr++) {
      for (let dc = -radius; dc <= radius; dc++) {
        const c = Math.round(fc + dc)
        const r = Math.round(fr + dr)
        if (c < 0 || c >= this.cols || r < 0 || r >= this.rows) continue

        const dist = Math.sqrt(dc * dc + dr * dr)
        if (dist > radius) continue

        const t = 1 - dist / radius
        const falloff = t * t * (3 - 2 * t) // smoothstep
        this.field[this.idx(c, r)] += strength * falloff
      }
    }
  }

  /** Click disturb */
  click(px: number, py: number) {
    this.disturb(px, py, this.config.clickRadius, this.config.clickStrength)
  }

  /** Drag disturb */
  drag(px: number, py: number) {
    this.disturb(px, py, this.config.dragRadius, this.config.dragStrength)
  }

  /** Step the simulation forward */
  step() {
    const { cols, rows, field, velocity, config } = this
    const next = new Float32Array(cols * rows)

    for (let r = 1; r < rows - 1; r++) {
      for (let c = 1; c < cols - 1; c++) {
        const i = this.idx(c, r)
        const avg = (
          field[this.idx(c - 1, r)] +
          field[this.idx(c + 1, r)] +
          field[this.idx(c, r - 1)] +
          field[this.idx(c, r + 1)]
        ) * 0.25

        velocity[i] += (avg - field[i]) * config.speed
        velocity[i] *= config.damping
        next[i] = field[i] + velocity[i]
      }
    }

    this.field.set(next)
  }

  /** Sample the gradient (slope) at a screen position → [fx, fy] force */
  gradient(px: number, py: number): [number, number] {
    const c = Math.floor(px / this.config.cellSize)
    const r = Math.floor(py / this.config.cellSize)

    if (c < 1 || c >= this.cols - 1 || r < 1 || r >= this.rows - 1) {
      return [0, 0]
    }

    const gx = (this.field[this.idx(c + 1, r)] - this.field[this.idx(c - 1, r)]) * 0.5
    const gy = (this.field[this.idx(c, r + 1)] - this.field[this.idx(c, r - 1)]) * 0.5

    return [gx, gy]
  }

  /** Get field height at screen position (for visual debug) */
  heightAt(c: number, r: number): number {
    if (c < 0 || c >= this.cols || r < 0 || r >= this.rows) return 0
    return this.field[this.idx(c, r)]
  }

  /** Render debug field to canvas */
  renderDebug(ctx: CanvasRenderingContext2D) {
    const { cols, rows, config } = this
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const h = this.heightAt(c, r)
        const absH = Math.abs(h)
        if (absH < 0.05) continue
        const alpha = Math.min(absH * 0.03, 0.08)
        const hue = h > 0 ? 220 : 200
        ctx.fillStyle = `hsla(${hue}, 50%, 40%, ${alpha})`
        ctx.fillRect(
          c * config.cellSize,
          r * config.cellSize,
          config.cellSize - 1,
          config.cellSize - 1
        )
      }
    }
  }
}
