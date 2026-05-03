#!/usr/bin/env python3
"""
Parse Google Takeout .mbox file (Memory-Efficient Edition)
==========================================================
Handles massive MBOX files (35GB+, 748K+ emails) without
running out of memory. Streams everything — never holds
all emails in RAM at once.

Usage:
  python scripts/01_parse_mbox.py "D:\\path\\to\\All mail Including Spam and Trash.mbox"

Output:
  data/email_metadata.jsonl  — one JSON object per email
  data/email_samples.jsonl   — random sample with full bodies
  data/email_stats.json      — summary statistics
"""

import sys
import json
import random
import email
import email.utils
import email.header
import email.policy
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from html.parser import HTMLParser

from tqdm import tqdm

DATA_DIR = Path(__file__).parent.parent / 'data'
METADATA_FILE = DATA_DIR / 'email_metadata.jsonl'
SAMPLE_FILE = DATA_DIR / 'email_samples.jsonl'
STATS_FILE = DATA_DIR / 'email_stats.json'

SAMPLES_PER_ERA = 800
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

SKIP_SENDERS = [
    'noreply', 'no-reply', 'notifications', 'mailer-daemon',
    'donotreply', 'newsletter', 'marketing', 'promo',
    'updates@', 'info@', 'support@', 'billing@',
    'notifications@', 'alert@', 'news@'
]


class HTMLStripper(HTMLParser):
    """Strip HTML tags, keep text."""
    def __init__(self):
        super().__init__()
        self.text = []
        self.ignore = False
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.ignore = True
    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.ignore = False
    def handle_data(self, data):
        if not self.ignore:
            self.text.append(data)
    def get_text(self):
        return ' '.join(self.text)


def strip_html(html_text):
    s = HTMLStripper()
    try:
        s.feed(html_text)
        return s.get_text()
    except:
        return html_text


def extract_body_from_bytes(raw_bytes, max_len=5000):
    """Extract plain text body from raw email bytes."""
    try:
        msg = email.message_from_bytes(raw_bytes, policy=email.policy.compat32)
    except:
        return ''

    body = ''
    try:
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == 'text/plain':
                    try:
                        charset = part.get_content_charset() or 'utf-8'
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = payload.decode(charset, errors='replace')
                            break
                    except:
                        pass
                elif content_type == 'text/html' and not body:
                    try:
                        charset = part.get_content_charset() or 'utf-8'
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = strip_html(payload.decode(charset, errors='replace'))
                    except:
                        pass
        else:
            try:
                charset = msg.get_content_charset() or 'utf-8'
                payload = msg.get_payload(decode=True)
                if payload:
                    text = payload.decode(charset, errors='replace')
                    if msg.get_content_type() == 'text/html':
                        text = strip_html(text)
                    body = text
            except:
                pass
    except:
        pass

    return body.strip()[:max_len]


def extract_headers_from_bytes(raw_bytes):
    """Extract just headers from raw email bytes (fast, no body parsing)."""
    try:
        msg = email.message_from_bytes(raw_bytes, policy=email.policy.compat32)
    except:
        return None

    from_addr = msg.get('From', '') or ''
    to_addr = msg.get('To', '') or ''
    subject = msg.get('Subject', '') or ''
    date_str = msg.get('Date', '') or ''

    # Decode subject
    if subject:
        try:
            decoded = email.header.decode_header(subject)
            subject = ''.join(
                part.decode(enc or 'utf-8', errors='replace') if isinstance(part, bytes) else str(part)
                for part, enc in decoded
            )
        except:
            pass

    # Parse date
    dt = None
    if date_str:
        try:
            dt = email.utils.parsedate_to_datetime(date_str)
        except:
            pass

    # Quick snippet from body (just first 200 chars)
    snippet = extract_body_from_bytes(raw_bytes, max_len=200)

    return {
        'from': from_addr,
        'to': to_addr,
        'subject': subject,
        'date': date_str,
        'dt': dt,
        'snippet': snippet,
    }


def stream_mbox(mbox_path):
    """
    Stream through an MBOX file yielding one raw email at a time.
    Never loads the whole file into memory.

    MBOX format: each message starts with a line beginning with "From "
    (with a space after From). Everything until the next "From " line
    is one message.
    """
    current_message = []
    message_count = 0

    with open(mbox_path, 'rb') as f:
        for line in f:
            if line.startswith(b'From ') and current_message:
                # Yield the completed message
                raw = b''.join(current_message)
                message_count += 1
                yield message_count, raw
                current_message = []
            else:
                current_message.append(line)

        # Don't forget the last message
        if current_message:
            raw = b''.join(current_message)
            message_count += 1
            yield message_count, raw


def is_automated(from_addr):
    from_lower = from_addr.lower()
    return any(skip in from_lower for skip in SKIP_SENDERS)


def main():
    if len(sys.argv) < 2:
        print('Usage: python 01_parse_mbox.py "D:\\path\\to\\file.mbox"')
        sys.exit(1)

    mbox_path = sys.argv[1]
    if not Path(mbox_path).exists():
        print(f"File not found: {mbox_path}")
        sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    file_size = Path(mbox_path).stat().st_size
    file_gb = file_size / (1024 ** 3)

    print(f"\n{'='*60}")
    print(f"  Email Ripples — MBOX Parser")
    print(f"  File: {Path(mbox_path).name}")
    print(f"  Size: {file_gb:.1f} GB")
    print(f"  Mode: Memory-efficient streaming")
    print(f"{'='*60}\n")

    # ── PHASE 1: Stream through MBOX, write metadata ──
    print("Phase 1: Extracting metadata (streaming)...")
    print("   This reads the file once, writing each email's info to disk.")
    print("   Nothing is held in memory. Progress updates every 10,000.\n")

    count = 0
    errors = 0
    flush_interval = 1000

    with open(METADATA_FILE, 'w', encoding='utf-8') as f:
        for msg_num, raw_bytes in stream_mbox(mbox_path):
            count += 1

            if count % 10000 == 0:
                print(f"   ... {count:,} processed")
                sys.stdout.flush()

            try:
                headers = extract_headers_from_bytes(raw_bytes)
                if headers is None:
                    errors += 1
                    continue

                dt = headers.pop('dt')
                meta = {
                    'id': str(count),
                    'date': headers['date'],
                    'timestamp': int(dt.timestamp() * 1000) if dt else 0,
                    'from': headers['from'],
                    'to': headers['to'],
                    'subject': headers['subject'],
                    'snippet': headers['snippet'],
                    'offset': msg_num,  # for seeking back later
                }

                f.write(json.dumps(meta, ensure_ascii=False) + '\n')

            except Exception as e:
                errors += 1
                if errors <= 10:
                    print(f"   Warning on message {count}: {type(e).__name__}: {e}")

            # Flush periodically so progress is visible on disk
            if count % flush_interval == 0:
                f.flush()

            # Free memory explicitly
            del raw_bytes

    print(f"\n   Done! {count:,} messages parsed ({errors} errors)")
    print(f"   Saved to {METADATA_FILE}\n")

    # ── PHASE 2: Read metadata back, smart sample ──
    print("Phase 2: Smart sampling for quote extraction...")
    print("   Reading metadata file, picking ~5,000 interesting emails.\n")

    messages_by_era = defaultdict(list)
    stats_years = defaultdict(int)
    stats_senders = defaultdict(int)
    earliest = None
    latest = None
    total_for_stats = 0

    with open(METADATA_FILE, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f):
            try:
                meta = json.loads(line)
            except:
                continue

            total_for_stats += 1
            ts = meta.get('timestamp', 0)

            # Stats accumulation (lightweight — just counters)
            if ts > 0:
                try:
                    dt = datetime.fromtimestamp(ts / 1000)
                    year_str = str(dt.year)
                    stats_years[year_str] += 1

                    ds = dt.strftime('%Y-%m-%d')
                    if earliest is None or ds < earliest:
                        earliest = ds
                    if latest is None or ds > latest:
                        latest = ds

                    # For sampling: check if it's a real human email
                    from_addr = meta.get('from', '')
                    snippet = meta.get('snippet', '')
                    if not is_automated(from_addr) and len(snippet) >= 30:
                        year = dt.year
                        for era_name, start_y, end_y in ERAS:
                            if start_y <= year < end_y:
                                # Store just enough to find it later
                                messages_by_era[era_name].append({
                                    'id': meta['id'],
                                    'line_num': line_num,
                                })
                                break
                except:
                    pass

            # Sender stats
            sender = meta.get('from', '')
            if '<' in sender:
                try:
                    sender = sender.split('<')[1].rstrip('>')
                except:
                    pass
            stats_senders[sender.lower()] += 1

    # Print era distribution
    print("   Era distribution (filtered, non-automated):")
    total_filtered = 0
    for era_name, _, _ in ERAS:
        n = len(messages_by_era[era_name])
        total_filtered += n
        print(f"   {era_name}: {n:>8,}")
    print(f"   {'Total':>9}: {total_filtered:>8,}\n")

    # Pick samples
    random.seed(42)
    sampled_line_nums = set()
    sampled_by_era = {}

    for era_name, _, _ in ERAS:
        pool = messages_by_era[era_name]
        n = min(SAMPLES_PER_ERA, len(pool))
        chosen = random.sample(pool, n)
        for c in chosen:
            sampled_line_nums.add(c['line_num'])
        sampled_by_era[era_name] = n
        print(f"   Sampled {n:,} from {era_name}")

    total_samples = len(sampled_line_nums)
    print(f"\n   Total samples: {total_samples:,}")
    print(f"   Now fetching full bodies for sampled emails...\n")

    # Free the era lists — we only need line numbers now
    del messages_by_era

    # ── Collect sampled metadata by re-reading the metadata file ──
    sampled_metas = {}
    with open(METADATA_FILE, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f):
            if line_num in sampled_line_nums:
                try:
                    sampled_metas[line_num] = json.loads(line)
                except:
                    pass

    # ── Now re-stream the MBOX to get full bodies for samples ──
    print("   Re-reading MBOX for full email bodies (streaming)...")
    print("   This will take a while — reading 37GB again.\n")

    # Build a set of message IDs we need
    needed_ids = {meta['id'] for meta in sampled_metas.values()}
    found = 0

    with open(SAMPLE_FILE, 'w', encoding='utf-8') as f:
        for msg_num, raw_bytes in stream_mbox(mbox_path):
            msg_id = str(msg_num)

            if msg_id in needed_ids:
                body = extract_body_from_bytes(raw_bytes, max_len=5000)

                # Find the matching metadata
                for ln, meta in sampled_metas.items():
                    if meta['id'] == msg_id:
                        meta['body'] = body
                        f.write(json.dumps(meta, ensure_ascii=False) + '\n')
                        found += 1
                        break

                needed_ids.discard(msg_id)

                if found % 500 == 0 and found > 0:
                    print(f"   ... {found:,}/{total_samples:,} bodies extracted")

                if not needed_ids:
                    print(f"   All samples found!")
                    break

            # Free memory
            del raw_bytes

            if msg_num % 50000 == 0:
                print(f"   ... scanning message {msg_num:,}")

    print(f"\n   Done! {found:,} sample bodies extracted")
    print(f"   Saved to {SAMPLE_FILE}\n")

    # ── PHASE 3: Write stats ──
    print("Phase 3: Generating stats...")

    top_senders = sorted(stats_senders.items(), key=lambda x: -x[1])[:50]

    stats = {
        'total_messages': count,
        'total_filtered': total_filtered,
        'total_samples': found,
        'date_range': {
            'earliest': earliest,
            'latest': latest,
        },
        'emails_per_year': dict(sorted(stats_years.items())),
        'top_senders': dict(top_senders),
    }

    with open(STATS_FILE, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2)

    print(f"   Total messages: {count:,}")
    print(f"   Date range: {earliest} to {latest}")
    print(f"   Saved to {STATS_FILE}\n")

    # Print a fun summary
    print("=" * 60)
    print("  YOUR EMAIL LIFE IN NUMBERS")
    print("=" * 60)
    print(f"  Total emails:     {count:,}")
    print(f"  First email:      {earliest}")
    print(f"  Latest email:     {latest}")
    print(f"  Human emails:     {total_filtered:,}")
    print(f"  Samples for AI:   {found:,}")
    print()
    print("  Emails per year:")
    for year in sorted(stats_years.keys()):
        bar_len = min(stats_years[year] // 1000, 40)
        bar = '#' * bar_len
        print(f"  {year}: {stats_years[year]:>8,}  {bar}")
    print()
    print("  Top 10 senders:")
    for sender, cnt in top_senders[:10]:
        print(f"  {cnt:>8,}  {sender}")
    print()
    print("=" * 60)
    print(f"  Next step: python scripts/02_analyze.py --provider ollama")
    print("=" * 60)
    print()


if __name__ == '__main__':
    main()
