"""Aggregation for multi-run eval passes (python -m evals.run --runs N).

Pure, deterministic, standard-library only: no API calls, no clock, no
filesystem. `aggregate_runs` takes the N per-run report dicts produced by
grader.grade_suite (plus the run.py-added keys) and returns a single
aggregate dict. run.py owns all I/O and the wall-clock timestamp.

Safety asymmetry baked in (eval-spec §4):
- suite_failed is True if ANY run hard-failed.
- A ship bar counts as met only if it cleared in EVERY run; the mean is
  reported alongside but never used to gate.
"""

from __future__ import annotations

from collections import Counter
from typing import Optional

# Decision fields whose per-run value is tracked for flicker reporting.
FLICKER_FIELDS = ("category", "priority", "tier", "action", "escalation_target")


def _stat(values: list[Optional[float]]) -> dict:
    """mean/min/max over the non-None values, plus the raw per-run list.

    None survives in `values` (e.g. a metric whose denominator was 0 in some
    run) but is excluded from mean/min/max. If every run is None, the three
    stats are None too.
    """
    present = [v for v in values if v is not None]
    if not present:
        return {"mean": None, "min": None, "max": None, "values": values}
    return {
        "mean": round(sum(present) / len(present), 4),
        "min": min(present),
        "max": max(present),
        "values": values,
    }


def _counter_to_ordered(counter: Counter) -> dict:
    """Counter -> plain dict ordered by count desc then key, JSON-string keys."""
    items = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return {str(k): v for k, v in items}


def classify(passed_count: int, runs: int) -> str:
    if passed_count == runs:
        return "stable_pass"
    if passed_count == 0:
        return "stable_fail"
    return "flaky"


def _ticket_stability(ticket_id: str, per_run_entries: list[dict], runs: int) -> dict:
    """One ticket's cross-run summary: pass rate, stability class, which
    decision fields flickered (with value counts), and violation flicker."""
    passed_count = sum(1 for e in per_run_entries if e["passed"])

    expected = per_run_entries[0]["expected"]
    flicker: dict[str, dict] = {}
    consistent_miss: dict[str, dict] = {}
    for field_name in FLICKER_FIELDS:
        seen = [e["got"].get(field_name) for e in per_run_entries]
        if len(set(map(_hashable, seen))) > 1:
            flicker[field_name] = _counter_to_ordered(Counter(seen))
        elif seen[0] != expected.get(field_name):
            # Value is identical across runs and wrong in all of them: this is
            # a consistent miss, the "why" behind a stable-fail ticket.
            consistent_miss[field_name] = {
                "expected": expected.get(field_name),
                "got": seen[0],
            }

    runs_with_violations = sum(1 for e in per_run_entries if e.get("violations"))
    entry = {
        "ticket_id": ticket_id,
        "passed_count": passed_count,
        "runs": runs,
        "pass_rate": f"{passed_count}/{runs}",
        "stability": classify(passed_count, runs),
        "flicker": flicker,
        "consistent_miss": consistent_miss,
    }
    if runs_with_violations:
        messages: Counter = Counter()
        for e in per_run_entries:
            for msg in e.get("violations", []):
                messages[msg] += 1
        entry["violation_flicker"] = {
            "runs_with_violations": runs_with_violations,
            "consistent": runs_with_violations == runs,
            "messages": _counter_to_ordered(messages),
        }
    return entry


def _hashable(value):
    """set() key for values that may be unhashable (lists in got); None-safe."""
    if isinstance(value, list):
        return tuple(value)
    return value


def aggregate_runs(reports: list[dict]) -> dict:
    """Combine N per-run report dicts into one aggregate report.

    Assumes every run graded the same tickets in the same order (same suite).
    Raises ValueError on an empty list or a ticket-set mismatch across runs,
    which would otherwise silently corrupt the stability table.
    """
    if not reports:
        raise ValueError("aggregate_runs requires at least one report")
    runs = len(reports)

    ticket_ids = [t["ticket_id"] for t in reports[0]["per_ticket"]]
    id_set = set(ticket_ids)
    for i, r in enumerate(reports):
        if {t["ticket_id"] for t in r["per_ticket"]} != id_set:
            raise ValueError(f"run {i} graded a different ticket set")

    # ticket_id -> list of per-run per_ticket entries, in run order
    by_ticket: dict[str, list[dict]] = {tid: [] for tid in ticket_ids}
    for r in reports:
        for entry in r["per_ticket"]:
            by_ticket[entry["ticket_id"]].append(entry)

    ticket_stability = [
        _ticket_stability(tid, by_ticket[tid], runs) for tid in ticket_ids
    ]
    summary = Counter(t["stability"] for t in ticket_stability)

    metric_keys = list(reports[0]["metrics"].keys())
    metrics_band = {
        key: _stat([r["metrics"].get(key) for r in reports]) for key in metric_keys
    }

    ship_bars = reports[0]["ship_bars"]
    ship_bar_status = {}
    for metric, bar in ship_bars.items():
        vals = [r["metrics"].get(metric) for r in reports]
        cleared = [v is not None and v >= bar for v in vals]
        stat = _stat(vals)
        ship_bar_status[metric] = {
            "bar": bar,
            "runs_cleared": sum(cleared),
            "runs_total": runs,
            "cleared_every_run": all(cleared),
            "mean": stat["mean"],
            "min": stat["min"],
            "max": stat["max"],
        }
    # Fabrication is a zero-tolerance pseudo-bar (§3 metric 6): 0 in every run.
    fab_vals = [r["metrics"].get("fabrication_violation_count", 0) for r in reports]
    ship_bar_status["fabrication_violation_count"] = {
        "bar": 0,
        "runs_cleared": sum(1 for v in fab_vals if v == 0),
        "runs_total": runs,
        "cleared_every_run": all(v == 0 for v in fab_vals),
        "mean": _stat(fab_vals)["mean"],
        "min": min(fab_vals),
        "max": max(fab_vals),
    }

    hard_fail_reasons = [
        {"run": i + 1, "reason": reason}
        for i, r in enumerate(reports)
        for reason in r["hard_fail_reasons"]
    ]

    return {
        "runs": runs,
        "triager": reports[0].get("triager"),
        "suite_failed": any(r["suite_failed"] for r in reports),
        "hard_fail_reasons": hard_fail_reasons,
        "tickets_total": reports[0]["tickets_total"],
        "tickets_passed_band": _stat([r["tickets_passed"] for r in reports]),
        "metrics_band": metrics_band,
        "ship_bars": ship_bars,
        "ship_bar_status": ship_bar_status,
        "stability_summary": {
            "stable_pass": summary.get("stable_pass", 0),
            "stable_fail": summary.get("stable_fail", 0),
            "flaky": summary.get("flaky", 0),
        },
        "ticket_stability": ticket_stability,
        "per_run": reports,
    }


def aggregate_ship_bars_met(aggregate: dict) -> bool:
    """True only if no run hard-failed and every gating bar (incl. the
    fabrication pseudo-bar) cleared in every run."""
    if aggregate["suite_failed"]:
        return False
    return all(s["cleared_every_run"] for s in aggregate["ship_bar_status"].values())


def metrics_view(report: dict) -> dict:
    """Schema-tolerant scalar metric map for delta reporting, so a scorecard
    can delta against either a single-run report ("metrics") or a prior
    aggregate ("metrics_band" -> per-metric mean)."""
    if "metrics_band" in report:
        return {k: band["mean"] for k, band in report["metrics_band"].items()}
    return report.get("metrics", {})
