# Tenant Onboarding Checklist

Quick-reference validation checklist. Use after onboarding to verify completeness.

## Required Files

- [ ] `tenants/<tenant>/tenant_config.json` exists and is valid JSON
- [ ] `tenants/<tenant>/RULES.md` has content (not just template placeholders)
- [ ] `tenants/<tenant>/availability.json` exists (can be empty template)
- [ ] `tenants/<tenant>/events.json` exists
- [ ] `tenants/<tenant>/output/` directory exists

## tenant_config.json Validation

### Required Fields
- [ ] `tenant_id` matches directory name
- [ ] `display_name` is set (not "店面名稱" or "CHANGE_ME")
- [ ] `region` is valid (TW / JP / US / ...)
- [ ] `timezone` is valid IANA timezone

### Shift Definitions
- [ ] `shift_defs` has at least 1 entry
- [ ] Each shift has `start`, `end`, `hours`
- [ ] `hours` <= time span between `start` and `end` (accounting for breaks)
- [ ] Time format is `HH:MM`

### Workstation Roles
- [ ] Every key in `shift_defs` has a corresponding entry in `workstation_roles`
- [ ] No orphan entries in `workstation_roles` (roles without matching shift_defs)

### Headcount
- [ ] `min_daily_headcount` values are reasonable (> 0 for at least weekday)

### Constraints
- [ ] `min_rest_hours` >= 11 (legal minimum in TW)
- [ ] `max_weekly_hours` >= `std_weekly_hours`
- [ ] `max_working_days` >= `min_working_days` (if min_working_days is set)

### Manager Constraints (if applicable)
- [ ] `early_shifts` and `late_shifts` reference valid shift codes from `shift_defs`
- [ ] `daily_early_count` + `daily_late_count` > 0

### CSV Parser
- [ ] `csv_parser` is set to a registered parser ID (check `data_loader.py`)
- [ ] At least 1 historical CSV is present in `tenants/<tenant>/` (for analyzer)

## RULES.md Validation

- [ ] Manager names listed (if manager system exists)
- [ ] Counter/reception assignments listed (if applicable)
- [ ] PT employee names listed (if applicable)

## Pipeline Validation

- [ ] `python3 scripts/analyzer.py tenants/<tenant>/ tenants/<tenant>/output/habits.json` runs without error
- [ ] `habits.json` employee count matches expected roster size
- [ ] `is_manager` flags in `habits.json` match RULES.md manager list
