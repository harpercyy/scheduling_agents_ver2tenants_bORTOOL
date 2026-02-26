---
name: analyzer
description: Analyze historical roster CSVs to build a Habit Model (habits.json) for each employee. Covers identity resolution and shift pattern extraction. Supports multiple tenants via tenant_config.json.
---

# Analyzer Skill

> **前置條件**：先讀取 `CLAUDE.md` 了解多租戶架構。

## Overview

The Analyzer reads historical weekly roster CSVs from `tenants/<tenant>/` and produces a **Habit Model** (`habits.json`) that records each employee's shift preferences, workstation skills, and overtime disposition.

**When to use this skill:**
- Before generating a new schedule (to feed current data into the Scheduler)
- After adding new historical rosters
- When employee habits need to be refreshed

---

## Inputs

| Item | Description |
|------|-------------|
| CSV files | Weekly roster files from `tenants/<tenant>/` folder |
| `tenant_config.json` | Tenant configuration — defines `csv_parser`, `workstation_roles` |
| `staff_roles.json` | Employee skills override (in tenant folder, optional) |

> **多租戶注意**：不同租戶的 CSV 格式可能不同。`tenant_config.json` 的 `csv_parser` 欄位決定使用哪個解析器。目前支援：
> - `gold_pig_v1` — 金豬外場 Excel 匯出格式（multi-row per employee, Traditional Chinese）
> - `generic` — 通用格式（待實作）

---

## Quick Start

```bash
# Analyze all weeks in a tenant folder (recommended)
python scripts/analyzer.py tenants/<tenant>/ habits.json

# Example
python scripts/analyzer.py tenants/glod-pig/ habits.json
```

---

## Step-by-Step Instructions

### Step 1 — Verify dependencies

```bash
pip install pandas  # if not already installed
```

### Step 2 — Verify tenant folder structure

```bash
ls tenants/<tenant>/
# Expected: tenant_config.json, staff_roles.json, *.csv, RULES.md
```

### Step 3 — Run the Analyzer

```bash
python scripts/analyzer.py <csv_path_or_tenant_folder> [output_habits.json]
```

**Arguments:**
- `<csv_path_or_tenant_folder>` — path to a single CSV **or** a tenant folder
- `[output_habits.json]` — output path (default: `habits.json`)

### Step 4 — Verify output

The Analyzer produces three files:

**`habits.json`** — one entry per employee:
```json
{
  "employee_id": "3",
  "chinese_name": "...",
  "english_name": "...",
  "preferred_shifts": ["B106", "B009", "B010"],
  "overtime_willingness": "fixed_overtime",
  "workstation_skills": ["烤手"],
  "avg_weekly_hours": 24.5,
  "avg_shifts_per_week": 9.8,
  "shift_frequency": {"B010": 3, "B106": 8}
}
```

> **Note:** `preferred_shifts` and `shift_frequency` may contain either shift codes (B106, B009) or time labels (morning, afternoon, evening), depending on historical data. The Scheduler and Auditor handle both formats automatically.

**`habits_coverage.json`** — per-shift staffing averages.

**`habits_demand.json`** — 4-scenario demand profile by workstation role.

### Step 5 — Pass to Scheduler

```bash
python scripts/ortools_solver.py habits.json habits_demand_shift.json schedule <week_start> tenants/<tenant>
```

---

## Output Schema

```
habits.json
└── employee_id          string
└── chinese_name         string
└── english_name         string
└── preferred_shifts     list     — shift codes or time labels
└── workstation_skills   list     — role names from tenant_config.json workstation_roles
└── overtime_willingness string   — "fixed_overtime"|"no_overtime"|"flexible"
└── rotation_flexibility string   — "full"|"morning_afternoon"|"none"
└── avg_weekly_hours     float
└── avg_shifts_per_week  float    — used by Scheduler SC3 for personalized fairness
└── shift_frequency      dict     — shift codes or time labels with counts
└── workstation_frequency dict
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Could not find date header row` | Check CSV is unmodified; verify `csv_parser` in `tenant_config.json` matches file format |
| `0 employees found` | Confirm CSV format matches the parser for this tenant |
| Incorrect shift counts | Check CSV week range covers full 7 days |
| Wrong workstation skills | Check `staff_roles.json` in tenant folder — it overrides CSV-derived skills |
