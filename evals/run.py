"""Eval suite entrypoint: python -m evals.run (--dummy | --agent v1) [--limit N]

Loads the golden suite through the DataSource adapter, runs every ticket
through the selected triager, grades deterministically (see grader.py),
prints a scorecard with token usage and estimated cost, and writes a
timestamped JSON report to evals/reports/. --limit N runs the first N
tickets as a smoke test (scorecard only, no report file written).

--runs N runs the whole suite N times and reports per-ticket stability plus
a mean/min-max metric band; the aggregate is written to
evals/reports/aggregates/. --runs 1 (default) keeps the single-run format.

Exit codes: 0 = all ship bars met, 1 = hard fail or a ship bar missed,
2 = no triager available / missing API key.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent.data_source import LocalJSONDataSource

from .aggregate import aggregate_runs, aggregate_ship_bars_met, metrics_view
from .grader import SHIP_BARS, KBCorpus, grade_suite

ROOT = Path(__file__).resolve().parent.parent
TICKETS_PATH = ROOT / "evals" / "golden_tickets.json"
KB_DIR = ROOT / "kb"
REPORTS_DIR = ROOT / "evals" / "reports"
AGGREGATES_DIR = REPORTS_DIR / "aggregates"

# Report-only metrics also get printed; only SHIP_BARS entries gate exit code.
METRIC_LABELS = {
    "classification_accuracy": "Classification accuracy",
    "tier_accuracy": "Tier accuracy",
    "priority_exact": "Priority (exact)",
    "priority_within_one": "Priority (within one)",
    "escalation_recall_overall": "Escalation recall (overall)",
    "escalation_recall_security": "Escalation recall (security)",
    "escalation_target_accuracy": "Escalation target accuracy",
    "deflection_rate": "Deflection rate (tier-1)",
}


def build_triager(args):
    if args.dummy:
        from .dummy_triager import DummyTriager
        return DummyTriager()
    if args.agent == "v1":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print(
                "ANTHROPIC_API_KEY is not set. The v1 triager makes live API\n"
                "calls; export the key and re-run:\n"
                "  $env:ANTHROPIC_API_KEY = '<your key>'   (PowerShell)\n"
                "  export ANTHROPIC_API_KEY='<your key>'   (bash)",
                file=sys.stderr,
            )
            sys.exit(2)
        from agent.triage_v1 import TriageV1
        return TriageV1(KB_DIR, REPORTS_DIR / "parse_failures")
    print("Select a triager: --dummy (baseline) or --agent v1.", file=sys.stderr)
    sys.exit(2)


def latest_previous_report() -> dict | None:
    if not REPORTS_DIR.is_dir():
        return None
    reports = sorted(REPORTS_DIR.glob("report_*.json"))
    if not reports:
        return None
    return json.loads(reports[-1].read_text(encoding="utf-8"))


def latest_previous_aggregate() -> dict | None:
    if not AGGREGATES_DIR.is_dir():
        return None
    reports = sorted(AGGREGATES_DIR.glob("report_*.json"))
    if not reports:
        return None
    return json.loads(reports[-1].read_text(encoding="utf-8"))


def fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value * 100:.1f}%"
    return str(value)


def fmt_count(value) -> str:
    """Format a count / mean-of-counts without the percent scaling fmt() applies."""
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.1f}" if value != int(value) else str(int(value))
    return str(value)


def print_scorecard(report: dict, previous: dict | None) -> None:
    if report["suite_failed"]:
        print("=" * 64)
        print("*** SUITE FAILED - HARD FAIL (eval-spec section 4) ***")
        for reason in report["hard_fail_reasons"]:
            print(f"  - {reason}")
        print("=" * 64)
        print()

    metrics = report["metrics"]
    prev_metrics = metrics_view(previous) if previous else {}

    print(f"Golden suite: {report['tickets_passed']}/{report['tickets_total']} "
          f"tickets fully correct ({report['triager']})")
    print()
    print(f"{'Metric':<32} {'Score':>8} {'Ship bar':>10} {'Status':>8} {'Delta':>10}")
    print("-" * 72)
    for key, label in METRIC_LABELS.items():
        value = metrics[key]
        bar = SHIP_BARS.get(key)
        if bar is None:
            status = "info"
        elif value is not None and value >= bar:
            status = "PASS"
        else:
            status = "MISS"
        prev = prev_metrics.get(key)
        if value is not None and prev is not None:
            delta = f"{(value - prev) * 100:+.1f}pp"
        else:
            delta = "-"
        print(f"{label:<32} {fmt(value):>8} {fmt(bar):>10} {status:>8} {delta:>10}")

    fab = metrics["fabrication_violation_count"]
    prev_fab = prev_metrics.get("fabrication_violation_count")
    fab_delta = f"{fab - prev_fab:+d}" if prev_fab is not None else "-"
    print(f"{'Fabrication violations':<32} {fab:>8} {'0':>10} "
          f"{'PASS' if fab == 0 else 'MISS':>8} {fab_delta:>10}")

    if previous is None:
        print("\n(no previous report in evals/reports/ - deltas unavailable)")

    if report.get("usage"):
        u = report["usage"]
        t = u["totals"]
        print(
            f"\nToken usage ({report['tickets_total']} tickets): "
            f"input {t['input_tokens']:,} | output {t['output_tokens']:,} | "
            f"cache read {t['cache_read_input_tokens']:,} | "
            f"cache write {t['cache_creation_input_tokens']:,}"
        )
        print(
            f"Estimated cost: ${u['estimated_cost_usd']:.4f} total "
            f"(${u['cost_per_ticket_usd']:.4f}/ticket)"
        )

    if report["failed_tickets"]:
        print(f"\nFailed tickets ({len(report['failed_tickets'])}):")
        for line in report["failed_tickets"]:
            print(f"  {line}")
    else:
        print("\nAll tickets passed.")


def attach_usage(report: dict, triager) -> None:
    """Merge the triager's per-ticket token usage and cost estimate into
    the report. No-op for triagers that don't record usage (dummy)."""
    usage_log = getattr(triager, "usage_log", None)
    pricing = getattr(triager, "pricing", None)
    if not usage_log or not pricing:
        return
    for entry, per_ticket in zip(usage_log, report["per_ticket"]):
        per_ticket["usage"] = entry
    totals = {k: sum(e.get(k, 0) for e in usage_log) for k in pricing}
    cost = sum(totals[k] * rate / 1_000_000 for k, rate in pricing.items())
    report["usage"] = {
        "totals": totals,
        "estimated_cost_usd": round(cost, 6),
        "cost_per_ticket_usd": round(cost / len(usage_log), 6),
        "pricing_usd_per_mtok": pricing,
    }


def run_once(args, tickets, kb) -> dict:
    """One full graded pass with a FRESH triager, so each run's usage_log has
    exactly len(tickets) entries and attach_usage's zip stays aligned."""
    triager = build_triager(args)
    report = grade_suite(tickets, triager, kb)
    report["triager"] = getattr(triager, "name", type(triager).__name__)
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    attach_usage(report, triager)
    return report


def single_run_exit_code(report: dict) -> int:
    bars_missed = any(
        report["metrics"][k] is None or report["metrics"][k] < bar
        for k, bar in SHIP_BARS.items()
    ) or report["metrics"]["fabrication_violation_count"] > 0
    return 1 if (report["suite_failed"] or bars_missed) else 0


def _stability_detail(t: dict) -> str:
    """One-line 'why' for a non-passing ticket: flickering fields with value
    counts, consistent misses, and violation flicker."""
    parts = []
    for field_name, counts in t.get("flicker", {}).items():
        vals = ", ".join(f"{v} x{n}" for v, n in counts.items())
        parts.append(f"{field_name}: {vals}")
    for field_name, miss in t.get("consistent_miss", {}).items():
        parts.append(f"{field_name}: {miss['got']} (want {miss['expected']})")
    vf = t.get("violation_flicker")
    if vf:
        scope = "every run" if vf["consistent"] else f"{vf['runs_with_violations']}/{t['runs']} runs"
        msgs = "; ".join(vf["messages"].keys())
        parts.append(f"VIOLATION ({scope}): {msgs}")
    return " | ".join(parts) if parts else "(fields pass individually; see per-run report)"


def print_aggregate_scorecard(agg: dict, previous: dict | None) -> None:
    runs = agg["runs"]
    if agg["suite_failed"]:
        print("=" * 64)
        print("*** SUITE FAILED - HARD FAIL in >=1 run (eval-spec section 4) ***")
        for item in agg["hard_fail_reasons"]:
            print(f"  - [run {item['run']}] {item['reason']}")
        print("=" * 64)
        print()

    tp = agg["tickets_passed_band"]
    band_passed = f"{fmt_count(tp['min'])}-{fmt_count(tp['max'])}"
    print(f"Golden suite ({runs} runs): mean {fmt_count(tp['mean'])}/"
          f"{agg['tickets_total']} tickets fully correct "
          f"(band {band_passed}) ({agg['triager']})")
    print()

    prev_metrics = metrics_view(previous) if previous else {}
    print(f"{'Metric':<32} {'Mean':>8} {'Band':>13} {'Ship bar':>9} "
          f"{'Status':>9} {'Dmean':>9}")
    print("-" * 82)
    for key, label in METRIC_LABELS.items():
        band = agg["metrics_band"][key]
        bar = SHIP_BARS.get(key)
        if bar is None:
            status = "info"
        else:
            st = agg["ship_bar_status"][key]
            status = "PASS" if st["cleared_every_run"] else f"MISS {st['runs_cleared']}/{runs}"
        band_str = f"{fmt(band['min'])}-{fmt(band['max'])}"
        prev = prev_metrics.get(key)
        if band["mean"] is not None and prev is not None:
            delta = f"{(band['mean'] - prev) * 100:+.1f}pp"
        else:
            delta = "-"
        print(f"{label:<32} {fmt(band['mean']):>8} {band_str:>13} "
              f"{fmt(bar):>9} {status:>9} {delta:>9}")

    fab_band = agg["metrics_band"]["fabrication_violation_count"]
    fab_st = agg["ship_bar_status"]["fabrication_violation_count"]
    fab_status = "PASS" if fab_st["cleared_every_run"] else f"MISS {fab_st['runs_cleared']}/{runs}"
    fab_band_str = f"{fmt_count(fab_band['min'])}-{fmt_count(fab_band['max'])}"
    print(f"{'Fabrication violations':<32} {fmt_count(fab_band['mean']):>8} "
          f"{fab_band_str:>13} {'0':>9} {fab_status:>9} {'-':>9}")

    s = agg["stability_summary"]
    print(f"\nStability across {runs} runs: {s['stable_pass']} stable-pass, "
          f"{s['stable_fail']} stable-fail, {s['flaky']} flaky")

    unstable = [t for t in agg["ticket_stability"] if t["stability"] != "stable_pass"]
    if unstable:
        print(f"\nNon-passing tickets ({len(unstable)}):")
        for t in unstable:
            tag = "FLAKY" if t["stability"] == "flaky" else "FAIL "
            print(f"  {tag} {t['ticket_id']:<7} {t['pass_rate']:>5}  {_stability_detail(t)}")
    else:
        print("\nAll tickets stable-pass across every run.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run",
                                     description="Run the golden eval suite.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dummy", action="store_true",
                       help="use the always-escalate baseline triager")
    group.add_argument("--agent", choices=["v1"],
                       help="use a live agent triager (needs ANTHROPIC_API_KEY)")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="smoke test: run only the first N tickets "
                             "(no report file written)")
    parser.add_argument("--runs", type=int, default=1, metavar="N",
                        help="run the whole suite N times and report per-ticket "
                             "stability + a mean/min-max metric band (default 1). "
                             "--runs 1 keeps the single-run report format.")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be >= 1")

    tickets = LocalJSONDataSource(TICKETS_PATH).load_tickets()
    limited = args.limit is not None and args.limit < len(tickets)
    if limited:
        tickets = tickets[:args.limit]
    kb = KBCorpus(KB_DIR)

    if args.runs == 1:
        previous = None if limited else latest_previous_report()
        report = run_once(args, tickets, kb)
        print_scorecard(report, previous)
        if limited:
            print(f"\n(smoke test on {len(tickets)} tickets - report not written)")
        else:
            REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            out_path = REPORTS_DIR / f"report_{stamp}.json"
            out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"\nReport written to {out_path.relative_to(ROOT)}")
        return single_run_exit_code(report)

    # Multi-run: fresh triager per pass, one aggregate over all N.
    previous = None if limited else latest_previous_aggregate()
    reports = []
    for i in range(args.runs):
        print(f"--- run {i + 1}/{args.runs} ---", file=sys.stderr, flush=True)
        reports.append(run_once(args, tickets, kb))

    aggregate = aggregate_runs(reports)
    aggregate["generated_at"] = datetime.now(timezone.utc).isoformat()
    print_aggregate_scorecard(aggregate, previous)

    if limited:
        print(f"\n(smoke test: {args.runs} runs on {len(tickets)} tickets - "
              f"report not written)")
    else:
        AGGREGATES_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = AGGREGATES_DIR / f"report_{stamp}_runs{args.runs}.json"
        out_path.write_text(json.dumps(aggregate, indent=2), encoding="utf-8")
        print(f"\nAggregate report written to {out_path.relative_to(ROOT)}")

    return 0 if aggregate_ship_bars_met(aggregate) else 1


if __name__ == "__main__":
    sys.exit(main())
