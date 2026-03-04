---
name: auditor
description: Validate a generated schedule against labor law (P0), hard constraints, tenant rules (P1), and employee preferences (P2). Reads tenant_config.json for coverage targets and constraints. Produces a structured audit report with violations and fix suggestions.
---

# Auditor Skill

> **前置條件**：先讀取 `CLAUDE.md` 了解多租戶架構。

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

| Item | Source | Description |
|------|--------|-------------|
| `schedule.csv/.json` | Scheduler output | The schedule to audit |
| `habits.json` | Analyzer output (optional) | For P2 preference checking |
| `tenant_dir` | `tenants/<tenant>/` | Tenant folder for config, rest days, manager rules |
| `week_start` | User specified | e.g. `2026-03-02` |
| `prev_schedule` | Previous run (optional) | For cross-week violation detection |

### Key Tenant Config Fields Used

| Field | From | Purpose |
|-------|------|---------|
| `min_daily_headcount` | `tenant_config.json` | P1: daily staffing minimum targets |
| `constraints` | `tenant_config.json` | P0: max hours, min rest, etc. |
| `shift_defs` | `tenant_config.json` | P0: hours calculation for shift codes |
| `manager_constraints` | `tenant_config.json` | P1: manager early/late coverage |
| `no_same_rest` | `tenant_config.json` | P1: forbidden same-day rest pairs |
| `is_manager` | `habits.json` (per employee) | Identifies managers for coverage checks |
| `designated_rest` | `rest_days.json` | P0: must not schedule on rest days |

---

## Quick Start

```bash
# Full audit (P0 + Hard + P1 + P2)
python scripts/auditor_tools.py schedule.csv habits.json audit_report.json tenants/<tenant> <week_start>

# With cross-week detection
python scripts/auditor_tools.py schedule_0309.csv habits.json audit_0309.json tenants/<tenant> 2026-03-09 schedule_0302.csv
```

---

## Step-by-Step Instructions

### Step 1 — Run the Auditor

```bash
python scripts/auditor_tools.py <schedule> [habits.json] [output.json] [tenant_dir] [week_start] [prev_schedule]
```

**Arguments:**
| Argument | Default | Example |
|----------|---------|---------|
| `schedule` | required | `schedule_0302.csv` or `.json` |
| `habits.json` | (P2 skipped if missing) | `habits.json` |
| `output.json` | `audit_report.json` | `audit_0302.json` |
| `tenant_dir` | none | `tenants/glod-pig` |
| `week_start` | inferred from schedule | `2026-03-02` |
| `prev_schedule` | none | `schedule_0302.csv` |

### Step 2 — Read the report

**Console output:**
```
稽核結果:
   整體狀態: ❌ 有違規
   🔴 P0: 0 項 (通過)
   🔴 Hard: 0 項 (通過)
   🟡 P1: 2 項違規
   🔵 P2: 22 項違規
```

**`audit_report.json` structure:**
```json
{
  "generated_at": "2026-03-02T18:30:00",
  "overall_pass": false,
  "total_violations": 24,
  "summary": { "P0": 0, "Hard": 0, "P1": 2, "P2": 22 },
  "violations": [
    {
      "priority": "P1",
      "rule_id": "P1-002",
      "description": "...",
      "date": "2026-03-02",
      "suggestion": "增派 2 名員工"
    }
  ]
}
```

### Step 3 — Fix violations

**P0 violations** — must fix before publishing:
1. Review `suggestion` field in the report
2. Adjust schedule or constraints
3. Re-run Auditor to confirm resolved

**P1 violations** — coverage shortfall:
- Verify `min_daily_headcount` in `tenant_config.json` is realistic for staff size
- Re-run Scheduler with adjusted parameters

**P2 violations** — preference mismatches:
- Review if critical employees are affected
- Swap shifts or re-run Scheduler with updated habit weights

### Step 4 — Closed-loop (optional)

If violations persist, re-run the full pipeline:

```bash
TENANT=<tenant>
WEEK=<week_start>

python scripts/analyzer.py tenants/$TENANT/ habits.json
python scripts/ortools_solver.py habits.json habits_demand_shift.json schedule $WEEK tenants/$TENANT
python scripts/auditor_tools.py schedule.csv habits.json audit_report.json tenants/$TENANT $WEEK
```

---

## Rule Reference

### P0 — 勞基法
| Rule ID | Description | Config Source |
|---------|-------------|---------------|
| P0-001 | Single shift ≤ 12 hours | `tenant_config.json → shift_defs` |
| P0-002 | Min rest between shifts | `tenant_config.json → constraints.min_rest_hours` |
| P0-003 | Weekly hours ≤ cap | `tenant_config.json → constraints.max_weekly_hours` |
| P0-004 | Max working days per week | `tenant_config.json → constraints.max_working_days` |
| P0-005 | No shifts on designated rest days | `rest_days.json` |

### Hard
| Rule ID | Description |
|---------|-------------|
| HC-001 | No double-booking on same day |

### P1 — Tenant Rules
| Rule ID | Description | Config Source |
|---------|-------------|---------------|
| P1-001 | Shift-level staffing minimum | Coverage targets |
| P1-002 | Daily total headcount minimum | `tenant_config.json → min_daily_headcount` |
| P1-003 | Manager early/late coverage | `tenant_config.json → manager_constraints` + `habits.json → is_manager` |
| P1-004 | No-same-rest pair violation | `tenant_config.json → no_same_rest` |
| P1-SC6 | No 5+ consecutive working days | Cross-week detection via `prev_schedule` |
| P1-SC7 | No work-rest-work pattern | Cross-week detection via `prev_schedule` |

### P2 — Employee Preferences
| Rule ID | Description | Config Source |
|---------|-------------|---------------|
| P2-001 | Non-preferred shift assigned | `habits.json → preferred_shifts` |
| P2-002 | No-overtime employee overworked | `habits.json → overtime_willingness` |

---

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | All checks passed |
| `1` | One or more violations found |
