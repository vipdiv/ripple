#!/usr/bin/env python3
"""
Parse Google Maps activity log from Takeout/My Activity (streaming JSON)
=========================================================================

Input: a single JSON array of Google Maps activity records, typically
~98MB and 250K-500K entries spanning many years. We can't load this
into memory; we iterate one record at a time with ijson.items(...).

Three whitelist tiers (extract):

  Type A — "Used Maps" with locationInfos
    Match:    title == "Used Maps" AND locationInfos non-empty.
    Coords:   "center=<lat>,<lng>" inside locationInfos[0].url.
    City:     null (downstream merger reverse-geocodes from coords).
    Confidence: low / needs_verification true (override below).

  Type D — "Explored on Google Maps"
    Match:    title == "Explored on Google Maps" AND
              subtitles[0].name is non-empty.
    City:     subtitles[0].name (e.g. "Baytown").
    Coords:   "ll=<lat>,<lng>" inside subtitles[0].url.
    Confidence: low / needs_verification true (override below).

  Type F — "Directions to <X>" with current-location description
    Match:    title.startswith("Directions to ") AND
              description.startswith("Current location\\n").
    Address:  everything after "Current location\\n" in description.
              Right-anchored comma split for city + state.
    Coords:   "@<lat>,<lng>,<zoom>z" inside titleUrl.
    Confidence: high / needs_verification false. This is the
              high-value tier — Google has the user's current
              location at the moment they requested directions.

Confidence override (applies after every successful extraction):
  If locationInfos has any entry whose source contains the substring
  "Location History", confidence is forced to "high",
  needs_verification to false, and details.location_history_corroborated
  is true. Otherwise the flag is false. The flag is always present.

Skiplist (silent — counted but not logged):
  - "Used Maps" with no locationInfos (the noise floor).
  - "Viewed area in Google Maps".
  - title.startswith("Searched for ").
  - title.startswith("Asked Maps when did I visit").
  - "Viewed your Timeline".
  - title matches r"^\\d+ notifications?$".
  - bare place-names — title is non-empty, doesn't start with any of
    "Used", "Explored", "Directions", "Viewed", "Searched", "Asked",
    and isn't a "<n> notification(s)" form. Catches entries like
    "Panera Bread", "48 Smokewood Ln".

Unknown titles (titles that don't match any whitelist or skiplist
rule) are logged to data/maps_activity_unknown_titles.log as
deduplicated <title>\\t<sample_timestamp> lines. The first timestamp
encountered wins.

Output: data/locations_from_maps_activity.json — same date-keyed
shape as parsers 09 / 10 / 11 / 12.

Standard library plus ijson (added to requirements.txt).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    import ijson
except ImportError:
    print(
        "Error: ijson is not installed. Run:\n"
        "  pip install ijson  (or: pip install -r requirements.txt)",
        file=sys.stderr,
    )
    sys.exit(1)

DEFAULT_OUTPUT = Path(__file__).parent.parent / "data" / "locations_from_maps_activity.json"
DEFAULT_UNKNOWN_LOG = Path(__file__).parent.parent / "data" / "maps_activity_unknown_titles.log"

US_STATE_ABBREVS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "PR",
}

# Verbs Google Maps uses to start activity titles. Anything that doesn't
# start with one of these is a bare place-name (route via skiplist).
KNOWN_VERB_PREFIXES = (
    "Used ", "Explored ", "Directions ", "Viewed ", "Searched ", "Asked ",
)

NOTIFICATIONS_RE = re.compile(r"^\d+ notifications?$")

# Coord regexes for each whitelist tier.
CENTER_RE = re.compile(r"center=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)")
LL_RE = re.compile(r"ll=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)")
AT_RE = re.compile(r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),\d+(?:\.\d+)?z")


def in_conus(lat: float, lng: float) -> bool:
    return 24.0 <= lat <= 49.0 and -125.0 <= lng <= -66.0


def country_for(lat, lng):
    if lat is None or lng is None:
        return None
    return "US" if in_conus(lat, lng) else None


# ─── Title routing ──────────────────────────────────────────────────────────

def classify_title(title: str) -> str:
    """Return one of: 'type_a', 'type_d', 'type_f', 'skip', 'unknown'.
    'skip' is silent; 'unknown' lands in the unknown-titles log."""
    if not title:
        return "skip"

    if title == "Viewed area in Google Maps":
        return "skip"
    if title.startswith("Searched for "):
        return "skip"
    if title.startswith("Asked Maps when did I visit"):
        return "skip"
    if title == "Viewed your Timeline":
        return "skip"
    if NOTIFICATIONS_RE.match(title):
        return "skip"

    if title == "Used Maps":
        return "type_a"
    if title == "Explored on Google Maps":
        return "type_d"
    if title.startswith("Directions to "):
        return "type_f"

    # Bare place-name — does not start with any recognized verb.
    if not any(title.startswith(v) for v in KNOWN_VERB_PREFIXES):
        return "skip"

    return "unknown"


def has_location_history(location_infos) -> bool:
    """True if any locationInfos entry has source containing 'Location History'."""
    if not location_infos:
        return False
    for li in location_infos:
        if not isinstance(li, dict):
            continue
        if "Location History" in (li.get("source") or ""):
            return True
    return False


# ─── Per-type extractors ────────────────────────────────────────────────────

def extract_type_a(item: dict):
    """'Used Maps' with locationInfos. Coords from locationInfos[0].url
    'center=<lat>,<lng>'. Returns partial dict or None if no coords."""
    location_infos = item.get("locationInfos") or []
    if not location_infos:
        return None
    first = location_infos[0]
    if not isinstance(first, dict):
        return None
    m = CENTER_RE.search(first.get("url") or "")
    if not m:
        return None
    try:
        lat = float(m.group(1))
        lng = float(m.group(2))
    except ValueError:
        return None
    return {
        "city": None,
        "state": None,
        "country": country_for(lat, lng),
        "lat": lat,
        "lng": lng,
        "subtype": "used_maps",
    }


def extract_type_d(item: dict):
    """'Explored on Google Maps'. City from subtitles[0].name; coords from
    subtitles[0].url 'll=<lat>,<lng>'."""
    subtitles = item.get("subtitles") or []
    if not subtitles:
        return None
    first = subtitles[0]
    if not isinstance(first, dict):
        return None
    name = (first.get("name") or "").strip()
    if not name:
        return None
    lat = lng = None
    m = LL_RE.search(first.get("url") or "")
    if m:
        try:
            lat = float(m.group(1))
            lng = float(m.group(2))
        except ValueError:
            lat = lng = None
    return {
        "city": name,
        "state": None,
        "country": country_for(lat, lng),
        "lat": lat,
        "lng": lng,
        "subtype": "explored",
    }


def extract_type_f(item: dict):
    """'Directions to <X>' with description starting 'Current location\\n'.
    Address from description tail; coords from titleUrl '@<lat>,<lng>,<zoom>z'."""
    description = item.get("description") or ""
    if not description.startswith("Current location\n"):
        return None
    address = description[len("Current location\n"):].strip()

    title = item.get("title") or ""
    destination = title[len("Directions to "):].strip() if title.startswith("Directions to ") else None

    lat = lng = None
    m = AT_RE.search(item.get("titleUrl") or "")
    if m:
        try:
            lat = float(m.group(1))
            lng = float(m.group(2))
        except ValueError:
            lat = lng = None

    # Right-anchored comma split for "..., City, ST ZIP" addresses.
    city = state = None
    country = country_for(lat, lng)
    if address:
        tokens = [t.strip() for t in address.split(",") if t.strip()]
        if len(tokens) >= 2:
            city = tokens[-2]
            state_zip = tokens[-1].split()
            if state_zip:
                cand = state_zip[0]
                if cand.upper() in US_STATE_ABBREVS:
                    state = cand.upper()
                    if country is None:
                        country = "US"
                else:
                    state = cand

    return {
        "city": city,
        "state": state,
        "country": country,
        "lat": lat,
        "lng": lng,
        "subtype": "directions_current_location",
        "destination": destination,
        "address": address or None,
    }


# ─── Main ───────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description="Parse Google Maps activity log (Takeout/My Activity) into a "
                    "date-keyed location dataset, streaming with ijson.",
    )
    p.add_argument(
        "activity_path",
        help='Path to MyActivity.json (e.g. "D:\\path\\Takeout\\My Activity\\Maps_2\\MyActivity.json")',
    )
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p.add_argument("--unknown-log", default=str(DEFAULT_UNKNOWN_LOG))
    args = p.parse_args()

    src = Path(args.activity_path)
    if not src.exists():
        print(f"Error: file not found at {src}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    unknown_path = Path(args.unknown_log)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    unknown_path.parent.mkdir(parents=True, exist_ok=True)

    file_size = src.stat().st_size
    print(f"Streaming {src.name} ({file_size / 1e6:.1f} MB)")
    print("─" * 70)

    locations: dict[str, list[dict]] = defaultdict(list)
    subtype_counts: Counter = Counter()
    confidence_counts: Counter = Counter()
    cities_seen: Counter = Counter()
    seen_unknowns: set[str] = set()
    unknown_log: list[tuple[str, str]] = []

    scanned = 0
    extracted = 0
    skipped = 0
    unknown_count = 0

    with open(src, "rb") as f:
        for item in ijson.items(f, "item"):
            scanned += 1

            if scanned % 10000 == 0:
                print(f"  Scanned {scanned:,}, extracted {extracted:,}, "
                      f"skipped {skipped:,}, unknown {unknown_count:,}")

            if not isinstance(item, dict):
                skipped += 1
                continue

            title = item.get("title") or ""
            cls = classify_title(title)

            if cls == "skip":
                skipped += 1
                continue

            if cls == "unknown":
                unknown_count += 1
                if title not in seen_unknowns:
                    seen_unknowns.add(title)
                    unknown_log.append((title, item.get("time") or ""))
                continue

            time_str = item.get("time") or ""
            date = time_str[:10] if len(time_str) >= 10 else ""
            if not date:
                skipped += 1
                continue

            if cls == "type_a":
                partial = extract_type_a(item)
            elif cls == "type_d":
                partial = extract_type_d(item)
            else:  # type_f
                partial = extract_type_f(item)

            if partial is None:
                # Match condition met but extraction failed (e.g. coords regex
                # didn't hit on Type A). Treat as silent skip — this is the
                # noise floor for malformed/empty whitelist entries.
                skipped += 1
                continue

            corroborated = has_location_history(item.get("locationInfos"))
            if partial["subtype"] == "directions_current_location" or corroborated:
                confidence = "high"
                needs_verification = False
            else:
                confidence = "low"
                needs_verification = True

            details: dict = {
                "activity_subtype": partial["subtype"],
                "location_history_corroborated": corroborated,
            }
            if partial["subtype"] == "directions_current_location":
                details["destination"] = partial.get("destination")
                details["address"] = partial.get("address")

            entry = {
                "city": partial["city"],
                "state": partial["state"],
                "country": partial["country"],
                "lat": partial["lat"],
                "lng": partial["lng"],
                "source": "google_maps_activity",
                "confidence": confidence,
                "needs_verification": needs_verification,
                "details": details,
            }
            locations[date].append(entry)
            extracted += 1
            subtype_counts[partial["subtype"]] += 1
            confidence_counts[confidence] += 1
            if partial["city"]:
                cities_seen[(partial["city"], partial["state"])] += 1

    # Final tally line so the last partial 10K shows up.
    print(f"  Scanned {scanned:,}, extracted {extracted:,}, "
          f"skipped {skipped:,}, unknown {unknown_count:,}")

    # Write extracted output (date-keyed JSON).
    sorted_locs = dict(sorted(locations.items()))
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sorted_locs, f, indent=2, ensure_ascii=False)

    # Write deduped unknown-titles log (TSV: <title>\t<first-seen-timestamp>).
    with open(unknown_path, "w", encoding="utf-8") as f:
        for title, ts in unknown_log:
            f.write(f"{title}\t{ts}\n")

    total_entries = sum(len(v) for v in locations.values())
    all_dates = sorted(locations.keys())

    print()
    print("─" * 70)
    print(f"Total scanned:       {scanned:>8,}")
    print(f"Total extracted:     {extracted:>8,}")
    print(f"Total skipped:       {skipped:>8,}")
    print(f"Total unknown:       {unknown_count:>8,} ({len(seen_unknowns):,} unique)")
    print()
    print(f"Output entries:      {total_entries:>8,}")
    print(f"Unique dates:        {len(all_dates):>8,}")
    if all_dates:
        print(f"Date range:          {all_dates[0]} -> {all_dates[-1]}")
    print()
    print("By activity_subtype:")
    for sub in ("used_maps", "explored", "directions_current_location"):
        if subtype_counts[sub]:
            print(f"  {sub:32s} {subtype_counts[sub]:>6,}")
    print()
    print("By confidence:")
    for conf in ("high", "low"):
        if confidence_counts[conf]:
            print(f"  {conf:12s} {confidence_counts[conf]:>6,}")
    print()
    if cities_seen:
        print("Top 20 cities (extracted entries with non-null city):")
        for (c, s), n in cities_seen.most_common(20):
            label = f"{c}, {s}" if s else c
            print(f"  {label:<35s} {n:>6,}")
        print()
    print(f"Wrote {output_path}")
    print(f"Wrote {unknown_path} ({len(seen_unknowns)} unique titles)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
