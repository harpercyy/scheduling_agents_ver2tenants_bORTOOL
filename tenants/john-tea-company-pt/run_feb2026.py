#!/usr/bin/env python3
"""
Generate availability.json for each week of Feb 2026 and run the solver.
Data extracted from 2026年02月南港門市員工班表.pdf
"""
import json
import os
import sys
import shutil

TENANT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(os.path.dirname(TENANT_DIR), "..", "scripts")
OUTPUT_DIR = os.path.join(TENANT_DIR, "output")

ALL_EMP_IDS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]

# ─── PT Availability Data (extracted from PDF) ───────────────────────────────
# Format: { employee_id: { "YYYY-MM-DD": { "start": "HH:MM", "end": "HH:MM" } } }
# X (not available) → omitted from dict
# All → 09:00-21:30
# {time}- → from time to 21:30
# -{time} or {time} → from 09:00 to time

def all_day():
    return {"start": "09:00", "end": "21:30"}

def from_time(t):
    return {"start": t, "end": "21:30"}

def until_time(t):
    return {"start": "09:00", "end": t}

# ─── Week 1: Feb 2-8 ─────────────────────────────────────────────────────────
# Events: 十六拜拜(Tue), 動漫展(Thu-Sun)
WEEK1_CLOSURES = []  # No closures
WEEK1_PT = {
    # 張雅淳: X | 18.5- | 18.5- | X | 18.5- | All | X
    "4": {
        "2026-02-03": from_time("18:30"),
        "2026-02-04": from_time("18:30"),
        "2026-02-06": from_time("18:30"),
        "2026-02-07": all_day(),
    },
    # 崔廣浩: 15- | X | X | 15- | X | X | -15
    "5": {
        "2026-02-02": from_time("15:00"),
        "2026-02-05": from_time("15:00"),
        "2026-02-08": until_time("15:00"),
    },
    # 郭俊毅: X | 15- | 15- | X | X | X | all-day(inferred)
    "6": {
        "2026-02-03": from_time("15:00"),
        "2026-02-04": from_time("15:00"),
        "2026-02-08": all_day(),
    },
    # 林佩樺: 17 | 17 | 17 | 17 | 17 | X | 17
    "7": {
        "2026-02-02": until_time("17:00"),
        "2026-02-03": until_time("17:00"),
        "2026-02-04": until_time("17:00"),
        "2026-02-05": until_time("17:00"),
        "2026-02-06": until_time("17:00"),
        "2026-02-08": until_time("17:00"),
    },
    # 肖沈: X | 17 | X | 17 | X | 17 | X
    "8": {
        "2026-02-03": until_time("17:00"),
        "2026-02-05": until_time("17:00"),
        "2026-02-07": until_time("17:00"),
    },
    # 譚曉琳: All | All | X | X | X | X | X
    "9": {
        "2026-02-02": all_day(),
        "2026-02-03": all_day(),
    },
    # 陳宥樺: shift(11-15) | shift(11-15) | shift(11-16) | shift(16-21:30) | X | X | shift(16-21:30)
    "10": {
        "2026-02-02": all_day(),
        "2026-02-03": all_day(),
        "2026-02-04": all_day(),
        "2026-02-05": all_day(),
        "2026-02-08": all_day(),
    },
}

# ─── Week 2: Feb 9-15 ────────────────────────────────────────────────────────
# Events: 動漫展(Mon), 洗水塔公休(Tue/10), 大掃除(Sat), 公休/小年夜(Sun/15)
WEEK2_CLOSURES = ["2026-02-10", "2026-02-15"]
WEEK2_PT = {
    # 張雅淳: X | (closed) | 18.5- | 18.5- | 18.5- | X | (closed)
    "4": {
        "2026-02-11": from_time("18:30"),
        "2026-02-12": from_time("18:30"),
        "2026-02-13": from_time("18:30"),
    },
    # 崔廣浩: X | (closed) | X | 15- | X | -15 | (closed)
    "5": {
        "2026-02-12": from_time("15:00"),
        "2026-02-14": until_time("15:00"),
    },
    # 郭俊毅: X | (closed) | 14- | 15- | X | 早(09-15) | (closed)
    "6": {
        "2026-02-11": from_time("14:00"),
        "2026-02-12": from_time("15:00"),
        "2026-02-14": until_time("15:00"),
    },
    # 林佩樺: 17 | (closed) | 17 | 17 | 17 | 17 | (closed)
    "7": {
        "2026-02-09": until_time("17:00"),
        "2026-02-11": until_time("17:00"),
        "2026-02-12": until_time("17:00"),
        "2026-02-13": until_time("17:00"),
        "2026-02-14": until_time("17:00"),
    },
    # 肖沈: X | (closed) | X | 17 | X | 17 | (closed)
    "8": {
        "2026-02-12": until_time("17:00"),
        "2026-02-14": until_time("17:00"),
    },
    # 譚曉琳: X | (closed) | All | All | X | All | (closed)
    "9": {
        "2026-02-11": all_day(),
        "2026-02-12": all_day(),
        "2026-02-14": all_day(),
    },
    # 陳宥樺: shift(10-15) | (closed) | shift(11-16) | X | shift(10-16) | shift(16-21:30) | (closed)
    "10": {
        "2026-02-09": all_day(),
        "2026-02-11": all_day(),
        "2026-02-13": all_day(),
        "2026-02-14": all_day(),
    },
}

# ─── Week 3: Feb 16-22 (CNY) ─────────────────────────────────────────────────
# 公休: Mon 16(除夕)-Thu 19(初三). Fri 20(初四): 營業11-19:30. Sat-Sun normal.
WEEK3_CLOSURES = ["2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19"]
WEEK3_PT = {
    # 張雅淳: (closed x4) | All | -18 | X
    "4": {
        "2026-02-20": {"start": "11:00", "end": "19:30"},  # reduced hours
        "2026-02-21": until_time("18:00"),
    },
    # 崔廣浩: (closed x4) | X | -15 | X
    "5": {
        "2026-02-21": until_time("15:00"),
    },
    # 郭俊毅: (closed x4) | all | all | 早(09-16)
    "6": {
        "2026-02-20": {"start": "11:00", "end": "19:30"},
        "2026-02-21": all_day(),
        "2026-02-22": until_time("16:00"),
    },
    # 林佩樺: (closed x4) | X | 17 | 17
    "7": {
        "2026-02-21": until_time("17:00"),
        "2026-02-22": until_time("17:00"),
    },
    # 肖沈: (closed x4) | X | 17 | X
    "8": {
        "2026-02-21": until_time("17:00"),
    },
    # 譚曉琳: (closed x4) | X | All | All
    "9": {
        "2026-02-21": all_day(),
        "2026-02-22": all_day(),
    },
    # 陳宥樺: (closed x4) | X | All | all-day(補)
    "10": {
        "2026-02-21": all_day(),
        "2026-02-22": all_day(),
    },
}

# ─── Week 4: Feb 23 - Mar 1 ──────────────────────────────────────────────────
# Events: 開工拜拜(Wed/25), 2/28放假(Sat/28, TW holiday)
WEEK4_CLOSURES = []  # 228 is a holiday (handled by region_holidays), not a full closure
WEEK4_PT = {
    # 張雅淳: 18.5- | 18.5- | 18.5- | 19- | X | All | X
    "4": {
        "2026-02-23": from_time("18:30"),
        "2026-02-24": from_time("18:30"),
        "2026-02-25": from_time("18:30"),
        "2026-02-26": from_time("19:00"),
        "2026-02-28": all_day(),
    },
    # 崔廣浩: X | 15- | X | X | -15 | X | X
    "5": {
        "2026-02-24": from_time("15:00"),
        "2026-02-27": until_time("15:00"),
    },
    # 郭俊毅: X | X | X | X | X | X | X (all off this week)
    "6": {},
    # 林佩樺: 17 | X | 17 | 17 | 17 | X | X
    "7": {
        "2026-02-23": until_time("17:00"),
        "2026-02-25": until_time("17:00"),
        "2026-02-26": until_time("17:00"),
        "2026-02-27": until_time("17:00"),
    },
    # 肖沈: X | 17 | X | 17 | X | 17 | X
    "8": {
        "2026-02-24": until_time("17:00"),
        "2026-02-26": until_time("17:00"),
        "2026-02-28": until_time("17:00"),
    },
    # 譚曉琳: X | 15- | X | X | All | All | X
    "9": {
        "2026-02-24": from_time("15:00"),
        "2026-02-27": all_day(),
        "2026-02-28": all_day(),
    },
    # 陳宥樺: X | 15:30- | X | 15:30- | X | X | X
    "10": {
        "2026-02-24": from_time("15:30"),
        "2026-02-26": from_time("15:30"),
    },
}


def build_availability(week_start: str, closures: list, pt_avail: dict) -> dict:
    """Build availability.json content for one week."""
    designated_rest = {}

    # Closure days: all employees rest
    for date_str in closures:
        for eid in ALL_EMP_IDS:
            if eid not in designated_rest:
                designated_rest[eid] = []
            designated_rest[eid].append(date_str)

    return {
        "week": week_start,
        "designated_rest": designated_rest,
        "pt_availability": pt_avail,
    }


def run_week(week_start: str, closures: list, pt_avail: dict, label: str):
    """Build availability.json and run the solver for one week."""
    import subprocess

    print(f"\n{'='*70}")
    print(f"  {label}: {week_start}")
    print(f"{'='*70}")

    # Write availability.json
    avail = build_availability(week_start, closures, pt_avail)
    avail_path = os.path.join(TENANT_DIR, "availability.json")
    with open(avail_path, "w", encoding="utf-8") as f:
        json.dump(avail, f, ensure_ascii=False, indent=2)

    # Run solver
    tag = week_start.replace("-", "")
    output_prefix = os.path.join(OUTPUT_DIR, f"schedule_{tag}")
    habits_path = os.path.join(OUTPUT_DIR, "habits.json")
    demand_path = os.path.join(OUTPUT_DIR, "habits_demand_shift.json")

    cmd = [
        sys.executable, os.path.join(SCRIPTS_DIR, "ortools_solver.py"),
        habits_path, demand_path, output_prefix, week_start, TENANT_DIR,
    ]
    result = subprocess.run(cmd, capture_output=False)

    if result.returncode != 0:
        print(f"  ⚠️ Solver returned non-zero for {week_start}")

    # Run auditor
    schedule_csv = f"{output_prefix}.csv"
    if os.path.exists(schedule_csv):
        audit_path = os.path.join(OUTPUT_DIR, f"audit_{tag}.json")
        cmd_audit = [
            sys.executable, os.path.join(SCRIPTS_DIR, "auditor_tools.py"),
            schedule_csv, habits_path, audit_path, TENANT_DIR, week_start,
        ]
        subprocess.run(cmd_audit, capture_output=False)
    else:
        print(f"  ❌ No schedule CSV produced for {week_start}")


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    weeks = [
        ("2026-02-02", WEEK1_CLOSURES, WEEK1_PT, "第一週 (2/2-2/8)"),
        ("2026-02-09", WEEK2_CLOSURES, WEEK2_PT, "第二週 (2/9-2/15)"),
        ("2026-02-16", WEEK3_CLOSURES, WEEK3_PT, "第三週 (2/16-2/22 春節)"),
        ("2026-02-23", WEEK4_CLOSURES, WEEK4_PT, "第四週 (2/23-3/1)"),
    ]

    for week_start, closures, pt_avail, label in weeks:
        run_week(week_start, closures, pt_avail, label)

    print(f"\n{'='*70}")
    print(f"  ✅ 2026年2月排班完成！")
    print(f"  📁 所有產出檔位於: {OUTPUT_DIR}")
    print(f"{'='*70}")
