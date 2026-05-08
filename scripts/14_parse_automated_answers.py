#!/usr/bin/env python3
"""
Parse Google Maps "Answers to automated questions" into a date-keyed
location dataset.
====================================================================

Each file is a single high-confidence visit signal. Google fires a
short prompt ("Is this place popular for lunch?", "Was there a wait
in line?") when its server thinks the user is physically at a known
place — the prompt firing IS the visit signal, regardless of whether
the user answered or what they answered. The reply (if any) is saved
in a tiny JSON file named after the unix-epoch-ms timestamp, e.g.:

    1433766219491.json
    {
      "placeUrl": "https://google.com/maps/?cid=0x86...:0x7b...",
      "selectedChoice": "Yes",
      "question": "Is this place popular for lunch?"
    }

The catch: each file contains a CID (Google's internal place ID) but
no lat / lng / city. We can't resolve CIDs without a Google Maps API
call. Strategy: extract everything we have (timestamp, CID, question,
answer) and emit confidence "high" with city / state / country / lat
/ lng all null. The downstream merger (script #18) inherits location
from same-day high-confidence sources (e.g. parser 13 Type F
"Directions to <X>" entries with current-location descriptions).

Some entries have "selectedChoice": "Unknown" and/or
"question": "Unknown" — degraded records where Google didn't preserve
the prompt. We keep them: the timestamp + CID is still a valid
presence signal even when the prompt content is gone.

Standard library only.

Usage:
  python scripts/14_parse_automated_answers.py "D:\\path\\Maps\\Answers to automated questions"
  python scripts/14_parse_automated_answers.py "...questions" \\
      --output data/locations_from_automated_answers.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_OUTPUT = Path(__file__).parent.parent / "data" / "locations_from_automated_answers.json"

# Match the cid= query param in a placeUrl. The CID itself is two hex
# numbers separated by a colon, e.g. 0x8640c77206699891:0x7b2428f75f7e39a4
CID_RE = re.compile(r"cid=([0-9a-fx:]+)", re.IGNORECASE)


def epoch_ms_to_iso(ms: int) -> str:
    """Convert epoch ms -> ISO-8601 UTC with millisecond precision and a Z suffix.
    e.g. 1433766219491 -> '2015-06-08T13:43:39.491Z'.
    """
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_one(path: Path):
    """Parse one .json answer file.

    Returns (date_iso, entry) on success, or (None, reason_str) on failure.
    Reasons: 'filename_not_integer', 'bad_json', 'no_placeurl', 'no_cid'.
    """
    try:
        ms = int(path.stem)
    except ValueError:
        return None, "filename_not_integer"

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None, "bad_json"
    if not isinstance(data, dict):
        return None, "bad_json"

    place_url = (data.get("placeUrl") or "").strip()
    if not place_url:
        return None, "no_placeurl"
    m = CID_RE.search(place_url)
    if not m:
        return None, "no_cid"
    cid = m.group(1)

    iso = epoch_ms_to_iso(ms)
    date = iso[:10]

    # question/selectedChoice may be missing or "Unknown" — preserve raw.
    question = data.get("question") or ""
    answer = data.get("selectedChoice") or ""

    entry = {
        "city": None,
        "state": None,
        "country": None,
        "lat": None,
        "lng": None,
        "source": "google_maps_automated_answer",
        "confidence": "high",
        "needs_verification": False,
        "details": {
            "cid": cid,
            "question": question,
            "answer": answer,
            "timestamp_ms": ms,
            "iso_timestamp": iso,
        },
    }
    return date, entry


def main() -> int:
    p = argparse.ArgumentParser(
        description="Parse Google Maps 'Answers to automated questions' files.",
    )
    p.add_argument(
        "answers_dir",
        help='Path to "Answers to automated questions" folder.',
    )
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = p.parse_args()

    src = Path(args.answers_dir)
    if not src.exists():
        print(f"Error: not found: {src}", file=sys.stderr)
        return 1
    if not src.is_dir():
        print(f"Error: not a directory: {src}", file=sys.stderr)
        return 1

    files = sorted(src.glob("*.json"))
    print(f"Found {len(files)} .json file(s) in {src}")
    print("─" * 70)

    locations: dict[str, list[dict]] = defaultdict(list)
    questions: Counter = Counter()
    answers: Counter = Counter()
    skip_reasons: Counter = Counter()
    extracted = 0

    for path in files:
        date, result = parse_one(path)
        if date is None:
            skip_reasons[result] += 1
            print(f"  - skip {path.name:30s} {result}")
            continue
        entry = result
        locations[date].append(entry)
        extracted += 1
        questions[entry["details"]["question"]] += 1
        answers[entry["details"]["answer"]] += 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sorted_locs = dict(sorted(locations.items()))
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sorted_locs, f, indent=2, ensure_ascii=False)

    all_dates = sorted(locations.keys())
    total_skipped = sum(skip_reasons.values())

    # Histogram: degraded "Unknown" prompts vs ones that preserved a real question.
    unknown_q = questions.get("Unknown", 0)
    valid_q = sum(n for q, n in questions.items() if q != "Unknown")

    print()
    print("─" * 70)
    print(f"Total scanned:     {len(files):>5,}")
    print(f"Total extracted:   {extracted:>5,}")
    print(f"Total skipped:     {total_skipped:>5,}")
    for reason in ("filename_not_integer", "bad_json", "no_placeurl", "no_cid"):
        if skip_reasons[reason]:
            print(f"  {reason:24s} {skip_reasons[reason]:>4,}")
    print()
    print(f"Unique dates:      {len(all_dates):>5,}")
    if all_dates:
        print(f"Date range:        {all_dates[0]} -> {all_dates[-1]}")
    print()
    print("Question quality:")
    print(f"  valid prompts    {valid_q:>4,}")
    print(f"  'Unknown'        {unknown_q:>4,}")
    print()
    if questions:
        print("Top 10 questions (by frequency):")
        for q, n in questions.most_common(10):
            display = q if len(q) <= 60 else q[:57] + "..."
            print(f"  {n:>4,}  {display}")
        print()
    if answers:
        print("Top 5 answers (by frequency):")
        for a, n in answers.most_common(5):
            display = a if len(a) <= 60 else a[:57] + "..."
            print(f"  {n:>4,}  {display}")
        print()
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
