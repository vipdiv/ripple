/**
 * Time-lapse controller.
 *
 * Owns the chronological-playback state (position, autoplay, speed) and
 * the scrubber-panel DOM wiring. Stays self-contained: main.ts hands in
 * the chronologically-sorted quotes plus a small set of hooks (font
 * lock, ripple bypass, audio bed, ambient ripples) and never reaches
 * into private state.
 *
 * This is the step-2 cut: manual scrubbing works (mouse and touch via
 * Pointer Events), quote and date readouts update live as you drag, and
 * the playhead position is fully owned here. Autoplay / audio / ambient
 * ripples land in later steps.
 */

import type { Quote } from './quotes'
import { quoteAtDate } from './quotes'

export interface TimelapseHooks {
  /** Fires before the scrubber slides up. Use to lock font, settle wobble, damp ripple. */
  onEnter?: () => void
  /** Fires before the scrubber slides down. Use to unlock font, restore ripple. */
  onExit?: () => void
  /** Fires whenever position changes (manual scrub or autoplay). */
  onPositionChange?: (p: number, quote: Quote | null, isoDate: string) => void
}

export class TimelapseController {
  private active = false
  private position = 0  // 0..1
  private dragging = false

  private toggleEl: HTMLButtonElement
  private panelEl: HTMLElement
  private trackWrapEl: HTMLElement
  private trackEl: HTMLElement
  private fillEl: HTMLElement
  private playheadEl: HTMLElement
  private dateEl: HTMLElement
  private ticksEl: HTMLElement
  private hooks: TimelapseHooks

  private chrono: Quote[] = []
  private minMs = 0
  private maxMs = 0
  /** Era-boundary positions [0..1] for tick rendering. */
  private eraTickPositions: number[] = []

  constructor(opts: {
    toggleEl: HTMLButtonElement
    panelEl: HTMLElement
    trackWrapEl: HTMLElement
    trackEl: HTMLElement
    fillEl: HTMLElement
    playheadEl: HTMLElement
    dateEl: HTMLElement
    ticksEl: HTMLElement
    chrono: Quote[]
    hooks?: TimelapseHooks
  }) {
    this.toggleEl = opts.toggleEl
    this.panelEl = opts.panelEl
    this.trackWrapEl = opts.trackWrapEl
    this.trackEl = opts.trackEl
    this.fillEl = opts.fillEl
    this.playheadEl = opts.playheadEl
    this.dateEl = opts.dateEl
    this.ticksEl = opts.ticksEl
    this.hooks = opts.hooks ?? {}
    this.setChronological(opts.chrono)

    this.toggleEl.addEventListener('click', () => this.toggle())

    // Pointer Events unify mouse and touch. Capture lets us follow the
    // pointer outside the track once a drag has started.
    this.trackWrapEl.addEventListener('pointerdown', this.onPointerDown)
    this.trackWrapEl.addEventListener('pointermove', this.onPointerMove)
    this.trackWrapEl.addEventListener('pointerup', this.onPointerUp)
    this.trackWrapEl.addEventListener('pointercancel', this.onPointerUp)
  }

  // ── public surface ─────────────────────────────────────────────

  get isActive(): boolean {
    return this.active
  }

  toggle(): void {
    if (this.active) this.exit()
    else this.enter()
  }

  enter(): void {
    if (this.active) return
    this.active = true
    this.hooks.onEnter?.()
    this.toggleEl.setAttribute('aria-pressed', 'true')
    this.toggleEl.title = 'Exit time-lapse'
    document.body.classList.add('timelapse-active')
    this.panelEl.hidden = false
    // Force a position re-emit so the floating quote and date refresh
    // immediately on entry without requiring user interaction.
    this.applyPosition(this.position, /*emit*/ true)
  }

  exit(): void {
    if (!this.active) return
    this.active = false
    this.hooks.onExit?.()
    this.toggleEl.setAttribute('aria-pressed', 'false')
    this.toggleEl.title = 'Time-lapse mode'
    document.body.classList.remove('timelapse-active', 'timelapse-playing')
    window.setTimeout(() => {
      if (!this.active) this.panelEl.hidden = true
    }, 500)
  }

  /** Replace the dataset (e.g. on hot reload) and rebuild ticks. */
  setChronological(quotes: Quote[]): void {
    this.chrono = quotes
    if (quotes.length === 0) {
      this.minMs = 0
      this.maxMs = 0
      this.eraTickPositions = []
      return
    }
    this.minMs = parseDate(quotes[0].date).getTime()
    this.maxMs = parseDate(quotes[quotes.length - 1].date).getTime()
    this.computeEraTicks()
    this.renderTicks()
  }

  // ── internal ───────────────────────────────────────────────────

  private computeEraTicks(): void {
    // Era boundaries from the spec; only the ones inside the data range render.
    const boundaries = [
      '2007-01-01', '2010-01-01', '2013-01-01', '2016-01-01',
      '2019-01-01', '2022-01-01', '2025-01-01',
    ]
    const span = this.maxMs - this.minMs
    if (span <= 0) { this.eraTickPositions = []; return }
    this.eraTickPositions = boundaries
      .map(iso => (parseDate(iso).getTime() - this.minMs) / span)
      .filter(p => p > 0 && p < 1)
  }

  private renderTicks(): void {
    this.ticksEl.replaceChildren()
    for (const p of this.eraTickPositions) {
      const tick = document.createElement('span')
      tick.style.left = `${(p * 100).toFixed(3)}%`
      this.ticksEl.appendChild(tick)
    }
  }

  private onPointerDown = (e: PointerEvent): void => {
    if (!this.active) return
    this.dragging = true
    this.trackWrapEl.classList.add('dragging')
    this.trackWrapEl.setPointerCapture(e.pointerId)
    this.applyPosition(this.positionFromEvent(e), true)
  }

  private onPointerMove = (e: PointerEvent): void => {
    if (!this.dragging) return
    this.applyPosition(this.positionFromEvent(e), true)
  }

  private onPointerUp = (e: PointerEvent): void => {
    if (!this.dragging) return
    this.dragging = false
    this.trackWrapEl.classList.remove('dragging')
    try { this.trackWrapEl.releasePointerCapture(e.pointerId) } catch { /* already released */ }
    // Stay at released position. No auto-resume of playback (per spec).
  }

  private positionFromEvent(e: PointerEvent): number {
    const rect = this.trackEl.getBoundingClientRect()
    if (rect.width <= 0) return 0
    return clamp01((e.clientX - rect.left) / rect.width)
  }

  private applyPosition(p: number, emit: boolean): void {
    this.position = clamp01(p)
    const fillPct = `${(this.position * 100).toFixed(3)}%`
    this.fillEl.style.width = fillPct
    this.playheadEl.style.left = fillPct

    const ms = this.minMs + (this.maxMs - this.minMs) * this.position
    const d = new Date(ms)
    const month = d.getUTCMonth() + 1
    const year = d.getUTCFullYear()
    this.dateEl.textContent = `${month} / ${year}`

    if (emit && this.hooks.onPositionChange) {
      const iso = isoFromMs(ms)
      const quote = quoteAtDate(this.chrono, iso)
      this.hooks.onPositionChange(this.position, quote, iso)
    }
  }
}

function parseDate(iso: string): Date {
  // Anchor at UTC midnight so timezone offsets don't shift readouts.
  return new Date(`${iso.slice(0, 10)}T00:00:00Z`)
}

function isoFromMs(ms: number): string {
  const d = new Date(ms)
  const yyyy = d.getUTCFullYear()
  const mm = String(d.getUTCMonth() + 1).padStart(2, '0')
  const dd = String(d.getUTCDate()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd}`
}

function clamp01(v: number): number {
  if (v < 0) return 0
  if (v > 1) return 1
  return v
}
