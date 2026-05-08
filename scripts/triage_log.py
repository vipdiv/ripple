#!/usr/bin/env python3
"""
Triage CLI for TSV review logs
==============================

Walks a TSV log file (each line "<timestamp>\\t<title>") one entry at
a time and captures a verdict per entry into a sidecar file:

    <log_path>.verdicts.tsv     # "<timestamp>\\t<title>\\t<verdict>"

Designed for the maps_*_for_review.log files produced by parser 13,
but not specific to any source — it works on any 2-column TSV where
the first column is a unique-ish timestamp and the second is the item
to triage.

Verdicts:
  v  visited   — keep this entry as a real visit signal
  p  phantom   — discard (false positive, planning, etc.)
  s  skip      — defer; entry will reappear on next run
  u  undo      — pop the most recent verdict from THIS session and
                 jump back to that entry to re-mark
  q  quit      — save progress and exit (progress is already saved
                 after every verdict; this just stops the loop)

Resume: load the sidecar at startup. Any (timestamp, title) already
present is filtered out before the loop begins. Re-running picks up
where the previous run left off.

Within-session undo only: undo unwinds verdicts made in the current
process. Verdicts inherited from earlier sessions stay put. To redo a
prior-session verdict, edit the sidecar TSV by hand.

Single-keypress where possible (msvcrt on Windows, termios+cbreak on
Unix); falls back to line-buffered input() when stdin isn't a TTY
(piped input for testing) or when neither low-level mechanism is
available. No third-party dependencies.

Usage:
  python scripts/triage_log.py data/maps_viewed_for_review.log
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


# ─── Single-keypress reader ─────────────────────────────────────────────────

def prompt_key(prompt: str) -> str:
    """Show a prompt, read one key, return it lowercased.

    Behavior depends on stdin:
      - Not a TTY (e.g. piped input from a test): read a whole line,
        return the first stripped char.
      - Windows TTY: msvcrt.getch (bytes -> char).
      - Unix TTY: termios cbreak mode + sys.stdin.read(1).
      - Anything else: input() fallback.

    The prompt is always shown. In TTY modes the typed char is echoed
    back manually since neither getch nor cbreak echoes for us.
    """
    sys.stdout.write(prompt)
    sys.stdout.flush()

    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        if not line:
            sys.stdout.write("[EOF]\n")
            return "q"
        s = line.strip()
        return s[:1].lower() if s else ""

    # Windows
    try:
        import msvcrt
        ch_b = msvcrt.getch()
        try:
            ch = ch_b.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            ch = ""
        sys.stdout.write(ch + "\n")
        sys.stdout.flush()
        return ch.lower() if ch else ""
    except ImportError:
        pass

    # Unix
    try:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write(ch + "\n")
        sys.stdout.flush()
        return ch.lower() if ch else ""
    except (ImportError, OSError, AttributeError):
        pass

    # Last-ditch fallback
    try:
        s = input()
        return s.strip()[:1].lower() if s.strip() else ""
    except EOFError:
        return "q"


# ─── Log + sidecar I/O ──────────────────────────────────────────────────────

def load_log(path: Path) -> list[tuple[str, str]]:
    """Return a list of (timestamp, title) from a 2-column TSV. Skips blanks
    and any line without a tab."""
    entries: list[tuple[str, str]] = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n").rstrip("\r")
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) < 2:
                continue
            entries.append((parts[0], parts[1]))
    return entries


def load_verdicts(path: Path) -> dict[tuple[str, str], str]:
    """Return an ordered dict {(timestamp, title): verdict}. Empty if the
    sidecar doesn't exist yet."""
    verdicts: dict[tuple[str, str], str] = {}
    if not path.exists():
        return verdicts
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n").rstrip("\r")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            verdicts[(parts[0], parts[1])] = parts[2]
    return verdicts


def append_verdict(path: Path, ts: str, title: str, verdict: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{ts}\t{title}\t{verdict}\n")


def rewrite_sidecar(path: Path, verdicts: dict[tuple[str, str], str]) -> None:
    """Used by undo to remove a previously-appended verdict."""
    with open(path, "w", encoding="utf-8") as f:
        for (ts, title), verdict in verdicts.items():
            f.write(f"{ts}\t{title}\t{verdict}\n")


# ─── Main loop ──────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description="Triage a 2-column TSV review log; verdicts go to a "
                    "sidecar at <log>.verdicts.tsv.",
    )
    p.add_argument("log_path", help="Path to the TSV log to triage.")
    args = p.parse_args()

    log_path = Path(args.log_path)
    if not log_path.exists():
        print(f"Error: not found: {log_path}", file=sys.stderr)
        return 1

    sidecar = log_path.with_suffix(log_path.suffix + ".verdicts.tsv")

    entries = load_log(log_path)
    if not entries:
        print(f"No entries in {log_path}.")
        return 0

    verdicts = load_verdicts(sidecar)

    print(f"Log:     {log_path}  ({len(entries)} entries)")
    print(f"Sidecar: {sidecar}", end="")
    if verdicts:
        print(f"  ({len(verdicts)} previously verdicted — resuming)")
    else:
        print()
    print()

    # Working list = original-order entries with no existing verdict.
    working = [(ts, title) for ts, title in entries if (ts, title) not in verdicts]
    if not working:
        v = sum(1 for x in verdicts.values() if x == "visited")
        p_ = sum(1 for x in verdicts.values() if x == "phantom")
        print(f"All {len(entries)} entries already verdicted "
              f"({v} visited, {p_} phantom). Nothing to do.")
        print(f"Sidecar is gitignored ({sidecar.name} matches *.verdicts.tsv).")
        return 0

    print(f"{len(working)} entries to triage. Press u to undo within this session.")
    print()

    undo_stack: list[int] = []   # indices into `working` where verdicts were made this run
    skipped_this_run = 0
    quit_requested = False
    i = 0

    try:
        while i < len(working):
            ts, title = working[i]
            print(f"[{i + 1} of {len(working)}]  {ts}")
            print(f"  {title}")
            print()
            print("  [v]isited  [p]hantom  [s]kip  [q]uit  [u]ndo")
            key = prompt_key("  > ")

            if key in ("v", "p"):
                verdict = "visited" if key == "v" else "phantom"
                verdicts[(ts, title)] = verdict
                append_verdict(sidecar, ts, title, verdict)
                undo_stack.append(i)
                i += 1
            elif key == "s":
                print("  -> skipped (no verdict; will reappear next run)")
                skipped_this_run += 1
                i += 1
            elif key == "u":
                if not undo_stack:
                    print("  -> nothing to undo this session")
                else:
                    prev_i = undo_stack.pop()
                    prev_ts, prev_title = working[prev_i]
                    verdicts.pop((prev_ts, prev_title), None)
                    rewrite_sidecar(sidecar, verdicts)
                    i = prev_i
                    print(f"  -> undid verdict on entry {prev_i + 1}")
            elif key == "q":
                quit_requested = True
                break
            else:
                print(f"  -> '{key}' not recognized; use v / p / s / q / u")
            print()
    except KeyboardInterrupt:
        print("\n[Ctrl+C — saving progress]")
        quit_requested = True

    # Final summary.
    visited_n = sum(1 for x in verdicts.values() if x == "visited")
    phantom_n = sum(1 for x in verdicts.values() if x == "phantom")
    remaining = len(entries) - len(verdicts)

    print()
    print("─" * 60)
    if quit_requested:
        print("Quit. Progress saved.")
    elif remaining == 0:
        print("Done — every entry has a verdict.")
    else:
        print("Loop ended.")
    print(f"  visited:           {visited_n}")
    print(f"  phantom:           {phantom_n}")
    print(f"  skipped this run:  {skipped_this_run}")
    print(f"  remaining:         {remaining}")
    print()
    if remaining == 0:
        print(f"Sidecar is gitignored ({sidecar.name} matches *.verdicts.tsv).")
        print("Merger #18 will read it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
