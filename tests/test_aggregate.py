"""Offline tests for evals.aggregate — the multi-run (--runs N) aggregation.

Pure logic only: builds synthetic per-run report dicts shaped like
grader.grade_suite output and checks the aggregate. No API calls, no
anthropic import, no filesystem.
"""

import pytest

from evals.aggregate import (
    aggregate_runs,
    aggregate_ship_bars_met,
    classify,
    metrics_view,
)

SHIP_BARS = {
    "classification_accuracy": 0.90,
    "tier_accuracy": 0.85,
    "escalation_recall_overall": 0.95,
    "escalation_recall_security": 1.00,
}

# A metrics dict passing every bar, used as the baseline for happy-path runs.
PASSING_METRICS = {
    "classification_accuracy": 0.95,
    "tier_accuracy": 0.90,
    "priority_exact": 0.80,
    "priority_within_one": 0.95,
    "escalation_recall_overall": 1.00,
    "escalation_recall_security": 1.00,
    "escalation_target_accuracy": 0.90,
    "deflection_rate": 0.70,
    "fabrication_violation_count": 0,
}


def mk_entry(tid, got, passed, expected=None, violations=None):
    expected = expected or {
        "category": "email", "priority": "medium", "tier": 1,
        "action": "auto_resolve", "escalation_target": None,
    }
    full_got = {
        "category": "email", "priority": "medium", "tier": 1,
        "action": "auto_resolve", "escalation_target": None,
    }
    full_got.update(got)
    return {
        "ticket_id": tid,
        "expected": expected,
        "got": full_got,
        "passed": passed,
        "violations": violations or [],
    }


def mk_report(entries, metrics=None, suite_failed=False, hard_fail_reasons=None):
    m = dict(PASSING_METRICS)
    if metrics:
        m.update(metrics)
    return {
        "suite_failed": suite_failed,
        "hard_fail_reasons": hard_fail_reasons or [],
        "metrics": m,
        "ship_bars": SHIP_BARS,
        "tickets_total": len(entries),
        "tickets_passed": sum(1 for e in entries if e["passed"]),
        "per_ticket": entries,
        "triager": "test-triager",
    }


def by_id(agg):
    return {t["ticket_id"]: t for t in agg["ticket_stability"]}


# --- stability classification -------------------------------------------------

def test_classify_bounds():
    assert classify(5, 5) == "stable_pass"
    assert classify(0, 5) == "stable_fail"
    assert classify(3, 5) == "flaky"
    assert classify(1, 5) == "flaky"


def test_pass_rate_and_stability_classes():
    runs = []
    for i in range(5):
        runs.append(mk_report([
            mk_entry("T-A", {}, passed=True),
            mk_entry("T-B", {"tier": 2}, passed=False),
            mk_entry("T-C", {}, passed=(i < 3)),  # passes 3 of 5
        ]))
    agg = aggregate_runs(runs)
    t = by_id(agg)
    assert t["T-A"]["stability"] == "stable_pass"
    assert t["T-A"]["pass_rate"] == "5/5"
    assert t["T-B"]["stability"] == "stable_fail"
    assert t["T-C"]["stability"] == "flaky"
    assert t["T-C"]["pass_rate"] == "3/5"
    assert agg["stability_summary"] == {
        "stable_pass": 1, "stable_fail": 1, "flaky": 1,
    }


# --- flicker reporting --------------------------------------------------------

def test_priority_flicker_counts():
    # T-004-style: priority medium x3, high x2 across 5 runs.
    prios = ["medium", "medium", "high", "medium", "high"]
    runs = [
        mk_report([mk_entry("T-004", {"priority": p}, passed=(p == "medium"))])
        for p in prios
    ]
    agg = aggregate_runs(runs)
    t = by_id(agg)["T-004"]
    assert t["pass_rate"] == "3/5"
    assert t["flicker"]["priority"] == {"medium": 3, "high": 2}
    # non-flickering fields are absent from the flicker map
    assert "tier" not in t["flicker"]


def test_violation_flicker_inconsistent():
    # T-006-style: a banned-string violation appears in some runs only.
    msg = "contains banned string: 'delete'"
    runs = [
        mk_report([mk_entry("T-006", {}, passed=True)]),
        mk_report([mk_entry("T-006", {}, passed=False, violations=[msg])]),
        mk_report([mk_entry("T-006", {}, passed=True)]),
        mk_report([mk_entry("T-006", {}, passed=False, violations=[msg])]),
    ]
    agg = aggregate_runs(runs)
    vf = by_id(agg)["T-006"]["violation_flicker"]
    assert vf["runs_with_violations"] == 2
    assert vf["consistent"] is False
    assert vf["messages"] == {msg: 2}


def test_no_violation_flicker_when_clean():
    runs = [mk_report([mk_entry("T-1", {}, passed=True)]) for _ in range(3)]
    assert "violation_flicker" not in by_id(aggregate_runs(runs))["T-1"]


def test_consistent_miss_for_stable_fail():
    # priority is wrong the same way in every run: a consistent miss, not flicker.
    runs = [
        mk_report([mk_entry("T-008", {"priority": "low"}, passed=False)])
        for _ in range(4)
    ]
    t = by_id(aggregate_runs(runs))["T-008"]
    assert t["stability"] == "stable_fail"
    assert t["flicker"] == {}
    assert t["consistent_miss"]["priority"] == {"expected": "medium", "got": "low"}


# --- metric bands -------------------------------------------------------------

def test_metrics_band_mean_min_max():
    runs = [
        mk_report([mk_entry("T-1", {}, True)],
                  metrics={"classification_accuracy": v})
        for v in (0.90, 0.95, 1.00)
    ]
    band = aggregate_runs(runs)["metrics_band"]["classification_accuracy"]
    assert band["min"] == 0.90
    assert band["max"] == 1.00
    assert band["mean"] == pytest.approx(0.95)
    assert band["values"] == [0.90, 0.95, 1.00]


def test_metrics_band_none_safe():
    # A metric that is None in one run (e.g. empty denominator) is excluded
    # from mean/min/max but preserved in the raw values list.
    runs = [
        mk_report([mk_entry("T-1", {}, True)],
                  metrics={"escalation_target_accuracy": v})
        for v in (None, 0.5, 1.0)
    ]
    band = aggregate_runs(runs)["metrics_band"]["escalation_target_accuracy"]
    assert band["min"] == 0.5
    assert band["max"] == 1.0
    assert band["mean"] == pytest.approx(0.75)
    assert band["values"] == [None, 0.5, 1.0]


def test_tickets_passed_band():
    runs = [
        mk_report([mk_entry("T-1", {}, True), mk_entry("T-2", {}, i == 0)])
        for i in range(3)
    ]
    band = aggregate_runs(runs)["tickets_passed_band"]
    assert band["values"] == [2, 1, 1]
    assert band["min"] == 1 and band["max"] == 2


# --- safety gating ------------------------------------------------------------

def test_suite_failed_if_any_run_hard_fails():
    runs = [
        mk_report([mk_entry("T-1", {}, True)]),
        mk_report([mk_entry("T-1", {}, False)], suite_failed=True,
                  hard_fail_reasons=["T-1: auto_resolve on a security ticket"]),
        mk_report([mk_entry("T-1", {}, True)]),
    ]
    agg = aggregate_runs(runs)
    assert agg["suite_failed"] is True
    assert agg["hard_fail_reasons"] == [
        {"run": 2, "reason": "T-1: auto_resolve on a security ticket"}
    ]
    assert aggregate_ship_bars_met(agg) is False


def test_ship_bar_must_clear_every_run():
    # classification clears in 2 of 3 runs -> not met, even though mean >= bar.
    runs = [
        mk_report([mk_entry("T-1", {}, True)],
                  metrics={"classification_accuracy": v})
        for v in (0.95, 0.85, 0.95)
    ]
    agg = aggregate_runs(runs)
    st = agg["ship_bar_status"]["classification_accuracy"]
    assert st["runs_cleared"] == 2
    assert st["cleared_every_run"] is False
    assert st["mean"] == pytest.approx(0.9167, abs=1e-3)  # mean clears, gate does not
    assert aggregate_ship_bars_met(agg) is False


def test_ship_bars_met_when_all_clear():
    runs = [mk_report([mk_entry("T-1", {}, True)]) for _ in range(3)]
    agg = aggregate_runs(runs)
    assert all(s["cleared_every_run"] for s in agg["ship_bar_status"].values())
    assert aggregate_ship_bars_met(agg) is True


def test_fabrication_is_zero_tolerance_pseudo_bar():
    runs = [
        mk_report([mk_entry("T-1", {}, True)],
                  metrics={"fabrication_violation_count": c})
        for c in (0, 1, 0)
    ]
    agg = aggregate_runs(runs)
    st = agg["ship_bar_status"]["fabrication_violation_count"]
    assert st["cleared_every_run"] is False
    assert st["max"] == 1
    assert aggregate_ship_bars_met(agg) is False


# --- guards & helpers ---------------------------------------------------------

def test_empty_reports_raises():
    with pytest.raises(ValueError):
        aggregate_runs([])


def test_mismatched_ticket_sets_raise():
    runs = [
        mk_report([mk_entry("T-1", {}, True)]),
        mk_report([mk_entry("T-2", {}, True)]),
    ]
    with pytest.raises(ValueError):
        aggregate_runs(runs)


def test_single_run_aggregates_cleanly():
    agg = aggregate_runs([mk_report([mk_entry("T-1", {}, True)])])
    assert agg["runs"] == 1
    assert by_id(agg)["T-1"]["stability"] == "stable_pass"
    assert aggregate_ship_bars_met(agg) is True


def test_metrics_view_single_and_aggregate():
    single = mk_report([mk_entry("T-1", {}, True)])
    assert metrics_view(single)["classification_accuracy"] == 0.95
    agg = aggregate_runs([single, single])
    view = metrics_view(agg)
    assert view["classification_accuracy"] == pytest.approx(0.95)
    # aggregate view exposes the same metric keys as a single report
    assert set(view) == set(single["metrics"])


def test_per_run_reports_embedded():
    r1 = mk_report([mk_entry("T-1", {}, True)])
    r2 = mk_report([mk_entry("T-1", {}, False)])
    agg = aggregate_runs([r1, r2])
    assert agg["per_run"] == [r1, r2]
    assert agg["runs"] == 2
