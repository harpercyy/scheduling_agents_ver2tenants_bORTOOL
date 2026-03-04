---
name: scheduler
description: Generate an optimized weekly roster using CP-SAT solver. Reads tenant_config.json for shift definitions and constraints. Takes habits.json and habits_demand_shift.json as input, produces schedule.csv and schedule.json. Retries automatically with relaxed constraints if infeasible.
---

# Scheduler Skill

> **前置條件**：先讀取 `CLAUDE.md` 了解多租戶架構。

## Overview

The Scheduler uses Google OR-Tools **CP-SAT** to generate a weekly roster that:
- Reads **tenant configuration** (`tenant_config.json`) for shift definitions, roles, and constraints
- Reads **demand profiles** (`habits_demand_shift.json`) for scenario-based staffing requirements
- Satisfies **hard constraints** (one shift/day, minimum rest, hours cap, skill match)
- Optimizes **soft objectives** (demand coverage, shift preference, fairness, shift frequency)
- **Retries automatically** with relaxed constraints if infeasible

**When to use this skill:**
- To generate a new week's schedule
- After the Analyzer has produced up-to-date `habits.json`
- When manual rescheduling is needed due to sick calls / changes

---

## Inputs

| Item | Source | Description |
|------|--------|-------------|
| `habits.json` | Analyzer output | Employee habit model |
| `habits_demand_shift.json` | Demand analysis output | Per-scenario shift code headcounts |
| `week_start_date` | User specified | e.g. `2026-03-02` (Monday of target week) |
| `tenant_dir` | `tenants/<tenant>/` | Tenant folder containing config files |
| `prev_schedule.csv` | Previous run (optional) | For cross-week constraints |

### Key Tenant Config Fields Used

| Field | From | Purpose |
|-------|------|---------|
| `shift_defs` | `tenant_config.json` | Defines all shift codes (start, end, hours) |
| `workstation_roles` | `tenant_config.json` | Maps shift codes to roles |
| `min_daily_headcount` | `tenant_config.json` | Minimum staffing per day type |
| `constraints` | `tenant_config.json` | min_rest_hours, max_weekly_hours, max_working_days |
| `manager_constraints` | `tenant_config.json` | Manager coverage requirements (early/late shifts, daily counts) |
| `no_same_rest` | `tenant_config.json` | Pairs that cannot rest on same day |
| `is_manager` | `habits.json` (per employee) | Identifies which employees are managers |
| `package_dates` | `events.json` | Private event dates (affects scenario detection) |
| `designated_rest` | `rest_days.json` | Employee-designated rest days |

### Demand Profile Format (`habits_demand_shift.json`)

Scenario names must match `tenant_config.json → scenarios`:

```json
{
  "平日": {
    "烤手": { "B103": 5, "B104": 3, "B009": 3, "B010": 3 },
    "領檯": { "櫃台(早)": 1, "櫃台(晚)": 1 }
  },
  "週末": { ... },
  "平日包場": { ... },
  "週末包場": { ... }
}
```

> Role names (烤手, 領檯) must match `tenant_config.json → workstation_roles` values.

---

## Quick Start

```bash
python scripts/ortools_solver.py habits.json habits_demand_shift.json schedule_0302 2026-03-02 tenants/<tenant>
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
python scripts/analyzer.py tenants/<tenant>/ habits.json

# Verify demand profile exists
cat habits_demand_shift.json

# Verify tenant config
cat tenants/<tenant>/tenant_config.json
```

### Step 3 — Run the Scheduler

```bash
python scripts/ortools_solver.py <habits.json> <demand_shift.json> [output_prefix] [week_start] [tenant_dir] [prev_schedule]
```

**Arguments:**
| Argument | Default | Example |
|----------|---------|---------|
| `habits.json` | required | `habits.json` |
| `demand_shift.json` | required | `habits_demand_shift.json` |
| `output_prefix` | `schedule` | `schedule_0302` |
| `week_start` | next Monday | `2026-03-02` |
| `tenant_dir` | none | `tenants/glod-pig` |
| `prev_schedule` | none | `schedule_0302.csv` |

**Examples:**
```bash
# First week of a tenant
python scripts/ortools_solver.py habits.json habits_demand_shift.json schedule_0302 2026-03-02 tenants/glod-pig

# Second week with cross-week constraints
python scripts/ortools_solver.py habits.json habits_demand_shift.json schedule_0309 2026-03-09 tenants/glod-pig schedule_0302.csv
```

### Step 4 — Verify outputs

Two files are created:

**`schedule_<prefix>.csv`** — flat table, one row per shift assignment:
```
date,day_of_week,employee_id,employee_name,shift_start,shift_end,workstation,workstation_role,leave_type
```

**`schedule_<prefix>.json`** — includes `schedule` array + `stats`:
```json
{
  "schedule": [...],
  "stats": {
    "employee_weekly_hours": { "3": 40.0 },
    "daily_coverage_by_shift": { "B103": [5, 5, 5, 5, 5, 6, 6] },
    "day_scenarios": ["平日", "平日", "平日", "平日", "平日", "週末", "週末"]
  }
}
```

### Step 5 — Handle INFEASIBLE

The solver **automatically retries** up to 3 times, progressively relaxing:

| Level | SC1 Demand | SC2 Preference | SC3 Fairness | SC4 No-OT | SC5 Frequency |
|-------|-----------|----------------|--------------|-----------|---------------|
| 0 | W=100 | Active | Active | Active | Active |
| 1 | W=100 | Off | Active | Off | Off |
| 2 | target=1 | Off | Active | Off | Off |

If all three levels fail, check employee count vs demand totals in `tenant_config.json`.

### Step 6 — Pass to Auditor

```bash
python scripts/auditor_tools.py schedule_0302.csv habits.json audit_0302.json tenants/<tenant> 2026-03-02
```

---

## Soft Constraint Reference

| ID | Name | Weight | Description |
|----|------|--------|-------------|
| SC1 | Demand Coverage | W_VAC=100 | Penalizes shortage and overstaffing per shift code per day |
| SC2 | Shift Preference | W_PREF=10 | Matches `preferred_shifts` — supports shift codes and time labels |
| SC3 | Fairness | W_FAIRNESS=15 | Personalized target from `avg_shifts_per_week` |
| SC4 | No-Overtime | W_EMPLOYEE_SOFT=8 | Penalizes late shifts for `no_overtime` employees |
| SC5 | Shift Frequency | W_SHIFT=5 | Uses `shift_frequency` history |
| SC6 | No 5-day streak | W_CONSEC5=30 | Penalizes 5+ consecutive working days (including cross-week) |
| SC7 | No work-rest-work | W_ALTERNATE=15 | Penalizes isolated rest days (including cross-week) |

## Hard Constraint Reference

| Rule | Behaviour |
|------|-----------|
| One shift per employee per day | Always enforced |
| Max working days per week | From `tenant_config.json → constraints.max_working_days` |
| Min rest between shifts | From `tenant_config.json → constraints.min_rest_hours` |
| Max weekly hours | From `tenant_config.json → constraints.max_weekly_hours` |
| Skill match (workstation_skills) | Always enforced |
| Designated rest days | Always enforced (labor law) |
| Manager coverage | From `tenant_config.json → manager_constraints` + `habits.json → is_manager` |
| No-same-rest pairs | From `tenant_config.json → no_same_rest` |
| Demand minimum per shift code | Always enforced (relaxed to 1 at Level 2) |
