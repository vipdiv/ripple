/**
 * One-time tooltip helper.
 *
 * Shows a small floating tooltip pointing at a target element. Stores its
 * dismissal in sessionStorage so it appears once per browser session and
 * never twice for the same key. Designed to be quiet and brief — appears
 * after a short delay, fades after a few seconds, and dismisses on any
 * pointer activity.
 *
 * Reused by Feature 1 (sound toggle hint) and planned for Feature 2's
 * time-lapse mode entry tooltip.
 */

export type TooltipAnchor = 'top-right' | 'bottom-center' | 'top-center'

export interface TooltipOptions {
  /** Unique sessionStorage key. */
  key: string
  /** Element the tooltip points at. */
  target: HTMLElement
  /** Tooltip text. The "tap" prefix can be styled via the .accent class if needed. */
  text: string
  /** Where the tooltip sits relative to the target. */
  anchor?: TooltipAnchor
  /** Milliseconds before the tooltip appears. */
  delayMs?: number
  /** Milliseconds the tooltip stays visible before fading. */
  durationMs?: number
}

export function showOneTimeTooltip(opts: TooltipOptions): void {
  const { key, target, text } = opts
  const anchor = opts.anchor ?? 'top-right'
  const delayMs = opts.delayMs ?? 500
  const durationMs = opts.durationMs ?? 4000

  try {
    if (sessionStorage.getItem(key)) return
  } catch {
    // sessionStorage may be unavailable (private mode, etc.) — show the tip anyway.
  }

  const showAt = window.setTimeout(() => {
    const tip = document.createElement('div')
    tip.className = `one-time-tooltip anchor-${anchor}`
    tip.setAttribute('role', 'status')
    tip.textContent = text

    document.body.appendChild(tip)
    positionTooltip(tip, target, anchor)

    // Reposition on resize while visible.
    const onResize = () => positionTooltip(tip, target, anchor)
    window.addEventListener('resize', onResize)

    // Reveal on next frame so the entrance transition runs.
    requestAnimationFrame(() => tip.classList.add('visible'))

    try { sessionStorage.setItem(key, '1') } catch { /* ignore */ }

    let dismissed = false
    const dismiss = () => {
      if (dismissed) return
      dismissed = true
      window.removeEventListener('resize', onResize)
      tip.classList.remove('visible')
      window.setTimeout(() => tip.remove(), 600)
    }

    // Auto-fade after duration.
    window.setTimeout(dismiss, durationMs)

    // Dismiss on any user interaction.
    const onAny = () => dismiss()
    window.addEventListener('pointerdown', onAny, { once: true })
    window.addEventListener('keydown', onAny, { once: true })
  }, delayMs)

  // Stash the timeout id on window so callers could clear if needed (rarely useful).
  ;(showOneTimeTooltip as unknown as { _last?: number })._last = showAt
}

function positionTooltip(tip: HTMLElement, target: HTMLElement, anchor: TooltipAnchor): void {
  const rect = target.getBoundingClientRect()
  const margin = 10
  switch (anchor) {
    case 'top-right': {
      // Tooltip sits below the target, right-aligned with it.
      tip.style.top = `${Math.round(rect.bottom + margin)}px`
      tip.style.right = `${Math.round(window.innerWidth - rect.right)}px`
      tip.style.left = 'auto'
      tip.style.bottom = 'auto'
      break
    }
    case 'bottom-center': {
      tip.style.bottom = `${Math.round(window.innerHeight - rect.top + margin)}px`
      tip.style.left = '50%'
      tip.style.right = 'auto'
      tip.style.top = 'auto'
      tip.style.transform = 'translateX(-50%)'
      break
    }
    case 'top-center': {
      tip.style.top = `${Math.round(rect.bottom + margin)}px`
      tip.style.left = '50%'
      tip.style.right = 'auto'
      tip.style.bottom = 'auto'
      tip.style.transform = 'translateX(-50%)'
      break
    }
  }
}
