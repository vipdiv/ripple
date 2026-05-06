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

export type Speed = 1 | 2 | 4 | 8
const SPEED_CYCLE: Speed[] = [1, 2, 4, 8]
/** 1x covers 0..1 in BASE_TRAVERSAL_SECONDS seconds; faster speeds divide that. */
const BASE_TRAVERSAL_SECONDS = 60
/**
 * Cap the per-frame delta so a stalled rAF (tab inactive, iOS Safari touch
 * throttling, GC pause, etc.) can't advance the timeline by an entire second
 * in one frame. 100 ms ≈ 10 FPS — enough headroom for normal jitter,
 * tight enough that the worst frame moves <1 second of timeline at 1x.
 */
const MAX_FRAME_DT_SECONDS = 0.1

/**
 * When true, the controller writes a small live readout
 * (`1x · 14.3s · pos 0.234`) to the #tl-debug element while time-lapse is
 * active. Flip to false to ship without the overlay.
 */
export const TIMELAPSE_DEBUG = true

export interface TimelapseHooks {
  /** Fires before the scrubber slides up. Use to lock font, settle wobble, damp ripple. */
  onEnter?: () => void
  /** Fires before the scrubber slides down. Use to unlock font, restore ripple. */
  onExit?: () => void
  /** Fires whenever position changes (manual scrub or autoplay). */
  onPositionChange?: (p: number, quote: Quote | null, isoDate: string) => void
  /** Fires only when the resolved quote at the current position changes. */
  onQuoteChange?: (quote: Quote, autoplay: boolean) => void
  /** Fires when the speed selector changes. */
  onSpeedChange?: (speed: Speed) => void
  /** Fires when autoplay starts/stops. */
  onPlayingChange?: (playing: boolean) => void
  /** Fires once per autoplay frame so callers can spawn ambient ripples etc. */
  onAutoplayTick?: (speed: Speed) => void
}

export class TimelapseController {
  private active = false
  private position = 0  // 0..1
  private dragging = false
  private playing = false
  private speed: Speed = 1
  private rafId: number | null = null
  private lastTickMs = 0
  private lastResolvedDate: string | null = null

  private toggleEl: HTMLButtonElement
  private panelEl: HTMLElement
  private playPauseEl: HTMLButtonElement
  private speedEl: HTMLButtonElement
  private trackWrapEl: HTMLElement
  private trackEl: HTMLElement
  private fillEl: HTMLElement
  private playheadEl: HTMLElement
  private dateEl: HTMLElement
  private ticksEl: HTMLElement
  private debugEl: HTMLElement | null
  private hooks: TimelapseHooks
  /** Cumulative ms spent in the playing state during this time-lapse session. */
  private playElapsedMs = 0
  /** performance.now() when the current play session started; 0 if paused. */
  private playStartMs = 0
  private debugIntervalId: number | null = null

  private chrono: Quote[] = []
  private minMs = 0
  private maxMs = 0
  /** Era-boundary positions [0..1] for tick rendering. */
  private eraTickPositions: number[] = []
  /** Sand-clip rects on the hourglass icon — driven directly by position. */
  private hgTopRect: SVGRectElement | null = null
  private hgBotRect: SVGRectElement | null = null

  constructor(opts: {
    toggleEl: HTMLButtonElement
    panelEl: HTMLElement
    playPauseEl: HTMLButtonElement
    speedEl: HTMLButtonElement
    trackWrapEl: HTMLElement
    trackEl: HTMLElement
    fillEl: HTMLElement
    playheadEl: HTMLElement
    dateEl: HTMLElement
    ticksEl: HTMLElement
    debugEl?: HTMLElement | null
    chrono: Quote[]
    hooks?: TimelapseHooks
  }) {
    this.toggleEl = opts.toggleEl
    this.panelEl = opts.panelEl
    this.playPauseEl = opts.playPauseEl
    this.speedEl = opts.speedEl
    this.trackWrapEl = opts.trackWrapEl
    this.trackEl = opts.trackEl
    this.fillEl = opts.fillEl
    this.playheadEl = opts.playheadEl
    this.dateEl = opts.dateEl
    this.ticksEl = opts.ticksEl
    this.debugEl = opts.debugEl ?? null
    this.hooks = opts.hooks ?? {}
    this.setChronological(opts.chrono)
    this.speedEl.textContent = `${this.speed}x`
    this.hgTopRect = this.toggleEl.querySelector<SVGRectElement>('.hg-top-rect')
    this.hgBotRect = this.toggleEl.querySelector<SVGRectElement>('.hg-bot-rect')
    this.updateHourglass(this.position)

    this.toggleEl.addEventListener('click', () => this.toggle())
    this.playPauseEl.addEventListener('click', () => this.togglePlay())
    this.speedEl.addEventListener('click', () => this.cycleSpeed())

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
    this.playElapsedMs = 0
    this.playStartMs = 0
    this.hooks.onEnter?.()
    this.toggleEl.setAttribute('aria-pressed', 'true')
    this.toggleEl.title = 'Exit time-lapse'
    this.startDebugRender()
    // Settle window — let the surface decay naturally before the dim filter
    // and scrubber slide-up commit. Spec: 'transition into time-lapse should
    // feel like the surface gently calming, not like someone hit pause.'
    window.setTimeout(() => {
      if (!this.active) return  // user already exited mid-settle
      document.body.classList.add('timelapse-active')
      this.panelEl.hidden = false
      // Force a position re-emit so the floating quote and date refresh
      // immediately on full entry without requiring user interaction.
      this.applyPosition(this.position, /*emit*/ true)
    }, 300)
  }

  exit(): void {
    if (!this.active) return
    this.pause()
    this.active = false
    this.hooks.onExit?.()
    this.toggleEl.setAttribute('aria-pressed', 'false')
    this.toggleEl.title = 'Time-lapse mode'
    document.body.classList.remove('timelapse-active', 'timelapse-playing')
    this.stopDebugRender()
    window.setTimeout(() => {
      if (!this.active) this.panelEl.hidden = true
    }, 500)
  }

  // ── playback ───────────────────────────────────────────────────

  get isPlaying(): boolean {
    return this.playing
  }

  get currentSpeed(): Speed {
    return this.speed
  }

  togglePlay(): void {
    if (this.playing) this.pause()
    else this.play()
  }

  play(): void {
    if (!this.active || this.playing) return
    // At the end of the timeline, play is a no-op — user must drag back.
    if (this.position >= 1) return
    this.playing = true
    document.body.classList.add('timelapse-playing')
    this.playPauseEl.setAttribute('aria-label', 'Pause')
    const now = performance.now()
    this.lastTickMs = now
    this.playStartMs = now
    this.rafId = requestAnimationFrame(this.tickFrame)
    this.hooks.onPlayingChange?.(true)
  }

  pause(): void {
    if (!this.playing) return
    this.playing = false
    // Bank the time spent in this play session so the debug counter
    // resumes from where it left off when the user presses play again.
    if (this.playStartMs > 0) {
      this.playElapsedMs += performance.now() - this.playStartMs
      this.playStartMs = 0
    }
    document.body.classList.remove('timelapse-playing')
    this.playPauseEl.setAttribute('aria-label', 'Play')
    if (this.rafId !== null) cancelAnimationFrame(this.rafId)
    this.rafId = null
    this.hooks.onPlayingChange?.(false)
  }

  cycleSpeed(): void {
    const i = SPEED_CYCLE.indexOf(this.speed)
    this.speed = SPEED_CYCLE[(i + 1) % SPEED_CYCLE.length]
    this.speedEl.textContent = `${this.speed}x`
    this.speedEl.classList.add('flash')
    window.setTimeout(() => this.speedEl.classList.remove('flash'), 600)
    this.hooks.onSpeedChange?.(this.speed)
  }

  private tickFrame = (): void => {
    if (!this.playing) return
    const now = performance.now()
    // Clamp dt so a stalled frame can't blow through the timeline in one shot.
    const rawDt = (now - this.lastTickMs) / 1000
    const dt = rawDt > MAX_FRAME_DT_SECONDS ? MAX_FRAME_DT_SECONDS : rawDt
    this.lastTickMs = now
    // 1x advances 1/60 per second; faster speeds scale linearly.
    const delta = (dt / BASE_TRAVERSAL_SECONDS) * this.speed
    let next = this.position + delta
    const reachedEnd = next >= 1
    if (reachedEnd) next = 1
    this.applyPosition(next, true)
    // Autoplay-only hook (manual scrub never reaches here) — used by main.ts
    // to spawn the speed-scaled tiny ambient ripples.
    this.hooks.onAutoplayTick?.(this.speed)
    if (reachedEnd) {
      this.pause()
      return
    }
    this.rafId = requestAnimationFrame(this.tickFrame)
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
    // Manual drag pauses autoplay (per spec — does not auto-resume).
    if (this.playing) this.pause()
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
    this.updateHourglass(this.position)

    const ms = this.minMs + (this.maxMs - this.minMs) * this.position
    const d = new Date(ms)
    const month = d.getUTCMonth() + 1
    const year = d.getUTCFullYear()
    this.dateEl.textContent = `${month} / ${year}`

    if (emit) {
      const iso = isoFromMs(ms)
      const quote = quoteAtDate(this.chrono, iso)
      this.hooks.onPositionChange?.(this.position, quote, iso)
      // Fire onQuoteChange only when the resolved quote actually changes.
      // We use the source quote's date as identity (cheap, stable).
      if (quote && quote.date !== this.lastResolvedDate) {
        this.lastResolvedDate = quote.date
        this.hooks.onQuoteChange?.(quote, this.playing)
      }
    }
  }

  // ── debug overlay ──────────────────────────────────────────────

  private startDebugRender(): void {
    if (!TIMELAPSE_DEBUG || !this.debugEl) return
    this.debugEl.hidden = false
    this.renderDebug()
    this.debugIntervalId = window.setInterval(() => this.renderDebug(), 100)
  }

  private stopDebugRender(): void {
    if (this.debugIntervalId !== null) {
      clearInterval(this.debugIntervalId)
      this.debugIntervalId = null
    }
    if (this.debugEl) this.debugEl.hidden = true
  }

  private renderDebug(): void {
    if (!this.debugEl) return
    // Live elapsed = banked + (currently-playing session, if any).
    const liveSessionMs = this.playing && this.playStartMs > 0
      ? performance.now() - this.playStartMs
      : 0
    const elapsedMs = this.playElapsedMs + liveSessionMs
    const elapsedS = (elapsedMs / 1000).toFixed(1)
    const pos = this.position.toFixed(3)
    this.debugEl.textContent = `${this.speed}x · ${elapsedS}s · pos ${pos}`
  }

  /**
   * Drive the SVG sand-clip rects from the position. Top chamber empties
   * (y goes 3 -> 16, height goes 13 -> 0); bottom fills in lockstep.
   */
  private updateHourglass(p: number): void {
    if (!this.hgTopRect || !this.hgBotRect) return
    const topY = 3 + 13 * p
    const topH = 13 * (1 - p)
    this.hgTopRect.setAttribute('y', topY.toFixed(2))
    this.hgTopRect.setAttribute('height', topH.toFixed(2))
    const botY = 29 - 13 * p
    const botH = 13 * p
    this.hgBotRect.setAttribute('y', botY.toFixed(2))
    this.hgBotRect.setAttribute('height', botH.toFixed(2))
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
