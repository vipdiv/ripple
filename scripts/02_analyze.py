#!/usr/bin/env python3
"""
Email Quote Analyzer — Multi-LLM Edition + AnythingLLM
======================================================
Takes sampled emails from Phase 1 and uses YOUR choice of LLM to
extract pull quotes tagged by mood for the ripple visualization.

PROVIDERS:
  anythingllm     100% local via AnythingLLM desktop (RAG-powered)
  ollama          Local, free — just run ollama serve
  openai          GPT-4o, GPT-4, GPT-4o-mini, etc.
  anthropic       Claude Sonnet, Opus, Haiku, etc.
  google          Gemini Pro, Flash, etc.
  openai-compatible  Groq, Together, Fireworks, OpenRouter, etc.

SETUP:
  pip install requests tqdm

  # For cloud providers, also:
  pip install openai        # openai, ollama, openai-compatible
  pip install anthropic     # anthropic
  pip install google-generativeai  # google

USAGE:
  # AnythingLLM (local, private, RAG-powered — RECOMMENDED)
  python 02_analyze.py --provider anythingllm

  # AnythingLLM with custom settings
  python 02_analyze.py --provider anythingllm \\
    --anythingllm-url http://localhost:3001 \\
    --anythingllm-key your-api-key \\
    --anythingllm-workspace gmail-archive

  # Ollama (local, free)
  python 02_analyze.py --provider ollama --model llama3.1

  # OpenAI
  export OPENAI_API_KEY=sk-...
  python 02_analyze.py --provider openai --model gpt-4o-mini

  # Groq (free tier)
  export OPENAI_COMPATIBLE_BASE_URL=https://api.groq.com/openai/v1
  export OPENAI_COMPATIBLE_API_KEY=gsk_...
  export OPENAI_COMPATIBLE_MODEL=llama-3.3-70b-versatile
  python 02_analyze.py --provider openai-compatible

  # Dry run (see what would happen without calling any LLM)
  python 02_analyze.py --dry-run
"""

import os
import sys
import json
import time
import argparse
import requests
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from abc import ABC, abstractmethod

from tqdm import tqdm

DATA_DIR = Path(__file__).parent.parent / 'data'
SAMPLE_FILE = DATA_DIR / 'email_samples.jsonl'
QUOTES_FILE = DATA_DIR / 'quotes.json'

BATCH_SIZE = 30

ERAS = [
    ('2004-2006', 2004, 2007),
    ('2007-2009', 2007, 2010),
    ('2010-2012', 2010, 2013),
    ('2013-2015', 2013, 2016),
    ('2016-2018', 2016, 2019),
    ('2019-2021', 2019, 2022),
    ('2022-2024', 2022, 2025),
    ('2025-now',  2025, 2030),
]

EXTRACT_PROMPT = """You are analyzing personal emails from the era "{era}" to find memorable pull quotes for an art installation.

These are real emails from someone's 22-year Gmail archive (333,000+ emails). Find the most interesting, human, emotional, funny, profound, or outrageous lines.

RULES:
- Extract EXACT quotes from the email text — do not paraphrase
- Each quote should be 5-40 words (short enough to display as text art)
- Tag each with a mood: funny, sad, outrageous, profound, mundane, tender, angry, absurd, nostalgic, existential
- Include the approximate date and a brief context note
- Skip marketing language, automated text, signatures, legal disclaimers
- Prioritize lines that feel deeply human
- If an email has nothing interesting, skip it

Return ONLY valid JSON (no markdown, no backticks, no preamble):
{{
  "quotes": [
    {{
      "text": "the exact quote",
      "mood": "funny",
      "date": "2008-03-15",
      "era": "{era}",
      "context": "brief note about this email",
      "from_type": "friend"
    }}
  ]
}}

from_type: friend | family | coworker | teacher | stranger | self
If nothing good, return {{"quotes": []}}

{emails}"""

THEME_PROMPT = """Analyze these pull quotes from a 22-year email archive and identify major life themes.

Return ONLY valid JSON:
{{
  "themes": [
    {{
      "name": "theme name",
      "description": "one sentence",
      "color_hint": "a CSS color",
      "quote_count": 0
    }}
  ],
  "timeline_narrative": "2-3 sentence poetic summary of this person's email life"
}}

Quotes:
{quotes}"""


# ═══════════════════════════════════════════════
# LLM PROVIDERS
# ═══════════════════════════════════════════════

class LLMProvider(ABC):
    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 4000) -> str:
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    def setup_for_emails(self, samples: list[dict]):
        """Optional hook for providers that need setup (like AnythingLLM RAG)."""
        pass


# ───────────────────────────────────────────────
# AnythingLLM — local, RAG-powered, 100% private
# ───────────────────────────────────────────────

class AnythingLLMProvider(LLMProvider):
    """
    Uses AnythingLLM's local API to:
    1. Create a workspace for your email archive
    2. Upload email batches as documents
    3. Embed them for RAG search
    4. Query the workspace to find great quotes

    Everything stays on your machine.
    """

    def __init__(self, base_url: str = 'http://localhost:3001',
                 api_key: str = None, workspace: str = 'gmail-archive'):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key or os.environ.get('ANYTHINGLLM_API_KEY', '')
        self.workspace_slug = workspace
        self._workspace_ready = False

        # Test connection
        try:
            r = requests.get(f'{self.base_url}/api/v1/auth', headers=self._headers, timeout=5)
            if r.status_code == 403 and not self.api_key:
                print("\n⚠️  AnythingLLM requires an API key.")
                print("   In AnythingLLM: Settings → Developer API → Create API Key")
                print("   Then: export ANYTHINGLLM_API_KEY=your-key-here\n")
                sys.exit(1)
        except requests.ConnectionError:
            print(f"\n❌ Can't reach AnythingLLM at {self.base_url}")
            print("   Make sure AnythingLLM Desktop is running.\n")
            sys.exit(1)

    @property
    def _headers(self):
        h = {'Content-Type': 'application/json'}
        if self.api_key:
            h['Authorization'] = f'Bearer {self.api_key}'
        return h

    @property
    def name(self) -> str:
        return f"AnythingLLM (local, workspace: {self.workspace_slug})"

    def _ensure_workspace(self):
        """Create workspace if it doesn't exist."""
        if self._workspace_ready:
            return

        # Check if workspace exists
        r = requests.get(f'{self.base_url}/api/v1/workspaces', headers=self._headers)
        if r.ok:
            workspaces = r.json().get('workspaces', [])
            for ws in workspaces:
                if ws.get('slug') == self.workspace_slug:
                    self._workspace_ready = True
                    print(f"   Using existing workspace: {self.workspace_slug}")
                    return

        # Create it
        r = requests.post(
            f'{self.base_url}/api/v1/workspace/new',
            headers=self._headers,
            json={'name': self.workspace_slug}
        )
        if r.ok:
            slug = r.json().get('workspace', {}).get('slug', self.workspace_slug)
            self.workspace_slug = slug
            self._workspace_ready = True
            print(f"   Created workspace: {self.workspace_slug}")
        else:
            print(f"   ⚠️ Couldn't create workspace: {r.text}")

    def setup_for_emails(self, samples: list[dict]):
        """Upload email samples as documents to the workspace for RAG."""
        self._ensure_workspace()

        print(f"\n📤 Uploading {len(samples)} email samples to AnythingLLM...")
        print("   This enables RAG — the local model can search your emails.\n")

        # Upload in chunks of 50 emails per document (to keep doc sizes manageable)
        chunk_size = 50
        uploaded = 0

        for i in range(0, len(samples), chunk_size):
            chunk = samples[i:i + chunk_size]
            chunk_num = i // chunk_size + 1

            # Format emails as a text document
            text_parts = [f"=== EMAIL BATCH {chunk_num} ===\n"]
            for j, email in enumerate(chunk):
                text_parts.append(
                    f"--- Email {i + j + 1} ---\n"
                    f"Date: {email.get('date', 'unknown')}\n"
                    f"From: {email.get('from', 'unknown')}\n"
                    f"Subject: {email.get('subject', '(no subject)')}\n"
                    f"Body:\n{email.get('body', email.get('snippet', ''))[:2000]}\n\n"
                )

            doc_text = '\n'.join(text_parts)

            # Upload as raw text document
            r = requests.post(
                f'{self.base_url}/api/v1/document/raw-text',
                headers=self._headers,
                json={
                    'textContent': doc_text,
                    'metadata': {
                        'title': f'email-batch-{chunk_num:04d}',
                        'description': f'Gmail archive batch {chunk_num} ({len(chunk)} emails)',
                    }
                }
            )

            if r.ok:
                doc_data = r.json()
                doc_location = doc_data.get('documents', [{}])[0].get('location', '')

                if doc_location:
                    # Embed into workspace
                    embed_r = requests.post(
                        f'{self.base_url}/api/v1/workspace/{self.workspace_slug}/update-embeddings',
                        headers=self._headers,
                        json={'adds': [doc_location]}
                    )
                    if embed_r.ok:
                        uploaded += len(chunk)

            if (i // chunk_size + 1) % 10 == 0:
                print(f"   ... {uploaded}/{len(samples)} emails uploaded & embedded")

            time.sleep(0.5)  # be gentle

        print(f"   ✅ {uploaded} emails uploaded and embedded for RAG\n")

    def complete(self, prompt: str, max_tokens: int = 4000) -> str:
        """Chat with the workspace — RAG will pull relevant email context."""
        self._ensure_workspace()

        r = requests.post(
            f'{self.base_url}/api/v1/workspace/{self.workspace_slug}/chat',
            headers=self._headers,
            json={
                'message': prompt,
                'mode': 'query',  # RAG mode — searches embedded docs
            }
        )

        if r.ok:
            data = r.json()
            return data.get('textResponse', '')
        else:
            print(f"   ⚠️ Chat error: {r.status_code} {r.text[:200]}")
            return '{"quotes": []}'


# ───────────────────────────────────────────────
# Other providers (unchanged from before)
# ───────────────────────────────────────────────

class OpenAIProvider(LLMProvider):
    def __init__(self, model: str = 'gpt-4o'):
        try:
            from openai import OpenAI
        except ImportError:
            sys.exit("❌ pip install openai")
        key = os.environ.get('OPENAI_API_KEY')
        if not key:
            sys.exit("❌ Set OPENAI_API_KEY")
        self.client = OpenAI(api_key=key)
        self.model = model

    @property
    def name(self): return f"OpenAI ({self.model})"

    def complete(self, prompt, max_tokens=4000):
        r = self.client.chat.completions.create(
            model=self.model, max_tokens=max_tokens,
            messages=[{'role': 'user', 'content': prompt}], temperature=0.7)
        return r.choices[0].message.content or ''


class AnthropicProvider(LLMProvider):
    def __init__(self, model: str = 'claude-sonnet-4-20250514'):
        try:
            import anthropic
        except ImportError:
            sys.exit("❌ pip install anthropic")
        key = os.environ.get('ANTHROPIC_API_KEY')
        if not key:
            sys.exit("❌ Set ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=key)
        self.model = model

    @property
    def name(self): return f"Anthropic ({self.model})"

    def complete(self, prompt, max_tokens=4000):
        r = self.client.messages.create(
            model=self.model, max_tokens=max_tokens,
            messages=[{'role': 'user', 'content': prompt}])
        return r.content[0].text


class GoogleProvider(LLMProvider):
    def __init__(self, model: str = 'gemini-2.0-flash'):
        try:
            import google.generativeai as genai
        except ImportError:
            sys.exit("❌ pip install google-generativeai")
        key = os.environ.get('GOOGLE_API_KEY')
        if not key:
            sys.exit("❌ Set GOOGLE_API_KEY")
        genai.configure(api_key=key)
        self.inst = genai.GenerativeModel(model)
        self._name = model

    @property
    def name(self): return f"Google ({self._name})"

    def complete(self, prompt, max_tokens=4000):
        r = self.inst.generate_content(
            prompt, generation_config={'max_output_tokens': max_tokens, 'temperature': 0.7})
        return r.text


class OllamaProvider(LLMProvider):
    def __init__(self, model: str = 'llama3.1', base_url: str = 'http://localhost:11434'):
        try:
            from openai import OpenAI
        except ImportError:
            sys.exit("❌ pip install openai")
        self.client = OpenAI(base_url=f'{base_url}/v1', api_key='ollama')
        self.model = model
        try:
            import urllib.request
            urllib.request.urlopen(f'{base_url}/api/tags', timeout=3)
        except Exception:
            sys.exit(f"❌ Can't reach Ollama at {base_url}. Run: ollama serve && ollama pull {model}")

    @property
    def name(self): return f"Ollama ({self.model}, local)"

    def complete(self, prompt, max_tokens=4000):
        r = self.client.chat.completions.create(
            model=self.model, max_tokens=max_tokens,
            messages=[{'role': 'user', 'content': prompt}], temperature=0.7)
        return r.choices[0].message.content or ''


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, model=None, base_url=None, api_key=None):
        try:
            from openai import OpenAI
        except ImportError:
            sys.exit("❌ pip install openai")
        self._api_key = api_key or os.environ.get('OPENAI_COMPATIBLE_API_KEY', '')
        self._base_url = base_url or os.environ.get('OPENAI_COMPATIBLE_BASE_URL', '')
        self.model = model or os.environ.get('OPENAI_COMPATIBLE_MODEL', 'default')
        if not self._base_url: sys.exit("❌ Set OPENAI_COMPATIBLE_BASE_URL")
        if not self._api_key: sys.exit("❌ Set OPENAI_COMPATIBLE_API_KEY")
        self.client = OpenAI(base_url=self._base_url, api_key=self._api_key)

    @property
    def name(self):
        from urllib.parse import urlparse
        return f"{urlparse(self._base_url).hostname} ({self.model})"

    def complete(self, prompt, max_tokens=4000):
        r = self.client.chat.completions.create(
            model=self.model, max_tokens=max_tokens,
            messages=[{'role': 'user', 'content': prompt}], temperature=0.7)
        return r.choices[0].message.content or ''


# ═══════════════════════════════════════════════
# PROVIDER FACTORY
# ═══════════════════════════════════════════════

PROVIDER_HELP = """
┌──────────────────────────────────────────────────────────────┐
│  Provider            Env Vars / Requirements                 │
├──────────────────────┬───────────────────────────────────────┤
│  anythingllm         │ ANYTHINGLLM_API_KEY (+ app running)  │
│  ollama              │ (none — free & local)                 │
│  openai              │ OPENAI_API_KEY                        │
│  anthropic           │ ANTHROPIC_API_KEY                     │
│  google              │ GOOGLE_API_KEY                        │
│  openai-compatible   │ OPENAI_COMPATIBLE_API_KEY             │
│                      │ + OPENAI_COMPATIBLE_BASE_URL          │
└──────────────────────┴───────────────────────────────────────┘

AnythingLLM setup (RECOMMENDED — 100% local & private):
  1. Install AnythingLLM Desktop
  2. Configure an LLM (Ollama, built-in, etc.)
  3. Settings → Developer API → Create API Key
  4. export ANYTHINGLLM_API_KEY=your-key
  5. python 02_analyze.py --provider anythingllm

Free cloud options:
  Groq    https://api.groq.com/openai/v1    (free tier!)
  Google  Gemini Flash has a generous free tier
"""


def create_provider(name, model=None, args=None):
    if name == 'anythingllm':
        url = getattr(args, 'anythingllm_url', None) or 'http://localhost:3001'
        key = getattr(args, 'anythingllm_key', None) or None
        ws = getattr(args, 'anythingllm_workspace', None) or 'gmail-archive'
        return AnythingLLMProvider(base_url=url, api_key=key, workspace=ws)
    elif name == 'ollama':
        return OllamaProvider(model=model or 'llama3.1')
    elif name == 'openai':
        return OpenAIProvider(model=model or 'gpt-4o')
    elif name == 'anthropic':
        return AnthropicProvider(model=model or 'claude-sonnet-4-20250514')
    elif name == 'google':
        return GoogleProvider(model=model or 'gemini-2.0-flash')
    elif name == 'openai-compatible':
        return OpenAICompatibleProvider(model=model)
    else:
        print(f"❌ Unknown provider: {name}")
        print(PROVIDER_HELP)
        sys.exit(1)


def detect_provider():
    env = os.environ.get('LLM_PROVIDER')
    if env: return env
    for var, name in [
        ('ANYTHINGLLM_API_KEY', 'anythingllm'),
        ('OPENAI_API_KEY', 'openai'),
        ('ANTHROPIC_API_KEY', 'anthropic'),
        ('GOOGLE_API_KEY', 'google'),
        ('OPENAI_COMPATIBLE_API_KEY', 'openai-compatible'),
    ]:
        if os.environ.get(var): return name
    return 'ollama'


# ═══════════════════════════════════════════════
# ANALYSIS LOGIC
# ═══════════════════════════════════════════════

def format_email_batch(emails):
    parts = []
    for i, e in enumerate(emails):
        parts.append(
            f"--- EMAIL {i+1} ---\n"
            f"Date: {e.get('date', '?')}\n"
            f"From: {e.get('from', '?')}\n"
            f"Subject: {e.get('subject', '(none)')}\n"
            f"Body:\n{e.get('body', e.get('snippet', ''))[:2000]}\n"
        )
    return '\n'.join(parts)


def parse_json_response(text):
    text = text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else text[3:]
        if '```' in text:
            text = text[:text.rfind('```')]
        text = text.strip()
    if text.startswith('json'):
        text = text[4:].strip()
    return json.loads(text)


def extract_quotes_batch(llm, emails, era):
    email_text = format_email_batch(emails)
    prompt = EXTRACT_PROMPT.format(era=era, emails=email_text)

    for attempt in range(3):
        try:
            response = llm.complete(prompt)
            result = parse_json_response(response)
            return result.get('quotes', [])
        except json.JSONDecodeError:
            if attempt < 2: time.sleep(2)
            else: return []
        except Exception as e:
            if '429' in str(e).lower() or 'rate' in str(e).lower():
                time.sleep(10 * (attempt + 1))
            else:
                print(f"   ⚠️ {e}")
                return []
    return []


def analyze_themes(llm, quotes):
    prompt = THEME_PROMPT.format(quotes=json.dumps(quotes[:200], indent=2))
    try:
        response = llm.complete(prompt, max_tokens=2000)
        return parse_json_response(response)
    except Exception as e:
        print(f"   ⚠️ Theme error: {e}")
        return {"themes": [], "timeline_narrative": ""}


# ═══════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='Extract quotes from your email archive using any LLM',
        epilog=PROVIDER_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument('--provider', '-p', type=str, default=None)
    parser.add_argument('--model', '-m', type=str, default=None)
    parser.add_argument('--batch-size', '-b', type=int, default=BATCH_SIZE)
    parser.add_argument('--dry-run', action='store_true')

    # AnythingLLM-specific
    parser.add_argument('--anythingllm-url', type=str, default='http://localhost:3001',
                        help='AnythingLLM server URL (default: http://localhost:3001)')
    parser.add_argument('--anythingllm-key', type=str, default=None,
                        help='AnythingLLM API key (or set ANYTHINGLLM_API_KEY)')
    parser.add_argument('--anythingllm-workspace', type=str, default='gmail-archive',
                        help='Workspace name (default: gmail-archive)')
    parser.add_argument('--skip-upload', action='store_true',
                        help='Skip uploading emails to AnythingLLM (if already done)')

    args = parser.parse_args()
    provider_name = args.provider or detect_provider()

    if not SAMPLE_FILE.exists():
        print(f"\n❌ Missing {SAMPLE_FILE}")
        print("   Run 01_export.py or 01_parse_mbox.py first")
        sys.exit(1)

    # Load samples
    print("\n📬 Loading email samples...")

    # Skip these senders — marketing, newsletters, automated junk
    SKIP_IN_ANALYSIS = [
        'rssfwd.com', 'rssfwd@',
        'groupon.com',
        'culturemap.com',
        'thumbtack.com',
        'michaels.com', 'emdeals.michaels',
        'nextdoor.com',
        'capitalone.com', 'notification.capitalone',
        'washingtonpost.com',
        'txt.voice.google.com',
        'noreply', 'no-reply', 'donotreply',
        'notifications@', 'newsletter',
        'mailer-daemon', 'postmaster@',
    ]

    samples = []
    skipped = 0
    with open(SAMPLE_FILE, encoding='utf-8') as f:
        for line in f:
            msg = json.loads(line)
            sender = msg.get('from', '').lower()
            if any(skip in sender for skip in SKIP_IN_ANALYSIS):
                skipped += 1
                continue
            samples.append(msg)
    print(f"   {len(samples):,} samples loaded ({skipped} junk senders filtered out)")

    if args.dry_run:
        print(f"\n🤖 Would use: {provider_name}")
        by_era = defaultdict(list)
        for s in samples:
            ts = s.get('timestamp', 0) / 1000
            if ts > 0:
                year = datetime.fromtimestamp(ts).year
                for era_name, sy, ey in ERAS:
                    if sy <= year < ey:
                        by_era[era_name].append(s)
                        break
        for en, sy, ey in ERAS:
            n = len(by_era.get(en, []))
            if n: print(f"   {en}: {n:,} emails → {n // args.batch_size + 1} batches")
        print("\n✅ Dry run. Remove --dry-run to process.\n")
        return

    # Create provider
    llm = create_provider(provider_name, args.model, args)
    print(f"\n🤖 Using: {llm.name}\n")

    # AnythingLLM: upload emails for RAG
    if provider_name == 'anythingllm' and not args.skip_upload:
        llm.setup_for_emails(samples)
        print("   💡 Next time, add --skip-upload to reuse existing embeddings\n")

    # Group by era
    by_era = defaultdict(list)
    for s in samples:
        ts = s.get('timestamp', 0) / 1000
        if ts > 0:
            year = datetime.fromtimestamp(ts).year
            for era_name, sy, ey in ERAS:
                if sy <= year < ey:
                    by_era[era_name].append(s)
                    break

    # Extract quotes
    all_quotes = []

    # Parallel config — send multiple batches at once
    max_workers = 8  # 8 simultaneous requests

    for era_name, _, _ in ERAS:
        era_emails = by_era.get(era_name, [])
        if not era_emails:
            continue

        print(f"\n📧 {era_name} — {len(era_emails):,} emails")

        if provider_name == 'anythingllm':
            # For AnythingLLM, we query the workspace with era-specific prompts
            # The RAG system finds relevant emails automatically
            era_prompt = EXTRACT_PROMPT.format(
                era=era_name,
                emails=f"Search the embedded email archive for emails from {era_name}. "
                       f"Find the most memorable, emotional, funny, or profound lines."
            )
            try:
                response = llm.complete(era_prompt)
                result = parse_json_response(response)
                quotes = result.get('quotes', [])
                all_quotes.extend(quotes)
                print(f"   → {len(quotes)} quotes from {era_name}")
            except Exception as e:
                print(f"   ⚠️ {e}")
            time.sleep(2)
        else:
            # Parallel batch processing
            from concurrent.futures import ThreadPoolExecutor, as_completed

            batches = [era_emails[i:i + args.batch_size]
                       for i in range(0, len(era_emails), args.batch_size)]

            era_quotes = []
            completed = 0
            total = len(batches)

            def process_batch(batch_data):
                batch, idx = batch_data
                return idx, extract_quotes_batch(llm, batch, era_name)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(process_batch, (batch, i)): i
                    for i, batch in enumerate(batches)
                }

                for future in as_completed(futures):
                    try:
                        idx, quotes = future.result()
                        era_quotes.extend(quotes)
                    except Exception as e:
                        print(f"   ⚠️ Batch error: {e}")
                    completed += 1
                    pct = completed / total * 100
                    bar_len = int(completed / total * 40)
                    bar = '█' * bar_len + '░' * (40 - bar_len)
                    print(f"\r   {era_name}: {bar} {completed}/{total} ({pct:.0f}%)", end='', flush=True)

            print()  # newline after progress
            all_quotes.extend(era_quotes)
            era_count = len(era_quotes)
            print(f"   → {era_count} quotes from {era_name}")

    print(f"\n📝 Total: {len(all_quotes)}")

    # Dedup
    seen = set()
    unique = []
    for q in all_quotes:
        key = q.get('text', '').lower().strip()
        if key not in seen and len(q.get('text', '')) > 10:
            seen.add(key)
            unique.append(q)
    print(f"   After dedup: {len(unique)}")

    # Themes
    print("\n🎨 Analyzing themes...")
    themes = analyze_themes(llm, unique)

    # Save
    output = {
        'meta': {
            'total_emails_scanned': len(samples),
            'total_quotes': len(unique),
            'llm_provider': llm.name,
            'generated_at': datetime.now().isoformat(),
        },
        'themes': themes.get('themes', []),
        'timeline_narrative': themes.get('timeline_narrative', ''),
        'quotes': unique,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(QUOTES_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Saved {len(unique)} quotes to {QUOTES_FILE}")

    moods = defaultdict(int)
    for q in unique:
        moods[q.get('mood', '?')] += 1
    print("\n   Mood breakdown:")
    for mood, count in sorted(moods.items(), key=lambda x: -x[1]):
        print(f"   {mood:>15}: {count:>3} {'█' * min(count, 40)}")

    print(f"\n🎉 Done! Next steps:")
    print(f"   mkdir -p public/data")
    print(f"   cp data/quotes.json public/data/")
    print(f"   npm run dev\n")


if __name__ == '__main__':
    main()
