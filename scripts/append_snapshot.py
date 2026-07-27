#!/usr/bin/env python3
"""Append a daily snapshot of release asset download counts to a history CSV.

GitHub only exposes a *cumulative* `download_count` per release asset, with no
time series. This script turns repeated snapshots into one by recording a dated
row per asset and computing the day-over-day delta against the most recent
earlier snapshot of that same asset.

Input is a TSV of `tag<TAB>asset<TAB>cumulative` rows (as produced by `gh api`).
Re-running on the same UTC date replaces that date's rows rather than
duplicating them, so manual re-runs stay idempotent.

Usage: append_snapshot.py <snapshot.tsv> <history.csv>
"""

import csv
import os
import sys
from datetime import datetime, timezone

FIELDNAMES = ["date", "tag", "asset", "cumulative", "delta"]


def read_history(path):
    """Return existing history rows, or [] if the file does not exist yet."""
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = set(FIELDNAMES) - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"{path}: missing expected columns {sorted(missing)}")
        return [dict(row) for row in reader]


def read_snapshot(path):
    """Parse the `tag<TAB>asset<TAB>count` TSV emitted by the gh api step."""
    rows = []
    with open(path, newline="", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.rstrip("\r\n")
            if not line.strip():
                continue
            fields = line.split("\t")
            if len(fields) != 3:
                raise SystemExit(
                    f"{path}:{lineno}: expected 3 tab-separated fields, got {len(fields)}"
                )
            tag, asset, raw_count = fields
            try:
                count = int(raw_count)
            except ValueError:
                raise SystemExit(
                    f"{path}:{lineno}: download count {raw_count!r} is not an integer"
                )
            rows.append({"tag": tag, "asset": asset, "cumulative": count})
    return rows


def latest_cumulative_by_asset(history):
    """Map (tag, asset) -> cumulative count from the most recent dated row."""
    latest = {}
    for row in sorted(history, key=lambda r: r["date"]):
        try:
            latest[(row["tag"], row["asset"])] = int(row["cumulative"])
        except ValueError:
            raise SystemExit(
                f"history row for {row['asset']} on {row['date']} has a "
                f"non-integer cumulative count: {row['cumulative']!r}"
            )
    return latest


def build_rows(snapshot, previous, today):
    """Pair each snapshot entry with its delta against the previous snapshot."""
    rows = []
    for entry in snapshot:
        key = (entry["tag"], entry["asset"])
        # No prior observation means no meaningful delta -- leave it blank
        # rather than claiming the whole cumulative count landed today.
        delta = "" if key not in previous else entry["cumulative"] - previous[key]
        rows.append(
            {
                "date": today,
                "tag": entry["tag"],
                "asset": entry["asset"],
                "cumulative": entry["cumulative"],
                "delta": delta,
            }
        )
    return rows


def write_history(path, rows):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: (r["date"], r["tag"], r["asset"])))


def main(argv):
    if len(argv) != 3:
        raise SystemExit(f"usage: {os.path.basename(argv[0])} <snapshot.tsv> <history.csv>")

    snapshot_path, history_path = argv[1], argv[2]
    snapshot = read_snapshot(snapshot_path)
    if not snapshot:
        print(f"{snapshot_path}: no release assets found; leaving history unchanged")
        return 0

    today = datetime.now(timezone.utc).date().isoformat()
    history = read_history(history_path)
    # Drop any rows already written for today so a re-run overwrites instead of
    # appending a duplicate (and a bogus zero delta).
    prior = [row for row in history if row["date"] != today]

    new_rows = build_rows(snapshot, latest_cumulative_by_asset(prior), today)
    write_history(history_path, prior + new_rows)

    for row in new_rows:
        print(f"{row['date']}  {row['asset']}  total={row['cumulative']}  delta={row['delta'] or 'n/a'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
