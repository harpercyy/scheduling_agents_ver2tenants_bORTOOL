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
"""

import argparse
import os
import sys
import glob
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

    args = parser.parse_args()

    week_start = args.week or get_next_monday()

    if args.all_tenants:
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
