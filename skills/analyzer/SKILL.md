---
name: analyzer
description: Analyze historical roster CSVs to build a Habit Model (habits.json) for each employee. Covers identity resolution and shift pattern extraction.
---

# Analyzer Skill

## Overview

The Analyzer reads historical weekly roster CSVs (e.g. from `tenants/glod-pig/`) and produces a **Habit Model** (`habits.json`) that records each employee's shift preferences, workstation skills, and overtime disposition.

**When to use this skill:**
- Before generating a new schedule (to feed current data into the Scheduler)
- After adding new historical rosters
- When employee habits need to be refreshed

---

## Inputs

| Item | Description |
|------|-------------|
| CSV files | Weekly roster files from `tenants/<tenant>/` folder |
| Format | Gold Pig-style Excel export (multi-row per employee, Traditional Chinese) |

---

## Quick Start

```bash
# Analyze a single week
python scripts/analyzer.py "tenants/glod-pig/row 1月週班表(外場) _0105-0111.csv"

# Analyze all weeks in a tenant folder (recommended)
python scripts/analyzer.py tenants/glod-pig/ habits.json
```

---

## Step-by-Step Instructions

### Step 1 — Verify dependencies

```bash
pip install pandas  # if not already installed
```

### Step 2 — Run the Analyzer

```bash
python scripts/analyzer.py <csv_path_or_folder> [output_habits.json]
```

**Arguments:**
- `<csv_path_or_folder>` — path to a single CSV **or** a folder of CSVs
- `[output_habits.json]` — output path (default: `habits.json`)

**Example — analyze all Gold Pig week files:**
```bash
python scripts/analyzer.py tenants/glod-pig/ habits.json
```

### Step 3 — Verify output

The Analyzer produces two files:

**`habits.json`** — one entry per employee:
```json
{
  "employee_id": "3",
  "chinese_name": "史曜誠",
  "english_name": "Money",
  "preferred_shifts": ["B106", "B009", "B010", "B011"],
  "available_hour_range": "6-24",
  "overtime_willingness": "fixed_overtime",
  "rotation_flexibility": "full",
  "workstation_skills": ["主管", "烤手", "櫃台"],
  "avg_weekly_hours": 24.5,
  "avg_shifts_per_week": 9.8,
  "shift_frequency": {"B010": 3, "B106": 8, "B009": 5, "B011": 1},
  "workstation_frequency": {"B010": 6, "B009": 4}
}
```

> **Note:** `preferred_shifts` and `shift_frequency` may contain either shift codes (B106, B009, B003) or time labels (morning, afternoon, evening), depending on how the historical data was recorded. The Scheduler and Auditor handle both formats automatically.

**`habits_coverage.json`** — per-shift staffing averages:
```json
{
  "morning":   { "avg_headcount": 8.2, "top_workstations": {"B103": 5} },
  "afternoon": { "avg_headcount": 10.1, "top_workstations": {"B104": 7} },
  "evening":   { "avg_headcount": 9.6, "top_workstations": {"B009": 6} }
}
```

### Step 4 — Pass to Scheduler

```bash
# Feed habits.json into the Scheduler skill
python scripts/ortools_solver.py habits.json habits_demand_shift.json schedule 2026-03-02 tenants/glod-pig
```

---

## Output Schema

```
habits.json
└── employee_id          string   — e.g. "3"
└── chinese_name         string   — e.g. "史曜誠"
└── english_name         string   — e.g. "Money"
└── preferred_shifts     list     — shift codes ["B106","B009"] or time labels ["morning","afternoon"]
└── workstation_skills   list     — e.g. ["主管", "烤手", "櫃台"]
└── overtime_willingness string   — "fixed_overtime"|"no_overtime"|"flexible"
└── rotation_flexibility string   — "full"|"morning_afternoon"|"none"
└── avg_weekly_hours     float
└── avg_shifts_per_week  float    — used by Scheduler SC3 for personalized fairness target
└── shift_frequency      dict     — shift codes {"B106":8,"B009":5} or time labels {"evening":12}
└── workstation_frequency dict
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Could not find date header row` | Check the CSV is unmodified; avoid resaving with different encoding |
| `0 employees found` | Confirm CSV is from the correct tenant format |
| Incorrect shift counts | Check the CSV week range covers full 7 days |
