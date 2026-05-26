"""
Tests for the OpenStates cumulative store + incremental refresh logic.

The client persists every bill it has fetched into a local cumulative store
and refreshes incrementally. When the daily 500-request quota is exhausted,
downstream signal detectors keep reading from the store and the next
refresh resumes after the daily reset. These tests pin that contract so
the quota cannot zero the bill data for the day.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts._lib.openstates_client import (
    _load_existing,
    persist_bills,
    persist_hearings,
)


def _bill(bid: str, action_date: str = "2026-05-20", identifier: str = "CA SB 1") -> dict:
    return {
        "id": bid,
        "identifier": identifier,
        "title": f"Bill {bid}",
        "jurisdiction_name": "California",
        "latest_action_date": action_date,
        "openstates_url": f"https://openstates.org/{bid}",
    }


def _hearing(hid: str, end_date: str = "2026-06-01") -> dict:
    return {
        "id": hid,
        "name": f"Hearing {hid}",
        "jurisdiction": "ca",
        "start_date": "2026-05-25",
        "end_date": end_date,
        "classification": "committee-meeting",
    }


def test_load_existing_returns_empty_when_file_missing(tmp_path: Path):
    by_id, last_fetch = _load_existing(tmp_path / "missing.json", "bills")
    assert by_id == {}
    assert last_fetch is None


def test_load_existing_indexes_records_by_id(tmp_path: Path):
    path = tmp_path / "bills.json"
    path.write_text(json.dumps({
        "fetched_at": "2026-05-20T00:00:00",
        "last_successful_fetch": "2026-05-20",
        "count": 2,
        "bills": [_bill("b1"), _bill("b2")],
    }))
    by_id, last_fetch = _load_existing(path, "bills")
    assert set(by_id.keys()) == {"b1", "b2"}
    assert last_fetch == "2026-05-20"


def test_load_existing_handles_legacy_files_without_last_successful_fetch(tmp_path: Path):
    """Backward compat: existing data/openstates_bills.json files written by
    the prior overwrite-on-each-run code path do not carry the new metadata
    field. The new loader must accept them and return last_fetch=None so the
    next live fetch seeds it fresh."""
    path = tmp_path / "legacy_bills.json"
    path.write_text(json.dumps({
        "fetched_at": "2026-05-20T00:00:00",
        "count": 1,
        "bills": [_bill("b1")],
    }))
    by_id, last_fetch = _load_existing(path, "bills")
    assert "b1" in by_id
    assert last_fetch is None


def test_persist_bills_merges_new_into_existing(tmp_path: Path):
    path = tmp_path / "bills.json"
    # Seed with one bill
    persist_bills([_bill("b1")], path, last_successful_fetch="2026-05-20")
    # Persist a second bill; the first must remain
    persist_bills([_bill("b2")], path, last_successful_fetch="2026-05-21")
    by_id, last_fetch = _load_existing(path, "bills")
    assert set(by_id.keys()) == {"b1", "b2"}
    assert last_fetch == "2026-05-21"


def test_persist_bills_replaces_existing_record_on_id_collision(tmp_path: Path):
    """When the same bill id is fetched again with a newer latest_action_date,
    the cumulative store keeps the newer copy."""
    path = tmp_path / "bills.json"
    persist_bills([_bill("b1", action_date="2026-05-10")], path, last_successful_fetch="2026-05-10")
    persist_bills([_bill("b1", action_date="2026-05-22")], path, last_successful_fetch="2026-05-22")
    by_id, _ = _load_existing(path, "bills")
    assert by_id["b1"]["latest_action_date"] == "2026-05-22"


def test_persist_bills_cumulative_false_preserves_legacy_overwrite_behavior(tmp_path: Path):
    """The legacy CLI entry point passes cumulative=False to overwrite the
    store; the test pins that contract so the CLI path keeps working."""
    path = tmp_path / "bills.json"
    persist_bills([_bill("b1"), _bill("b2")], path, last_successful_fetch="2026-05-20")
    persist_bills([_bill("b3")], path, last_successful_fetch="2026-05-21", cumulative=False)
    by_id, _ = _load_existing(path, "bills")
    assert set(by_id.keys()) == {"b3"}


def test_persist_hearings_prunes_past_events(tmp_path: Path):
    """Hearings whose end_date is before today are pruned on each merge so the
    cumulative store does not grow indefinitely with stale events."""
    path = tmp_path / "hearings.json"
    persist_hearings(
        [_hearing("h_past", end_date="2020-01-01"), _hearing("h_future", end_date="2099-12-31")],
        path,
        last_successful_fetch="2026-05-20",
    )
    by_id, _ = _load_existing(path, "hearings")
    assert "h_past" not in by_id
    assert "h_future" in by_id


def test_persist_hearings_keeps_undated_events(tmp_path: Path):
    """Hearings without an end_date should not be pruned; the source may omit
    the field even on valid future events."""
    h = _hearing("h_undated")
    del h["end_date"]
    path = tmp_path / "hearings.json"
    persist_hearings([h], path, last_successful_fetch="2026-05-20")
    by_id, _ = _load_existing(path, "hearings")
    assert "h_undated" in by_id
