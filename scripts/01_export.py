#!/usr/bin/env python3
"""
Email Export Script — Gmail API Smart Sampling
==============================================
Pulls metadata (date, from, to, subject, snippet) for ALL emails,
then fetches full bodies for a smart sample of ~5,000 across all eras.

Setup:
  1. Go to https://console.cloud.google.com
  2. Create a project → Enable "Gmail API"
  3. Credentials → Create OAuth 2.0 Client ID → Desktop App
  4. Download the JSON → save as scripts/credentials/credentials.json
  5. pip install google-auth-oauthlib google-api-python-client tqdm
  6. python scripts/01_export.py

First run opens a browser for OAuth consent. Token is cached after that.
"""

import os
import sys
import json
import time
import base64
import email
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tqdm import tqdm

# --- Config ---
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
CREDS_DIR = Path(__file__).parent / 'credentials'
CREDS_FILE = CREDS_DIR / 'credentials.json'
TOKEN_FILE = CREDS_DIR / 'token.json'
DATA_DIR = Path(__file__).parent.parent / 'data'
METADATA_FILE = DATA_DIR / 'email_metadata.jsonl'  # one JSON obj per line
SAMPLE_FILE = DATA_DIR / 'email_samples.jsonl'
STATS_FILE = DATA_DIR / 'email_stats.json'

# How many full bodies to fetch per era
SAMPLES_PER_ERA = 800
ERAS = [
    ('2004-2006', '2004/01/01', '2007/01/01'),
    ('2007-2009', '2007/01/01', '2010/01/01'),
    ('2010-2012', '2010/01/01', '2013/01/01'),
    ('2013-2015', '2013/01/01', '2016/01/01'),
    ('2016-2018', '2016/01/01', '2019/01/01'),
    ('2019-2021', '2019/01/01', '2022/01/01'),
    ('2022-2024', '2022/01/01', '2025/01/01'),
    ('2025-now',  '2025/01/01', '2027/01/01'),
]

BATCH_SIZE = 500  # messages.list page size (max 500)
RATE_LIMIT_DELAY = 0.02  # seconds between API calls


def authenticate():
    """Handle OAuth2 flow, return Gmail service."""
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDS_FILE.exists():
                print(f"\n❌ Missing credentials file: {CREDS_FILE}")
                print("   Download OAuth credentials from Google Cloud Console")
                print("   and save as scripts/credentials/credentials.json\n")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)


def fetch_all_message_ids(service):
    """Fetch ALL message IDs. Returns list of {id, threadId}."""
    print("\n📬 Fetching all message IDs...")
    all_ids = []
    page_token = None
    
    while True:
        try:
            results = service.users().messages().list(
                userId='me',
                maxResults=BATCH_SIZE,
                pageToken=page_token,
                q='',  # all messages
                fields='messages(id,threadId),nextPageToken,resultSizeEstimate'
            ).execute()
            
            messages = results.get('messages', [])
            all_ids.extend(messages)
            
            if len(all_ids) % 5000 < BATCH_SIZE:
                estimate = results.get('resultSizeEstimate', '?')
                print(f"   ... {len(all_ids):,} IDs fetched (estimate: {estimate:,})")

            page_token = results.get('nextPageToken')
            if not page_token:
                break

            time.sleep(RATE_LIMIT_DELAY)

        except HttpError as e:
            if e.resp.status == 429:
                print("   ⏳ Rate limited, waiting 10s...")
                time.sleep(10)
            else:
                raise

    print(f"   ✅ Total: {len(all_ids):,} message IDs\n")
    return all_ids


def fetch_message_metadata(service, msg_id):
    """Fetch metadata (headers + snippet) for a single message."""
    try:
        msg = service.users().messages().get(
            userId='me',
            id=msg_id,
            format='metadata',
            metadataHeaders=['From', 'To', 'Subject', 'Date'],
            fields='id,snippet,internalDate,payload/headers,labelIds'
        ).execute()
        
        headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
        
        return {
            'id': msg['id'],
            'date': headers.get('Date', ''),
            'timestamp': int(msg.get('internalDate', 0)),
            'from': headers.get('From', ''),
            'to': headers.get('To', ''),
            'subject': headers.get('Subject', ''),
            'snippet': msg.get('snippet', ''),
            'labels': msg.get('labelIds', []),
        }
    except HttpError as e:
        if e.resp.status == 429:
            time.sleep(5)
            return fetch_message_metadata(service, msg_id)
        return None


def fetch_message_body(service, msg_id):
    """Fetch full message body text."""
    try:
        msg = service.users().messages().get(
            userId='me',
            id=msg_id,
            format='full',
            fields='id,snippet,payload'
        ).execute()

        body_text = extract_body(msg.get('payload', {}))
        return body_text

    except HttpError as e:
        if e.resp.status == 429:
            time.sleep(5)
            return fetch_message_body(service, msg_id)
        return None


def extract_body(payload):
    """Recursively extract plain text from message payload."""
    body = ''
    mime_type = payload.get('mimeType', '')

    if mime_type == 'text/plain':
        data = payload.get('body', {}).get('data', '')
        if data:
            body = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
    elif 'parts' in payload:
        for part in payload['parts']:
            body += extract_body(part)

    return body[:5000]  # cap at 5000 chars per email


def phase1_metadata(service, message_ids):
    """Phase 1: Fetch metadata for all messages."""
    print("📋 Phase 1: Fetching metadata for all messages...")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Resume support: skip already-fetched IDs
    existing_ids = set()
    if METADATA_FILE.exists():
        with open(METADATA_FILE) as f:
            for line in f:
                obj = json.loads(line)
                existing_ids.add(obj['id'])
        print(f"   Resuming — {len(existing_ids):,} already fetched")

    remaining = [m for m in message_ids if m['id'] not in existing_ids]
    print(f"   {len(remaining):,} remaining\n")

    with open(METADATA_FILE, 'a') as f:
        for i, msg in enumerate(tqdm(remaining, desc="Metadata")):
            meta = fetch_message_metadata(service, msg['id'])
            if meta:
                f.write(json.dumps(meta) + '\n')
            
            if i % 100 == 0:
                f.flush()
            time.sleep(RATE_LIMIT_DELAY)

    print(f"\n   ✅ Metadata saved to {METADATA_FILE}\n")


def phase2_smart_sample(service):
    """Phase 2: Read metadata, pick smart samples per era, fetch bodies."""
    print("🎯 Phase 2: Smart sampling full bodies by era...")

    # Load all metadata
    messages_by_era = defaultdict(list)
    with open(METADATA_FILE) as f:
        for line in f:
            msg = json.loads(line)
            ts = msg['timestamp'] / 1000  # ms -> s
            dt = datetime.fromtimestamp(ts)
            
            # Skip likely automated/marketing emails
            from_addr = msg.get('from', '').lower()
            if any(skip in from_addr for skip in [
                'noreply', 'no-reply', 'notifications', 'mailer-daemon',
                'donotreply', 'newsletter', 'marketing', 'promo',
                'updates@', 'info@', 'support@', 'billing@',
                'notifications@', 'alert@', 'news@'
            ]):
                continue

            # Skip very short snippets (likely empty or auto)
            if len(msg.get('snippet', '')) < 20:
                continue

            for era_name, start, end in ERAS:
                era_start = datetime.strptime(start, '%Y/%m/%d')
                era_end = datetime.strptime(end, '%Y/%m/%d')
                if era_start <= dt < era_end:
                    messages_by_era[era_name].append(msg)
                    break

    # Print era distribution
    print("\n   Era distribution (filtered):")
    total = 0
    for era_name, _, _ in ERAS:
        count = len(messages_by_era[era_name])
        total += count
        print(f"   {era_name}: {count:>8,} emails")
    print(f"   {'Total':>9}: {total:>8,}\n")

    # Sample from each era
    import random
    random.seed(42)  # reproducible

    sampled = []
    for era_name, _, _ in ERAS:
        pool = messages_by_era[era_name]
        n = min(SAMPLES_PER_ERA, len(pool))
        chosen = random.sample(pool, n)
        sampled.extend(chosen)
        print(f"   Sampled {n:,} from {era_name}")

    print(f"\n   Total samples: {len(sampled):,}")
    print(f"   Fetching full bodies...\n")

    # Fetch bodies
    with open(SAMPLE_FILE, 'w') as f:
        for msg in tqdm(sampled, desc="Bodies"):
            body = fetch_message_body(service, msg['id'])
            msg['body'] = body or ''
            f.write(json.dumps(msg) + '\n')
            time.sleep(RATE_LIMIT_DELAY)

    print(f"\n   ✅ Samples saved to {SAMPLE_FILE}\n")


def phase3_stats():
    """Phase 3: Generate summary statistics."""
    print("📊 Phase 3: Generating stats...")

    stats = {
        'total_messages': 0,
        'date_range': {'earliest': None, 'latest': None},
        'top_senders': defaultdict(int),
        'emails_per_year': defaultdict(int),
        'emails_per_month': defaultdict(int),
    }

    with open(METADATA_FILE) as f:
        for line in f:
            msg = json.loads(line)
            stats['total_messages'] += 1

            ts = msg['timestamp'] / 1000
            dt = datetime.fromtimestamp(ts)
            date_str = dt.strftime('%Y-%m-%d')

            if stats['date_range']['earliest'] is None or date_str < stats['date_range']['earliest']:
                stats['date_range']['earliest'] = date_str
            if stats['date_range']['latest'] is None or date_str > stats['date_range']['latest']:
                stats['date_range']['latest'] = date_str

            year = dt.strftime('%Y')
            month = dt.strftime('%Y-%m')
            stats['emails_per_year'][year] += 1
            stats['emails_per_month'][month] += 1

            sender = msg.get('from', 'unknown')
            # Extract just the email address
            if '<' in sender:
                sender = sender.split('<')[1].rstrip('>')
            stats['top_senders'][sender.lower()] += 1

    # Keep top 50 senders
    top = sorted(stats['top_senders'].items(), key=lambda x: -x[1])[:50]
    stats['top_senders'] = dict(top)

    with open(STATS_FILE, 'w') as f:
        json.dump(stats, f, indent=2)

    print(f"   Total: {stats['total_messages']:,}")
    print(f"   Range: {stats['date_range']['earliest']} → {stats['date_range']['latest']}")
    print(f"   ✅ Stats saved to {STATS_FILE}\n")


def main():
    service = authenticate()

    if '--ids-only' in sys.argv:
        ids = fetch_all_message_ids(service)
        print(f"Found {len(ids):,} messages")
        return

    if '--sample-only' in sys.argv:
        phase2_smart_sample(service)
        phase3_stats()
        return

    if '--stats-only' in sys.argv:
        phase3_stats()
        return

    # Full pipeline
    ids = fetch_all_message_ids(service)
    phase1_metadata(service, ids)
    phase2_smart_sample(service)
    phase3_stats()

    print("🎉 Done! Next step: python scripts/02_analyze.py")


if __name__ == '__main__':
    main()
