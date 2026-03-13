---
name: weight-tuner
description: Tune scheduler objective weights via sweep testing. Design weight configurations, run parallel sweeps, and analyze structured reports to find the best balance between demand coverage, fairness, and preferences.
---

# Weight Tuner Skill

> **前置條件**：先讀取 `CLAUDE.md` 了解多租戶架構，再讀取 `skills/scheduler/SKILL.md` 了解排班求解器。

## Overview

Weight tuning adjusts the CP-SAT solver's objective weights to find the best trade-off between competing goals (demand coverage vs preferences vs fairness). The workflow is:

1. **Diagnose** — Read baseline audit to identify which rules are failing
2. **Design** — Create a `weight_sweep.json` with candidate weight configurations
3. **Execute** — Run `run.py --sweep` to test all configs (Scheduler + Auditor)
4. **Analyze** — Read `sweep_report_<TIMESTAMP>.json` for rule-level diffs
5. **Iterate** — Narrow down or adjust weights, re-sweep

**When to use this skill:**
- After initial scheduling produces too many P1/P2 violations
- When you want to explore the trade-off space (e.g. preference vs fairness)
- When headcount violations persist and you need to determine if it's a weight or supply issue

---

## Inputs

| Item | Source | Description |
|------|--------|-------------|
| `weight_sweep.json` | User-created (see template below) | Array of `{label, weights}` configs to test |
| `habits.json` | Analyzer output | Must exist before sweep |
| `habits_demand_shift.json` | Demand analysis output | Must exist before sweep |
| `tenant_config.json` | Tenant directory | Shift defs, constraints, headcount targets |
| `prev_schedule.csv` | Previous week (optional) | For cross-week constraints |

---

## Quick Start

```bash
# 1. Copy and edit a sweep config
cp tenants/TEMPLATE/weight_sweep.json tenants/<tenant>/weight_sweep.json
# Edit: add weight combinations to test

# 2. Run sweep (parallel recommended)
python3 scripts/run.py --tenant <tenant> --week 2026-03-09 \
  --sweep tenants/<tenant>/weight_sweep.json --parallel 4

# 3. Check console output for summary + rule breakdown tables
# 4. Read the structured report
cat tenants/<tenant>/output/sweep_report_<TIMESTAMP>.json
```

---

## Step-by-Step Guide

### Step 1: Diagnose — Read baseline audit

Look at an existing audit file's `by_rule` distribution:

| Pattern | Diagnosis |
|---------|-----------|
| Many days with P1-002 (headcount) | Supply shortage — too few eligible employees |
| P1-002 only on weekends | `min_daily_headcount` weekend targets too high |
| P1-SC6 (consecutive days) | `W_CONSEC5` too low, or `max_consecutive_working_days` too strict |
| P1-SC7 (isolated rest) | `W_ALTERNATE` too low |
| High P2-001 (preferences) | `W_PREF` too low relative to `W_VAC` |
| P0 or Hard > 0 | Hard constraint conflict — **not a weight problem** |

### Step 2: Design — Create weight_sweep.json

Always include a `baseline` (empty weights = defaults) as the first entry:

```json
[
  {"label": "baseline", "weights": {}},
  {"label": "high_pref", "weights": {"W_PREF": 30, "W_SHIFT": 15}},
  {"label": "high_fairness", "weights": {"W_FAIRNESS": 40}},
  {"label": "balanced", "weights": {"W_PREF": 20, "W_FAIRNESS": 25, "W_CONSEC5": 50}}
]
```

### Step 3: Execute sweep

```bash
python3 scripts/run.py --tenant glod-pig --week 2026-03-09 \
  --prev-schedule tenants/glod-pig/output/schedule_20260302.csv \
  --sweep tenants/glod-pig/weight_sweep.json --parallel 4
```

Console output includes:
- **Summary table** — P0/Hard/P1/P2 totals per config
- **Rule breakdown table** — violation count per rule_id per config

### Step 4: Read the sweep report

The sweep automatically produces `tenants/<tenant>/output/sweep_report_<TIMESTAMP>.json`:

```json
{
  "configs": [
    {
      "label": "baseline",
      "summary": {"P0": 0, "Hard": 0, "P1": 2, "P2": 26},
      "by_rule": {"P1-002": 2, "P2-001": 26}
    }
  ],
  "comparison": {
    "baseline_label": "baseline",
    "diffs": [
      {
        "label": "high_pref",
        "delta": {"P0": 0, "Hard": 0, "P1": -1, "P2": +5},
        "improved_rules": ["P1-002"],
        "regressed_rules": ["P2-001"],
        "resolved_rules": [],
        "new_rules": []
      }
    ]
  }
}
```

Key fields in `comparison.diffs`:
- `delta` — change from baseline (negative = better)
- `improved_rules` — rules with fewer violations than baseline
- `regressed_rules` — rules with more violations
- `resolved_rules` — rules that went from >0 to 0
- `new_rules` — rules that went from 0 to >0

### Step 5: Iterate or Decide

- If a config resolves P1 without adding P0/Hard, adopt those weights
- If trade-offs exist (e.g. -2 P1 but +5 P2), decide based on business priority
- If no config improves P1, the problem is likely **supply-side** (see below)

---

## Weight Reference

| Weight | Default | Controls | Increase when... | Decrease when... |
|--------|---------|----------|-------------------|------------------|
| `W_VAC` | 100 | SC1: Demand coverage (shift-level headcount) | Understaffed shifts | Overstaffing to meet demand forces bad schedules |
| `W_FAIRNESS` | 15 | SC3: Even hours distribution across employees | Hours spread is too uneven | Fairness blocking demand coverage |
| `W_PREF` | 10 | SC2: Employee shift preferences | Too many P2-001 violations | Preferences blocking demand coverage |
| `W_EMPLOYEE_SOFT` | 8 | SC4: No-overtime employee soft limit | Overtime employees getting too many shifts | Need more flexibility |
| `W_SHIFT` | 5 | SC5: Shift frequency preference | Employees getting unusual shift patterns | Frequency blocking other objectives |
| `W_CONSEC5` | 30 | SC6: Penalize 5 consecutive working days | P1-SC6 violations | Need longer work streaks for coverage |
| `W_ALTERNATE` | 15 | SC7: Penalize isolated rest (W-R-W) | P1-SC7 violations | Need isolated rest for coverage |
| `W_ALT_CROSS` | 50 | SC7-cross: Cross-week W-R-W penalty | Cross-week pattern violations | Not using prev_schedule |
| `W_PT_EVENING` | 20 | SC8: Part-timers prefer evening shifts | PT assigned too many early shifts | PT need to fill early gaps |
| `W_HEADCOUNT` | 200 | HC8: Minimum daily headcount (soft) | P1-002 headcount violations | Headcount forcing bad individual schedules |

---

## Supply vs Demand Diagnosis

When weight tuning doesn't resolve violations, the root cause is often structural:

| Symptom | Likely Cause | Action |
|---------|-------------|--------|
| P1-002 on most days across all configs | Not enough schedulable employees | Hire more staff or reduce `min_daily_headcount` |
| 26 employees with `workstation_skills=["服務"]` never scheduled | No shift codes map to "服務" in `workstation_roles` | Add service-role shift mappings in `tenant_config.json` |
| P0 > 0 regardless of weights | Hard constraint conflict (rest hours, skill mismatch) | Check `constraints` in `tenant_config.json` |
| P1-002 only on weekends | Weekend headcount targets too aggressive | Lower `min_daily_headcount.saturday/sunday` |
| All configs produce identical results | Weights don't affect the binding constraints | Problem is in hard constraints, not objectives |

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `habits.json not found` | Run analyzer first: `--step analyzer` |
| Sweep produces identical results | Weights may not affect binding constraints; check if P0/Hard exist |
| `ERR` in sweep table | Check worker error in console; often a solver timeout |
| Report JSON missing `comparison` | Only 1 config in sweep (need >= 2 for comparison) |
| Sweep takes too long | Use `--parallel 4` or reduce config count |
