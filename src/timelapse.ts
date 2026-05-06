/**
 * Time-lapse controller.
 *
 * Step 1 (this commit): UI shell only — toggles a body class, slides the
 * scrubber panel up/down, and tracks active state. Future steps will add
 * manual scrubbing, autoplay, audio bed, and ambient ripples.
 *
 * The controller is intentionally self-contained: main.ts wires the
 * hourglass click and a few hooks (settle, font lock, ripple damping)
 * but never reaches into private state. Public surface is enter/exit
 * and isActive.
 */

export interface TimelapseHooks {
  /** Called when the user enters time-lapse, before the scrubber slides up. */
  onEnter?: () => void
  /** Called when the user exits time-lapse, before the scrubber slides down. */
  onExit?: () => void
}

export class TimelapseController {
  private active = false
  private toggleEl: HTMLButtonElement
  private panelEl: HTMLElement
  private hooks: TimelapseHooks

  constructor(toggleEl: HTMLButtonElement, panelEl: HTMLElement, hooks: TimelapseHooks = {}) {
    this.toggleEl = toggleEl
    this.panelEl = panelEl
    this.hooks = hooks

    this.toggleEl.addEventListener('click', () => this.toggle())
  }

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
  }

  exit(): void {
    if (!this.active) return
    this.active = false
    this.hooks.onExit?.()
    this.toggleEl.setAttribute('aria-pressed', 'false')
    this.toggleEl.title = 'Time-lapse mode'
    document.body.classList.remove('timelapse-active', 'timelapse-playing')
    // Wait for the slide-down animation to finish before fully hiding so
    // the transform/opacity transition can run.
    window.setTimeout(() => {
      if (!this.active) this.panelEl.hidden = true
    }, 500)
  }
}
