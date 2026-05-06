/**
 * Email Ripples — Main Entry
 * 
 * 333,000 emails as an interactive water ripple surface.
 * Click and drag to send waves through your digital history.
 */

import './style.css'
import { RippleField } from './ripple-field'
import { layoutText, updateParticles, type WordParticle } from './word-layout'
import { QuoteManager, MOOD_COLORS, TIMELINE_NARRATIVE, THEMES, ALL_THEMES } from './quotes'
import { SoundEngine } from './sound'
import { showOneTimeTooltip } from './tooltip'
import { TimelapseController } from './timelapse'

// --- Canvas setup ---
const canvas = document.getElementById('c') as HTMLCanvasElement
const ctx = canvas.getContext('2d')!
const dpr = Math.min(window.devicePixelRatio || 1, 2)

let W = 0
let H = 0

// --- State ---
const ripple = new RippleField()
const quotes = new QuoteManager()
const sound = new SoundEngine()
let words: WordParticle[] = []
let transitioning = false
let transitionAlpha = 1

// --- DOM refs ---
const moodEl = document.getElementById('current-mood')!
const quoteDateEl = document.getElementById('quote-date')!
const quoteContextEl = document.getElementById('quote-context')!
const quoteFromEl = document.getElementById('quote-from')!
const quoteFontEl = document.getElementById('quote-font')!
const modeToggleEl = document.getElementById('mode-toggle') as HTMLButtonElement
const infoButtonEl = document.getElementById('info-button') as HTMLButtonElement
const infoModalEl = document.getElementById('info-modal') as HTMLElement
const infoCloseEl = infoModalEl.querySelector('.info-close') as HTMLButtonElement
const infoBackdropEl = infoModalEl.querySelector('.info-backdrop') as HTMLElement
const themeToggleEl = document.getElementById('theme-toggle') as HTMLButtonElement
const themeLabelEl = document.getElementById('theme-label')!
const themeDropdownEl = document.getElementById('theme-dropdown') as HTMLElement
const soundToggleEl = document.getElementById('sound-toggle') as HTMLButtonElement
const fullscreenToggleEl = document.getElementById('fullscreen-toggle') as HTMLButtonElement
const timelapseToggleEl = document.getElementById('timelapse-toggle') as HTMLButtonElement
const timelapsePanelEl = document.getElementById('timelapse-panel') as HTMLElement
const tlPlayPauseEl = document.getElementById('tl-playpause') as HTMLButtonElement
const tlSpeedEl = document.getElementById('tl-speed') as HTMLButtonElement
const tlTrackWrapEl = document.getElementById('tl-track-wrap') as HTMLElement
const tlTrackEl = document.getElementById('tl-track') as HTMLElement
const tlFillEl = document.getElementById('tl-fill') as HTMLElement
const tlPlayheadEl = document.getElementById('tl-playhead') as HTMLElement
const tlDateEl = document.getElementById('tl-date') as HTMLElement
const tlTicksEl = document.getElementById('tl-ticks') as HTMLElement
const tagRowEl = document.getElementById('tag-row') as HTMLElement
const tagToggleEl = document.getElementById('tag-toggle') as HTMLButtonElement
const tagLabelEl = document.getElementById('tag-label')!
const tagCloudEl = document.getElementById('tag-cloud') as HTMLElement
const mobileMoodPrevEl = document.getElementById('m-mood-prev') as HTMLButtonElement
const mobileMoodEl = document.getElementById('m-mood') as HTMLButtonElement
const mobileMoodNextEl = document.getElementById('m-mood-next') as HTMLButtonElement
const mobileNextEl = document.getElementById('m-next') as HTMLButtonElement
const mobileThemeEl = document.getElementById('m-theme') as HTMLButtonElement

// --- Display modes ---
type DisplayMode = 'art' | 'read' | 'invert'
const MODES: DisplayMode[] = ['art', 'read', 'invert']
let displayMode: DisplayMode = 'art'

function setDisplayMode(mode: DisplayMode) {
  displayMode = mode
  document.body.dataset.mode = mode
  // Mode button has both a text label (desktop) and an icon (mobile);
  // CSS handles which is visible, JS updates both so they stay in sync.
  const modeIcon = mode === 'invert' ? '☀' : mode === 'read' ? '☾' : '◐'
  const text = modeToggleEl.querySelector('.mode-text')
  const icon = modeToggleEl.querySelector('.mode-icon')
  if (text) text.textContent = mode.toUpperCase()
  if (icon) icon.textContent = modeIcon
}

function cycleDisplayMode() {
  const i = MODES.indexOf(displayMode)
  setDisplayMode(MODES[(i + 1) % MODES.length])
}

// --- Modal ---
function openInfo() {
  // If autoplay is running in time-lapse, pause it (keeping the audio bed)
  // so the user isn't reading while quotes scroll past behind the dim filter.
  if (timelapse?.isActive && timelapse.isPlaying) timelapse.pause()
  infoModalEl.hidden = false
}
function closeInfo() {
  infoModalEl.hidden = true
}

// --- Theme dropdown ---
function buildThemeDropdown() {
  themeDropdownEl.replaceChildren()
  const items: { value: string; label: string; count: number }[] = [
    { value: ALL_THEMES, label: 'all', count: quotes.quotes.length },
    ...THEMES.map(t => ({
      value: t.name,
      label: t.name,
      count: t.quote_count ?? quotes.quotes.filter(q => q.theme === t.name).length,
    })),
  ]
  for (const item of items) {
    const btn = document.createElement('button')
    btn.type = 'button'
    btn.role = 'option'
    btn.dataset.value = item.value
    const label = document.createElement('span')
    label.className = 'theme-name'
    label.textContent = item.label
    const count = document.createElement('span')
    count.className = 'theme-count'
    count.textContent = String(item.count)
    btn.appendChild(label)
    btn.appendChild(count)
    btn.addEventListener('click', () => selectTheme(item.value))
    themeDropdownEl.appendChild(btn)
  }
  refreshThemeSelection()
}

// Mobile uses a static "themes"/"tags" label; the chosen one is highlighted
// inside the dropdown/cloud when opened.
const mobileMq = window.matchMedia('(hover: none) and (pointer: coarse), (max-width: 720px)')

function refreshThemeSelection() {
  const useStatic = mobileMq.matches
  themeLabelEl.textContent = useStatic ? 'themes' : quotes.themeLabel
  if (mobileThemeEl) {
    mobileThemeEl.textContent = useStatic ? 'themes' : quotes.themeLabel
  }
  for (const btn of themeDropdownEl.querySelectorAll<HTMLButtonElement>('button')) {
    btn.setAttribute('aria-selected', btn.dataset.value === quotes.currentTheme ? 'true' : 'false')
  }
}

function openThemeDropdown() {
  themeDropdownEl.hidden = false
  themeToggleEl.setAttribute('aria-expanded', 'true')
}
function closeThemeDropdown() {
  themeDropdownEl.hidden = true
  themeToggleEl.setAttribute('aria-expanded', 'false')
}
function toggleThemeDropdown() {
  if (themeDropdownEl.hidden) openThemeDropdown()
  else closeThemeDropdown()
}

function selectTheme(theme: string) {
  quotes.filterByTheme(theme)
  refreshThemeSelection()
  refreshTagRow()
  relayout()
  ripple.disturb(W / 2, H / 2, 8, 10)
  closeThemeDropdown()
}

// --- Tag cloud ---
function refreshTagRow() {
  const hasTheme = quotes.currentTheme !== ALL_THEMES
  tagRowEl.hidden = !hasTheme
  if (!hasTheme) {
    closeTagCloud()
    return
  }
  buildTagCloud()
  refreshTagLabel()
}

function buildTagCloud() {
  tagCloudEl.replaceChildren()
  const items = quotes.tagsInCurrentTheme()
  for (const { tag, count } of items) {
    const btn = document.createElement('button')
    btn.type = 'button'
    btn.dataset.tag = tag
    btn.setAttribute('aria-pressed', tag === quotes.currentTag ? 'true' : 'false')
    const label = document.createElement('span')
    label.textContent = tag
    const c = document.createElement('span')
    c.className = 'tag-count'
    c.textContent = String(count)
    btn.appendChild(label)
    btn.appendChild(c)
    btn.addEventListener('click', () => toggleTag(tag))
    tagCloudEl.appendChild(btn)
  }
}

function refreshTagLabel() {
  const showStatic = mobileMq.matches || !quotes.currentTag
  tagLabelEl.textContent = showStatic ? 'tags' : (quotes.currentTag as string)
  for (const btn of tagCloudEl.querySelectorAll<HTMLButtonElement>('button')) {
    btn.setAttribute('aria-pressed', btn.dataset.tag === quotes.currentTag ? 'true' : 'false')
  }
}

function toggleTag(tag: string) {
  const next = quotes.currentTag === tag ? null : tag
  quotes.filterByTag(next)
  refreshTagLabel()
  relayout()
  ripple.disturb(W / 2, H / 2, 6, 8)
  closeTagCloud()
}

function openTagCloud() {
  if (quotes.currentTheme === ALL_THEMES) return
  tagCloudEl.hidden = false
  tagToggleEl.setAttribute('aria-expanded', 'true')
}
function closeTagCloud() {
  tagCloudEl.hidden = true
  tagToggleEl.setAttribute('aria-expanded', 'false')
}
function toggleTagCloud() {
  if (tagCloudEl.hidden) openTagCloud()
  else closeTagCloud()
}

function renderInfoExtras() {
  const card = infoModalEl.querySelector('.info-card') as HTMLElement
  const controls = card.querySelector('p.controls')!

  if (TIMELINE_NARRATIVE) {
    const section = document.createElement('section')
    section.className = 'info-timeline'
    const h = document.createElement('h3')
    h.textContent = 'Timeline'
    section.appendChild(h)
    const p = document.createElement('p')
    p.className = 'narrative'
    p.textContent = TIMELINE_NARRATIVE
    section.appendChild(p)
    card.insertBefore(section, controls)
  }
}

// --- Era → Gmail font ---
type EraFont = { family: string; label: string }
const FONT_ARIAL: EraFont = { family: 'Arial, sans-serif', label: 'Arial' }
const FONT_ROBOTO: EraFont = { family: '"Roboto", "Helvetica Neue", Arial, sans-serif', label: 'Roboto' }

function getEraFont(era: string | undefined): EraFont {
  const start = parseInt((era ?? '').slice(0, 4), 10)
  if (Number.isFinite(start) && start >= 2016) return FONT_ROBOTO
  return FONT_ARIAL
}

let currentFont: EraFont = FONT_ARIAL

// --- Mouse ---
let mouseDown = false
let lastDragX = -999
let lastDragY = -999

// --- Resize ---
function resize() {
  W = window.innerWidth
  H = window.innerHeight
  canvas.width = W * dpr
  canvas.height = H * dpr
  canvas.style.width = W + 'px'
  canvas.style.height = H + 'px'
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  
  ripple.resize(W, H)
  if (timelapse?.isActive && lastTimelapseQuote) {
    // Re-render the chronological quote at the new center; bypasses the
    // mood/theme filter that relayout() would apply.
    renderTimelapseQuote(lastTimelapseQuote)
  } else {
    relayout()
  }
}

function relayout() {
  const quote = quotes.current()
  if (!quote) return

  currentFont = getEraFont(quote.era)
  words = layoutText(quote.text, W, H, {
    font: `${getFontSize()}px ${currentFont.family}`,
    fontSize: getFontSize(),
    lineHeight: Math.round(getFontSize() * 1.7),
    padding: getPadding(),
    offsetY: Math.round(H * 0.35), // center-ish vertically
  })

  updateUI(quote)
}

function getFontSize(): number {
  // Responsive font size
  if (W < 500) return 14
  if (W < 800) return 17
  if (W < 1200) return 22
  return 26
}

function getPadding(): number {
  if (W < 500) return 20
  if (W < 800) return 32
  return 60
}

function updateUI(quote: { mood: string; date: string; context: string; from_type: string }) {
  document.body.setAttribute('data-mood', quote.mood)
  moodEl.textContent = quotes.moodLabel
  moodEl.style.color = getMoodColor(quote.mood, displayMode === 'invert' ? 0.85 : 0.7)
  quoteDateEl.textContent = formatDate(quote.date)
  quoteContextEl.textContent = quote.context
  quoteFromEl.textContent = quote.from_type ? `from ${quote.from_type}` : ''
  quoteFontEl.textContent = `set in ${currentFont.label}`
  if (mobileMoodEl) mobileMoodEl.textContent = quotes.moodLabel
  sound.setMood(quote.mood)
}

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

function formatDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? '')
  if (!m) return iso
  const year = Number(m[1])
  const month = Number(m[2])
  const day = Number(m[3])
  if (!year || month < 1 || month > 12 || !day) return iso
  return `${MONTH_NAMES[month - 1]} ${day}, ${year}`
}

function getMoodColor(mood: string, alpha: number = 1): string {
  const c = MOOD_COLORS[mood] || { hue: 210, sat: 40, light: 55 }
  return `hsla(${c.hue}, ${c.sat}%, ${c.light}%, ${alpha})`
}

// --- Quote transitions ---
function transitionToNext() {
  if (transitioning) return
  transitioning = true
  transitionAlpha = 1

  // Burst ripple at center
  ripple.disturb(W / 2, H / 2, 10, 15)
  sound.triggerTransitionSwell()
  
  // Fade out
  const fadeOut = () => {
    transitionAlpha -= 0.04
    if (transitionAlpha > 0) {
      requestAnimationFrame(fadeOut)
    } else {
      transitionAlpha = 0
      quotes.next()
      relayout()
      // Fade in
      const fadeIn = () => {
        transitionAlpha += 0.03
        if (transitionAlpha < 1) {
          requestAnimationFrame(fadeIn)
        } else {
          transitionAlpha = 1
          transitioning = false
        }
      }
      fadeIn()
    }
  }
  fadeOut()
}

// --- Input ---
canvas.addEventListener('mousedown', e => {
  if (timelapse.isActive) return
  mouseDown = true
  lastDragX = e.clientX
  lastDragY = e.clientY
  ripple.click(e.clientX, e.clientY)
  sound.triggerPing(e.clientX, e.clientY, W)
  sound.onDragStart()
})

canvas.addEventListener('mousemove', e => {
  if (!mouseDown || timelapse.isActive) return
  const dx = e.clientX - lastDragX
  const dy = e.clientY - lastDragY
  const dist = Math.sqrt(dx * dx + dy * dy)
  if (dist >= ripple.config.dragSpacing) {
    ripple.drag(e.clientX, e.clientY)
    sound.onDragMove(dist, e.clientX, e.clientY, W, H)
    lastDragX = e.clientX
    lastDragY = e.clientY
  }
})

canvas.addEventListener('mouseup', () => {
  mouseDown = false
  sound.onDragEnd()
})
canvas.addEventListener('mouseleave', () => {
  mouseDown = false
  sound.onDragEnd()
})

// Touch
canvas.addEventListener('touchstart', e => {
  if (timelapse.isActive) return
  e.preventDefault()
  const t = e.touches[0]
  mouseDown = true
  lastDragX = t.clientX
  lastDragY = t.clientY
  ripple.click(t.clientX, t.clientY)
  sound.triggerPing(t.clientX, t.clientY, W)
  sound.onDragStart()
}, { passive: false })

canvas.addEventListener('touchmove', e => {
  if (timelapse.isActive) return
  e.preventDefault()
  const t = e.touches[0]
  if (!mouseDown) return
  const dx = t.clientX - lastDragX
  const dy = t.clientY - lastDragY
  const dist = Math.sqrt(dx * dx + dy * dy)
  if (dist >= ripple.config.dragSpacing) {
    ripple.drag(t.clientX, t.clientY)
    sound.onDragMove(dist, t.clientX, t.clientY, W, H)
    lastDragX = t.clientX
    lastDragY = t.clientY
  }
}, { passive: false })

canvas.addEventListener('touchend', () => {
  mouseDown = false
  sound.onDragEnd()
})

// Keyboard
window.addEventListener('keydown', e => {
  if (e.code === 'Escape') {
    if (!infoModalEl.hidden) { closeInfo(); return }
    if (!themeDropdownEl.hidden) { closeThemeDropdown(); return }
    if (!tagCloudEl.hidden) { closeTagCloud(); return }
  }
  if (!infoModalEl.hidden) return

  if (e.code === 'Space') {
    e.preventDefault()
    transitionToNext()
  } else if (e.code === 'ArrowRight') {
    quotes.nextMood(1)
    relayout()
    ripple.disturb(W / 2, H / 2, 8, 10)
  } else if (e.code === 'ArrowLeft') {
    quotes.nextMood(-1)
    relayout()
    ripple.disturb(W / 2, H / 2, 8, 10)
  } else if (e.code === 'KeyR') {
    cycleDisplayMode()
  } else if (e.code === 'KeyF' && supportsFullscreen) {
    e.preventDefault()
    toggleFullscreen()
  }
})

// Mode toggle + info modal
modeToggleEl.addEventListener('click', cycleDisplayMode)
infoButtonEl.addEventListener('click', openInfo)
infoCloseEl.addEventListener('click', closeInfo)
infoBackdropEl.addEventListener('click', closeInfo)

// Sound toggle (off by default; first click triggers AudioContext init)
function refreshSoundButton() {
  const on = sound.isEnabled
  soundToggleEl.setAttribute('aria-pressed', on ? 'true' : 'false')
  soundToggleEl.classList.toggle('muted', !on)
  soundToggleEl.title = on ? 'Mute sound' : 'Turn on sound'
  const labelEl = soundToggleEl.querySelector('.sound-label')
  if (labelEl) labelEl.textContent = on ? 'sound on' : 'sound off'
}
soundToggleEl.addEventListener('click', () => {
  sound.toggle()
  // Re-apply current quote's mood now that audio may be live
  const q = quotes.current()
  if (q) sound.setMood(q.mood)
  refreshSoundButton()
})
refreshSoundButton()

// First-time hint: nudge users toward the sound toggle on initial visit.
showOneTimeTooltip({
  key: 'ripple.tooltip.sound',
  target: soundToggleEl,
  text: 'tap to enable ambient sound',
  anchor: 'top-right',
  delayMs: 500,
  durationMs: 4000,
})

// Fullscreen toggle (desktop only — touch devices handle fullscreen via OS UI)
const supportsFullscreen = typeof document.documentElement.requestFullscreen === 'function'
function refreshFullscreenButton() {
  const on = !!document.fullscreenElement
  fullscreenToggleEl.setAttribute('aria-pressed', on ? 'true' : 'false')
  fullscreenToggleEl.title = on ? 'Exit fullscreen (F)' : 'Fullscreen (F)'
}
function toggleFullscreen() {
  if (!supportsFullscreen) return
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {})
  } else {
    document.documentElement.requestFullscreen().catch(() => {})
  }
}
// Time-lapse mode
let lockedFont: EraFont | null = null
let tlAlpha = 1
let tlFadeRafId: number | null = null
let lastTimelapseQuote: { text: string; date: string; context: string; from_type: string } | null = null
/**
 * While time-lapse is active, particle physics is normally gated off so the
 * floating words sit still. On entry though, we let them keep updating for
 * a brief settle window so the existing wobble decays naturally instead of
 * snapping. performance.now() less than this value -> physics still runs.
 */
let tlPhysicsUntilMs = 0
const TL_ENTRY_SETTLE_MS = 700

function fadeInTimelapseQuote() {
  // 120ms ease-in fade — only used during autoplay quote transitions.
  if (tlFadeRafId !== null) cancelAnimationFrame(tlFadeRafId)
  const t0 = performance.now()
  tlAlpha = 0
  const step = () => {
    const dt = performance.now() - t0
    tlAlpha = Math.min(1, dt / 120)
    if (tlAlpha < 1) tlFadeRafId = requestAnimationFrame(step)
    else tlFadeRafId = null
  }
  tlFadeRafId = requestAnimationFrame(step)
}

function renderTimelapseQuote(quote: { text: string; date: string; context: string; from_type: string }) {
  const drawFont = lockedFont ?? currentFont
  words = layoutText(quote.text, W, H, {
    font: `${getFontSize()}px ${drawFont.family}`,
    fontSize: getFontSize(),
    lineHeight: Math.round(getFontSize() * 1.7),
    padding: getPadding(),
    offsetY: Math.round(H * 0.35),
  })
  quoteDateEl.textContent = formatDate(quote.date)
  quoteContextEl.textContent = quote.context
  quoteFromEl.textContent = quote.from_type ? `from ${quote.from_type}` : ''
  quoteFontEl.textContent = `set in ${drawFont.label}`
}

const timelapse = new TimelapseController({
  toggleEl: timelapseToggleEl,
  panelEl: timelapsePanelEl,
  playPauseEl: tlPlayPauseEl,
  speedEl: tlSpeedEl,
  trackWrapEl: tlTrackWrapEl,
  trackEl: tlTrackEl,
  fillEl: tlFillEl,
  playheadEl: tlPlayheadEl,
  dateEl: tlDateEl,
  ticksEl: tlTicksEl,
  chrono: quotes.chronological(),
  hooks: {
    onEnter: () => {
      // Lock the font that's currently active so it doesn't swap mid-scrub.
      lockedFont = currentFont
      tlAlpha = 1
      // Let the existing ripple field and word particles decay naturally.
      // updateParticles() keeps running until the settle window ends; the
      // ripple field's built-in damping (0.985 per step) carries it to flat.
      tlPhysicsUntilMs = performance.now() + TL_ENTRY_SETTLE_MS
      // Crossfade audio: regular bed fades down, time-lapse bed fades up.
      sound.enableTimelapse()
      // First-time-this-session tooltip explaining the lockout.
      showOneTimeTooltip({
        key: 'ripple.tooltip.timelapse',
        anchor: 'viewport-top',
        accent: 'time-lapse mode',
        text: '— ripples pause until you exit',
        delayMs: 300,
        durationMs: 4000,
      })
    },
    onExit: () => {
      lockedFont = null
      tlAlpha = 1
      if (tlFadeRafId !== null) {
        cancelAnimationFrame(tlFadeRafId)
        tlFadeRafId = null
      }
      // Crossfade audio back to the regular ambient.
      sound.disableTimelapse()
      // Re-render the displayed quote with the era-correct font.
      relayout()
    },
    onQuoteChange: (quote, autoplay) => {
      lastTimelapseQuote = quote
      renderTimelapseQuote(quote)
      if (autoplay) fadeInTimelapseQuote()
    },
    onSpeedChange: (speed) => sound.setTimelapseSpeed(speed),
    onAutoplayTick: (speed) => {
      // Tiny ambient ripple, probability-per-frame keyed to playback speed.
      // Shape is much smaller / lower-amplitude than user-click ripples.
      const prob = TL_RIPPLE_PROB[speed]
      if (Math.random() < prob) {
        const x = Math.random() * W
        const y = Math.random() * H
        ripple.disturb(x, y, 2, 0.7)
      }
    },
  },
})

const TL_RIPPLE_PROB: Record<1 | 2 | 4 | 8, number> = {
  1: 0.005,
  2: 0.012,
  4: 0.025,
  8: 0.05,
}

if (supportsFullscreen) {
  fullscreenToggleEl.hidden = false
  fullscreenToggleEl.addEventListener('click', toggleFullscreen)
  document.addEventListener('fullscreenchange', () => {
    refreshFullscreenButton()
    // Fullscreen transitions also fire a window resize, but call ours
    // directly so the ripple field is guaranteed to know about the new
    // dimensions.
    resize()
  })
}

// Theme dropdown
themeToggleEl.addEventListener('click', e => {
  e.stopPropagation()
  closeTagCloud()
  toggleThemeDropdown()
})
themeDropdownEl.addEventListener('click', e => e.stopPropagation())

// Tag cloud
tagToggleEl.addEventListener('click', e => {
  e.stopPropagation()
  closeThemeDropdown()
  toggleTagCloud()
})
tagCloudEl.addEventListener('click', e => e.stopPropagation())

document.addEventListener('click', () => {
  if (!themeDropdownEl.hidden) closeThemeDropdown()
  if (!tagCloudEl.hidden) closeTagCloud()
})

// Mobile control bar
function bumpRipple() { ripple.disturb(W / 2, H / 2, 6, 6) }
mobileMoodPrevEl.addEventListener('click', () => { quotes.nextMood(-1); relayout(); bumpRipple() })
mobileMoodNextEl.addEventListener('click', () => { quotes.nextMood(1); relayout(); bumpRipple() })
mobileMoodEl.addEventListener('click', () => { quotes.nextMood(1); relayout(); bumpRipple() })
mobileNextEl.addEventListener('click', () => transitionToNext())
mobileThemeEl.addEventListener('click', e => {
  e.stopPropagation()
  closeTagCloud()
  toggleThemeDropdown()
})
// Mouse wheel — advance to next quote (acts like Spacebar)
window.addEventListener('wheel', e => {
  // Let the modal and theme dropdown handle their own scrolling
  if (!infoModalEl.hidden) return
  if (!themeDropdownEl.hidden) return
  e.preventDefault()
  transitionToNext()
}, { passive: false })

// --- Render ---
function render() {
  ctx.clearRect(0, 0, W, H)

  // Subtle field visualization
  ripple.renderDebug(ctx)

  // Draw words
  const fontSize = getFontSize()
  const drawFont = lockedFont ?? currentFont
  ctx.font = `${fontSize}px ${drawFont.family}`
  ctx.textBaseline = 'top'

  const quote = quotes.current()
  const mood = quote?.mood || 'profound'
  const moodColor = MOOD_COLORS[mood] || { hue: 210, sat: 40, light: 55 }

  const invert = displayMode === 'invert'

  for (const w of words) {
    const speed = Math.sqrt(w.vx * w.vx + w.vy * w.vy)
    const displacement = Math.sqrt((w.x - w.ox) ** 2 + (w.y - w.oy) ** 2)

    let hue: number, sat: number, light: number, alpha: number

    if (speed > 2) {
      // Fast motion: bright, desaturated
      const t = Math.min(speed / 8, 1)
      hue = moodColor.hue
      sat = moodColor.sat * (1 - t * 0.5)
      light = moodColor.light + 30 * t
      alpha = 0.95
    } else if (displacement > 1) {
      // Displaced: mood-tinted
      const t = Math.min(displacement / 20, 1)
      hue = moodColor.hue
      sat = moodColor.sat * (0.4 + 0.6 * t)
      light = moodColor.light - 5 + 15 * t
      alpha = 0.6 + 0.3 * t
    } else {
      // At rest: dim base
      hue = moodColor.hue
      sat = moodColor.sat * 0.2
      light = 50
      alpha = 0.4
    }

    if (invert) {
      // Flip lightness so colors read as dark ink on a light page
      light = Math.max(8, Math.min(35, 90 - light))
      sat = Math.min(85, sat + 10)
      alpha = Math.min(1, alpha + 0.25)
    }

    // Apply transition alpha (regular mode + time-lapse autoplay crossfade)
    alpha *= transitionAlpha
    if (timelapse.isActive) alpha *= tlAlpha

    ctx.fillStyle = `hsla(${hue}, ${sat}%, ${light}%, ${alpha})`
    ctx.fillText(w.text, w.x, w.y)
  }
}

// --- Ambient ripples ---
function ambientRipple() {
  if (timelapse.isActive) return
  const x = Math.random() * W
  const y = Math.random() * H
  ripple.disturb(x, y, 3, 2)
}

// --- Initial splash ---
function initialSplash() {
  ripple.disturb(W / 2, H / 2, 8, 8)
  setTimeout(() => ripple.disturb(W * 0.3, H * 0.4, 5, 5), 200)
  setTimeout(() => ripple.disturb(W * 0.7, H * 0.6, 5, 5), 400)
  setTimeout(() => ripple.disturb(W * 0.2, H * 0.7, 4, 4), 700)
}

// --- Main loop ---
function loop() {
  ripple.step()
  // Particles update normally outside time-lapse, and during the entry
  // settle window so the wobble can decay smoothly into the dim state.
  if (!timelapse.isActive || performance.now() < tlPhysicsUntilMs) {
    updateParticles(
      words,
      (x, y) => ripple.gradient(x, y),
      0.9,   // ripple force
      0.015, // spring
      0.88,  // damping
    )
  }
  render()
  requestAnimationFrame(loop)
}

// --- Init ---
function init() {
  setDisplayMode(displayMode)
  renderInfoExtras()
  buildThemeDropdown()
  refreshTagRow()
  window.addEventListener('resize', resize)
  mobileMq.addEventListener('change', () => {
    refreshThemeSelection()
    refreshTagLabel()
  })
  resize()
  initialSplash()
  // Re-measure once Roboto/Plex actually finish loading
  if ('fonts' in document) {
    document.fonts.ready.then(relayout).catch(() => {})
  }
  
  // Ambient ripples
  setInterval(ambientRipple, 3500)
  
  // Auto-advance quotes every 12 seconds (paused during time-lapse)
  setInterval(() => {
    if (!mouseDown && !timelapse.isActive) transitionToNext()
  }, 12000)

  loop()
}

init()
