# Email Ripples

Your 333,000 Gmail emails visualized as an interactive water ripple surface, powered by [@chenglou/pretext](https://github.com/chenglou/pretext).

Pull quotes from 22 years of email history — funny, sad, outrageous, profound — float as text on a liquid surface. Click and drag to send ripples through your digital history.

## Architecture

```
email-ripples/
├── scripts/           # Python scripts to extract & analyze emails
│   ├── 01_export.py   # Gmail API bulk export (headers + snippets)
│   ├── 02_analyze.py  # Theme analysis + quote extraction via Claude API
│   └── credentials/   # OAuth creds (gitignored)
├── src/               # Vite + TypeScript frontend
│   ├── main.ts        # Entry point
│   ├── ripple-field.ts # Wave simulation engine
│   ├── word-layout.ts  # Pretext-powered text layout
│   ├── quotes.ts       # Quote data + rotation logic
│   └── style.css
├── public/
├── data/              # Generated quote JSON (gitignored until curated)
│   └── quotes.json
├── package.json
├── tsconfig.json
├── vite.config.ts
└── README.md
```

## Setup

### Step 1: Email Export

You have two options:

#### Option A: Google Takeout (Recommended for full archive)
1. Go to https://takeout.google.com
2. Deselect all, then select only **Mail**
3. Choose `.mbox` format
4. Download and extract
5. Run: `python scripts/01_parse_mbox.py ~/path/to/All\ mail.mbox`

#### Option B: Gmail API (Targeted smart sampling)
1. Go to https://console.cloud.google.com
2. Create a project, enable Gmail API
3. Create OAuth 2.0 credentials (Desktop app)
4. Download `credentials.json` to `scripts/credentials/`
5. Run: `python scripts/01_export.py`

### Step 2: Analyze & Extract Quotes

Pick ANY LLM provider. AnythingLLM (100% local) is recommended for privacy.

```bash
# ── OPTION A: AnythingLLM (local, private, RAG-powered) ──
# 1. Install AnythingLLM Desktop → https://anythingllm.com
# 2. Set up a local LLM (built-in Gemma, or connect Ollama)
# 3. Settings → Developer API → Create API Key
export ANYTHINGLLM_API_KEY=your-key-here
python scripts/02_analyze.py --provider anythingllm
# First run uploads emails to workspace for RAG search
# Subsequent runs: add --skip-upload to reuse embeddings

# ── OPTION B: Ollama (local, free, no key needed) ──
# ollama pull llama3.1 && ollama serve
python scripts/02_analyze.py --provider ollama

# ── OPTION C: Any cloud provider ──
export OPENAI_API_KEY=sk-...           # OpenAI
export ANTHROPIC_API_KEY=sk-ant-...    # Anthropic
export GOOGLE_API_KEY=AI...            # Google Gemini
python scripts/02_analyze.py           # auto-detects from env var

# ── OPTION D: Free cloud via Groq ──
export OPENAI_COMPATIBLE_BASE_URL=https://api.groq.com/openai/v1
export OPENAI_COMPATIBLE_API_KEY=gsk_...
export OPENAI_COMPATIBLE_MODEL=llama-3.3-70b-versatile
python scripts/02_analyze.py --provider openai-compatible

# ── Dry run (preview without calling any LLM) ──
python scripts/02_analyze.py --dry-run
```

This samples ~5,000 emails across all eras, identifies themes, and extracts ~200 pull quotes tagged by mood.

### Step 3: Build & Deploy

```bash
npm install
npm run dev          # local dev server
npm run build        # production build
npm run deploy       # deploy to GitHub Pages
```

## Controls

- **Click** — drop a ripple at that point
- **Click & drag** — draw a continuous ripple trail
- **Spacebar** — cycle to next quote batch
- **Arrow keys** — navigate between mood categories
- Ambient ripples fire automatically every few seconds

## Credits

- Ripple physics inspired by [jeantimex/ripples](https://github.com/jeantimex/ripples)
- Text layout by [chenglou/pretext](https://github.com/chenglou/pretext)
- Your 22 years of Gmail history
