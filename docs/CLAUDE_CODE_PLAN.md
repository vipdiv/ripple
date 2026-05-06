# Email Ripples — Implementation Plan: Speaker Pulse + Time-lapse Mode

You are Claude Code working on the Email Ripples project (https://github.com/vipdiv/ripple). This file is your complete brief for two features. Read it all before writing any code.

---

## How to ship this

**Two features, two branches, in this order.**

1. **Branch `feature/speaker-pulse`** off `main`. Implement Feature 1 (speaker pulse). Update README per the README section in this file. Commit and open a PR.
2. **Wait for the human to confirm Feature 1 is shipped** before starting Feature 2.
3. **Branch `feature/timelapse-mode`** off `main` (after speaker pulse is merged). Implement Feature 2 (time-lapse). Update README per the README section in this file. Commit and open a PR.

If anything in this brief is ambiguous, ask the human before deciding. The aesthetic of this piece is intentional — calm, observational, slightly melancholy. When choosing between two options, pick the quieter one.

---

# FEATURE 1 — SPEAKER ICON BREATHING PULSE

**The problem:** the muted speaker icon (top right) is hard to see on mobile, especially in bright sunlight. The mute slash makes it ambiguous-looking rather than discoverable.

**The fix:** when muted, the icon pulses gently to draw the eye.

## Visual spec

When muted:
- Opacity oscillates between roughly 40% and 90% on a slow ~2.4 second cycle (slow, breath-like — not a frantic blink).
- A faint warm amber glow (`box-shadow`) pulses in sync. Use the existing accent color if there is one. If not, use `rgba(201, 168, 106, 0.18)` for the glow.
- The mute slash stroke can shift to the warm accent color (`#c9a86a` or matching) when muted, so the whole icon reads as "intentionally off, click me" instead of "broken."

When unmuted:
- Solid, no animation, standard opacity (~85%).
- No glow.
- Standard stroke color.

## Implementation notes

- Pure CSS — two `@keyframes` (one for opacity, one for box-shadow), applied via a `.muted` class toggle.
- `ease-in-out infinite` so the pulse feels organic.
- Clean transition between muted/unmuted states — let CSS transitions handle the fade to solid, don't snap mid-pulse.
- Tap target stays at least 44×44px on mobile.

## Acceptance check

- Muted icon visibly pulses without being distracting.
- Findable on a phone in bright sunlight within ~1 second.
- Unmuted state is calm and still.
- No layout shift when toggling.

## README updates for Feature 1

Make these edits to `README.md`:

### Edit 1 — Update the Controls list

Find this line:
```
- **🔇 / 🔊** — toggle ambient sound (off by default)
```

Replace with:
```
- **🔇 / 🔊** — toggle ambient sound (off by default; the muted icon gently pulses with a faint amber glow so it's easy to spot)
```

### Edit 2 — Add a bullet to the Sound design section

Find this line (last bullet in the Sound design list, just before "Sound is **off by default**"):
```
- Advancing a quote produces a soft tonal swell, like a breath
```

Add this new bullet right after it:
```
- The speaker icon at the top right pulses gently while sound is muted — opacity oscillates between 40% and 90% on a slow ~2.4 second cycle with a faint amber glow. When sound is on, the icon is solid and still.
```

## Suggested commit message for Feature 1

```
feat(speaker): add breathing-pulse animation for muted speaker icon

Makes the muted state more discoverable on mobile, especially in bright
light where the small icon and slash were easy to miss. Pulse is subtle
(~2.4s cycle, 40-90% opacity, faint amber glow) and stops cleanly when
unmuted.
```

---

# FEATURE 2 — TIME-LAPSE MODE

**The concept:** a separate mode triggered by an hourglass icon. Watch 22 years of email pass by chronologically. The piece becomes a quiet observation experience — strictly chronological, calm, with a steady ambient audio bed that reacts gently to playback speed. Click/drag ripple interaction is paused during this mode.

This is **not** "regular mode but faster." It's a distinct mode with its own audio, visual rules, and interaction model.

## Entering and exiting time-lapse — transition matters

**Entering:**
1. User taps the hourglass icon.
2. The currently-displayed quote finishes whatever wobble/spring animation it's in. Don't interrupt it.
3. The ripple field damps out to flat (no in-flight ripples leftover from clicks).
4. Once the surface is settled, time-lapse takes over: quote interaction disables, dim filter fades in, scrubber slides up, audio bed crossfades.
5. **The font that was active at the moment of entering is locked for the entire time-lapse session.** Whether Arial or Roboto, no swapping during scrub. Even when the user scrubs across the 2018 boundary, font does not change. This is intentional — font swaps mid-scrub feel like a glitch.

**Exiting:**
1. User taps the hourglass again, OR taps the dimmed canvas, OR hits Escape on desktop.
2. Scrubber slides down, dim filter fades out, audio crossfades back to regular ambient.
3. The quote at the user's stopped position becomes the new "current" quote in regular mode.
4. Font snaps back to era-correct (Arial pre-2018, Roboto 2018+).
5. Click/drag interaction returns.

## Filter behavior during time-lapse

**All filters are bypassed.** Mood, theme, tag — none apply. Time-lapse always plays the full chronological sequence, August 2004 → April 2026, every quote in date order. When user exits, previously-active filters restore.

## The hourglass icon

- **Position:** bottom-right corner. ~22px from edges on desktop; clear of iOS Safari home indicator on mobile. 44×44px tap target.
- **Visual:** an hourglass shape — two triangular sand chambers inside a frame.
- **Sand animation:** the icon visually empties from top to bottom as the timeline progresses. At position 0 (Aug 2004), top chamber is full of accent-colored sand. At position 1 (Apr 2026), top is empty, bottom is full. Update continuously.
- **Active state:** colored border (accent), slight background tint, scales to ~94% to show "pressed in."

The sand-emptying detail is what makes the icon feel like a time control. Worth getting right.

## The scrubber panel

Slides up from the bottom when hourglass is tapped. Centered horizontally. Max-width ~720px desktop, near full-width on mobile.

**Layout left to right:**

1. **Play/pause button** — round, 34×34px, accent-bordered. Icon swaps between play triangle and pause bars.
2. **Speed pill** — small button showing `1x`, `2x`, `4x`, or `8x`. Tap to cycle through. When changed, briefly highlights in accent (~600ms). Tabular-numeric so digit doesn't shift width.
3. **Track** — horizontal line.
   - Background: thin faint line.
   - Filled portion: accent color.
   - Era boundary tick marks at: 2007-01-01, 2010-01-01, 2013-01-01, 2016-01-01, 2019-01-01, 2022-01-01, 2025-01-01. Slightly taller than regular ticks. Subtle.
   - **Playhead:** small accent-colored dot with a glow. Grows slightly when being dragged.
4. **Date readout** — right-aligned. Format: `8 / 2004` (numeric month / 4-digit year). Tabular-numeric. ~78px min-width. Same dim ink color as other UI labels.

**No era label anywhere in the time-lapse UI. No era-boundary flash animation.** User infers era from the date. Keep it quiet.

**Slide-in animation:** ~450ms, ease-out cubic-bezier, comes up from below with opacity fade.

## Playback behavior

**Speed mapping:**

| Speed | Full timeline traversal |
|-------|-------------------------|
| 1x    | ~60 seconds             |
| 2x    | ~30 seconds             |
| 4x    | ~15 seconds             |
| 8x    | ~7.5 seconds            |

**Quote display during playback:**

- Quotes appear one at a time, **fade in/out at their position** (no spring-settle, no big bounce, no wave-displacement settling). Calm.
- Crossfade duration: ~120ms during time-lapse (regular mode keeps its existing transition).
- The current quote at any moment = the most recent quote in the dataset whose date is ≤ the current timeline position.
- Strictly chronological. No filter, no shuffling.

**No click/drag ripple interaction:**

- Canvas clicks and touches do nothing on the canvas during time-lapse.
- Scrubber controls (play, speed, track, hourglass) are obviously still tappable.

**Tiny ambient ripples auto-fire:**

- Small, faint, non-interactive ripples at random positions.
- Probability per autoplay tick:
  - 1x → ~0.5% per tick (sparse)
  - 2x → ~1.2% per tick
  - 4x → ~2.5% per tick
  - 8x → ~5% per tick (constant gentle shimmer)
- Visually subtle — much smaller and more transparent than user-click ripples in regular mode.
- **No tiny ripples during manual scrubbing.** Only during autoplay.

## Manual scrubbing

- Mouse and touch both supported.
- Drag the playhead anywhere on the track → quote and date update live.
- **If autoplay was running when drag begins, pause it.**
- When drag ends, **stay at the released position. Do not auto-resume playback.** User must hit play again.
- Playhead grows slightly during drag (visual feedback).
- Tapping anywhere on the track (not just the playhead) jumps the playhead there.

## Audio bed during time-lapse

**Crossfade:** Entering time-lapse, regular ambient drone fades out over ~1.2s and time-lapse bed fades in over the same window. Reverse on exit.

**Components of the time-lapse bed:**

1. **Warm low drone** — two slightly detuned sine oscillators around 55Hz, lowpass-filtered ~380Hz. Always playing while time-lapse is active. Volume nudges up slightly with speed (0.06 base → up to ~0.12 at 8x). Smooth ramps over 1.5s.

2. **Heartbeat / clock tick** — soft low thump at a tempo that maps to playback speed. Each tick = quick sine sweep from ~80Hz down to ~35Hz, ~220ms long, peak gain ~0.16.
   - 1x → 60 BPM
   - 2x → 75 BPM
   - 4x → 95 BPM
   - 8x → 115 BPM
   - **Pulse only tracks autoplay speed. Manual scrubbing does not change the pulse rate.** Pulse keeps ticking at last-set speed regardless of whether autoplay is running.
   - When autoplay is paused (but time-lapse still active), the pulse can keep going as a "you're still in time-lapse" reminder.

3. **High shimmer** — a single triangle oscillator around 880Hz through a bandpass filter (~2200Hz center, Q ~6).
   - 1x and 2x → silent
   - 4x → very faint (~0.008)
   - 8x → faint but present (~0.018)
   - Smooth 1.5s ramps when speed changes.

**All speed transitions smoothed.** Every gain change uses `linearRampToValueAtTime` over ~1.5s. Never instant. Tapping the speed pill should feel a beat later in the audio, not instantly.

**Master mute respect:** If the speaker is muted when time-lapse starts, entire bed stays silent. The pulse, drone, shimmer all route through the existing master mute gain. Unmuting mid-time-lapse fades the bed in.

## Signaling that interaction is paused

Three combined signals so the user understands clicks won't do anything:

**1. Cursor change.**
On desktop, canvas cursor switches from `crosshair` to `default` while time-lapse is active.

**2. Subtle canvas dim/desaturate.**
While time-lapse is active, apply CSS filter to the stage:
```css
filter: brightness(0.78) saturate(0.85);
```
With a 0.6s ease transition on entry/exit. Communicates "we're observing now."

The scrubber, hourglass, and speaker should NOT be dimmed. Only the stage / quote canvas dims.

**3. First-time tooltip.**
First time user enters time-lapse mode in a session, show a small tooltip near top-center:

> **time-lapse mode** — ripples pause until you exit

- Appears ~300ms after time-lapse activates (so it doesn't compete with the slide-up animation).
- Auto-dismisses after 4 seconds with a fade.
- Stored in `sessionStorage` (not `localStorage`) so it shows once per browser session.
- Style: small, faint background pill, ink color text, accent for "time-lapse mode" emphasis.

## Mobile considerations

- Hourglass clears iOS Safari home indicator and is reachable with a thumb.
- Scrubber track on mobile: track itself can be 2px tall visually, but draggable area should be ≥24px tall (transparent padding wrapper).
- Speed pill ≥44px wide on mobile.
- Test scrubber drag with one thumb on a real phone — must feel responsive.

## What does NOT change

- Regular ripple physics and click-to-ripple interaction unchanged when not in time-lapse.
- Mood filter, theme filter, tag cloud, display modes (Art/Read/Invert), info modal, era-based font swapping in regular mode — none change.
- Existing audio system stays intact. The time-lapse bed is added alongside, not replacing.

## Implementation order (suggested commits within the branch)

1. Hourglass icon + empty scrubber panel that slides up. No playback yet, just the UI shell.
2. Manual scrubbing — quote and date update as you drag. No audio yet.
3. Autoplay — play/pause/speed cycling, animated progression.
4. Lockout signaling — cursor, dim filter, first-time tooltip.
5. Audio bed — drone, heartbeat with speed mapping, shimmer, smooth crossfades.
6. Tiny ambient ripples during autoplay.
7. Polish: hourglass sand animation, edge cases.

## Edge cases to handle

- **Speaker mute toggled during time-lapse** → audio bed respects the mute (fade out if muting, fade in if unmuting).
- **Speed changed during pause** → next playback uses new speed. Audio bed updates immediately on speed change regardless of play/pause state.
- **User reaches end of timeline (position 1.0)** → autoplay pauses. Stay at position 1.0 with final quote shown. User can drag back or exit.
- **User opens info modal during time-lapse** → pause autoplay, but keep audio bed running. Stay consistent.
- **Display mode (Art/Read/Invert) toggled during time-lapse** → still works. Dim filter stacks with display mode. Test in Invert mode specifically — make sure it reads cleanly.
- **Window resize during time-lapse** → scrubber re-centers. Quote re-renders at new center.

## Files likely to be touched

- `src/main.ts` — wire up new buttons, state machine for time-lapse
- `src/quotes.ts` — add a chronological-iteration helper that ignores filters
- `src/style.css` — hourglass styles, scrubber panel, dim filter, tooltip
- `src/sound.ts` — extend with the time-lapse bed (drone, heartbeat, shimmer)
- New: `src/timelapse.ts` — the playback loop, speed handling, position management. Self-contained module.
- `index.html` — add hourglass button SVG, scrubber panel markup, tooltip element.
- `README.md` — apply the README updates below.

## README updates for Feature 2

### Edit 1 — Add a new section called "Time-lapse mode"

Place it between the existing `## Controls` section and the existing `## Sound design` section.

Paste this:

```markdown
## Time-lapse mode

A separate way to experience the archive: watch 22 years of email pass by chronologically, like a film of an inbox aging in fast-forward.

Tap the **hourglass icon** in the bottom-right corner. The current ripple settles, the canvas dims slightly, and a scrubber slides up from the bottom. The hourglass itself visually empties from top to bottom as the timeline progresses.

Three ways to move through the archive:

- **Press play** — autoplay through every quote in chronological order, August 2004 to April 2026
- **Tap the speed pill** — cycle through `1x`, `2x`, `4x`, `8x`. At 1x the full 22 years take ~60 seconds. At 8x, ~7.5 seconds
- **Drag the playhead** — manually scrub anywhere on the timeline. Quote and date update live as you drag

The date readout (`8 / 2004` style — month / year) updates continuously. Tiny ambient ripples fire automatically during playback, scaling with speed — sparse drops at 1x, gentle constant shimmer at 8x.

**During time-lapse, the regular click-to-ripple interaction is paused.** The cursor changes to default and the canvas dims slightly to signal observation mode. A first-time tooltip appears the first time you enter the mode in a session as a reminder. Mood, theme, and tag filters are bypassed — time-lapse always plays the full archive in date order. Filters restore when you exit.

Tap the hourglass again, or tap anywhere on the dimmed canvas, to exit. You return to whatever quote you stopped on.
```

### Edit 2 — Update the Controls list

Find this line:
```
- **?** or click the info button — about this project
```

Add this new bullet right after it:
```
- **⏳ Hourglass** (bottom right) — open time-lapse mode, scrub through 22 years chronologically
```

### Edit 3 — Add a Sound design bullet

Find the line that reads:
```
- The speaker icon at the top right pulses gently while sound is muted...
```
(this should already exist after Feature 1 has shipped)

Add this new bullet right after it:
```
- During time-lapse mode, a separate audio bed crossfades in: a warm low drone plus a soft heartbeat-like pulse. The pulse rate maps to playback speed — roughly 60 BPM at 1x, 75 BPM at 2x, 95 BPM at 4x, 115 BPM at 8x. At 4x and 8x a faint high shimmer creeps in. Speed transitions are smoothed over ~1.5 seconds so the bed never jars. Manual scrubbing doesn't change the pulse rate — only autoplay speed does. The whole bed respects the master mute toggle.
```

### Edit 4 — Append to the Typography section

Find the closing line:
```
The font changes automatically as quotes transition between eras. A small "set in Arial" or "set in Roboto" label appears near the quote info as a nod to this history.
```

Add this paragraph right after:
```
During time-lapse mode the font is intentionally locked at whatever was active when the mode was entered. It does not swap when scrubbing across era boundaries — that would feel like a glitch during fast playback. The era-correct font returns the moment time-lapse exits.
```

## Suggested commit message for Feature 2

```
feat(timelapse): add hourglass scrubber for chronological 22-year playback

Adds a separate "watching" mode triggered by an hourglass icon in the
bottom-right. Includes manual scrubbing, autoplay at 1x/2x/4x/8x, a
speed-reactive heartbeat audio bed, tiny auto-firing ripples, and clear
signaling that interaction is paused (cursor + dim + first-time tooltip).
Font is locked during time-lapse to avoid mid-scrub glitches.
```

---

# Test checklist (both features)

- [ ] Speaker pulse looks right on iOS Safari and Android Chrome.
- [ ] Tapping hourglass settles in-flight quote wobble before transitioning.
- [ ] Font does NOT swap mid-scrub when crossing 2018 boundary inside time-lapse.
- [ ] Font DOES swap correctly when exiting time-lapse on a post-2018 quote.
- [ ] Heartbeat tempo audibly different at 1x vs 2x vs 4x vs 8x.
- [ ] Speed changes don't produce audible clicks or jumps.
- [ ] Master mute makes the time-lapse bed silent.
- [ ] Manual drag does not auto-resume playback.
- [ ] Tiny ripples don't spawn during manual drag.
- [ ] First-time tooltip appears on first entry, not on second entry of same session.
- [ ] Cursor changes from crosshair to default on desktop when entering time-lapse.
- [ ] Date readout is `8 / 2004` style.
- [ ] No era label appears anywhere during time-lapse.
- [ ] Mood/theme filters bypass during time-lapse but restore on exit.
- [ ] Dim filter doesn't make Invert mode (light background) look broken.

---

# When in doubt

Ask before deciding. The aesthetic is intentional — calm, observational, slightly melancholy. If a choice would push the piece toward "app-like" or "loud," pick the quieter one.
