#!/usr/bin/env python3
"""
Parse Google Maps Takeout Reviews.json into a date-keyed location dataset
=========================================================================

Reviews.json is a GeoJSON FeatureCollection where each Feature is one
review the user wrote. Every review is a verified visit — the user
showed up at the place, used the service, and bothered to write about
it later. This is the strongest possible location signal in the
archive: confidence is "verified" for every entry.

Usage:
  python scripts/09_parse_reviews.py "D:\\path\\to\\Reviews.json"
  python scripts/09_parse_reviews.py "...Reviews.json" \\
      --output data/locations_from_reviews.json

Output:
  data/locations_from_reviews.json — dict keyed by ISO date (YYYY-MM-DD),
                                      each value a list of review dicts
                                      with place / city / state / country /
                                      lat / lng / rating / source /
                                      confidence / review_excerpt.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_OUTPUT = Path(__file__).parent.parent / "data" / "locations_from_reviews.json"


# Country-name -> ISO-2 fallback. Only consulted when location.country_code
# is missing from the JSON.
COUNTRY_NAME_TO_CODE = {
    "united states": "US",
    "usa": "US",
    "canada": "CA",
    "united kingdom": "UK",
    "england": "UK",
    "scotland": "UK",
    "wales": "UK",
    "northern ireland": "UK",
    "india": "IN",
    "mexico": "MX",
    "france": "FR",
    "germany": "DE",
    "italy": "IT",
    "spain": "ES",
    "netherlands": "NL",
    "japan": "JP",
    "china": "CN",
    "australia": "AU",
    "singapore": "SG",
    "hong kong": "HK",
    "uae": "AE",
    "united arab emirates": "AE",
}

US_STATE_ABBREVS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "PR",
}


def parse_address(address: str) -> dict:
    """Parse a full address string from the right.

    Format we expect (Google's normalized form):
      "<street, possibly with internal commas>, <city>, <ST ZIP>, <country>"

    Returns {city, state, country_code} with None for any field we can't
    determine. The street segment is intentionally not returned — we only
    care about the higher-level place.
    """
    out = {"city": None, "state": None, "country_code": None}
    if not address:
        return out

    parts = [p.strip() for p in address.split(",") if p.strip()]
    if len(parts) < 2:
        # Single segment — best we can do is treat it as a city.
        out["city"] = address.strip()
        return out

    # Last segment: country name (sometimes), or a postcode/state in
    # international forms. We only strip it if it matches a known country.
    last = parts[-1]
    last_lower = last.lower()
    body = parts
    if last_lower in COUNTRY_NAME_TO_CODE:
        out["country_code"] = COUNTRY_NAME_TO_CODE[last_lower]
        body = parts[:-1]

    if not body:
        return out

    last_body = body[-1]
    tokens = last_body.split()

    # US format: last body segment is "ST ZIP" or "ST ZIP-1234".
    if tokens and len(tokens[0]) == 2 and tokens[0].upper() in US_STATE_ABBREVS:
        out["state"] = tokens[0].upper()
        if not out["country_code"]:
            out["country_code"] = "US"
        if len(body) >= 2:
            out["city"] = body[-2]
        return out

    # Non-US: try to peel a city off the front of the last body segment by
    # taking leading alphabetic tokens until we hit something with a digit
    # (postcode). Falls back to body[-2] or the whole last segment.
    city_words = []
    for tok in tokens:
        if any(ch.isdigit() for ch in tok):
            break
        city_words.append(tok)
    if city_words:
        out["city"] = " ".join(city_words)
    elif len(body) >= 2:
        out["city"] = body[-2]
    else:
        out["city"] = last_body
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parse a Google Maps Takeout Reviews.json file.",
    )
    parser.add_argument(
        "reviews_path",
        help='Path to Reviews.json (e.g. "D:\\path\\to\\Maps (your places)\\Reviews.json")',
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output JSON path (default: data/locations_from_reviews.json).",
    )
    args = parser.parse_args()

    reviews_path = Path(args.reviews_path)
    if not reviews_path.exists():
        print(f"Error: file not found at {reviews_path}", file=sys.stderr)
        return 1

    with open(reviews_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("type") != "FeatureCollection":
        print(
            f"Error: expected GeoJSON FeatureCollection, got type={data.get('type')!r}",
            file=sys.stderr,
        )
        return 1

    features = data.get("features", [])
    print(f"Loaded {len(features)} reviews from {reviews_path.name}")
    print("─" * 60)

    locations: dict[str, list[dict]] = defaultdict(list)
    cities_seen: Counter = Counter()
    places_seen: set[str] = set()
    skipped_no_date = 0
    skipped_bad_shape = 0
    unparsed_addresses = 0

    for feat in features:
        if not isinstance(feat, dict):
            skipped_bad_shape += 1
            continue
        props = feat.get("properties") or {}

        date_str = props.get("date", "")
        if not date_str:
            skipped_no_date += 1
            continue
        date = date_str[:10]  # YYYY-MM-DD prefix of ISO 8601

        loc = props.get("location") or {}
        place_name = loc.get("name") or ""
        address = loc.get("address") or ""
        country_code_explicit = loc.get("country_code")

        parsed = parse_address(address)
        if address and parsed["city"] is None:
            unparsed_addresses += 1
        country_code = country_code_explicit or parsed.get("country_code")

        # GeoJSON geometry order is [longitude, latitude].
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        lng = coords[0] if len(coords) >= 1 else None
        lat = coords[1] if len(coords) >= 2 else None

        rating = props.get("five_star_rating_published")
        review_text = props.get("review_text_published") or ""

        entry = {
            "place_name": place_name,
            "city": parsed.get("city"),
            "state": parsed.get("state"),
            "country": country_code,
            "lat": lat,
            "lng": lng,
            "rating": rating,
            "source": "google_review",
            "confidence": "verified",
            "review_excerpt": review_text[:200],
        }
        locations[date].append(entry)
        cities_seen[(parsed.get("city"), parsed.get("state"))] += 1
        if place_name:
            places_seen.add(place_name)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sorted_locations = dict(sorted(locations.items()))
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sorted_locations, f, indent=2, ensure_ascii=False)

    # Summary
    all_dates = sorted(locations.keys())
    total_reviews = sum(len(v) for v in locations.values())
    print()
    print(f"Total reviews:       {total_reviews:>5,}")
    if skipped_no_date:
        print(f"Skipped (no date):   {skipped_no_date:>5,}")
    if skipped_bad_shape:
        print(f"Skipped (bad shape): {skipped_bad_shape:>5,}")
    if unparsed_addresses:
        print(f"Unparsed addresses:  {unparsed_addresses:>5,}")
    if all_dates:
        print(f"Date range:          {all_dates[0]} -> {all_dates[-1]}")
    print(f"Unique places:       {len(places_seen):>5,}")
    print(f"Unique dates:        {len(locations):>5,}")
    print()
    print("Top 20 cities:")
    for (c, s), n in cities_seen.most_common(20):
        if c is None:
            label = "[unparsed]"
        else:
            label = f"{c}, {s}" if s else c
        print(f"  {label:<40s} {n:>5,}")
    print()
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
