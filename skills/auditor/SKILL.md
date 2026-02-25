---
name: auditor
description: Validate a generated schedule against labor law (P0), hard constraints, tenant rules (P1), and employee preferences (P2). Produces a structured audit report with violations and fix suggestions.
---

# Auditor Skill

## Overview

The Auditor validates a schedule against four priority layers:

| Priority | Layer | Meaning |
|----------|-------|---------|
| P0 | 勞基法 (Labor Law) | Legal — must fix before publishing |
| Hard | System Constraints | Should never appear from solver output |
| P1 | 租戶規則 (Tenant Rules) | Business coverage requirements |
| P2 | 員工偏好 (Preferences) | Soft violations — fix if possible |

**When to use this skill:**
- After the Scheduler generates a new schedule
- Before publishing the roster to employees
- After manual edits to verify compliance
- As part of the closed-loop feedback cycle

---

## Inputs

| Item | Description |
|------|-------------|
| `schedule.csv` or `schedule.json` | Output from the Scheduler skill |
| `habits.json` (optional) | For P2 preference checking |

---

## Quick Start

```bash
# Full audit (P0 + Hard + P1 + P2)
python scripts/auditor_tools.py schedule.csv habits.json audit_report.json

# P0 + P1 only (no preference checking)
python scripts/auditor_tools.py schedule.csv
```

---

## Step-by-Step Instructions

### Step 1 — Run the Auditor

```bash
python scripts/auditor_tools.py <schedule> [habits.json] [output_report.json]
```

**Arguments:**
| Argument | Default | Example |
|----------|---------|---------|
| `schedule` | required | `schedule.csv` or `schedule.json` |
| `habits.json` | (P2 skipped if missing) | `habits.json` |
| `output_report.json` | `audit_report.json` | `audit_0302.json` |

### Step 2 — Read the report

**Console output:**
```
稽核結果:
   整體狀態: ❌ 有違規
   P0: 0 項 (通過)
   Hard: 0 項 (通過)
   P1: 0 項 (通過)
   P2: 22 項違規
```

**`audit_report.json` structure:**
```json
{
  "generated_at": "2026-03-02T18:30:00",
  "overall_pass": false,
  "total_violations": 22,
  "summary": {"P0": 0, "Hard": 0, "P1": 0, "P2": 22},
  "violations": [
    {
      "priority": "P2",
      "rule_id": "P2-001",
      "description": "員工 吳咨錞(8) 在 2026-03-03 被排入非偏好班次（偏好: B104, B103，實際: 櫃台）",
      "employee_id": "8",
      "date": "2026-03-03",
      "suggestion": "優先考慮將此員工排入 B104 班次"
    }
  ]
}
```

### Step 3 — Fix violations

**P0 violations** — must fix before publishing:
1. Open `schedule.csv`
2. Apply changes per the `suggestion` field in the report
3. Re-run the Auditor to confirm resolved
4. If structural (recurring), feed violations back into Scheduler constraints

**P1 violations** — coverage shortfall:
- Check if any employees can add shifts on the flagged dates
- Or run the Scheduler again with higher minimum coverage for those dates

**P2 violations** — preference mismatches:
- Review if critical employees are affected
- Swap shifts manually or re-run Scheduler with updated habit weights

### Step 4 — Closed-loop (optional)

If P0 violations persist after manual edits, re-run the full pipeline:

```bash
# 1. Re-run Analyzer with corrected data
python scripts/analyzer.py tenants/glod-pig/ habits.json

# 2. Re-schedule
python scripts/ortools_solver.py habits.json habits_demand_shift.json schedule 2026-03-02 tenants/glod-pig

# 3. Re-audit
python scripts/auditor_tools.py schedule.csv habits.json audit_report.json
```

---

## Rule Reference

### P0 — 勞基法
| Rule ID | Description | How hours are calculated |
|---------|-------------|--------------------------|
| P0-001 | Single shift must not exceed 12 hours | Uses `SHIFT_DEFS` hours (deducting breaks), falls back to raw time span for unknown shift codes |
| P0-002 | Minimum 11 hours rest between consecutive shifts | Raw time gap between shift end and next shift start |
| P0-003 | Weekly hours must not exceed 46 hours | Uses `SHIFT_DEFS` hours (deducting breaks), falls back to raw time span for unknown shift codes |
| P0-004 | At least 1 day off per 7 consecutive days | Counts distinct working dates |

### Hard
| Rule ID | Description |
|---------|-------------|
| HC-001 | Employee cannot be double-booked on same day |

### P1 — Tenant Rules
| Rule ID | Description |
|---------|-------------|
| P1-001 | Minimum staffing per shift not met |

### P2 — Employee Preferences
| Rule ID | Description | How matching works |
|---------|-------------|---------------------|
| P2-001 | Assigned to non-preferred shift | Compares the assigned shift code (workstation) directly against `preferred_shifts` list. Supports both shift codes (B106, B009) and time labels. |
| P2-002 | No-overtime employee scheduled for >5 shifts/week | Checks `overtime_willingness == "no_overtime"` |

---

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | All checks passed |
| `1` | One or more violations found |
