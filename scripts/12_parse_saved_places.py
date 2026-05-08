#!/usr/bin/env python3
"""
Parse Google Maps Takeout "Saved Places.json"
==============================================

Saved Places is a degraded GeoJSON FeatureCollection. Most features have
null-island coordinates [0, 0] and the real location data is buried in
the google_maps_url field. This parser recovers what it can via three
extraction tiers in priority order, then skips anything that fails all
of them.

Critical design rule: every entry from this source gets confidence "low"
and needs_verification true, regardless of how cleanly the data parses.
Saved != visited. The cleanest entry in the file (a structured Tier-1
record with full lat/lng) is just as likely to be a phantom as the
sketchiest URL-coords-only entry — the user has confirmed at least one
"clean" entry corresponds to a place they have never actually been to.
The visualization layer must NOT auto-render these as visits.

Extraction tiers, in order:

  Tier 1 — structured: geometry.coordinates is non-zero AND
           properties.location is present. lat/lng come from coords
           ([lng, lat] order in GeoJSON), city/state/country come
           from properties.location.address parsed right-to-left.
           extraction_method = "structured".

  Tier 2 — url_coords: q parameter of google_maps_url is exactly
           "<lat>,<lng>" (regex ^-?\\d+\\.\\d+,-?\\d+\\.\\d+$). Tried
           BEFORE Tier 3 so a numeric address fragment can't accidentally
           match the address pattern. city/state are null (no city info
           is recoverable); country defaults to "US" if the coords fall
           inside a tight CONUS bbox, else null. extraction_method =
           "url_coords"; details.url_coords preserves the raw string.

  Tier 3 — url_address: q parameter is a comma-separated address.
           URL-decoded ("+" -> space, then urllib.parse.unquote),
           split on comma. street = tokens[0], city = tokens[-2],
           state = first word of tokens[-1]. country = "US" if state
           matches a US 2-letter abbrev, else null. extraction_method =
           "url_address"; details.url_address preserves the full
           decoded string.

  Tier 4 — skip: log date + URL and continue. Defensive; not expected
           to fire on the user's current 7-feature file but guards
           against future variants.

Standard library only.

Usage:
  python scripts/12_parse_saved_places.py "D:\\path\\to\\Saved Places.json"
  python scripts/12_parse_saved_places.py "...Saved Places.json" \\
      --output data/locations_from_saved_places.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_OUTPUT = Path(__file__).parent.parent / "data" / "locations_from_saved_places.json"

US_STATE_ABBREVS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "PR",
}

# Country-name -> ISO-2 for Tier 1 fallback when location.country_code
# is missing.
COUNTRY_NAME_TO_CODE = {
    "united states": "US", "usa": "US", "us": "US",
    "canada": "CA", "ca": "CA",
    "united kingdom": "UK", "uk": "UK", "gb": "UK",
    "india": "IN", "mexico": "MX",
    "japan": "JP", "australia": "AU",
}

# Strict pattern for Tier 2: q is exactly "<lat>,<lng>" with no extra
# text. "32.123,-95.456" matches; "Houston, TX" does not.
URL_COORDS_RE = re.compile(r"^-?\d+\.\d+,-?\d+\.\d+$")


def in_conus(lat: float, lng: float) -> bool:
    """Tight CONUS bounding box. Used only for Tier 2 country inference."""
    return 24.0 <= lat <= 49.0 and -125.0 <= lng <= -66.0


def normalize_country(raw: str | None) -> str | None:
    if not raw:
        return None
    val = raw.strip().lower()
    if not val:
        return None
    if val in COUNTRY_NAME_TO_CODE:
        return COUNTRY_NAME_TO_CODE[val]
    if len(val) == 2:
        return val.upper()
    return None


def parse_address_right(address: str) -> tuple[str | None, str | None, str | None]:
    """Right-anchored parser for 'Street, City, ST ZIP, Country' format.
    Same shape as the Reviews parser. Returns (city, state, country)."""
    if not address:
        return None, None, None
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if len(parts) < 2:
        return None, None, None

    country = None
    last_lower = parts[-1].lower()
    if last_lower in COUNTRY_NAME_TO_CODE:
        country = COUNTRY_NAME_TO_CODE[last_lower]
        body = parts[:-1]
    else:
        body = parts

    if not body:
        return None, None, country

    last_body = body[-1]
    tokens = last_body.split()
    if tokens and len(tokens[0]) == 2 and tokens[0].upper() in US_STATE_ABBREVS:
        state = tokens[0].upper()
        if not country:
            country = "US"
        city = body[-2] if len(body) >= 2 else None
        return city, state, country

    # Non-US fallback — keep alphabetic prefix as city.
    city_words = []
    for tok in tokens:
        if any(ch.isdigit() for ch in tok):
            break
        city_words.append(tok)
    city = " ".join(city_words) if city_words else (body[-2] if len(body) >= 2 else last_body)
    return city, None, country


def get_q_param(url: str) -> str | None:
    """Extract and URL-decode the q parameter from a Google Maps URL.
    Uses urllib.parse.parse_qs which handles both '+' and '%XX' decoding.
    Returns None if the URL has no q (or query) parameter."""
    if not url:
        return None
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None
    qs = urllib.parse.parse_qs(parsed.query)
    qvals = qs.get("q") or qs.get("query")
    if not qvals:
        return None
    return qvals[0]


# ─── Per-tier extractors ────────────────────────────────────────────────────

def try_structured(feature: dict) -> dict | None:
    """Tier 1: clean GeoJSON entry with non-zero coords + location object."""
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if len(coords) < 2:
        return None
    lng, lat = coords[0], coords[1]
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return None
    if lat == 0 and lng == 0:
        return None

    props = feature.get("properties") or {}
    location = props.get("location")
    if not isinstance(location, dict):
        return None

    address = (location.get("address") or "").strip()
    place_name = (location.get("name") or "").strip() or None
    country_explicit = normalize_country(location.get("country_code"))

    city, state, country_from_addr = parse_address_right(address)
    country = country_explicit or country_from_addr

    return {
        "city": city,
        "state": state,
        "country": country,
        "lat": float(lat),
        "lng": float(lng),
        "details": {
            "place_name": place_name,
            "extraction_method": "structured",
            "url_address": None,
            "url_coords": None,
        },
    }


def try_url_coords(q: str | None) -> dict | None:
    """Tier 2: q is exactly '<lat>,<lng>'."""
    if not q:
        return None
    if not URL_COORDS_RE.match(q):
        return None
    lat_s, lng_s = q.split(",")
    try:
        lat = float(lat_s)
        lng = float(lng_s)
    except ValueError:
        return None
    country = "US" if in_conus(lat, lng) else None
    return {
        "city": None,
        "state": None,
        "country": country,
        "lat": lat,
        "lng": lng,
        "details": {
            "place_name": None,
            "extraction_method": "url_coords",
            "url_address": None,
            "url_coords": f"{lat_s},{lng_s}",
        },
    }


def try_url_address(q: str | None) -> dict | None:
    """Tier 3: q is a comma-separated address. parse_qs already URL-decoded
    once; the spec's prescribed extra unquote pass guards against any
    double-encoded inputs and is a no-op otherwise."""
    if not q:
        return None
    decoded = urllib.parse.unquote(q.replace("+", " "))
    tokens = [t.strip() for t in decoded.split(",") if t.strip()]
    if len(tokens) < 2:
        return None
    city = tokens[-2]
    state_zip = tokens[-1].split()
    if not state_zip:
        return None
    state = state_zip[0]
    is_us = state.upper() in US_STATE_ABBREVS
    return {
        "city": city,
        "state": state.upper() if is_us else state,
        "country": "US" if is_us else None,
        "lat": None,
        "lng": None,
        "details": {
            "place_name": None,
            "extraction_method": "url_address",
            "url_address": decoded,
            "url_coords": None,
        },
    }


# ─── Main ───────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description="Parse a Google Maps Takeout 'Saved Places.json' file.",
    )
    p.add_argument(
        "saved_places_path",
        help='Path to Saved Places.json (e.g. "D:\\path\\Maps (your places)\\Saved Places.json")',
    )
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = p.parse_args()

    src = Path(args.saved_places_path)
    if not src.exists():
        print(f"Error: file not found at {src}", file=sys.stderr)
        return 1

    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("type") != "FeatureCollection":
        print(
            f"Error: expected GeoJSON FeatureCollection, got type={data.get('type')!r}",
            file=sys.stderr,
        )
        return 1

    features = data.get("features") or []
    print(f"Loaded {len(features)} feature(s) from {src.name}")
    print("─" * 78)

    locations: dict[str, list[dict]] = defaultdict(list)
    method_counts: Counter = Counter()
    skipped_log: list[tuple[str, str]] = []

    for feat in features:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties") or {}
        date_iso = (props.get("date") or "")[:10]
        if not date_iso:
            skipped_log.append(("(no date)", str(props.get("google_maps_url") or "")))
            continue

        url = props.get("google_maps_url") or ""
        q = get_q_param(url)

        partial = try_structured(feat)
        if partial is None:
            partial = try_url_coords(q)
        if partial is None:
            partial = try_url_address(q)
        if partial is None:
            skipped_log.append((date_iso, url))
            print(f"  - skip {date_iso}  no extraction worked  {url[:60]}")
            continue

        entry = {
            "city": partial["city"],
            "state": partial["state"],
            "country": partial["country"],
            "lat": partial["lat"],
            "lng": partial["lng"],
            "source": "google_maps_saved_place",
            "confidence": "low",
            "needs_verification": True,
            "details": partial["details"],
        }
        locations[date_iso].append(entry)
        method_counts[partial["details"]["extraction_method"]] += 1

        if partial["lat"] is None:
            coord_str = "                -"
        else:
            coord_str = f"{partial['lat']:>9.4f},{partial['lng']:>10.4f}"
        city_str = (partial["city"] or "-")[:18]
        state_str = partial["state"] or "-"
        print(f"  + {date_iso}  {partial['details']['extraction_method']:12s} "
              f"{coord_str}  {city_str:<18s} {state_str}")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sorted_locs = dict(sorted(locations.items()))
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sorted_locs, f, indent=2, ensure_ascii=False)

    total = sum(len(v) for v in locations.values())
    print()
    print("─" * 78)
    print(f"Total entries:    {total:>3,}")
    print(f"Unique dates:     {len(locations):>3,}")
    print()
    print("By extraction method:")
    for method in ("structured", "url_coords", "url_address"):
        print(f"  {method:14s} {method_counts[method]:>3,}")
    if skipped_log:
        print()
        print(f"Skipped (Tier 4): {len(skipped_log)}")
        for date, url in skipped_log:
            print(f"  {date}  {url[:80]}")
    print()
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
