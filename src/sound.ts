/**
 * Sound engine — generative ambient audio for the ripple surface.
 *
 * Ported from the sound-preview.html prototype. Uses the native Web Audio
 * API (no Tone.js dependency) so the bundle stays small and CPU usage low.
 *
 * Lifecycle:
 *   - constructed in a disabled state (browser autoplay policy)
 *   - enable() must run inside a user gesture (e.g. click handler)
 *   - subsequent triggers do nothing while disabled
 */

type MoodTone = {
  root: number
  osc1: OscillatorType
  osc2: OscillatorType
  filter: number
  q: number
  noise: number
  wet: number
  detune: number
}

const MOOD_TONES: Record<string, MoodTone> = {
  mundane:     { root: 65,  osc1: 'triangle', osc2: 'sine',     filter: 200, q: 1, noise: 0.008, wet: 0.3,  detune: 0.3 },
  tender:      { root: 82,  osc1: 'sine',     osc2: 'sine',     filter: 350, q: 0.5, noise: 0.012, wet: 0.5, detune: 0.5 },
  nostalgic:   { root: 55,  osc1: 'triangle', osc2: 'triangle', filter: 280, q: 2, noise: 0.02,  wet: 0.6,  detune: 1.5 },
  funny:       { root: 130, osc1: 'square',   osc2: 'sawtooth', filter: 500, q: 1, noise: 0.005, wet: 0.2,  detune: 0.8 },
  absurd:      { root: 147, osc1: 'sawtooth', osc2: 'square',   filter: 600, q: 3, noise: 0.015, wet: 0.25, detune: 3 },
  sad:         { root: 44,  osc1: 'sine',     osc2: 'triangle', filter: 120, q: 4, noise: 0.025, wet: 0.65, detune: 0.2 },
  existential: { root: 38,  osc1: 'sine',     osc2: 'sine',     filter: 100, q: 6, noise: 0.03,  wet: 0.7,  detune: 0.1 },
  angry:       { root: 55,  osc1: 'sawtooth', osc2: 'sawtooth', filter: 350, q: 8, noise: 0.04,  wet: 0.15, detune: 5 },
  outrageous:  { root: 73,  osc1: 'square',   osc2: 'sawtooth', filter: 400, q: 5, noise: 0.03,  wet: 0.2,  detune: 4 },
  profound:    { root: 62,  osc1: 'sine',     osc2: 'triangle', filter: 250, q: 1, noise: 0.01,  wet: 0.7,  detune: 0.4 },
}

const REST_DRONE_GAIN = 0.015
const REST_NOISE_GAIN = 0.008
const MASTER_GAIN = 0.8

function createReverb(actx: AudioContext, duration: number, decay: number): ConvolverNode {
  const len = Math.floor(actx.sampleRate * duration)
  const impulse = actx.createBuffer(2, len, actx.sampleRate)
  for (let ch = 0; ch < 2; ch++) {
    const data = impulse.getChannelData(ch)
    for (let i = 0; i < len; i++) {
      data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, decay)
    }
  }
  const conv = actx.createConvolver()
  conv.buffer = impulse
  return conv
}

function createPinkNoise(actx: AudioContext): AudioBufferSourceNode {
  const bufferSize = 2 * actx.sampleRate
  const buffer = actx.createBuffer(1, bufferSize, actx.sampleRate)
  const data = buffer.getChannelData(0)
  let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0
  for (let i = 0; i < bufferSize; i++) {
    const white = Math.random() * 2 - 1
    b0 = 0.99886 * b0 + white * 0.0555179
    b1 = 0.99332 * b1 + white * 0.0750759
    b2 = 0.96900 * b2 + white * 0.1538520
    b3 = 0.86650 * b3 + white * 0.3104856
    b4 = 0.55000 * b4 + white * 0.5329522
    b5 = -0.7616 * b5 - white * 0.0168980
    data[i] = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362) * 0.05
    b6 = white * 0.115926
  }
  const node = actx.createBufferSource()
  node.buffer = buffer
  node.loop = true
  return node
}

export type TimelapseSpeed = 1 | 2 | 4 | 8
const TL_BPM_FOR_SPEED: Record<TimelapseSpeed, number> = { 1: 60, 2: 75, 4: 95, 8: 115 }
const TL_DRONE_GAIN_FOR_SPEED: Record<TimelapseSpeed, number> = { 1: 0.06, 2: 0.08, 4: 0.10, 8: 0.12 }
const TL_SHIMMER_GAIN_FOR_SPEED: Record<TimelapseSpeed, number> = { 1: 0, 2: 0, 4: 0.008, 8: 0.018 }

export class SoundEngine {
  private actx: AudioContext | null = null
  private enabled = false
  private isDragging = false
  private currentMood = 'mundane'

  private masterGain!: GainNode
  private convolver!: ConvolverNode
  private regularBus!: GainNode
  private droneOsc1!: OscillatorNode
  private droneOsc2!: OscillatorNode
  private droneGain!: GainNode
  private droneFilter!: BiquadFilterNode
  private noiseGain!: GainNode
  private interactOsc!: OscillatorNode
  private interactGain!: GainNode
  private interactFilter!: BiquadFilterNode

  // Time-lapse bed (drone, heartbeat pulse, high shimmer)
  private tlBus!: GainNode
  private tlDroneOsc1!: OscillatorNode
  private tlDroneOsc2!: OscillatorNode
  private tlDroneGain!: GainNode
  private tlDroneFilter!: BiquadFilterNode
  private tlPulseGain!: GainNode
  private tlShimmerOsc!: OscillatorNode
  private tlShimmerGain!: GainNode
  private tlShimmerFilter!: BiquadFilterNode
  private tlActive = false
  private tlSpeed: TimelapseSpeed = 1
  private pulseTimerId: number | null = null

  /** Whether audio is currently active and producing sound. */
  get isEnabled(): boolean {
    return this.enabled && this.actx !== null
  }

  /**
   * Enable audio. Must be called inside a user-gesture handler the first time
   * (browser autoplay policy). Subsequent enable() calls just unmute.
   */
  enable(): void {
    if (!this.actx) {
      this.initGraph()
    } else if (this.actx.state === 'suspended') {
      this.actx.resume().catch(() => {})
    }
    this.enabled = true
    if (this.actx) {
      const now = this.actx.currentTime
      this.masterGain.gain.cancelScheduledValues(now)
      this.masterGain.gain.linearRampToValueAtTime(MASTER_GAIN, now + 0.2)
    }
  }

  disable(): void {
    this.enabled = false
    if (!this.actx) return
    const now = this.actx.currentTime
    this.masterGain.gain.cancelScheduledValues(now)
    this.masterGain.gain.linearRampToValueAtTime(0, now + 0.2)
  }

  toggle(): boolean {
    if (this.enabled) this.disable()
    else this.enable()
    return this.enabled
  }

  setMood(mood: string): void {
    this.currentMood = mood
    if (!this.actx) return
    const t = MOOD_TONES[mood] || MOOD_TONES.mundane
    const now = this.actx.currentTime

    this.droneOsc1.type = t.osc1
    this.droneOsc2.type = t.osc2

    this.droneOsc1.frequency.cancelScheduledValues(now)
    this.droneOsc1.frequency.linearRampToValueAtTime(t.root, now + 1.5)
    this.droneOsc2.frequency.cancelScheduledValues(now)
    this.droneOsc2.frequency.linearRampToValueAtTime(t.root + t.detune, now + 1.5)
    this.interactOsc.frequency.cancelScheduledValues(now)
    this.interactOsc.frequency.linearRampToValueAtTime(t.root * 2, now + 1.5)

    this.droneFilter.frequency.cancelScheduledValues(now)
    this.droneFilter.frequency.linearRampToValueAtTime(t.filter, now + 1.5)
    this.droneFilter.Q.cancelScheduledValues(now)
    this.droneFilter.Q.linearRampToValueAtTime(t.q, now + 1.5)

    this.noiseGain.gain.cancelScheduledValues(now)
    this.noiseGain.gain.linearRampToValueAtTime(t.noise, now + 1.5)
  }

  triggerPing(x: number, _y: number, screenW: number): void {
    if (!this.actx || !this.enabled) return
    const actx = this.actx
    const now = actx.currentTime

    const freq = 800 + (x / Math.max(1, screenW)) * 1200 + Math.random() * 400
    const osc = actx.createOscillator()
    osc.type = 'sine'
    osc.frequency.value = freq

    const gain = actx.createGain()
    gain.gain.setValueAtTime(0, now)
    gain.gain.linearRampToValueAtTime(0.12, now + 0.005)
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.5)

    osc.connect(gain)
    gain.connect(this.convolver)
    gain.connect(this.regularBus)

    osc.start(now)
    osc.stop(now + 0.6)
  }

  onDragStart(): void {
    if (!this.actx || !this.enabled) return
    this.isDragging = true
    const now = this.actx.currentTime

    this.droneGain.gain.cancelScheduledValues(now)
    this.droneGain.gain.linearRampToValueAtTime(0.12, now + 0.4)

    this.noiseGain.gain.cancelScheduledValues(now)
    this.noiseGain.gain.linearRampToValueAtTime(0.04, now + 0.4)

    this.interactGain.gain.cancelScheduledValues(now)
    this.interactGain.gain.linearRampToValueAtTime(0.08, now + 0.2)

    this.droneFilter.frequency.cancelScheduledValues(now)
    this.droneFilter.frequency.linearRampToValueAtTime(400, now + 0.4)
  }

  onDragMove(speed: number, x: number, y: number, screenW: number, screenH: number): void {
    if (!this.actx || !this.enabled || !this.isDragging) return
    const actx = this.actx
    const now = actx.currentTime
    const t = MOOD_TONES[this.currentMood] || MOOD_TONES.mundane
    const clampedSpeed = Math.min(speed / 10, 1)

    const interactFilterTarget = t.filter + 600 * clampedSpeed
    this.interactFilter.frequency.cancelScheduledValues(now)
    this.interactFilter.frequency.linearRampToValueAtTime(interactFilterTarget, now + 0.08)

    const droneFilterTarget = t.filter + 400 * clampedSpeed
    this.droneFilter.frequency.cancelScheduledValues(now)
    this.droneFilter.frequency.linearRampToValueAtTime(droneFilterTarget, now + 0.1)

    const volTarget = 0.08 + clampedSpeed * 0.12
    this.interactGain.gain.cancelScheduledValues(now)
    this.interactGain.gain.linearRampToValueAtTime(volTarget, now + 0.08)

    const yNorm = 1 - (y / Math.max(1, screenH))
    const pitchMult = 0.8 + yNorm * 0.8
    this.interactOsc.frequency.cancelScheduledValues(now)
    this.interactOsc.frequency.linearRampToValueAtTime(t.root * 2 * pitchMult, now + 0.08)

    const xNorm = (x / Math.max(1, screenW)) - 0.5
    this.droneOsc2.frequency.cancelScheduledValues(now)
    this.droneOsc2.frequency.linearRampToValueAtTime(t.root * 1.003 + xNorm * 4, now + 0.1)
  }

  onDragEnd(): void {
    if (!this.actx || !this.enabled) return
    this.isDragging = false
    const now = this.actx.currentTime
    const t = MOOD_TONES[this.currentMood] || MOOD_TONES.mundane

    this.droneGain.gain.cancelScheduledValues(now)
    this.droneGain.gain.linearRampToValueAtTime(REST_DRONE_GAIN, now + 1.2)

    this.noiseGain.gain.cancelScheduledValues(now)
    this.noiseGain.gain.linearRampToValueAtTime(REST_NOISE_GAIN, now + 1)

    this.interactGain.gain.cancelScheduledValues(now)
    this.interactGain.gain.linearRampToValueAtTime(0, now + 0.8)

    this.interactFilter.frequency.cancelScheduledValues(now)
    this.interactFilter.frequency.linearRampToValueAtTime(200, now + 1)

    this.droneFilter.frequency.cancelScheduledValues(now)
    this.droneFilter.frequency.linearRampToValueAtTime(t.filter, now + 1.2)

    this.droneOsc2.frequency.cancelScheduledValues(now)
    this.droneOsc2.frequency.linearRampToValueAtTime(t.root * 1.003, now + 1)
  }

  triggerTransitionSwell(): void {
    if (!this.actx || !this.enabled) return
    const now = this.actx.currentTime

    this.droneGain.gain.cancelScheduledValues(now)
    this.droneGain.gain.linearRampToValueAtTime(0.05, now + 0.3)
    this.droneGain.gain.linearRampToValueAtTime(REST_DRONE_GAIN, now + 1.8)

    this.noiseGain.gain.cancelScheduledValues(now)
    this.noiseGain.gain.linearRampToValueAtTime(0.02, now + 0.3)
    this.noiseGain.gain.linearRampToValueAtTime(REST_NOISE_GAIN, now + 1.8)
  }

  private initGraph(): void {
    const Ctor: typeof AudioContext =
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext ?? AudioContext
    const actx = new Ctor()
    this.actx = actx

    this.masterGain = actx.createGain()
    this.masterGain.gain.value = 0
    this.masterGain.connect(actx.destination)

    // Two parallel buses feed the master so we can crossfade between the
    // regular drag-driven layer and the time-lapse playback bed without
    // touching the underlying nodes individually.
    this.regularBus = actx.createGain()
    this.regularBus.gain.value = 1
    this.regularBus.connect(this.masterGain)

    this.tlBus = actx.createGain()
    this.tlBus.gain.value = 0
    this.tlBus.connect(this.masterGain)

    this.convolver = createReverb(actx, 3, 2.5)
    const reverbGain = actx.createGain()
    reverbGain.gain.value = 0.3
    this.convolver.connect(reverbGain)
    reverbGain.connect(this.regularBus)

    const dryGain = actx.createGain()
    dryGain.gain.value = 0.7
    dryGain.connect(this.regularBus)

    // Drone path
    this.droneFilter = actx.createBiquadFilter()
    this.droneFilter.type = 'lowpass'
    this.droneFilter.frequency.value = 200
    this.droneFilter.Q.value = 2
    this.droneFilter.connect(dryGain)
    this.droneFilter.connect(this.convolver)

    this.droneGain = actx.createGain()
    this.droneGain.gain.value = REST_DRONE_GAIN
    this.droneGain.connect(this.droneFilter)

    this.droneOsc1 = actx.createOscillator()
    this.droneOsc1.type = 'sawtooth'
    this.droneOsc1.frequency.value = 65
    this.droneOsc1.connect(this.droneGain)
    this.droneOsc1.start()

    this.droneOsc2 = actx.createOscillator()
    this.droneOsc2.type = 'triangle'
    this.droneOsc2.frequency.value = 65.3
    this.droneOsc2.connect(this.droneGain)
    this.droneOsc2.start()

    // LFO sweeps the drone filter for slow organic motion
    const lfo = actx.createOscillator()
    lfo.type = 'sine'
    lfo.frequency.value = 0.08
    const lfoGain = actx.createGain()
    lfoGain.gain.value = 60
    lfo.connect(lfoGain)
    lfoGain.connect(this.droneFilter.frequency)
    lfo.start()

    // Pink noise for analog warmth
    this.noiseGain = actx.createGain()
    this.noiseGain.gain.value = REST_NOISE_GAIN
    const noiseFilter = actx.createBiquadFilter()
    noiseFilter.type = 'lowpass'
    noiseFilter.frequency.value = 600
    this.noiseGain.connect(noiseFilter)
    noiseFilter.connect(dryGain)
    const noise = createPinkNoise(actx)
    noise.connect(this.noiseGain)
    noise.start()

    // Drag interaction layer
    this.interactFilter = actx.createBiquadFilter()
    this.interactFilter.type = 'lowpass'
    this.interactFilter.frequency.value = 200
    this.interactFilter.connect(dryGain)
    this.interactFilter.connect(this.convolver)

    this.interactGain = actx.createGain()
    this.interactGain.gain.value = 0
    this.interactGain.connect(this.interactFilter)

    this.interactOsc = actx.createOscillator()
    this.interactOsc.type = 'sine'
    this.interactOsc.frequency.value = 130
    this.interactOsc.connect(this.interactGain)
    this.interactOsc.start()

    // ── Time-lapse bed (silent until enableTimelapse() crossfades the bus) ──
    // Drone: two slightly detuned sine oscillators around 55Hz through a soft lowpass.
    this.tlDroneFilter = actx.createBiquadFilter()
    this.tlDroneFilter.type = 'lowpass'
    this.tlDroneFilter.frequency.value = 380
    this.tlDroneFilter.Q.value = 0.7
    this.tlDroneFilter.connect(this.tlBus)

    this.tlDroneGain = actx.createGain()
    this.tlDroneGain.gain.value = TL_DRONE_GAIN_FOR_SPEED[1]
    this.tlDroneGain.connect(this.tlDroneFilter)

    this.tlDroneOsc1 = actx.createOscillator()
    this.tlDroneOsc1.type = 'sine'
    this.tlDroneOsc1.frequency.value = 55
    this.tlDroneOsc1.connect(this.tlDroneGain)
    this.tlDroneOsc1.start()

    this.tlDroneOsc2 = actx.createOscillator()
    this.tlDroneOsc2.type = 'sine'
    this.tlDroneOsc2.frequency.value = 55.4
    this.tlDroneOsc2.connect(this.tlDroneGain)
    this.tlDroneOsc2.start()

    // Pulse gain — transient ticks (created on each fireTick) feed this gain bus.
    this.tlPulseGain = actx.createGain()
    this.tlPulseGain.gain.value = 1
    this.tlPulseGain.connect(this.tlBus)

    // Shimmer: triangle around 880Hz through a bandpass — silent at 1x/2x.
    this.tlShimmerFilter = actx.createBiquadFilter()
    this.tlShimmerFilter.type = 'bandpass'
    this.tlShimmerFilter.frequency.value = 2200
    this.tlShimmerFilter.Q.value = 6
    this.tlShimmerFilter.connect(this.tlBus)

    this.tlShimmerGain = actx.createGain()
    this.tlShimmerGain.gain.value = 0
    this.tlShimmerGain.connect(this.tlShimmerFilter)

    this.tlShimmerOsc = actx.createOscillator()
    this.tlShimmerOsc.type = 'triangle'
    this.tlShimmerOsc.frequency.value = 880
    this.tlShimmerOsc.connect(this.tlShimmerGain)
    this.tlShimmerOsc.start()

    this.setMood(this.currentMood)
  }

  // ── Time-lapse bed control ─────────────────────────────────────

  enableTimelapse(): void {
    if (!this.actx || this.tlActive) return
    this.tlActive = true
    const now = this.actx.currentTime
    // ~1.2s crossfade between buses
    this.regularBus.gain.cancelScheduledValues(now)
    this.regularBus.gain.linearRampToValueAtTime(0, now + 1.2)
    this.tlBus.gain.cancelScheduledValues(now)
    this.tlBus.gain.linearRampToValueAtTime(1, now + 1.2)
    this.applyTimelapseSpeed(this.tlSpeed)
    this.startPulse()
  }

  disableTimelapse(): void {
    if (!this.actx || !this.tlActive) return
    this.tlActive = false
    const now = this.actx.currentTime
    this.regularBus.gain.cancelScheduledValues(now)
    this.regularBus.gain.linearRampToValueAtTime(1, now + 1.2)
    this.tlBus.gain.cancelScheduledValues(now)
    this.tlBus.gain.linearRampToValueAtTime(0, now + 1.2)
    this.stopPulse()
  }

  /** Update the time-lapse speed (1 / 2 / 4 / 8). Pulse, drone, shimmer all reflect smoothly. */
  setTimelapseSpeed(speed: TimelapseSpeed): void {
    this.tlSpeed = speed
    this.applyTimelapseSpeed(speed)
    if (this.tlActive) {
      // Restart pulse interval at the new tempo. Phase resets but at ambient
      // volumes the discontinuity is inaudible.
      this.startPulse()
    }
  }

  private applyTimelapseSpeed(speed: TimelapseSpeed): void {
    if (!this.actx) return
    const now = this.actx.currentTime
    // 1.5s smoothing on every gain change so the bed never jars on speed switches.
    this.tlDroneGain.gain.cancelScheduledValues(now)
    this.tlDroneGain.gain.linearRampToValueAtTime(TL_DRONE_GAIN_FOR_SPEED[speed], now + 1.5)
    this.tlShimmerGain.gain.cancelScheduledValues(now)
    this.tlShimmerGain.gain.linearRampToValueAtTime(TL_SHIMMER_GAIN_FOR_SPEED[speed], now + 1.5)
  }

  private startPulse(): void {
    this.stopPulse()
    if (!this.actx) return
    const period = 60000 / TL_BPM_FOR_SPEED[this.tlSpeed]
    // Fire one immediately, then on interval, so the user hears a tick the
    // moment the bed comes up rather than waiting up to a full period.
    this.fireTick()
    this.pulseTimerId = window.setInterval(() => this.fireTick(), period)
  }

  private stopPulse(): void {
    if (this.pulseTimerId !== null) {
      clearInterval(this.pulseTimerId)
      this.pulseTimerId = null
    }
  }

  private fireTick(): void {
    // Skip oscillator creation entirely when sound is disabled — saves CPU
    // while time-lapse is active under master mute. The interval keeps
    // running so unmuting picks back up on the next period.
    if (!this.actx || !this.enabled) return
    const now = this.actx.currentTime
    const osc = this.actx.createOscillator()
    osc.type = 'sine'
    osc.frequency.setValueAtTime(80, now)
    osc.frequency.exponentialRampToValueAtTime(35, now + 0.22)
    const gain = this.actx.createGain()
    gain.gain.setValueAtTime(0, now)
    gain.gain.linearRampToValueAtTime(0.16, now + 0.02)
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.22)
    osc.connect(gain)
    gain.connect(this.tlPulseGain)
    osc.start(now)
    osc.stop(now + 0.25)
  }
}
