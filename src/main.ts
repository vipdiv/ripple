/**
 * Email Ripples — Main Entry
 * 
 * 333,000 emails as an interactive water ripple surface.
 * Click and drag to send waves through your digital history.
 */

import './style.css'
import { RippleField } from './ripple-field'
import { layoutText, updateParticles, type WordParticle } from './word-layout'
import { QuoteManager, MOOD_COLORS } from './quotes'

// --- Canvas setup ---
const canvas = document.getElementById('c') as HTMLCanvasElement
const ctx = canvas.getContext('2d')!
const dpr = Math.min(window.devicePixelRatio || 1, 2)

let W = 0
let H = 0

// --- State ---
const ripple = new RippleField()
const quotes = new QuoteManager()
let words: WordParticle[] = []
let transitioning = false
let transitionAlpha = 1

// --- DOM refs ---
const moodEl = document.getElementById('current-mood')!
const quoteDateEl = document.getElementById('quote-date')!
const quoteContextEl = document.getElementById('quote-context')!

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
  relayout()
}

function relayout() {
  const quote = quotes.current()
  if (!quote) return

  words = layoutText(quote.text, W, H, {
    font: `${getFontSize()}px "IBM Plex Mono", monospace`,
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

function updateUI(quote: { mood: string; date: string; context: string }) {
  document.body.setAttribute('data-mood', quote.mood)
  moodEl.textContent = quotes.moodLabel
  moodEl.style.color = getMoodColor(quote.mood, 0.5)
  quoteDateEl.textContent = quote.date
  quoteContextEl.textContent = quote.context
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
  mouseDown = true
  lastDragX = e.clientX
  lastDragY = e.clientY
  ripple.click(e.clientX, e.clientY)
})

canvas.addEventListener('mousemove', e => {
  if (!mouseDown) return
  const dx = e.clientX - lastDragX
  const dy = e.clientY - lastDragY
  if (Math.sqrt(dx * dx + dy * dy) >= ripple.config.dragSpacing) {
    ripple.drag(e.clientX, e.clientY)
    lastDragX = e.clientX
    lastDragY = e.clientY
  }
})

canvas.addEventListener('mouseup', () => { mouseDown = false })
canvas.addEventListener('mouseleave', () => { mouseDown = false })

// Touch
canvas.addEventListener('touchstart', e => {
  e.preventDefault()
  const t = e.touches[0]
  mouseDown = true
  lastDragX = t.clientX
  lastDragY = t.clientY
  ripple.click(t.clientX, t.clientY)
}, { passive: false })

canvas.addEventListener('touchmove', e => {
  e.preventDefault()
  const t = e.touches[0]
  if (!mouseDown) return
  const dx = t.clientX - lastDragX
  const dy = t.clientY - lastDragY
  if (Math.sqrt(dx * dx + dy * dy) >= ripple.config.dragSpacing) {
    ripple.drag(t.clientX, t.clientY)
    lastDragX = t.clientX
    lastDragY = t.clientY
  }
}, { passive: false })

canvas.addEventListener('touchend', () => { mouseDown = false })

// Keyboard
window.addEventListener('keydown', e => {
  if (e.code === 'Space') {
    e.preventDefault()
    transitionToNext()
  } else if (e.code === 'ArrowRight') {
    quotes.nextMood(1)
    relayout()
    // Burst
    ripple.disturb(W / 2, H / 2, 8, 10)
  } else if (e.code === 'ArrowLeft') {
    quotes.nextMood(-1)
    relayout()
    ripple.disturb(W / 2, H / 2, 8, 10)
  }
})

// --- Render ---
function render() {
  ctx.clearRect(0, 0, W, H)

  // Subtle field visualization
  ripple.renderDebug(ctx)

  // Draw words
  const fontSize = getFontSize()
  ctx.font = `${fontSize}px "IBM Plex Mono", monospace`
  ctx.textBaseline = 'top'

  const quote = quotes.current()
  const mood = quote?.mood || 'profound'
  const moodColor = MOOD_COLORS[mood] || { hue: 210, sat: 40, light: 55 }

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

    // Apply transition alpha
    alpha *= transitionAlpha

    ctx.fillStyle = `hsla(${hue}, ${sat}%, ${light}%, ${alpha})`
    ctx.fillText(w.text, w.x, w.y)
  }
}

// --- Ambient ripples ---
function ambientRipple() {
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
  updateParticles(
    words,
    (x, y) => ripple.gradient(x, y),
    0.9,   // ripple force
    0.015, // spring
    0.88,  // damping
  )
  render()
  requestAnimationFrame(loop)
}

// --- Init ---
function init() {
  window.addEventListener('resize', resize)
  resize()
  initialSplash()
  
  // Ambient ripples
  setInterval(ambientRipple, 3500)
  
  // Auto-advance quotes every 12 seconds
  setInterval(() => {
    if (!mouseDown) transitionToNext()
  }, 12000)

  loop()
}

init()
