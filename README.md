# Email Ripples

An interactive water ripple visualization of 512,780 Gmail emails spanning 22 years (2004–2026), powered by [@chenglou/pretext](https://github.com/chenglou/pretext).

**[→ View the live site](https://vipdiv.github.io/ripple/)**

---

## What is this?

Over half a million emails were exported from a single Gmail account that's been active since August 2004 — just months after Gmail launched by invitation only. A sampling of ~5,000 emails was analyzed by AI to extract ~1,460 pull quotes tagged by mood: funny, sad, outrageous, profound, tender, absurd, nostalgic, existential, angry, and mundane.

These quotes now float as text on a liquid surface. Click and drag to send ripples through 22 years of digital life.

### Themes

Analysis of the quotes revealed 10 recurring life themes across the archive:

- **Arts, Dance & Performance** (414 quotes) — a life woven through Houston's arts scene
- **Career & Hustle** (213) — job applications, side gigs, career pivots across multiple industries
- **Family & Togetherness** (209) — the logistics of love: dinners, birthdays, rodeo trips
- **Community & Volunteering** (171) — student council, bake sales, hurricane relief
- **Resilience & Crisis** (138) — hurricanes, illness, financial freefall, and the emails that got people through
- **Student Life & University** (107) — quiz deadlines and Spring Break at the University of Houston
- **Financial Reality** (102) — overdrawn accounts, debt notices, negative net worth
- **Early Internet & Social Media** (90) — MySpace, Friendster, Facebook wall posts, Google Buzz
- **Cultural Identity** (61) — Diwali, Hindi classes, dharma, building brown pride on a Texas campus
- **The Mundane Beautiful** (38) — taco receipts, Red Lobster cravings, watermelon at watch parties

Each theme can be drilled down further using subtopic tags (suchu dance, uber, hurricane katrina, etc.) — see the tag cloud on the live site.

### Timeline narrative

*It begins in 2004 with three emails and an invitation from a high school teacher. The early years are loud with student council drama, bake sale logistics, and MySpace friend requests — a young person building community in every direction at once. By the late 2000s, the tone shifts: job applications multiply, the recession hits, and emails start carrying the weight of real life — a parent's illness, financial struggle, career reinvention. Through it all, the arts thread never breaks: dance performances, gallery volunteering, documentary dreams, and a creative partnership that runs like a quiet current beneath everything else. The 2020s bring volume — 50,000 emails a year — but also tenderness. Family gatherings get harder to schedule and more precious when they happen. Twenty-two years of inbox, and the pattern is clear: this is someone who keeps showing up.*

## Privacy

All personal names have been replaced with pseudonyms drawn from the names of Gmail's original development team and early Google engineers — a small tribute to the people who built the platform that held these messages for two decades.

Phone numbers, email addresses, and other identifying information have been removed. Any resemblance to real individuals is coincidental.

The anonymization is handled by `scripts/03_anonymize.py`, which anyone can run on their own data.

---

## How it was made

### The pipeline

```
Google Takeout (.mbox)
        |
   01_parse_mbox.py     ->  streams 35GB, samples ~5,000 human emails
        |
   02_analyze.py        ->  LLM extracts quotes, tags moods
        |                       |
   03_anonymize.py      ->  replaces names, strips PII  (optional, recommended)
        |                       |
   04_tag.py            ->  adds subtopic tags           (optional)
        |
   quotes.json          ->  static data for the ripple site
        |
   Vite + Pretext       ->  interactive visualization
        |
   GitHub Pages         ->  live at vipdiv.github.io/ripple
```

**Every script after Step 2 is optional.** The site works with just the raw quotes from the analyzer. Each script reads `quotes.json`, enhances it, and saves it back. Run whichever ones you want, skip the rest. Nothing breaks.

| Script | Required? | What it does |
|--------|-----------|-------------|
| `01_parse_mbox.py` | Yes | Parses your email archive, samples emails for analysis |
| `02_analyze.py` | Yes | Sends samples to an LLM, extracts quotes tagged by mood |
| `03_anonymize.py` | No (recommended) | Replaces names with pseudonyms, strips email/phone/SSN |
| `04_tag.py` | No | Adds subtopic tags for drill-down filtering on the site |

### Why Google Takeout and not the Gmail API?

Google Takeout exports your entire archive as a single `.mbox` file — a standard format that can be processed locally without any API keys, rate limits, or internet connection. For 500K+ emails, Takeout is dramatically faster than the Gmail API (which limits you to ~50 emails/second and would take hours of API calls).

That said, the Gmail API (`scripts/01_export.py`) is included as an alternative for targeted sampling if you don't want to download your entire archive.

### Why not process everything locally?

You absolutely can — and the project supports it. The `02_analyze.py` script works with Ollama (100% local, free) and AnythingLLM (local RAG-powered analysis).

In this project's case, local processing on a Windows laptop without an NVIDIA GPU was extremely slow (~13 minutes per batch, estimated 40+ hours total). The tradeoff was made to use Google's Gemini API instead, which completed in ~20 minutes. Since the emails were exported *from* Google in the first place, no new privacy exposure occurred — Google had already stored these messages for 22 years.

If you have a machine with a decent GPU, local processing via Ollama is the recommended path for maximum privacy.

### Why not live-query the Gmail API from the website?

Several reasons this was built as a static site with pre-extracted data:

- **Privacy** — a live connection would expose your Gmail inbox through the website
- **Speed** — extracting and analyzing quotes takes minutes, not milliseconds
- **Cost** — every visitor would trigger Gmail API calls + LLM calls
- **Rate limits** — Gmail API throttling would break any public-facing site
- **Offline works** — the static site works without internet, on any device, forever

### How sampling works

The full archive contained 512,780 emails. The parser filters out automated messages (newsletters, noreply addresses, notifications, marketing) leaving ~322,000 real human emails. From those, 800 are randomly sampled from each of 8 eras for equal time representation.

This means the visualization represents roughly **1.6%** of the total human email archive. Each time you re-run the pipeline, you get a different random sample. To increase coverage, adjust the `SAMPLES_PER_ERA` value in `01_parse_mbox.py`.

---

## Build your own

Want to make this with your own email archive? Here's the full pipeline. **Scripts 3 and 4 are optional** — skip them if you don't need anonymization or subtopic tags.

### Prerequisites

- Python 3.10+ with `pip install tqdm`
- Node.js 18+ with npm
- An email archive (Gmail via Google Takeout, or any `.mbox` file)
- An LLM (local via Ollama, or any cloud API)

### Step 1: Export your email (required)

**Option A: Google Takeout (recommended for full archive)**
1. Go to [takeout.google.com](https://takeout.google.com)
2. Deselect all, then select only **Mail**
3. Choose `.mbox` format, download and extract
4. Run: `python scripts/01_parse_mbox.py "/path/to/All mail Including Spam and Trash.mbox"`

**Option B: Gmail API (targeted sampling, no full download)**
1. Set up OAuth credentials at [console.cloud.google.com](https://console.cloud.google.com)
2. Run: `python scripts/01_export.py`

**Option C: Any .mbox file**

The parser works with any standard `.mbox` file, not just Gmail. If you have email archives from Thunderbird, Apple Mail, or other clients, point the parser at them.

### Step 2: Extract quotes with any LLM (required)

```bash
# LOCAL + FREE (recommended for privacy):
ollama pull llama3.1 && ollama serve
python scripts/02_analyze.py --provider ollama

# LOCAL + RAG-POWERED:
# Install AnythingLLM Desktop, set up a local model, create API key
export ANYTHINGLLM_API_KEY=your-key
python scripts/02_analyze.py --provider anythingllm

# CLOUD (fast, cheap -- use if local is too slow):
export GOOGLE_API_KEY=your-key          # Google Gemini
python scripts/02_analyze.py --provider google

export OPENAI_API_KEY=sk-...            # OpenAI
python scripts/02_analyze.py --provider openai

export ANTHROPIC_API_KEY=sk-ant-...     # Anthropic
python scripts/02_analyze.py            # auto-detects from env var

# FREE CLOUD (Groq):
export OPENAI_COMPATIBLE_BASE_URL=https://api.groq.com/openai/v1
export OPENAI_COMPATIBLE_API_KEY=gsk_...
export OPENAI_COMPATIBLE_MODEL=llama-3.3-70b-versatile
python scripts/02_analyze.py --provider openai-compatible

# PREVIEW (see what would happen without calling any LLM):
python scripts/02_analyze.py --dry-run
```

**Speed vs privacy tradeoff:**

| Provider | Privacy | Speed (5K emails) | Cost |
|----------|---------|-------------------|------|
| Ollama (CPU, no GPU) | 100% local | 4-40 hours | Free |
| Ollama (with GPU) | 100% local | 30-60 min | Free |
| AnythingLLM | 100% local | 1-4 hours | Free |
| Groq | Cloud | 20-30 min | Free tier |
| Google Gemini Flash | Cloud | 15-20 min | ~$10-15 |
| OpenAI GPT-4o-mini | Cloud | 10-15 min | ~$8-12 |

*Costs can vary significantly based on average email length, batch size, number of retries, and whether you need to re-run due to errors. The estimates above reflect real-world usage including typical retries — budget 2x the estimate to be safe. Local options (Ollama, AnythingLLM) are always free regardless of re-runs.*

### Step 3: Anonymize for privacy (optional, recommended)

**Skip this step if you're keeping the site private or don't mind names being visible.**

```bash
# Standard anonymization -- replaces names, strips PII:
python scripts/03_anonymize.py

# Preview what would change without modifying anything:
python scripts/03_anonymize.py --preview

# Keep specific public figure names unchanged:
python scripts/03_anonymize.py --keep "Rosa Parks,Martin Luther King"

# Add your own names that must be scrubbed:
python scripts/03_anonymize.py --add-names "MyBoss,MyEx,MyDoctor"

# Use a different pseudonym shuffle:
python scripts/03_anonymize.py --seed 99
```

The anonymizer:
- Detects South Asian and Western names automatically
- Replaces each name with a consistent pseudonym (same person = same fake name everywhere)
- Uses names from Gmail's founding team as pseudonyms
- Strips email addresses, phone numbers, SSNs, credit card numbers, IP addresses, and street addresses
- Saves a private `data/name_mapping.json` (gitignored) so you can look up who maps to whom
- Has no dependencies beyond Python's standard library

### Step 4: Tag subtopics (optional)

**Skip this step if you don't want the tag cloud drill-down feature on the site.**

```bash
# Auto-tag quotes with detected subtopics:
python scripts/04_tag.py

# Preview tags without saving:
python scripts/04_tag.py --preview

# Add custom tags specific to your life:
python scripts/04_tag.py --add "my band:rehearsal,gig,setlist"
python scripts/04_tag.py --add "camping:campsite,tent,hiking,trail"

# Only keep tags that appear 3+ times:
python scripts/04_tag.py --min-count 3
```

The tagger:
- Scans quote text and context for 37 built-in subtopics (uber, hurricane, facebook, wedding, etc.)
- Adds a `tags` array to each quote for the site's tag cloud feature
- Supports custom tags via `--add` for topics specific to your life
- Tags with fewer than `--min-count` matches are automatically removed
- If you skip this step, the site works normally — no tags means no tag cloud

### Step 5: Build and deploy

```bash
npm install
npm run dev          # local dev server at localhost:5173
npm run build        # production build to dist/
npm run deploy       # deploy to GitHub Pages
```

---

## Controls

- **Click and drag** — send ripples through the text
- **Spacebar** or **mouse wheel** — next quote
- **Left/Right arrow keys** — filter by mood
- **Theme dropdown** — filter by life theme (Arts, Career, Family, etc.)
- **Tag cloud** — when a theme is selected, drill down into subtopics
- **R** or click the toggle — cycle Art / Read / Invert display modes
- **F** or click the fullscreen toggle — enter or exit fullscreen (desktop only; mobile already handles this through the OS UI)
- **♪ sound off / sound on** — toggle ambient sound (off by default; the muted button gently pulses with a faint amber glow so it's easy to spot)
- **?** or click the info button — about this project
- Quotes auto-advance every 10 seconds
- Ambient ripples fire automatically
- On phones and tablets a thin tappable control bar appears at the bottom (mood, next, themes, mode); sound and info stay in the top right

## Sound design

A tap on the speaker icon turns on a generative ambient layer built with the Web Audio API — no audio files, every sound is synthesized at runtime:

- A faint warm drone (dual oscillators with slight detune, pink-noise bed, slow LFO sweeping the filter cutoff) sits below everything as the resting state
- Each click triggers a short glass-like ping with a slight pitch randomization
- Dragging opens the filter and raises the volume in real time — speed maps to brightness, vertical position maps to pitch, horizontal position detunes the drone
- The current quote's mood selects a different oscillator waveform, root frequency, filter character, and noise floor — a sad/existential quote sounds darker and grittier than a funny/absurd one. Transitions between moods crossfade smoothly
- Advancing a quote produces a soft tonal swell, like a breath
- The **sound off** button at the top right (a music note ♪ next to the label) pulses gently while sound is muted — opacity oscillates between 40% and 90% on a slow ~2.4 second cycle with a faint amber glow. When sound is on, the button reads as **sound on** and sits solid and still next to ART and ?.
- The first time you visit, a small tooltip near the sound button reminds you that sound is opt-in. It dismisses on tap and only appears once per browser session.

Sound is **off by default** and only initialized on the first toggle click (browser autoplay policy).

## Typography

Quotes are rendered in the actual default font Gmail used during each quote's era — a subtle detail that makes the text feel native to the time it was written.

| Era | Font | Why |
|-----|------|-----|
| 2004-2017 | Arial | Gmail's original default from launch day through 14 years of dominance |
| 2018-2021 | Roboto | Google's Material Design overhaul brought Roboto to Gmail around 2018 |
| 2022-now | Roboto | Closest publicly available match to Google Sans, Gmail's current proprietary interface font |

The font changes automatically as quotes transition between eras. A small "set in Arial" or "set in Roboto" label appears near the quote info as a nod to this history.

## Tech

- [Pretext](https://github.com/chenglou/pretext) for text measurement and layout
- [Vite](https://vite.dev) + TypeScript
- 2D wave height field simulation inspired by [jeantimex/ripples](https://github.com/jeantimex/ripples)
- Era-based typography matching Gmail's actual default fonts (2004-present)
- Generative ambient sound via the native [Web Audio API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API) — no audio files, no Tone.js dependency
- Three display modes (art / read / invert) keyed off CSS variables
- Responsive layout with a touch-only control bar for phones and tablets
- Deployed via GitHub Pages

## Run locally

```bash
npm install
npm run dev
```

## Credits

- Ripple physics inspired by [jeantimex/ripples](https://github.com/jeantimex/ripples)
- Text layout by [chenglou/pretext](https://github.com/chenglou/pretext)
- Pseudonyms from [Gmail's founding team](https://en.wikipedia.org/wiki/History_of_Gmail)
- Built with [Claude](https://claude.ai)
