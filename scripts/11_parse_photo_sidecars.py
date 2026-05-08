#!/usr/bin/env python3
"""
Parse Google Maps Takeout photo sidecars (.jpg.json)
=====================================================

Each photo the user contributed to Google Maps has a JSON sidecar with
EXIF GPS, capture timestamp, and (sometimes) a place name or caption.
The user physically took the photo at that location, so confidence is
"verified" for every entry.

Reverse-geocoding lat/lng to city/state is intentionally deferred to a
downstream merger script — this parser leaves city / state / country
as null and emits the raw coordinates plus the photo filename and
date.

Sidecar shape (modern unified Takeout format, identical regardless of
when the photo was taken):

  {
    "title":        "<filename>.jpg",
    "description":  "<optional caption>",
    "imageViews":   "<unused>",
    "creationTime": { "timestamp": "<epoch sec>", "formatted": "..." },
    "photoTakenTime": { "timestamp": "<epoch sec>", "formatted": "..." },
    "geoDataExif":  { "latitude": <float>, "longitude": <float>,
                      "altitude": <float> }
  }

Older "geoData" key (without the "Exif" suffix) is also accepted as a
fallback for any pre-unified-format sidecar that may show up.

Standard library only.

Usage:
  python scripts/11_parse_photo_sidecars.py "D:\\path\\to\\Photos and videos"
  python scripts/11_parse_photo_sidecars.py "...\\Photos and videos" \\
      --output data/locations_from_photos.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_OUTPUT = Path(__file__).parent.parent / "data" / "locations_from_photos.json"


def epoch_seconds_to_iso_date(s) -> str | None:
    """Convert an epoch-seconds-as-string to YYYY-MM-DD UTC.

    Google writes the timestamp as a string in these sidecars (e.g.
    "1625078829"). Returns None if the input is missing, non-numeric,
    or not a sensible date.
    """
    if s is None:
        return None
    try:
        epoch = int(str(s).strip())
    except (ValueError, TypeError):
        return None
    if epoch <= 0:
        return None
    try:
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None
    return dt.strftime("%Y-%m-%d")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Parse Google Maps photo sidecars (.jpg.json) into a "
                    "date-keyed location dataset.",
    )
    p.add_argument(
        "photos_root",
        help='Path to "Photos and videos" folder containing the .jpg.json sidecars.',
    )
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = p.parse_args()

    root = Path(args.photos_root)
    if not root.exists():
        print(f"Error: not found: {root}", file=sys.stderr)
        return 1
    if not root.is_dir():
        print(f"Error: not a directory: {root}", file=sys.stderr)
        return 1

    sidecars = sorted(root.glob("*.jpg.json"))
    print(f"Found {len(sidecars)} .jpg.json sidecar(s) in {root}")
    print("─" * 70)

    locations: dict[str, list[dict]] = defaultdict(list)
    skips: Counter = Counter()

    for path in sidecars:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            skips["bad_json"] += 1
            print(f"  - skip {path.name:46s} bad JSON ({type(e).__name__})")
            continue

        if not isinstance(data, dict):
            skips["bad_json"] += 1
            print(f"  - skip {path.name:46s} top-level is not an object")
            continue

        # Date — prefer photoTakenTime over creationTime (when shot vs uploaded).
        taken = data.get("photoTakenTime") or {}
        created = data.get("creationTime") or {}
        date = (
            epoch_seconds_to_iso_date(taken.get("timestamp"))
            or epoch_seconds_to_iso_date(created.get("timestamp"))
        )
        if not date:
            skips["no_date"] += 1
            print(f"  - skip {path.name:46s} no usable timestamp")
            continue

        # Coordinates — geoDataExif preferred, geoData accepted as fallback.
        geo = data.get("geoDataExif") or data.get("geoData") or {}
        lat = geo.get("latitude")
        lng = geo.get("longitude")
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            skips["no_gps"] += 1
            print(f"  - skip {path.name:46s} no GPS in sidecar")
            continue
        if lat == 0 and lng == 0:
            # Stripped-EXIF photos default to 0,0. Skip — that's not a location.
            skips["zero_gps"] += 1
            print(f"  - skip {path.name:46s} GPS is 0,0 (likely stripped)")
            continue

        # Filename — prefer the explicit `title` field, fall back to the
        # sidecar's own filename minus the trailing ".json".
        filename = (data.get("title") or "").strip()
        if not filename:
            filename = path.name[: -len(".json")] if path.name.endswith(".json") else path.name

        details: dict = {"photo_filename": filename}

        # Caption / description — only included when actually written.
        description = (data.get("description") or "").strip()
        if description:
            details["description"] = description[:200]

        # If a future sidecar variant adds a place name, capture it.
        # (Not present in the modern unified format, but cheap to support.)
        place = data.get("place") or {}
        place_name = (place.get("name") if isinstance(place, dict) else None) or ""
        place_name = place_name.strip()
        if place_name:
            details["place_name"] = place_name

        entry = {
            "city": None,
            "state": None,
            "country": None,
            "lat": float(lat),
            "lng": float(lng),
            "source": "google_maps_photo",
            "confidence": "verified",
            "needs_verification": False,
            "details": details,
        }
        locations[date].append(entry)
        print(f"  + {path.name:46s} {date}  {float(lat):>11.6f}, {float(lng):>12.6f}")

    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sorted_locs = dict(sorted(locations.items()))
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sorted_locs, f, indent=2, ensure_ascii=False)

    # Summary
    total = sum(len(v) for v in locations.values())
    all_dates = sorted(locations.keys())
    print()
    print("─" * 70)
    print(f"Total entries:    {total:>5,}")
    print(f"Unique dates:     {len(all_dates):>5,}")
    if all_dates:
        print(f"Date range:       {all_dates[0]} -> {all_dates[-1]}")
    if skips:
        print()
        print("Skipped:")
        for reason in ("bad_json", "no_date", "no_gps", "zero_gps"):
            if skips[reason]:
                print(f"  {reason:12s} {skips[reason]:>3,}")
    print()
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
