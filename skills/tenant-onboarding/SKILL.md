---
name: tenant-onboarding
description: Interactive wizard to onboard a new tenant. Copies TEMPLATE, guides user through tenant_config.json + RULES.md setup, validates CSV placement, and runs analyzer to verify the pipeline. Use when adding a new store/restaurant.
---

# Tenant Onboarding Skill

> **Trigger**: User mentions adding a new tenant/store, or says something like "onboard nara", "new tenant", "add a store".

## Overview

This skill guides the user through creating a fully-configured tenant directory from TEMPLATE, step by step. It replaces the manual SOP in CLAUDE.md S4 with an interactive, validated workflow.

**Input**: `$ARGUMENTS` = tenant name (slug), e.g. `nara`

---

## Workflow

### Step 0: Parse Arguments

Extract `<tenant>` from `$ARGUMENTS`. If empty, ask the user for a tenant slug (lowercase, hyphens OK, no spaces).

Validate:
- Slug is lowercase alphanumeric + hyphens only
- `tenants/<tenant>/` does not already exist

### Step 1: Copy TEMPLATE

```bash
cp -r tenants/TEMPLATE tenants/<tenant>
```

Confirm the directory was created with all expected files:
- `tenant_config.json`
- `availability.json`
- `events.json`
- `rest_days.json`
- `line_name_map.json`
- `RULES.md`
- `output/`

### Step 2: Collect Basic Info

Ask the user for (one question at a time is fine, or batch if they volunteer info):

| Field | Example | Required |
|-------|---------|----------|
| `display_name` | "奈良 日式料理" | Yes |
| `region` | TW / JP / US | Yes |
| `timezone` | Asia/Taipei | Yes (default from region) |

Update `tenant_config.json`:
- Set `tenant_id` to `<tenant>`
- Set `display_name`, `region`, `timezone`

### Step 3: Define Shifts

Ask the user to provide their shift codes. Explain the format:

> Each shift needs: code, start time, end time, actual work hours (after break deduction).
> Example: `B001  08:00-17:00  8h`

Accept input in any reasonable format (table, list, free text) and parse into `shift_defs`.

If the user has a CSV/Excel with historical schedules, note that shift codes can also be extracted from those later via analyzer.

### Step 4: Define Workstation Roles

For each shift code defined in Step 3, ask what role/workstation it maps to.

Common patterns:
- All shifts map to one role (e.g. "外場")
- Some shifts are special (e.g. "櫃台(早)" -> "領檯早")

Update `workstation_roles` in `tenant_config.json`.

### Step 5: Headcount & Constraints

Ask about daily minimum headcount:

| Day Type | Question |
|----------|----------|
| `weekday` | Minimum staff on a normal weekday? |
| `saturday` | Minimum staff on Saturday? |
| `sunday` | Minimum staff on Sunday? |
| `package` | Minimum staff on private event days? |

Review constraint defaults and ask if any need adjustment:

| Constraint | Default | Meaning |
|------------|---------|---------|
| `min_rest_hours` | 11 | Min hours between shifts |
| `max_weekly_hours` | 46 | Max hours per week |
| `max_working_days` | 5 | Max working days per week |
| `std_weekly_hours` | 40 | Standard (non-OT) hours |
| `max_consecutive_working_days` | 4 | Max consecutive work days |
| `pt_min_shift_hour` | 17 | Earliest PT shift start hour |

### Step 6: Manager Constraints (Optional)

Ask: "Does this store have a manager/supervisor shift system?"

If yes, collect:
- Which shifts are early/late manager shifts
- Min managers per early/late period
- Whether managers count toward headcount

If no, leave `manager_constraints` at defaults (all zeros).

### Step 7: No-Same-Rest Pairs (Optional)

Ask: "Are there employee pairs who must NOT have the same rest day?"

If yes, collect pairs as `[id_a, id_b]`.

### Step 8: RULES.md

Ask the user to describe their scheduling rules in natural language. Fill in the RULES.md sections:
1. Staff roster (managers, counter staff, PT workers)
2. Basic scheduling principles
3. Role-specific rules
4. Daily headcount & special situations
5. SOP

Remind user: Analyzer will parse manager names and counter assignments from this file.

### Step 9: Place Historical CSVs

Ask: "Do you have historical schedule CSVs? If so, place them in `tenants/<tenant>/`."

If the CSV format differs from existing parsers, note that a new `csv_parser` may need to be registered in `data_loader.py`. For now, set `csv_parser` to `"generic"` unless the user specifies otherwise.

### Step 10: Validate

Run the analyzer to verify everything works:

```bash
python3 scripts/analyzer.py tenants/<tenant>/ tenants/<tenant>/output/habits.json
```

Check for:
- No Python errors
- `habits.json` is produced
- Employee count looks reasonable
- `is_manager` flags match RULES.md

If errors occur, diagnose and fix before proceeding.

### Step 11: Summary

Print a checklist showing completion status:

```
Tenant Onboarding: <tenant>
================================
[x] Directory created
[x] Basic info (tenant_id, region, timezone)
[x] Shift definitions (N shifts)
[x] Workstation roles mapped
[x] Headcount requirements set
[x] Constraints configured
[x] Manager constraints (configured / skipped)
[x] No-same-rest pairs (N pairs / none)
[x] RULES.md written
[x] Historical CSVs placed (N files)
[x] Analyzer validation passed
[ ] availability.json (fill before first schedule run)
[ ] events.json (fill if there are private events)

Next steps:
1. Fill availability.json before scheduling (see: skills/availability/SKILL.md)
2. Run full pipeline: python3 scripts/run.py --tenant <tenant> --week YYYY-MM-DD
3. Review audit results and iterate if needed
```

---

## Checklist Reference

See `checklist.md` in this skill directory for the condensed validation checklist.

---

## Error Handling

| Error | Resolution |
|-------|------------|
| `tenants/<tenant>/` already exists | Ask user: overwrite, pick different name, or resume |
| Missing shift_defs | Cannot proceed past Step 3 without at least 1 shift |
| Analyzer fails on CSV | Check csv_parser setting, may need new parser |
| No CSVs available | Skip Step 10 analyzer run; user will need CSVs before scheduling |

---

## Migration to Plugin (Future)

This skill is designed for easy migration to a Plugin (方案 B) if needed:
- `SKILL.md` moves to `plugin/skills/setup/SKILL.md`
- Add `plugin.json` manifest + `commands/create-tenant/command.md`
- No rewrite needed
