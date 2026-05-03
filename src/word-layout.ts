/**
 * Word Layout — Pretext-powered text positioning
 * 
 * Uses @chenglou/pretext to measure and lay out text into lines,
 * then converts each word-segment into an independently animated particle.
 */

import { prepareWithSegments, layoutWithLines } from '@chenglou/pretext'

export interface WordParticle {
  text: string
  ox: number   // origin x
  oy: number   // origin y
  x: number    // current x
  y: number    // current y
  vx: number   // velocity x
  vy: number   // velocity y
  w: number    // measured width
  lineIndex: number
}

export interface LayoutConfig {
  font: string
  fontSize: number
  lineHeight: number
  padding: number
  offsetY: number  // top offset for header clearance
}

export const DEFAULT_LAYOUT: LayoutConfig = {
  font: '15px "IBM Plex Mono", monospace',
  fontSize: 15,
  lineHeight: 24,
  padding: 40,
  offsetY: 80,
}

/**
 * Lay out text into word particles using Pretext.
 * Each non-whitespace segment becomes an independent particle.
 */
export function layoutText(
  text: string,
  width: number,
  height: number,
  config: LayoutConfig = DEFAULT_LAYOUT
): WordParticle[] {
  const maxWidth = width - config.padding * 2

  // Use Pretext for accurate line breaking
  const prepared = prepareWithSegments(text, config.font)
  const { lines } = layoutWithLines(prepared, maxWidth, config.lineHeight)

  const particles: WordParticle[] = []
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d')!
  ctx.font = config.font

  for (let li = 0; li < lines.length; li++) {
    const line = lines[li]
    const y = config.offsetY + li * config.lineHeight

    // Stop if we'd go off screen
    if (y > height - 40) break

    // Split line text into words and position them
    const lineText = line.text
    const words = lineText.split(/(\s+)/)
    let x = config.padding

    for (const word of words) {
      if (!word || /^\s+$/.test(word)) {
        // It's whitespace — advance x but don't create a particle
        x += ctx.measureText(word).width
        continue
      }

      const w = ctx.measureText(word).width

      particles.push({
        text: word,
        ox: x,
        oy: y,
        x: x,
        y: y,
        vx: 0,
        vy: 0,
        w: w,
        lineIndex: li,
      })

      x += w
    }
  }

  return particles
}

/**
 * Physics update: apply ripple forces, spring to origin, damp.
 */
export function updateParticles(
  particles: WordParticle[],
  gradientFn: (x: number, y: number) => [number, number],
  rippleForce: number = 0.9,
  spring: number = 0.015,
  damping: number = 0.88,
) {
  for (const p of particles) {
    const [gx, gy] = gradientFn(p.ox, p.oy)

    // Ripple pushes
    p.vx -= gx * rippleForce
    p.vy -= gy * rippleForce

    // Spring to origin
    p.vx += (p.ox - p.x) * spring
    p.vy += (p.oy - p.y) * spring

    // Damp
    p.vx *= damping
    p.vy *= damping

    // Integrate
    p.x += p.vx
    p.y += p.vy
  }
}
