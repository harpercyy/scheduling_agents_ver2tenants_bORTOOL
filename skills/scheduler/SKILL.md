---
name: scheduler
description: Generate an optimized weekly roster using CP-SAT solver. Takes habits.json and habits_demand_shift.json as input, produces schedule.csv and schedule.json. Retries automatically with relaxed constraints if infeasible.
---

# Scheduler Skill

## Overview

The Scheduler uses Google OR-Tools **CP-SAT** to generate a weekly roster that:
- Reads **demand profiles** (`habits_demand_shift.json`) for 4 staffing scenarios
- Satisfies **hard constraints** (one shift/day, minimum rest, hours cap, skill match)
- Optimizes **soft objectives** (demand coverage, shift preference, fairness, shift frequency)
- **Retries automatically** with relaxed constraints if infeasible

**When to use this skill:**
- To generate a new week's schedule
- After the Analyzer has produced up-to-date `habits.json`
- When manual rescheduling is needed due to sick calls / changes

---

## Inputs

| Item | Description |
|------|-------------|
| `habits.json` | Output from the Analyzer skill |
| `habits_demand_shift.json` | Demand profile: per-scenario shift code headcounts |
| `week_start_date` | e.g. `2026-03-02` (Monday of the target week) |
| `tenant_dir` (optional) | Tenant folder containing `events.json` for package dates |

### Demand Profile Format (`habits_demand_shift.json`)

```json
{
  "平日無包場": {
    "烤手": { "B103": 5, "B104": 3, "B009": 3, "B010": 3, "B008": 3, "B003": 1 },
    "櫃台早班": { "櫃台": 1 },
    "櫃台晚班": { "櫃台": 1 }
  },
  "假日無包場": { ... },
  "平日有包場": { ... },
  "假日有包場": { ... }
}
```

Scenarios are selected per day based on weekday/weekend x package dates from `events.json`.

---

## Quick Start

```bash
python scripts/ortools_solver.py habits.json habits_demand_shift.json schedule 2026-03-02 tenants/glod-pig
```

---

## Step-by-Step Instructions

### Step 1 — Verify dependencies

```bash
pip install ortools  # Google OR-Tools (includes CP-SAT)
```

### Step 2 — Ensure inputs are ready

```bash
# Run Analyzer first if habits.json is missing or stale
python scripts/analyzer.py tenants/glod-pig/ habits.json

# Verify demand profile exists
cat habits_demand_shift.json
```

### Step 3 — Run the Scheduler

```bash
python scripts/ortools_solver.py <habits.json> <demand_shift.json> [output_prefix] [week_start] [tenant_dir]
```

**Arguments:**
| Argument | Default | Example |
|----------|---------|---------|
| `habits.json` | required | `habits.json` |
| `demand_shift.json` | required | `habits_demand_shift.json` |
| `output_prefix` | `schedule` | `schedule_0302` |
| `week_start` | next Monday | `2026-03-02` |
| `tenant_dir` | none | `tenants/glod-pig` |

**Full example:**
```bash
python scripts/ortools_solver.py habits.json habits_demand_shift.json schedule_0302 2026-03-02 tenants/glod-pig
```

### Step 4 — Verify outputs

Two files are created:

**`schedule.csv`** — flat table, one row per shift assignment:
```
date,day_of_week,employee_id,employee_name,shift_start,shift_end,workstation,workstation_role,leave_type
2026-03-02,週一,3,史曜誠 (Money),12:00,22:00,B106,烤手,
2026-03-02,週一,6,陳恩齊 (Eggsy),10:00,19:00,B003,烤手,
```

**`schedule.json`** — includes `schedule` array + `stats`:
```json
{
  "schedule": [...],
  "stats": {
    "employee_weekly_hours": {"3": 40.0, "6": 40.0},
    "daily_coverage_by_shift": {
      "B103": [5, 5, 5, 5, 5, 6, 6],
      "B104": [3, 3, 3, 3, 3, 2, 2]
    },
    "day_scenarios": ["平日無包場", "平日無包場", ...]
  }
}
```

### Step 5 — Handle INFEASIBLE

If constraints cannot be satisfied, the solver **automatically retries** up to 3 times, progressively relaxing:

| Level | SC1 Demand | SC2 Preference | SC3 Fairness | SC4 No-OT | SC5 Frequency |
|-------|-----------|----------------|--------------|-----------|---------------|
| 0 | W=100 | Active | Active | Active | Active |
| 1 | W=100 | Off | Active | Off | Off |
| 2 | target=1 | Off | Active | Off | Off |

If all three levels fail, check employee count vs demand totals.

### Step 6 — Pass to Auditor

```bash
python scripts/auditor_tools.py schedule.csv habits.json audit_report.json
```

---

## Soft Constraint Reference

| ID | Name | Weight | Description |
|----|------|--------|-------------|
| SC1 | Demand Coverage | W_VAC=100 | Penalizes both shortage and overstaffing per shift code per day. Shifts with zero demand are heavily penalized. |
| SC2 | Shift Preference | W_PREF=10 | Matches `preferred_shifts` — supports both shift codes (B106, B009) and time labels (morning, afternoon, evening). Rank 0 = no penalty. |
| SC3 | Fairness | W_FAIRNESS=15 | Personalized target from `avg_shifts_per_week` (values >7 halved as biweekly data, clamped [1,6], default 5). Penalizes both over- and under-scheduling. |
| SC4 | No-Overtime | W_EMPLOYEE_SOFT=8 | Penalizes late shifts (start >= 17:00) for `no_overtime` employees. |
| SC5 | Shift Frequency | W_SHIFT=5 | Uses `shift_frequency` history. Penalty inversely proportional to frequency — most-worked shift = 0 penalty. |

## Hard Constraint Reference

| Rule | Behaviour |
|------|-----------|
| One shift per employee per day | Always enforced |
| At most 6 days per week | Always enforced |
| >= 11 hours rest (late shift -> no early next day) | Always enforced |
| <= 46 hours/week (using SHIFT_DEFS hours) | Always enforced |
| Skill match (workstation_skills) | Always enforced |
| Demand minimum per shift code per day | Always enforced (relaxed to 1 at Level 2) |
