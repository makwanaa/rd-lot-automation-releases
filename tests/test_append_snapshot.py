"""Unit tests for scripts/append_snapshot.py."""

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "append_snapshot", REPO_ROOT / "scripts" / "append_snapshot.py"
)
append_snapshot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(append_snapshot)


def write_tsv(path, rows):
    path.write_text("".join(f"{t}\t{a}\t{c}\n" for t, a, c in rows), encoding="utf-8")
    return path


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def run(tmp_path, rows, csv_name="stats/history.csv", monkeypatch=None, today=None):
    """Invoke main() with a snapshot TSV, optionally pinning the UTC date."""
    tsv = write_tsv(tmp_path / "snapshot.tsv", rows)
    history = tmp_path / csv_name
    if today is not None:
        monkeypatch.setattr(append_snapshot, "datetime", _FrozenDatetime(today))
    append_snapshot.main(["append_snapshot.py", str(tsv), str(history)])
    return history


class _FrozenDate:
    def __init__(self, value):
        self._value = value

    def isoformat(self):
        return self._value


class _FrozenNow:
    def __init__(self, value):
        self._value = value

    def date(self):
        return _FrozenDate(self._value)


class _FrozenDatetime:
    def __init__(self, value):
        self._value = value

    def now(self, tz=None):
        return _FrozenNow(self._value)


# --- Happy path ---


def test_first_snapshot_creates_csv_with_blank_delta(tmp_path):
    history = run(tmp_path, [("v1.0.0", "setup.exe", 5)])

    rows = read_csv(history)
    assert rows == [
        {"date": rows[0]["date"], "tag": "v1.0.0", "asset": "setup.exe", "cumulative": "5", "delta": ""}
    ]


def test_second_day_records_delta(tmp_path, monkeypatch):
    run(tmp_path, [("v1.0.0", "setup.exe", 5)], monkeypatch=monkeypatch, today="2026-01-01")
    history = run(
        tmp_path, [("v1.0.0", "setup.exe", 9)], monkeypatch=monkeypatch, today="2026-01-02"
    )

    rows = read_csv(history)
    assert [(r["date"], r["cumulative"], r["delta"]) for r in rows] == [
        ("2026-01-01", "5", ""),
        ("2026-01-02", "9", "4"),
    ]


def test_rows_are_sorted_by_date_tag_asset(tmp_path, monkeypatch):
    run(tmp_path, [("v1.0.0", "b.exe", 1), ("v1.0.0", "a.exe", 2)], monkeypatch=monkeypatch, today="2026-01-02")
    history = run(tmp_path, [("v1.0.0", "a.exe", 3)], monkeypatch=monkeypatch, today="2026-01-01")

    rows = read_csv(history)
    assert [(r["date"], r["asset"]) for r in rows] == [
        ("2026-01-01", "a.exe"),
        ("2026-01-02", "a.exe"),
        ("2026-01-02", "b.exe"),
    ]


# --- Edge cases ---


def test_same_day_rerun_replaces_rather_than_duplicates(tmp_path, monkeypatch):
    run(tmp_path, [("v1.0.0", "setup.exe", 5)], monkeypatch=monkeypatch, today="2026-01-01")
    history = run(
        tmp_path, [("v1.0.0", "setup.exe", 7)], monkeypatch=monkeypatch, today="2026-01-01"
    )

    rows = read_csv(history)
    assert len(rows) == 1
    assert rows[0]["cumulative"] == "7"
    assert rows[0]["delta"] == ""


def test_same_day_rerun_keeps_delta_against_previous_day(tmp_path, monkeypatch):
    run(tmp_path, [("v1.0.0", "setup.exe", 5)], monkeypatch=monkeypatch, today="2026-01-01")
    run(tmp_path, [("v1.0.0", "setup.exe", 8)], monkeypatch=monkeypatch, today="2026-01-02")
    history = run(
        tmp_path, [("v1.0.0", "setup.exe", 11)], monkeypatch=monkeypatch, today="2026-01-02"
    )

    rows = read_csv(history)
    assert len(rows) == 2
    assert (rows[1]["cumulative"], rows[1]["delta"]) == ("11", "6")


def test_new_asset_midstream_gets_blank_delta(tmp_path, monkeypatch):
    run(tmp_path, [("v1.0.0", "old.exe", 5)], monkeypatch=monkeypatch, today="2026-01-01")
    history = run(
        tmp_path,
        [("v1.0.0", "old.exe", 6), ("v1.1.0", "new.exe", 3)],
        monkeypatch=monkeypatch,
        today="2026-01-02",
    )

    rows = {(r["asset"], r["date"]): r for r in read_csv(history)}
    assert rows[("old.exe", "2026-01-02")]["delta"] == "1"
    assert rows[("new.exe", "2026-01-02")]["delta"] == ""


def test_disappearing_asset_leaves_history_intact(tmp_path, monkeypatch):
    run(
        tmp_path,
        [("v1.0.0", "gone.exe", 5), ("v1.0.0", "kept.exe", 1)],
        monkeypatch=monkeypatch,
        today="2026-01-01",
    )
    history = run(tmp_path, [("v1.0.0", "kept.exe", 4)], monkeypatch=monkeypatch, today="2026-01-02")

    rows = read_csv(history)
    assert len(rows) == 3
    assert any(r["asset"] == "gone.exe" and r["date"] == "2026-01-01" for r in rows)


def test_asset_recreated_with_lower_count_records_negative_delta(tmp_path, monkeypatch):
    run(tmp_path, [("v1.0.0", "setup.exe", 20)], monkeypatch=monkeypatch, today="2026-01-01")
    history = run(tmp_path, [("v1.0.0", "setup.exe", 2)], monkeypatch=monkeypatch, today="2026-01-02")

    assert read_csv(history)[1]["delta"] == "-18"


def test_zero_downloads_records_zero_delta(tmp_path, monkeypatch):
    run(tmp_path, [("v1.0.0", "setup.exe", 5)], monkeypatch=monkeypatch, today="2026-01-01")
    history = run(tmp_path, [("v1.0.0", "setup.exe", 5)], monkeypatch=monkeypatch, today="2026-01-02")

    assert read_csv(history)[1]["delta"] == "0"


def test_empty_snapshot_leaves_existing_history_untouched(tmp_path, monkeypatch):
    history = run(tmp_path, [("v1.0.0", "setup.exe", 5)], monkeypatch=monkeypatch, today="2026-01-01")
    before = history.read_text(encoding="utf-8")

    empty = write_tsv(tmp_path / "empty.tsv", [])
    append_snapshot.main(["append_snapshot.py", str(empty), str(history)])

    assert history.read_text(encoding="utf-8") == before


def test_blank_lines_in_snapshot_are_ignored(tmp_path):
    tsv = tmp_path / "snapshot.tsv"
    tsv.write_text("v1.0.0\tsetup.exe\t5\n\n\n", encoding="utf-8")
    history = tmp_path / "history.csv"

    append_snapshot.main(["append_snapshot.py", str(tsv), str(history)])

    assert len(read_csv(history)) == 1


def test_asset_name_with_comma_survives_round_trip(tmp_path):
    history = run(tmp_path, [("v1.0.0", "setup,final.exe", 5)])

    assert read_csv(history)[0]["asset"] == "setup,final.exe"


def test_nested_history_directory_is_created(tmp_path):
    history = run(tmp_path, [("v1.0.0", "setup.exe", 5)], csv_name="a/b/c/history.csv")

    assert history.exists()


# --- Failure modes ---


def test_malformed_tsv_row_is_rejected(tmp_path):
    tsv = tmp_path / "snapshot.tsv"
    tsv.write_text("v1.0.0\tsetup.exe\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="expected 3 tab-separated fields"):
        append_snapshot.main(["append_snapshot.py", str(tsv), str(tmp_path / "h.csv")])


def test_non_integer_download_count_is_rejected(tmp_path):
    tsv = write_tsv(tmp_path / "snapshot.tsv", [("v1.0.0", "setup.exe", "many")])

    with pytest.raises(SystemExit, match="is not an integer"):
        append_snapshot.main(["append_snapshot.py", str(tsv), str(tmp_path / "h.csv")])


def test_history_with_missing_columns_is_rejected(tmp_path):
    history = tmp_path / "history.csv"
    history.write_text("date,asset\n2026-01-01,setup.exe\n", encoding="utf-8")
    tsv = write_tsv(tmp_path / "snapshot.tsv", [("v1.0.0", "setup.exe", 5)])

    with pytest.raises(SystemExit, match="missing expected columns"):
        append_snapshot.main(["append_snapshot.py", str(tsv), str(history)])


def test_history_with_corrupt_cumulative_is_rejected(tmp_path):
    history = tmp_path / "history.csv"
    history.write_text(
        "date,tag,asset,cumulative,delta\n2026-01-01,v1.0.0,setup.exe,oops,\n", encoding="utf-8"
    )
    tsv = write_tsv(tmp_path / "snapshot.tsv", [("v1.0.0", "setup.exe", 5)])

    with pytest.raises(SystemExit, match="non-integer cumulative count"):
        append_snapshot.main(["append_snapshot.py", str(tsv), str(history)])


def test_wrong_argument_count_is_rejected(tmp_path):
    with pytest.raises(SystemExit, match="usage:"):
        append_snapshot.main(["append_snapshot.py", str(tmp_path / "only.tsv")])


def test_missing_snapshot_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        append_snapshot.main(
            ["append_snapshot.py", str(tmp_path / "nope.tsv"), str(tmp_path / "h.csv")]
        )
