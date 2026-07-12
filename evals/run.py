"""Eval suite entrypoint: python -m evals.run [--dummy]

Loads evals/golden_tickets.json, runs every ticket through the selected
triager, grades deterministically (see grader.py), prints a scorecard, and
writes a timestamped JSON report to evals/reports/.

Exit codes: 0 = all ship bars met, 1 = hard fail or a ship bar missed,
2 = no triager available.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .grader import SHIP_BARS, KBCorpus, grade_suite

ROOT = Path(__file__).resolve().parent.parent
TICKETS_PATH = ROOT / "evals" / "golden_tickets.json"
KB_DIR = ROOT / "kb"
REPORTS_DIR = ROOT / "evals" / "reports"

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


def build_triager(use_dummy: bool):
    if use_dummy:
        from .dummy_triager import DummyTriager
        return DummyTriager()
    print(
        "No agent triager exists yet. Run with --dummy for the baseline, or\n"
        "wire the real agent into evals/run.py:build_triager once it lands.",
        file=sys.stderr,
    )
    sys.exit(2)


def latest_previous_report() -> dict | None:
    if not REPORTS_DIR.is_dir():
        return None
    reports = sorted(REPORTS_DIR.glob("report_*.json"))
    if not reports:
        return None
    return json.loads(reports[-1].read_text(encoding="utf-8"))


def fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value * 100:.1f}%"
    return str(value)


def print_scorecard(report: dict, previous: dict | None) -> None:
    if report["suite_failed"]:
        print("=" * 64)
        print("*** SUITE FAILED — HARD FAIL (eval-spec §4) ***")
        for reason in report["hard_fail_reasons"]:
            print(f"  - {reason}")
        print("=" * 64)
        print()

    metrics = report["metrics"]
    prev_metrics = previous["metrics"] if previous else {}

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

    if report["failed_tickets"]:
        print(f"\nFailed tickets ({len(report['failed_tickets'])}):")
        for line in report["failed_tickets"]:
            print(f"  {line}")
    else:
        print("\nAll tickets passed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run",
                                     description="Run the golden eval suite.")
    parser.add_argument("--dummy", action="store_true",
                        help="use the always-escalate baseline triager")
    args = parser.parse_args(argv)

    triager = build_triager(args.dummy)
    tickets = json.loads(TICKETS_PATH.read_text(encoding="utf-8"))["tickets"]
    kb = KBCorpus(KB_DIR)

    previous = latest_previous_report()
    report = grade_suite(tickets, triager, kb)
    report["triager"] = getattr(triager, "name", type(triager).__name__)
    report["generated_at"] = datetime.now(timezone.utc).isoformat()

    print_scorecard(report, previous)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = REPORTS_DIR / f"report_{stamp}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport written to {out_path.relative_to(ROOT)}")

    bars_missed = any(
        report["metrics"][k] is None or report["metrics"][k] < bar
        for k, bar in SHIP_BARS.items()
    ) or report["metrics"]["fabrication_violation_count"] > 0
    return 1 if (report["suite_failed"] or bars_missed) else 0


if __name__ == "__main__":
    sys.exit(main())
