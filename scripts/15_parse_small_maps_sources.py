#!/usr/bin/env python3
"""
Parse Maps "Suggested edits" + "Traffic incidents" into a month-keyed
location dataset
=====================================================================

Two small Maps Takeout sources combined into one parser since neither
warrants its own script:

  Source 1: "Suggested edits to business establishments" (~5 files)
    JSON files. Each one has a CID inside placeUrl, an editAction
    label, and a metadata.createTime ISO timestamp. Some entries also
    have a businessExistenceChange sub-object describing what the
    user reported about the place — preserved verbatim if present.

  Source 2: "Traffic incident reports and votes" (~5 files)
    JSON files named by Unix-epoch-ms. Each one contains only
    contributionType + disruptionType — no placeUrl, no coordinates.
    The filename IS the timestamp.

Both sources are high-confidence visit signals (the entries were
GPS-anchored at the time the user filed them) but the export doesn't
preserve coordinates. city / state / country / lat / lng are all null
in the output; the merger script #18 inherits location from same-
month Type F directions entries (parser #13).

Date keys are YYYY-MM, NOT YYYY-MM-DD. The visualization for these
sources works at month granularity.

Defensive policy: NO street addresses anywhere in output. Neither
source carries them, but the rule is enforced as policy.

Pre-flight safety check: if the resolved output path is inside the
repo and is NOT mentioned in .gitignore, the script refuses to write
anything and exits non-zero. This catches the easy mistake of
introducing a new output file without registering it as ignored.
Outputs to paths outside the repo (e.g. /tmp/ for smoke tests) are
allowed without the gitignore check, since they can't pollute the
repo's git history.

Standard library only.

Usage:
  python scripts/15_parse_small_maps_sources.py \\
      "D:\\path\\Maps\\Suggested edits to business establishments" \\
      "D:\\path\\Maps\\Traffic incident reports and votes"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "data" / "locations_from_small_maps_sources.json"

CID_RE = re.compile(r"cid=([0-9a-fx:]+)", re.IGNORECASE)


def output_in_gitignore(output_path: Path, repo_root: Path) -> bool:
    """Safety check. Return True if the output path is OK to write."""
    try:
        rel = output_path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return True  # outside repo, no commit risk
    gi = repo_root / ".gitignore"
    if not gi.exists():
        return False
    return rel in gi.read_text(encoding="utf-8")


def epoch_ms_to_iso(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _empty_location_fields() -> dict:
    """Every entry in this parser's output starts with the same null fields.
    The merger script #18 fills these in by month-window inheritance."""
    return {
        "city": None, "state": None, "country": None, "lat": None, "lng": None,
    }


# ─── Suggested edits ────────────────────────────────────────────────────────

def parse_suggested_edit(path: Path):
    """Parse one suggested-edit JSON file.

    Returns (yyyy_mm, entry) on success, or (None, reason_str) on failure.
    Reasons: 'bad_json', 'no_cid', 'no_create_time'.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None, "bad_json"
    if not isinstance(data, dict):
        return None, "bad_json"

    place_url = (data.get("placeUrl") or "").strip()
    m = CID_RE.search(place_url)
    if not m:
        return None, "no_cid"
    cid = m.group(1)

    metadata = data.get("metadata") or {}
    iso = (metadata.get("createTime") or "").strip()
    if len(iso) < 7:
        return None, "no_create_time"
    yyyy_mm = iso[:7]

    details = {
        "cid": cid,
        "edit_action": data.get("editAction") or "",
        "iso_timestamp": iso,
    }
    bec = data.get("businessExistenceChange")
    if bec is not None:
        details["business_existence_change"] = bec

    entry = {
        **_empty_location_fields(),
        "source": "google_maps_suggested_edit",
        "confidence": "high",
        "needs_verification": False,
        "details": details,
    }
    return yyyy_mm, entry


# ─── Traffic incidents ──────────────────────────────────────────────────────

def parse_traffic_incident(path: Path):
    """Parse one traffic-incident JSON file. Filename is the epoch-ms timestamp.

    Returns (yyyy_mm, entry) on success, or (None, reason_str) on failure.
    Reasons: 'filename_not_integer', 'bad_json'.
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

    iso = epoch_ms_to_iso(ms)
    yyyy_mm = iso[:7]

    entry = {
        **_empty_location_fields(),
        "source": "google_maps_traffic_incident",
        "confidence": "high",
        "needs_verification": False,
        "details": {
            "contribution_type": data.get("contributionType") or "",
            "disruption_type": data.get("disruptionType") or "",
            "iso_timestamp": iso,
            "timestamp_ms": ms,
        },
    }
    return yyyy_mm, entry


# ─── Main ───────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description="Parse Maps Suggested Edits + Traffic Incidents into a "
                    "month-keyed location dataset.",
    )
    p.add_argument(
        "suggested_edits_dir",
        help='Path to "Suggested edits to business establishments" folder',
    )
    p.add_argument(
        "traffic_incidents_dir",
        help='Path to "Traffic incident reports and votes" folder',
    )
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = p.parse_args()

    output_path = Path(args.output)

    # Pre-flight gitignore safety check.
    if not output_in_gitignore(output_path, REPO_ROOT):
        print(
            f"ERROR: {output_path}\n"
            f"  is inside the repo root but is NOT mentioned in .gitignore.\n"
            f"  Add an explicit line for it, then re-run.",
            file=sys.stderr,
        )
        return 2

    edits_dir = Path(args.suggested_edits_dir)
    traffic_dir = Path(args.traffic_incidents_dir)
    if not edits_dir.is_dir():
        print(f"Error: not a directory: {edits_dir}", file=sys.stderr)
        return 1
    if not traffic_dir.is_dir():
        print(f"Error: not a directory: {traffic_dir}", file=sys.stderr)
        return 1

    edits_files = sorted(edits_dir.glob("*.json"))
    traffic_files = sorted(traffic_dir.glob("*.json"))
    print(f"Suggested edits dir:   {edits_dir} ({len(edits_files)} file(s))")
    print(f"Traffic incidents dir: {traffic_dir} ({len(traffic_files)} file(s))")
    print("─" * 72)

    locations: dict[str, list[dict]] = defaultdict(list)

    edits_extracted = 0
    edits_skipped: Counter = Counter()
    edit_actions: Counter = Counter()
    for path in edits_files:
        result = parse_suggested_edit(path)
        if result[0] is None:
            edits_skipped[result[1]] += 1
            print(f"  - skip edit/{path.name:34s}  {result[1]}")
            continue
        yyyy_mm, entry = result
        locations[yyyy_mm].append(entry)
        edits_extracted += 1
        edit_actions[entry["details"]["edit_action"]] += 1
        print(f"  + edit  {path.name:34s}  {yyyy_mm}")

    traffic_extracted = 0
    traffic_skipped: Counter = Counter()
    disruption_types: Counter = Counter()
    for path in traffic_files:
        result = parse_traffic_incident(path)
        if result[0] is None:
            traffic_skipped[result[1]] += 1
            print(f"  - skip traf/{path.name:34s}  {result[1]}")
            continue
        yyyy_mm, entry = result
        locations[yyyy_mm].append(entry)
        traffic_extracted += 1
        disruption_types[entry["details"]["disruption_type"]] += 1
        print(f"  + traf  {path.name:34s}  {yyyy_mm}")

    # Write output (month-keyed dict, ascending).
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sorted_locs = dict(sorted(locations.items()))
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sorted_locs, f, indent=2, ensure_ascii=False)

    months = sorted(locations.keys())
    total_extracted = edits_extracted + traffic_extracted

    print()
    print("─" * 72)
    print("Suggested edits:")
    print(f"  scanned:    {len(edits_files):>4,}")
    print(f"  extracted:  {edits_extracted:>4,}")
    if edits_skipped:
        for reason in ("bad_json", "no_cid", "no_create_time"):
            if edits_skipped[reason]:
                print(f"  skip {reason:18s} {edits_skipped[reason]:>4,}")
    print()
    print("Traffic incidents:")
    print(f"  scanned:    {len(traffic_files):>4,}")
    print(f"  extracted:  {traffic_extracted:>4,}")
    if traffic_skipped:
        for reason in ("bad_json", "filename_not_integer"):
            if traffic_skipped[reason]:
                print(f"  skip {reason:18s} {traffic_skipped[reason]:>4,}")
    print()
    print(f"Total extracted:  {total_extracted:>4,}")
    print(f"Unique months:    {len(months):>4,}")
    if months:
        print(f"Month range:      {months[0]} -> {months[-1]}")

    source_counts: Counter = Counter()
    for entries in locations.values():
        for e in entries:
            source_counts[e["source"]] += 1
    if source_counts:
        print()
        print("By source:")
        for src, n in source_counts.most_common():
            print(f"  {src:34s} {n:>3,}")

    if edit_actions:
        print()
        print("Top 5 edit_action values:")
        for action, n in edit_actions.most_common(5):
            print(f"  {n:>3,}  {action}")

    if disruption_types:
        print()
        print("Top 5 disruption_type values:")
        for dt, n in disruption_types.most_common(5):
            print(f"  {n:>3,}  {dt}")

    print()
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
