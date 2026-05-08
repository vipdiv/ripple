#!/usr/bin/env python3
"""
Parse Google Maps activity log from Takeout/My Activity (streaming JSON)
=========================================================================

Input: a single JSON array of Google Maps activity records, typically
~98MB and 250K-500K entries spanning many years. We can't load this
into memory; we iterate one record at a time with ijson.items(...).

Four whitelist tiers (extract):

  Type A — "Used Maps" / "Used Google Maps" with locationInfos
    Match:    title in {"Used Maps", "Used Google Maps"} AND
              locationInfos non-empty.
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

  Type G — bare place-name with Location History flag
    Match:    title is non-empty AND doesn't start with any of the
              recognized verb prefixes (Used / Explored / Directions /
              Viewed / Searched / Asked) AND locationInfos contains
              an entry whose source matches "location history"
              (case-insensitive). Checked in main() BEFORE
              classify_title so the bare-place silent-skip doesn't
              pre-empt us; verb-prefixed titles fall through
              unchanged.
    Place:    title = the place name (no separate place lookup).
              titleUrl optionally carries an ftid (older format) or
              cid (newer format) — extracted to details when present,
              otherwise null. Neither is required.
    Coords:   none. The export doesn't carry coords here, but the
              LH flag is itself a high-confidence presence signal —
              Google's GPS history is asserting the user was there.
    Confidence: high / needs_verification false; details.location_
              history_corroborated is always true for this tier.

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
DEFAULT_ASKED_LOG = Path(__file__).parent.parent / "data" / "maps_asked_queries_for_review.log"
DEFAULT_VIEWED_LOG = Path(__file__).parent.parent / "data" / "maps_viewed_for_review.log"

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

# Place-identifier params Google embeds in titleUrl. Type G entries (bare
# place-name + Location History flag) carry one of these but no coords.
FTID_RE = re.compile(r"ftid=([0-9a-fx:]+)", re.IGNORECASE)
CID_RE = re.compile(r"cid=([0-9a-fx:]+)", re.IGNORECASE)


def in_conus(lat: float, lng: float) -> bool:
    return 24.0 <= lat <= 49.0 and -125.0 <= lng <= -66.0


def country_for(lat, lng):
    if lat is None or lng is None:
        return None
    return "US" if in_conus(lat, lng) else None


# ─── Title routing ──────────────────────────────────────────────────────────

def classify_title(title: str) -> str:
    """Return one of: 'type_a', 'type_d', 'type_f', 'skip',
    'skip_asked', 'skip_viewed', 'unknown'.

    'skip' is a silent skip with no triage log. 'skip_asked' and
    'skip_viewed' are silent in the main extraction stream but get
    written to dedicated review logs because some entries in those
    buckets contain factual data worth a manual second look. 'unknown'
    lands in the unknown-titles log.

    Order matters: more specific exact-match / prefix-match rules come
    before the broader catch-alls so a title like 'Viewed your Timeline'
    silent-skips without falling through to the generic 'Viewed '
    prefix that triggers the triage log.
    """
    if not title:
        return "skip"

    # Specific silent skips (no triage).
    if title == "Viewed area in Google Maps":
        return "skip"
    if title == "Viewed your Timeline":
        return "skip"
    if title.startswith("Viewed area around "):
        return "skip"
    if title.startswith("Searched for "):
        return "skip"
    if NOTIFICATIONS_RE.match(title):
        return "skip"

    # Whitelist tiers — order vs. the broad Viewed/Asked prefixes below
    # doesn't matter (the whitelist exact-matches and 'Directions to '
    # don't overlap with 'Viewed ' or 'Asked Maps ').
    if title in ("Used Maps", "Used Google Maps"):
        return "type_a"
    if title == "Explored on Google Maps":
        return "type_d"
    if title.startswith("Directions to "):
        return "type_f"

    # Triage skips. 'Asked Maps ' subsumes the older
    # 'Asked Maps when did I visit' rule — every Asked-Maps entry now
    # gets logged for manual review since some are meta queries
    # ("when did I visit X?") that reveal places the user has been.
    if title.startswith("Asked Maps "):
        return "skip_asked"
    # 'Viewed ' is a broad catchall after the more specific rules above.
    # Catches 'Viewed The Rice Box' etc. — some have full addresses
    # worth manually recovering.
    if title.startswith("Viewed "):
        return "skip_viewed"

    # Bare place-name — does not start with any recognized verb.
    if not any(title.startswith(v) for v in KNOWN_VERB_PREFIXES):
        return "skip"

    return "unknown"


def has_location_history(location_infos) -> bool:
    """True if any locationInfos entry has a source field containing
    "location history" (case-insensitive).

    Real-world source strings include "Previously saved in your Location
    History", "From your Location History", and lowercase variants
    depending on the export year. The May 8 real-data run produced
    corroborated: 0 because the substring match was title-case-only and
    silently missed mixed-casing rows. Lowercasing both sides fixes it.
    """
    if not location_infos:
        return False
    for li in location_infos:
        if not isinstance(li, dict):
            continue
        if "location history" in (li.get("source") or "").lower():
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

    country_from_coords = country_for(lat, lng)
    city, state, country_from_addr = parse_directions_address(address)
    country = country_from_coords or country_from_addr

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


def is_bare_place_name(title: str) -> bool:
    """True if title is non-empty and starts with none of the recognized
    Maps verb prefixes — i.e. it would otherwise route to the bare-place
    silent skip in classify_title."""
    if not title:
        return False
    return not any(title.startswith(v) for v in KNOWN_VERB_PREFIXES)


def extract_type_g(item: dict):
    """Type G: bare place-name with the Location History flag.

    The LH flag is itself the visit signal — Google's GPS history is
    confirming presence at the place named in the title. titleUrl
    optionally carries an ftid (older format) or cid (newer format)
    identifier; we extract whichever is present, neither is required.

    Returns a partial dict on success, or None if the title is empty
    (the pre-check should already have filtered those out).
    """
    title = (item.get("title") or "").strip()
    if not title:
        return None
    title_url = item.get("titleUrl") or ""
    ftid = None
    cid = None
    m = FTID_RE.search(title_url)
    if m:
        ftid = m.group(1)
    else:
        m = CID_RE.search(title_url)
        if m:
            cid = m.group(1)
    return {
        "place_name": title,
        "ftid": ftid,
        "cid": cid,
        "subtype": "location_history_place",
    }


def parse_directions_address(address: str):
    """Right-anchored, state-aware parser for Type F addresses.

    Walks comma-separated tokens left-to-right looking for the first one
    whose first word is a 2-letter US state abbreviation. The token
    immediately before the state token is the city. Anything after the
    state token (country suffix, etc.) is discarded. Trailing
    parentheticals on the city ("Houston (Heights)") are stripped.

    Falls back to the old "last segment is state-zip, second-to-last is
    city" heuristic if no US state token is found anywhere — preserves
    best-effort behavior for the rare non-US Type F entry without
    pretending we got a confident match.

    Returns (city, state, country) where country is "US" when a US state
    token was found, None otherwise.
    """
    if not address:
        return None, None, None
    tokens = [t.strip() for t in address.split(",") if t.strip()]
    if len(tokens) < 2:
        return None, None, None

    for i, tok in enumerate(tokens):
        words = tok.split()
        if not words:
            continue
        first = words[0]
        if len(first) == 2 and first.upper() in US_STATE_ABBREVS:
            state = first.upper()
            if i == 0:
                return None, state, "US"
            raw_city = tokens[i - 1]
            city = re.sub(r"\s*\([^)]*\)\s*$", "", raw_city).strip() or None
            return city, state, "US"

    # Non-US fallback.
    state_zip = tokens[-1].split()
    if state_zip:
        state_cand = state_zip[0]
        city = tokens[-2] if len(tokens) >= 2 else None
        if city:
            city = re.sub(r"\s*\([^)]*\)\s*$", "", city).strip() or None
        return city, state_cand, None
    return None, None, None


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
    p.add_argument("--asked-log", default=str(DEFAULT_ASKED_LOG),
                   help="Triage log for 'Asked Maps ...' entries (no dedup).")
    p.add_argument("--viewed-log", default=str(DEFAULT_VIEWED_LOG),
                   help="Triage log for 'Viewed ...' entries that fell through "
                        "the more specific Viewed rules (no dedup).")
    args = p.parse_args()

    src = Path(args.activity_path)
    if not src.exists():
        print(f"Error: file not found at {src}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    unknown_path = Path(args.unknown_log)
    asked_path = Path(args.asked_log)
    viewed_path = Path(args.viewed_log)
    for p_ in (output_path, unknown_path, asked_path, viewed_path):
        p_.parent.mkdir(parents=True, exist_ok=True)

    file_size = src.stat().st_size
    print(f"Streaming {src.name} ({file_size / 1e6:.1f} MB)")
    print("─" * 70)

    locations: dict[str, list[dict]] = defaultdict(list)
    subtype_counts: Counter = Counter()
    confidence_counts: Counter = Counter()
    cities_seen: Counter = Counter()

    # Unknown-titles tracking: count occurrences per title (for the
    # frequency-sorted summary in Task B) AND remember the first-seen
    # timestamp (for the dedup'd log file).
    unknown_counts: Counter = Counter()
    unknown_first_ts: dict[str, str] = {}

    # Triage logs (Tasks E + G): no dedup, every matching entry recorded.
    asked_log: list[tuple[str, str]] = []
    viewed_log: list[tuple[str, str]] = []

    scanned = 0
    extracted = 0
    skipped = 0
    unknown_count = 0
    asked_count = 0
    viewed_count = 0
    location_history_count = 0  # Task C — count of corroborated extractions.

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
            time_str = item.get("time") or ""
            location_infos = item.get("locationInfos") or []

            # Type G — bare place-name with Location History flag. Checked
            # BEFORE classify_title so the bare-place silent-skip doesn't
            # pre-empt us. Verb-prefixed titles fall through unchanged:
            #   - "Used Maps" + LH    -> Type A, corroborated=True override
            #   - "Asked Maps " + LH  -> triage (LH doesn't override triage)
            #   - "Viewed " + LH      -> triage (same)
            if is_bare_place_name(title) and has_location_history(location_infos):
                date = time_str[:10] if len(time_str) >= 10 else ""
                if not date:
                    skipped += 1
                    continue
                g = extract_type_g(item)
                if g is None:
                    skipped += 1
                    continue
                entry = {
                    "city": None,
                    "state": None,
                    "country": None,
                    "lat": None,
                    "lng": None,
                    "source": "google_maps_activity",
                    "confidence": "high",
                    "needs_verification": False,
                    "details": {
                        "activity_subtype": "location_history_place",
                        "location_history_corroborated": True,
                        "place_name": g["place_name"],
                        "ftid": g["ftid"],
                        "cid": g["cid"],
                        "iso_timestamp": time_str,
                    },
                }
                locations[date].append(entry)
                extracted += 1
                subtype_counts["location_history_place"] += 1
                confidence_counts["high"] += 1
                location_history_count += 1
                continue

            cls = classify_title(title)

            if cls == "skip":
                skipped += 1
                continue

            if cls == "skip_asked":
                skipped += 1
                asked_count += 1
                asked_log.append((time_str, title))
                continue

            if cls == "skip_viewed":
                skipped += 1
                viewed_count += 1
                viewed_log.append((time_str, title))
                continue

            if cls == "unknown":
                unknown_count += 1
                unknown_counts[title] += 1
                if title not in unknown_first_ts:
                    unknown_first_ts[title] = time_str
                continue

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
            if corroborated:
                location_history_count += 1
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

    # Write deduped unknown-titles log (TSV: <title>\t<first-seen-timestamp>),
    # ordered by frequency descending so the most common patterns are at
    # the top — convenient when triaging which to add to the whitelist /
    # skiplist next.
    with open(unknown_path, "w", encoding="utf-8") as f:
        for title, _count in unknown_counts.most_common():
            f.write(f"{title}\t{unknown_first_ts.get(title, '')}\n")

    # Triage logs (Tasks E + G). No dedup — every matching entry written
    # in the order it was encountered.
    with open(asked_path, "w", encoding="utf-8") as f:
        for ts, title in asked_log:
            f.write(f"{ts}\t{title}\n")
    with open(viewed_path, "w", encoding="utf-8") as f:
        for ts, title in viewed_log:
            f.write(f"{ts}\t{title}\n")

    total_entries = sum(len(v) for v in locations.values())
    all_dates = sorted(locations.keys())

    print()
    print("─" * 70)
    print(f"Total scanned:       {scanned:>8,}")
    print(f"Total extracted:     {extracted:>8,}")
    print(f"Total skipped:       {skipped:>8,}")
    print(f"  asked-maps triage  {asked_count:>8,}")
    print(f"  viewed-x triage    {viewed_count:>8,}")
    print(f"Total unknown:       {unknown_count:>8,} ({len(unknown_counts):,} unique)")
    print()
    print(f"Output entries:      {total_entries:>8,}")
    print(f"Unique dates:        {len(all_dates):>8,}")
    if all_dates:
        print(f"Date range:          {all_dates[0]} -> {all_dates[-1]}")
    print()
    print("By activity_subtype:")
    for sub in ("used_maps", "explored", "directions_current_location",
                "location_history_place"):
        if subtype_counts[sub]:
            print(f"  {sub:32s} {subtype_counts[sub]:>6,}")
    print()
    print("By confidence:")
    for conf in ("high", "low"):
        if confidence_counts[conf]:
            print(f"  {conf:12s} {confidence_counts[conf]:>6,}")
    # Task C — show how many extracted entries got upgraded by the
    # location_history_corroborated override.
    print(f"  {'corroborated':12s} {location_history_count:>6,}  "
          f"(of which by Location History source flag)")
    print()
    if cities_seen:
        print("Top 20 cities (extracted entries with non-null city):")
        for (c, s), n in cities_seen.most_common(20):
            label = f"{c}, {s}" if s else c
            print(f"  {label:<35s} {n:>6,}")
        print()
    # Task B — surface the most common unknown title patterns inline so
    # the user doesn't have to open the log file to decide what to route
    # next.
    if unknown_counts:
        print("Top 20 unknown titles (by frequency):")
        for title, n in unknown_counts.most_common(20):
            display = title if len(title) <= 60 else title[:57] + "..."
            print(f"  {n:>6,}  {display}")
        print()
    print(f"Wrote {output_path}")
    print(f"Wrote {unknown_path}  ({len(unknown_counts)} unique titles)")
    print(f"Wrote {asked_path}    ({asked_count} entries)")
    print(f"Wrote {viewed_path}   ({viewed_count} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
