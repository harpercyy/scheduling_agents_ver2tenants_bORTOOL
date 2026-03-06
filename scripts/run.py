#!/usr/bin/env python3
"""
run.py — Unified CLI for the Schedule Agents pipeline.

Runs the full pipeline: Analyzer → Demand Analysis → Scheduler → Auditor
for one or all tenants. All output files are written to tenants/<tenant>/output/.

Usage:
  python scripts/run.py --tenant glod-pig --week 2026-03-02
  python scripts/run.py --tenant glod-pig --week 2026-03-09 --prev-schedule schedule_0302.csv
  python scripts/run.py --all-tenants --week 2026-03-02
  python scripts/run.py --tenant glod-pig --week 2026-03-02 --step auditor
  python scripts/run.py --tenant glod-pig --week 2026-03-02 --sweep tenants/glod-pig/weight_sweep.json
"""

import argparse
import json
import os
import sys
import glob
import multiprocessing
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def get_next_monday() -> str:
    today = datetime.today()
    days_ahead = (7 - today.weekday()) % 7 or 7
    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


def discover_tenants(tenants_root: str = "tenants") -> list:
    """Return list of tenant directory paths that have a tenant_config.json."""
    tenants = []
    if not os.path.isdir(tenants_root):
        return tenants
    for name in sorted(os.listdir(tenants_root)):
        tenant_dir = os.path.join(tenants_root, name)
        config_path = os.path.join(tenant_dir, "tenant_config.json")
        if os.path.isdir(tenant_dir) and os.path.exists(config_path):
            if name != "TEMPLATE":
                tenants.append(tenant_dir)
    return tenants


def ensure_output_dir(tenant_dir: str) -> str:
    """Create and return tenants/<tenant>/output/ directory."""
    output_dir = os.path.join(tenant_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def run_pipeline(tenant_dir: str, week_start: str,
                 prev_schedule: str = None, step: str = "all"):
    """Run the full or partial pipeline for a single tenant.
    All output files are written to tenants/<tenant>/output/.
    """
    from data_loader import load_tenant_config

    config = load_tenant_config(tenant_dir)
    output_dir = ensure_output_dir(tenant_dir)
    week_tag = week_start.replace("-", "")  # e.g. "20260302"
    now_tag = datetime.now().strftime("%Y%m%d_%H%M%S")  # e.g. "20260225_191000"
    file_tag = f"{week_tag}_{now_tag}"  # e.g. "20260302_20260225_191000"

    # Output file paths — all under tenants/<tenant>/output/
    habits_path = os.path.join(output_dir, "habits.json")
    demand_path = os.path.join(output_dir, "habits_demand_shift.json")
    schedule_prefix = os.path.join(output_dir, f"schedule_{file_tag}")
    audit_path = os.path.join(output_dir, f"audit_{file_tag}.json")

    print(f"📂 輸出目錄: {output_dir}")

    steps_to_run = []
    if step == "all":
        steps_to_run = ["analyzer", "demand", "scheduler", "auditor"]
    elif step in ("analyzer", "demand", "scheduler", "auditor"):
        steps_to_run = [step]
    else:
        print(f"❌ Unknown step: {step}. Use: analyzer, demand, scheduler, auditor, all")
        return False

    success = True

    # Step 1: Analyzer
    if "analyzer" in steps_to_run:
        print(f"\n{'='*60}")
        print(f"  Step 1/4: Analyzer — {config.display_name}")
        print(f"{'='*60}")
        from analyzer import run_analyzer
        csv_paths = sorted(glob.glob(os.path.join(tenant_dir, "*.csv")))
        if not csv_paths:
            print(f"⚠️  No CSV files found in {tenant_dir}")
        else:
            run_analyzer(csv_paths, habits_path)

    # Step 2: Demand Analysis
    if "demand" in steps_to_run:
        print(f"\n{'='*60}")
        print(f"  Step 2/4: Demand Analysis — {config.display_name}")
        print(f"{'='*60}")
        from demand_shift_analysis import run
        run(tenant_dir, demand_path)

    # Step 3: Scheduler
    if "scheduler" in steps_to_run:
        print(f"\n{'='*60}")
        print(f"  Step 3/4: Scheduler — {config.display_name}")
        print(f"{'='*60}")
        if not os.path.exists(habits_path):
            print(f"⚠️  {habits_path} not found — run analyzer first")
            success = False
        elif not os.path.exists(demand_path):
            print(f"⚠️  {demand_path} not found — run demand analysis first")
            success = False
        else:
            from ortools_solver import run_scheduler
            run_scheduler(
                habits_path=habits_path,
                demand_path=demand_path,
                output_prefix=schedule_prefix,
                week_start=week_start,
                tenant_dir=tenant_dir,
                prev_schedule_path=prev_schedule,
            )

    # Step 4: Auditor
    if "auditor" in steps_to_run:
        print(f"\n{'='*60}")
        print(f"  Step 4/4: Auditor — {config.display_name}")
        print(f"{'='*60}")
        schedule_csv = f"{schedule_prefix}.csv"
        if not os.path.exists(schedule_csv):
            print(f"⚠️  {schedule_csv} not found — run scheduler first")
            success = False
        else:
            from auditor_tools import run_auditor
            run_auditor(
                schedule_path=schedule_csv,
                habits_path=habits_path,
                output_path=audit_path,
                tenant_dir=tenant_dir,
                week_start=week_start,
                prev_schedule_path=prev_schedule,
            )

    # Summary
    if success:
        print(f"\n📁 所有產出檔位於: {output_dir}/")

    return success


def _sweep_worker(args):
    """Worker function for parallel sweep — runs Scheduler + Auditor for one config.
    Must be a top-level function for multiprocessing pickling.
    """
    (label, weights, habits_path, demand_path, schedule_prefix,
     audit_path, week_start, tenant_dir, prev_schedule) = args

    # Each worker re-imports to avoid shared state issues
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ortools_solver import run_scheduler
    from auditor_tools import run_auditor

    try:
        run_scheduler(
            habits_path=habits_path,
            demand_path=demand_path,
            output_prefix=schedule_prefix,
            week_start=week_start,
            tenant_dir=tenant_dir,
            prev_schedule_path=prev_schedule,
            weights_override=weights,
        )

        schedule_csv = f"{schedule_prefix}.csv"
        if not os.path.exists(schedule_csv):
            return {"label": label, "weights": weights,
                    "P0": "ERR", "Hard": "ERR", "P1": "ERR", "P2": "ERR"}

        report = run_auditor(
            schedule_path=schedule_csv,
            habits_path=habits_path,
            output_path=audit_path,
            tenant_dir=tenant_dir,
            week_start=week_start,
            prev_schedule_path=prev_schedule,
        )

        summary = report.get("summary", {})
        return {
            "label": label,
            "weights": weights,
            "P0": summary.get("P0", 0),
            "Hard": summary.get("Hard", 0),
            "P1": summary.get("P1", 0),
            "P2": summary.get("P2", 0),
            "audit_path": audit_path,
            "schedule_path": f"{schedule_prefix}.csv",
        }
    except Exception as e:
        return {"label": label, "weights": weights,
                "P0": "ERR", "Hard": "ERR", "P1": "ERR", "P2": "ERR",
                "error": str(e)}


def run_sweep(tenant_dir: str, week_start: str, sweep_path: str,
              prev_schedule: str = None, parallel: int = 0):
    """Run multiple weight configurations, compare Scheduler + Auditor results.
    Args:
        parallel: Number of parallel workers. 0 = sequential, -1 = cpu_count.
    """
    from data_loader import load_tenant_config

    config = load_tenant_config(tenant_dir)
    output_dir = ensure_output_dir(tenant_dir)

    habits_path = os.path.join(output_dir, "habits.json")
    demand_path = os.path.join(output_dir, "habits_demand_shift.json")

    if not os.path.exists(habits_path):
        print(f"❌ {habits_path} not found — run analyzer first")
        return False
    if not os.path.exists(demand_path):
        print(f"❌ {demand_path} not found — run demand analysis first")
        return False

    with open(sweep_path, encoding="utf-8") as f:
        sweep_configs = json.load(f)

    if not isinstance(sweep_configs, list) or not sweep_configs:
        print("❌ sweep JSON must be a non-empty array of {label, weights} objects")
        return False

    week_tag = week_start.replace("-", "")
    now_tag = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Build task args for each config
    task_args = []
    for idx, cfg in enumerate(sweep_configs):
        label = cfg.get("label", f"config_{idx}")
        weights = cfg.get("weights", {})
        file_tag = f"{week_tag}_{now_tag}_{label}"
        schedule_prefix = os.path.join(output_dir, f"schedule_{file_tag}")
        audit_path = os.path.join(output_dir, f"audit_{file_tag}.json")
        task_args.append((label, weights, habits_path, demand_path,
                          schedule_prefix, audit_path, week_start,
                          tenant_dir, prev_schedule))

    n_workers = parallel if parallel > 0 else (multiprocessing.cpu_count() if parallel == -1 else 0)
    mode = f"parallel ({n_workers} workers)" if n_workers > 0 else "sequential"

    print(f"\n{'='*60}")
    print(f"  Weight Sweep — {config.display_name}")
    print(f"  {len(sweep_configs)} configuration(s) from {sweep_path}")
    print(f"  mode: {mode}")
    print(f"{'='*60}")

    if n_workers > 0:
        with multiprocessing.Pool(processes=n_workers) as pool:
            results = pool.map(_sweep_worker, task_args)
    else:
        results = []
        for idx, args in enumerate(task_args):
            label = args[0]
            weights = args[1]
            print(f"\n{'─'*60}")
            print(f"  [{idx+1}/{len(task_args)}] {label}")
            if weights:
                print(f"  weights: {weights}")
            else:
                print(f"  weights: (defaults)")
            print(f"{'─'*60}")
            results.append(_sweep_worker(args))

    # Print comparison table
    _print_sweep_table(results)

    # Generate structured report
    report_path = _generate_sweep_report(results, output_dir, sweep_path)
    print(f"  Sweep report saved to {report_path}\n")

    return True


def _load_rule_breakdown(audit_path: str) -> dict:
    """Load audit JSON and return {rule_id: count} breakdown."""
    from collections import Counter
    if not audit_path or not os.path.exists(audit_path):
        return {}
    try:
        with open(audit_path, encoding="utf-8") as f:
            audit = json.load(f)
        return dict(Counter(v["rule_id"] for v in audit.get("violations", [])))
    except Exception:
        return {}


def _generate_sweep_report(results: list, output_dir: str, sweep_path: str) -> str:
    """Generate a structured sweep comparison report JSON."""
    now_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(output_dir, f"sweep_report_{now_tag}.json")

    configs = []
    for r in results:
        by_rule = _load_rule_breakdown(r.get("audit_path"))
        configs.append({
            "label": r["label"],
            "weights": r["weights"],
            "summary": {k: r[k] for k in ("P0", "Hard", "P1", "P2")},
            "by_rule": by_rule,
            "audit_path": r.get("audit_path", ""),
            "schedule_path": r.get("schedule_path", ""),
        })

    # Build comparison diffs (first config = baseline)
    comparison = {}
    if len(configs) >= 2:
        baseline = configs[0]
        bl_summary = baseline["summary"]
        bl_rules = baseline["by_rule"]
        diffs = []
        for cfg in configs[1:]:
            c_summary = cfg["summary"]
            c_rules = cfg["by_rule"]

            # Delta calculation (skip if ERR)
            delta = {}
            for k in ("P0", "Hard", "P1", "P2"):
                bv, cv = bl_summary.get(k, 0), c_summary.get(k, 0)
                if isinstance(bv, int) and isinstance(cv, int):
                    delta[k] = cv - bv
                else:
                    delta[k] = "ERR"

            all_rules = set(bl_rules) | set(c_rules)
            improved = [r for r in sorted(all_rules) if c_rules.get(r, 0) < bl_rules.get(r, 0)]
            regressed = [r for r in sorted(all_rules) if c_rules.get(r, 0) > bl_rules.get(r, 0)]
            resolved = [r for r in sorted(all_rules) if bl_rules.get(r, 0) > 0 and c_rules.get(r, 0) == 0]
            new_rules = [r for r in sorted(all_rules) if bl_rules.get(r, 0) == 0 and c_rules.get(r, 0) > 0]

            diffs.append({
                "label": cfg["label"],
                "delta": delta,
                "improved_rules": improved,
                "regressed_rules": regressed,
                "resolved_rules": resolved,
                "new_rules": new_rules,
            })
        comparison = {
            "baseline_label": baseline["label"],
            "diffs": diffs,
        }

    report = {
        "generated_at": datetime.now().isoformat(),
        "sweep_config": sweep_path,
        "configs": configs,
        "comparison": comparison,
    }

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return report_path


def _print_sweep_table(results: list):
    """Print a formatted comparison table of sweep results."""
    if not results:
        return

    # Column widths
    max_label = max(len(r["label"]) for r in results)
    lw = max(max_label, 5)  # min width for "Label"

    def fmt(val):
        return str(val).rjust(4)

    header = (f"{'Label'.ljust(lw)}  {'P0':>4}  {'Hard':>4}  "
              f"{'P1':>4}  {'P2':>4}  {'Total':>5}")
    sep = "─" * len(header)

    print(f"\n{'='*60}")
    print("  Weight Sweep — Comparison Table")
    print(f"{'='*60}\n")
    print(f"  {header}")
    print(f"  {sep}")

    for r in results:
        p0 = r["P0"]
        hard = r["Hard"]
        p1 = r["P1"]
        p2 = r["P2"]
        if isinstance(p0, int) and isinstance(p1, int) and isinstance(p2, int):
            hard_v = hard if isinstance(hard, int) else 0
            total = p0 + hard_v + p1 + p2
        else:
            total = "ERR"
        print(f"  {r['label'].ljust(lw)}  {fmt(p0)}  {fmt(hard)}  "
              f"{fmt(p1)}  {fmt(p2)}  {str(total).rjust(5)}")

    print(f"  {sep}")

    # Rule breakdown table
    all_breakdowns = {}
    for r in results:
        bd = _load_rule_breakdown(r.get("audit_path"))
        all_breakdowns[r["label"]] = bd

    all_rule_ids = sorted(set(rid for bd in all_breakdowns.values() for rid in bd))
    if all_rule_ids:
        rw = max(len(rid) for rid in all_rule_ids)
        rw = max(rw, 7)  # min width for "Rule ID"

        rule_header = f"{'Rule ID'.ljust(rw)}"
        for r in results:
            rule_header += f"  {r['label']:>{lw}}"
        rule_sep = "\u2500" * len(rule_header)

        print(f"\n  Rule Breakdown:")
        print(f"  {rule_header}")
        print(f"  {rule_sep}")
        for rid in all_rule_ids:
            row = f"  {rid.ljust(rw)}"
            for r in results:
                cnt = all_breakdowns[r["label"]].get(rid, 0)
                row += f"  {cnt:>{lw}}"
            print(row)
        print(f"  {rule_sep}")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Schedule Agents — Unified Pipeline Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/run.py --tenant glod-pig --week 2026-03-02
  python scripts/run.py --tenant glod-pig --week 2026-03-09 --prev-schedule tenants/glod-pig/output/schedule_20260302.csv
  python scripts/run.py --all-tenants --week 2026-03-02
  python scripts/run.py --tenant glod-pig --week 2026-03-02 --step auditor
  python scripts/run.py --tenant glod-pig --week 2026-03-02 --sweep tenants/glod-pig/weight_sweep.json

Output files are written to tenants/<tenant>/output/:
  habits.json, habits_demand_shift.json,
  schedule_<YYYYMMDD>_<TIMESTAMP>.csv/.json, audit_<YYYYMMDD>_<TIMESTAMP>.json
        """,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tenant", help="Tenant ID (e.g. glod-pig)")
    group.add_argument("--all-tenants", action="store_true",
                       help="Run pipeline for all configured tenants")
    parser.add_argument("--week", default=None,
                        help="Week start date (YYYY-MM-DD, default: next Monday)")
    parser.add_argument("--prev-schedule", default=None,
                        help="Previous week schedule CSV (for cross-week constraints)")
    parser.add_argument("--step", default="all",
                        choices=["all", "analyzer", "demand", "scheduler", "auditor"],
                        help="Run only a specific pipeline step (default: all)")
    parser.add_argument("--sweep", default=None, metavar="JSON_FILE",
                        help="Run weight sweep: test multiple weight configs "
                             "(Scheduler+Auditor) and compare results")
    parser.add_argument("--parallel", type=int, default=0, metavar="N",
                        help="Parallel workers for --sweep (0=sequential, "
                             "-1=all CPUs, N=specific count)")

    args = parser.parse_args()

    week_start = args.week or get_next_monday()

    if args.sweep:
        if not args.tenant:
            print("❌ --sweep requires --tenant (not --all-tenants)")
            sys.exit(1)
        tenant_dir = f"tenants/{args.tenant}"
        if not os.path.isdir(tenant_dir):
            print(f"❌ Tenant directory not found: {tenant_dir}")
            sys.exit(1)
        if not os.path.exists(args.sweep):
            print(f"❌ Sweep config not found: {args.sweep}")
            sys.exit(1)
        run_sweep(tenant_dir, week_start, args.sweep, args.prev_schedule,
                  parallel=args.parallel)
    elif args.all_tenants:
        tenants = discover_tenants()
        if not tenants:
            print("❌ No tenant directories found in tenants/")
            sys.exit(1)
        print(f"🏪 Found {len(tenants)} tenant(s): {[os.path.basename(t) for t in tenants]}")
        for tenant_dir in tenants:
            run_pipeline(tenant_dir, week_start, args.prev_schedule, args.step)
    else:
        tenant_dir = f"tenants/{args.tenant}"
        if not os.path.isdir(tenant_dir):
            print(f"❌ Tenant directory not found: {tenant_dir}")
            sys.exit(1)
        run_pipeline(tenant_dir, week_start, args.prev_schedule, args.step)


if __name__ == "__main__":
    main()
