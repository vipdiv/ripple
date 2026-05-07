#!/usr/bin/env python3
"""
Parse Uber Data Request CSVs into a date-keyed location dataset
================================================================

Reads the eight location-relevant CSVs from an Uber data request
("Uber Data Request <id>/Uber Data/") and writes the same date-keyed
shape used by 09_parse_reviews.py.

Confidence hierarchy across the location-mining scripts (this script
introduces "low"; verified is the strongest):

    verified > high > medium > low

  verified — user-authored or transactional signal that proves the
             user was somewhere on a specific day. Reviews, support
             tickets, completed driver trips/payments, Uber Eats orders.
  high     — user-declared facts. Saved home, signup city.
  medium   — inferred from email context (used by 08_extract_locations).
  low      — Rider/trips_data-0.csv: needs_verification: true on every
             entry, since the rider trip log includes cancelled trips
             and approximate request locations.

Personal identifiers are NEVER written to the output:
  - Names, phone numbers, email addresses, account/referral IDs
  - Trip UUIDs, license plates, card numbers, payment-method IDs
  - Full street addresses (only city/state/country/lat/lng kept)

Driver/driver_lifetime_trips-0.csv (~3K rows) and Driver/
driver_payments-0.csv (~15K rows) are deduped per (date, city) so a
day with 30 trips emits one entry with details.trip_count = 30 rather
than 30 near-duplicate entries. The other six files emit one entry
per row (or per distinct order, in the Eats case).

Saved-home / saved-work entries from rider_eater_saved_locations.csv
are emitted under a synthetic "_saved" date key, since they're
persistent facts rather than time-bound visits.

Standard library only.

Usage:
  python scripts/10_parse_uber_csvs.py "D:\\path\\to\\Uber Data"
  python scripts/10_parse_uber_csvs.py "...\\Uber Data" \\
      --output data/locations_from_uber.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterator

DEFAULT_OUTPUT = Path(__file__).parent.parent / "data" / "locations_from_uber.json"

# Files we look for, relative to the "Uber Data" root. The "Account and
# Profile" subfolder contains a literal space in its name.
FILES = {
    "customer_support_tickets": "Account and Profile/customer_support_tickets-0.csv",
    "driver_profile":           "Account and Profile/driver_profile-0.csv",
    "rider_saved_locations":    "Account and Profile/rider_eater_saved_locations.csv",
    "user_profile":             "Account and Profile/user_profile-0.csv",
    "driver_lifetime_trips":    "Driver/driver_lifetime_trips-0.csv",
    "driver_payments":          "Driver/driver_payments-0.csv",
    "eats_orders":              "Eats/user_orders-0.csv",
    "rider_trips":              "Rider/trips_data-0.csv",
}

# Country-name -> ISO-2 fallback. Only consulted when the CSV has a
# free-text country column rather than an already-coded value.
COUNTRY_MAP = {
    "united states": "US", "usa": "US", "us": "US",
    "canada": "CA", "ca": "CA",
    "united kingdom": "UK", "uk": "UK", "gb": "UK",
    "india": "IN", "in": "IN",
    "mexico": "MX", "mx": "MX",
}


def normalize_country(raw: str | None) -> str | None:
    if not raw:
        return None
    val = raw.strip().lower()
    if not val:
        return None
    if val in COUNTRY_MAP:
        return COUNTRY_MAP[val]
    if len(val) == 2:
        return val.upper()
    return None


def to_iso_date(timestamp: str | None) -> str | None:
    """Take the YYYY-MM-DD prefix from any of the timestamp formats Uber
    uses ("2014-11-15 02:21:27", "2014-11-15T02:21:27.000Z")."""
    if not timestamp:
        return None
    s = timestamp.strip()
    if len(s) < 10:
        return None
    prefix = s[:10]
    if prefix[4] != "-" or prefix[7] != "-":
        return None
    return prefix


def to_float(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(s: str | None) -> int | None:
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def open_csv(path: Path):
    """Open a CSV with BOM-tolerant UTF-8."""
    return open(path, "r", encoding="utf-8-sig", newline="")


def _trim(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    return s or None


def _filter_none(d: dict) -> dict:
    """Drop keys whose value is None."""
    return {k: v for k, v in d.items() if v is not None}


# ─── Per-file parsers ───────────────────────────────────────────────────────

def parse_customer_support_tickets(path: Path) -> Iterator[tuple[str, dict]]:
    with open_csv(path) as f:
        for row in csv.DictReader(f):
            date = to_iso_date(row.get("Create Ticket Time"))
            city = _trim(row.get("City"))
            if not date or not city:
                continue
            yield date, {
                "city": city,
                "state": None,
                "country": normalize_country(row.get("Country")),
                "lat": None,
                "lng": None,
                "source": "uber_support_ticket",
                "confidence": "verified",
                "needs_verification": False,
                "details": _filter_none({
                    "modality": _trim(row.get("Modality")),
                    "product": _trim(row.get("Product Name")),
                }),
            }


def parse_driver_profile(path: Path) -> Iterator[tuple[str, dict]]:
    with open_csv(path) as f:
        for row in csv.DictReader(f):
            date = to_iso_date(row.get("Signup Date"))
            city = _trim(row.get("Signup City"))
            if not date or not city:
                continue
            yield date, {
                "city": city,
                "state": None,
                "country": normalize_country(row.get("Operating Country")),
                "lat": None,
                "lng": None,
                "source": "uber_driver_signup",
                "confidence": "high",
                "needs_verification": False,
                "details": _filter_none({
                    "operating_city": _trim(row.get("Operating City")),
                    "lifetime_trips": to_int(row.get("Lifetime Completed Trips")),
                }),
            }


def parse_rider_saved_locations(path: Path) -> Iterator[tuple[str, dict]]:
    """Saved locations are persistent facts — emitted under '_saved'."""
    with open_csv(path) as f:
        for row in csv.DictReader(f):
            city = _trim(row.get("City"))
            if not city:
                continue
            label = _trim(row.get("Label"))
            yield "_saved", {
                "city": city,
                "state": _trim(row.get("State")),
                "country": normalize_country(row.get("Country code")),
                "lat": to_float(row.get("Latitude")),
                "lng": to_float(row.get("Longitude")),
                "source": "uber_saved_location",
                "confidence": "high",
                "needs_verification": False,
                "details": _filter_none({"label": label.lower() if label else None}),
            }


def parse_user_profile(path: Path) -> Iterator[tuple[str, dict]]:
    with open_csv(path) as f:
        for row in csv.DictReader(f):
            date = to_iso_date(row.get("Signup Date"))
            city = _trim(row.get("Signup City"))
            if not date or not city:
                continue
            yield date, {
                "city": city,
                "state": None,
                "country": normalize_country(row.get("Country")),
                "lat": to_float(row.get("Signup Lat")),
                "lng": to_float(row.get("Signup Long")),
                "source": "uber_user_signup",
                "confidence": "high",
                "needs_verification": False,
                "details": _filter_none({"user_type": _trim(row.get("User Type"))}),
            }


def parse_driver_lifetime_trips(path: Path) -> Iterator[tuple[str, dict]]:
    """Dedup per (date, city). 2913-row file -> one entry per city per day
    with details.trip_count."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    with open_csv(path) as f:
        for row in csv.DictReader(f):
            ts = _trim(row.get("begintrip_timestamp_local")) or _trim(row.get("request_timestamp_local"))
            date = to_iso_date(ts)
            city = _trim(row.get("city_name"))
            if not date or not city:
                continue
            counts[(date, city)] += 1
    for (date, city), n in counts.items():
        yield date, {
            "city": city,
            "state": None,
            "country": None,
            "lat": None,
            "lng": None,
            "source": "uber_driver_trip",
            "confidence": "verified",
            "needs_verification": False,
            "details": {"trip_count": n},
        }


def parse_driver_payments(path: Path) -> Iterator[tuple[str, dict]]:
    """Dedup per (date, city). 15K-row file -> one entry per city per day
    with details.payment_count (payment events != trip count)."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    with open_csv(path) as f:
        for row in csv.DictReader(f):
            date = to_iso_date(row.get("Local Timestamp"))
            city = _trim(row.get("City Name"))
            if not date or not city:
                continue
            counts[(date, city)] += 1
    for (date, city), n in counts.items():
        yield date, {
            "city": city,
            "state": None,
            "country": None,
            "lat": None,
            "lng": None,
            "source": "uber_driver_payment",
            "confidence": "verified",
            "needs_verification": False,
            "details": {"payment_count": n},
        }


def parse_eats_orders(path: Path) -> Iterator[tuple[str, dict]]:
    """Each CSV row is one item — group by (Request_Time_Local, Restaurant)
    so multi-item orders collapse to one entry with details.item_count."""
    orders: dict[tuple[str, str], dict] = {}
    with open_csv(path) as f:
        for row in csv.DictReader(f):
            ts = _trim(row.get("Request_Time_Local"))
            date = to_iso_date(ts)
            city = _trim(row.get("City_Name"))
            restaurant = _trim(row.get("Restaurant_Name"))
            if not date or not city or not restaurant:
                continue
            key = (ts or "", restaurant)
            if key not in orders:
                orders[key] = {
                    "date": date,
                    "city": city,
                    "restaurant": restaurant,
                    "item_count": 0,
                    "status": _trim(row.get("Order_Status")),
                }
            orders[key]["item_count"] += 1
    for o in orders.values():
        yield o["date"], {
            "city": o["city"],
            "state": None,
            "country": None,
            "lat": None,
            "lng": None,
            "source": "uber_eats_order",
            "confidence": "verified",
            "needs_verification": False,
            "details": _filter_none({
                "restaurant": o["restaurant"],
                "item_count": o["item_count"],
                "order_status": o["status"],
            }),
        }


def parse_rider_trips(path: Path) -> Iterator[tuple[str, dict]]:
    """Low-confidence rider log. One entry per row, needs_verification=true.
    Strips full street addresses (begintrip_address, dropoff_address) and
    card_number — only city + lat/lng kept."""
    with open_csv(path) as f:
        for row in csv.DictReader(f):
            ts = _trim(row.get("begintrip_timestamp_local")) or _trim(row.get("request_timestamp_local"))
            date = to_iso_date(ts)
            city = _trim(row.get("city_name"))
            if not date or not city:
                continue
            lat = to_float(row.get("begintrip_lat"))
            if lat is None:
                lat = to_float(row.get("request_lat"))
            lng = to_float(row.get("begintrip_lng"))
            if lng is None:
                lng = to_float(row.get("request_lng"))
            details: dict = _filter_none({"product": _trim(row.get("product_type_name"))})
            is_completed = (row.get("is_completed") or "").strip().lower()
            if is_completed in ("true", "false"):
                details["is_completed"] = (is_completed == "true")
            yield date, {
                "city": city,
                "state": None,
                "country": None,
                "lat": lat,
                "lng": lng,
                "source": "uber_rider_trip",
                "confidence": "low",
                "needs_verification": True,
                "details": details,
            }


PARSERS = {
    "customer_support_tickets": parse_customer_support_tickets,
    "driver_profile":           parse_driver_profile,
    "rider_saved_locations":    parse_rider_saved_locations,
    "user_profile":             parse_user_profile,
    "driver_lifetime_trips":    parse_driver_lifetime_trips,
    "driver_payments":          parse_driver_payments,
    "eats_orders":              parse_eats_orders,
    "rider_trips":              parse_rider_trips,
}


# ─── Main ───────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description="Parse the eight location-relevant CSVs from an Uber Data Request export.",
    )
    p.add_argument(
        "uber_root",
        help='Path to the "Uber Data" folder (containing "Account and Profile/", "Driver/", etc.)',
    )
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = p.parse_args()

    root = Path(args.uber_root)
    if not root.exists():
        print(f"Error: not found: {root}", file=sys.stderr)
        return 1

    print(f"Reading from: {root}")
    print("─" * 60)

    locations: dict[str, list[dict]] = defaultdict(list)
    source_counts: Counter = Counter()
    confidence_counts: Counter = Counter()
    cities_seen: Counter = Counter()
    skipped_files: list[str] = []

    for label, rel in FILES.items():
        path = root / rel
        if not path.exists():
            skipped_files.append(rel)
            print(f"  - skip {label:32s} (not found: {rel})")
            continue
        n = 0
        for date, entry in PARSERS[label](path):
            locations[date].append(entry)
            source_counts[entry["source"]] += 1
            confidence_counts[entry["confidence"]] += 1
            cities_seen[(entry["city"], entry.get("state"))] += 1
            n += 1
        print(f"  + {label:32s} {n:>5,} entries")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sorted_locs = dict(sorted(locations.items()))
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sorted_locs, f, indent=2, ensure_ascii=False)

    total = sum(len(v) for v in locations.values())
    dated = [d for d in locations if d != "_saved"]

    print()
    print("─" * 60)
    print(f"Total entries:     {total:>6,}")
    print(f"Unique dates:      {len(dated):>6,}")
    if dated:
        print(f"Date range:        {min(dated)} -> {max(dated)}")
    if "_saved" in locations:
        print(f"Saved entries:     {len(locations['_saved']):>6,} (under '_saved')")
    print()
    print("By source:")
    for src, n in source_counts.most_common():
        print(f"  {src:30s} {n:>6,}")
    print()
    print("By confidence:")
    for conf in ("verified", "high", "medium", "low"):
        if confidence_counts[conf]:
            print(f"  {conf:12s} {confidence_counts[conf]:>6,}")
    print()
    print("Top 20 cities:")
    for (c, s), n in cities_seen.most_common(20):
        label = f"{c}, {s}" if s else c
        print(f"  {label:<35s} {n:>6,}")
    print()
    if skipped_files:
        print("Files not found:")
        for f in skipped_files:
            print(f"  {f}")
        print()
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
