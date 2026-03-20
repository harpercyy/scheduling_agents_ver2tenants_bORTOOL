#!/usr/bin/env python3
"""
ortools_solver.py — Scheduler Agent: Demand-Aware CP-SAT Shift Scheduler.

Reads habits_demand_shift.json to determine which shift codes (B003, B104, 櫃台(早)…)
are needed, and in what quantities, for each of 4 staffing scenarios:
  平日 / 平日包場 / 週末 / 週末包場

For each day of the target week:
  1. Determine its scenario (weekday/weekend × has-event/no-event)
  2. Load required headcounts per shift code from the demand profile
  3. Build CP-SAT constraints to meet those headcounts

Scenarios covered:
  S3: Automated Schedule Generation
  S4: Constraint-aware / Demand-driven Scheduling
  S5: Retry with relaxed constraints on INFEASIBLE
"""

import json
import sys
import os
import csv
import re
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_loader import (Habit, ScheduleEntry, load_habits_json,
                          load_tenant_config, load_manager_constraints,
                          load_availability, get_region_holidays)

try:
    from ortools.sat.python import cp_model
except ImportError:
    print("❌ 請先安裝 ortools: pip install ortools")
    sys.exit(1)


# ─── DEPRECATED: SHIFT_DEFS placeholder ─────────────────────────────────────
# Kept as empty dict for backward compatibility (auditor_tools.py imports this).
# Real shift definitions are loaded from tenant_config.json via load_tenant_config().
# TODO: Remove once auditor_tools.py is decoupled (Step 5).
SHIFT_DEFS = {}

DAYS_TW = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

# ─── Default Objective Weights ───────────────────────────────────────────────
# Overridable via weights_override parameter (used by --sweep mode).
DEFAULT_WEIGHTS = {
    "W_VAC": 100,           # SC1: demand coverage
    "W_FAIRNESS": 15,       # SC3: fairness
    "W_PREF": 10,           # SC2: shift preference
    "W_EMPLOYEE_SOFT": 8,   # SC4: no-overtime employee soft constraint
    "W_SHIFT": 5,           # SC5: shift frequency preference
    "W_CONSEC5": 30,        # SC6: consecutive 5 working days
    "W_ALTERNATE": 15,      # SC7: work-rest-work isolated pattern
    "W_ALT_CROSS": 50,      # SC7-cross: cross-week work-rest-work
    "W_PT_EVENING": 20,     # SC8: PT prefer evening shifts
    "W_HEADCOUNT": 200,     # HC8: minimum daily headcount (soft mode)
}

# ─── Scenario Detection ───────────────────────────────────────────────────────

def is_holiday(date_str: str, holidays: set = None) -> bool:
    """Check if a date is a holiday (weekend or in the holidays set).
    Args:
        date_str: Date in 'YYYY-MM-DD' format.
        holidays: Set of holiday date strings. If None, uses empty set.
    """
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.weekday() >= 5 or date_str in (holidays or set())
    except ValueError:
        return False


def load_package_dates(tenant_dir: str) -> set:
    """Load 包場 dates from events.json, return as set of 'YYYY-MM-DD' strings."""
    pkg = set()
    if not tenant_dir:
        return pkg
    events_path = os.path.join(tenant_dir, "events.json")
    if os.path.exists(events_path):
        with open(events_path, encoding="utf-8") as f:
            data = json.load(f)
        for d in data.get("package_dates", []):
            d = d.strip()
            if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
                pkg.add(d)
            else:
                # Short form M-D → try to resolve
                try:
                    m, day = map(int, d.split("-"))
                    year = datetime.today().year
                    pkg.add(f"{year}-{m:02d}-{day:02d}")
                except Exception:
                    pass
    return pkg


def get_scenario(date_str: str, package_dates: set, holidays: set = None,
                  scenarios: list = None) -> str:
    """Map a date to its scenario name.
    scenarios order convention: [weekday, weekday+package, weekend, weekend+package].
    """
    labels = scenarios or ["平日", "平日包場", "週末", "週末包場"]
    hol = is_holiday(date_str, holidays)
    pkg = date_str in package_dates
    if hol and pkg:  return labels[3]
    if hol:          return labels[2]
    if pkg:          return labels[1]
    return labels[0]


# ─── Rest Days & Manager Config Loaders ──────────────────────────────────────

def load_rest_days(path: str, week_start: str, num_days: int = 7) -> dict:
    """
    Load designated rest days from rest_days.json.
    Returns {employee_id: set of day_indices} where day_index is 0..6.
    """
    result = {}
    if not path or not os.path.exists(path):
        return result

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    designated = data.get("designated_rest", {})
    ws = datetime.strptime(week_start, "%Y-%m-%d")

    for emp_id, dates in designated.items():
        day_indices = set()
        for date_str in dates:
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d")
                diff = (d - ws).days
                if 0 <= diff < num_days:
                    day_indices.add(diff)
            except ValueError:
                pass
        if day_indices:
            result[emp_id] = day_indices

    return result



def load_prev_tail(prev_schedule_path: str, habits: list) -> dict:
    """
    Load the previous week's schedule and extract the last 4 days per employee.
    Returns: { employee_id: {"working": [bool x4], "last_shift": str|None} }
    The 4 booleans correspond to day[-4], day[-3], day[-2], day[-1] of the previous week.
    """
    if not prev_schedule_path or not os.path.exists(prev_schedule_path):
        return {}

    # Load entries from CSV or JSON
    if prev_schedule_path.endswith(".json"):
        with open(prev_schedule_path, encoding="utf-8") as f:
            data = json.load(f)
        entries = data.get("schedule", data) if isinstance(data, dict) else data
    else:
        entries = []
        with open(prev_schedule_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                entries.append(row)

    if not entries:
        return {}

    # Determine the last 4 dates in the previous schedule
    all_dates = sorted(set(e.get("date", "") for e in entries if e.get("date")))
    if not all_dates:
        return {}
    tail_dates = all_dates[-4:] if len(all_dates) >= 4 else all_dates

    # Build working set per employee per date
    working_by_emp_date = defaultdict(dict)  # {emp_id: {date: shift_code}}
    for e in entries:
        eid = e.get("employee_id", "")
        date = e.get("date", "")
        leave = e.get("leave_type", "")
        if eid and date and not leave:
            working_by_emp_date[eid][date] = e.get("workstation", "")

    # Build prev_tail for each employee referenced in habits
    result = {}
    emp_ids = set(h.employee_id for h in habits)
    for eid in emp_ids:
        emp_dates = working_by_emp_date.get(eid, {})
        # Build 4-element boolean array (pad with False if fewer than 4 days)
        working = []
        last_shift = None
        for d in tail_dates:
            if d in emp_dates:
                working.append(True)
                last_shift = emp_dates[d]
            else:
                working.append(False)
                last_shift = None
        # Pad front with False if fewer than 4 tail days
        while len(working) < 4:
            working.insert(0, False)
        # last_shift is from the very last date
        last_date = tail_dates[-1]
        last_shift = emp_dates.get(last_date)
        result[eid] = {"working": working[-4:], "last_shift": last_shift}

    return result


# ─── Demand-Shift Profile ─────────────────────────────────────────────────────

def load_demand_profile(demand_path: str) -> dict:
    """
    Load habits_demand_shift.json.
    Returns: {scenario: {role: {shift_code: required_headcount}}}
    """
    with open(demand_path, encoding="utf-8") as f:
        return json.load(f)


def get_day_requirements(scenario: str, demand_profile: dict) -> list:
    """
    For a given scenario, return a flat list of required shift slots:
      [{"shift_code": "B104", "role": "烤手", "required": 4}, ...]

    Each entry represents ONE shift slot that needs `required` staff assigned.
    We create one slot per shift code × required count (flattened so the solver
    can treat each slot independently).

    """
    profile = demand_profile.get(scenario, {})
    slots = []
    for role, shift_counts in profile.items():
        for shift_code, count in shift_counts.items():
            slots.append({
                "shift_code": shift_code,
                "role": role,
                "required": count,
            })
    return slots

# ─── Workstation Role Helper ──────────────────────────────────────────────────

def _default_role(workstation_roles: dict = None) -> str:
    """Return the most common role from workstation_roles, or first value as fallback."""
    if not workstation_roles:
        return "烤手"
    from collections import Counter
    role_counts = Counter(workstation_roles.values())
    return role_counts.most_common(1)[0][0]


def shift_code_to_role(shift_code: str, workstation_roles: dict = None) -> str:
    """Map a shift code to its workstation role using tenant config.
    Falls back to the most common role in workstation_roles.
    """
    if workstation_roles:
        return workstation_roles.get(shift_code, _default_role(workstation_roles))
    return _default_role()


# ─── Employee Skill Matching ──────────────────────────────────────────────────

def employee_can_do_shift(habit: Habit, shift_code: str, role: str,
                          workstation_roles: dict = None) -> bool:
    """
    Check if an employee can be assigned to a given shift code.
    Uses skill-based matching via workstation_roles from tenant config.
    """
    role_needed = shift_code_to_role(shift_code, workstation_roles)
    skills = set(habit.workstation_skills)

    if not skills:
        # No skill data → only default role
        return role_needed == _default_role(workstation_roles)

    return role_needed in skills


# ─── Solver ───────────────────────────────────────────────────────────────────

class DemandScheduleSolver:
    """
    CP-SAT solver that assigns employees to shift codes based on
    a demand profile read from habits_demand_shift.json.

    Variables:
      x[e][d][sc] = 1 if employee e works shift code sc on day d

    Hard constraints:
      - At most 1 shift per employee per day
      - At most 6 days per week
      - Min 11h rest (no late shift followed by early next day)
      - Max weekly hours cap
      - Skill match (only assign shift codes the employee can do)
      - Demand: each shift code on each day must meet required headcount

    Soft objectives:
      - Prefer preferred shift times
      - Penalize overtime for no_overtime employees
      - Fairness in weekly hours
    """

    def __init__(self, habits: list, demand_profile: dict,
                 week_start_date: str, package_dates: set = None,
                 enforce_preferences: bool = True,
                 rest_days: dict = None, manager_config: dict = None,
                 min_daily_headcount: dict = None,
                 prev_tail: dict = None,
                 tenant_config=None,
                 weights_override: dict = None,
                 pt_availability: dict = None):
        self.habits = habits
        self.demand_profile = demand_profile
        self.package_dates = package_dates or set()
        self.enforce_preferences = enforce_preferences
        self.rest_days = rest_days or {}
        self.manager_config = manager_config or {}
        self.min_daily_headcount = min_daily_headcount  # from tenant_config.min_daily_headcount
        self.prev_tail = prev_tail or {}
        self.pt_availability = pt_availability or {}
        self.weights = {**DEFAULT_WEIGHTS, **(weights_override or {})}

        # Tenant config — provides shift_defs, workstation_roles, constraints, holidays
        self.tenant_config = tenant_config
        if tenant_config:
            self.shift_defs = tenant_config.shift_defs
            self.workstation_roles = tenant_config.workstation_roles
            self.max_weekly_hours = tenant_config.constraints.get("max_weekly_hours", 46)
            self.min_rest_hours = tenant_config.constraints.get("min_rest_hours", 11)
            self.holidays = tenant_config.region_holidays
        else:
            self.shift_defs = SHIFT_DEFS  # fallback to module-level (deprecated)
            self.workstation_roles = {}
            self.max_weekly_hours = 46
            self.min_rest_hours = 11
            self.holidays = set()

        self.week_start = datetime.strptime(week_start_date, "%Y-%m-%d")
        self.num_days = 7
        self.num_employees = len(habits)

        # Auto-generate PT shifts if configured
        self._generate_pt_shifts()

        # Build the full list of shift codes used across all scenarios
        all_codes = set()
        for scenario_profile in demand_profile.values():
            for role, code_counts in scenario_profile.items():
                for code in code_counts:
                    all_codes.add(code)
        # Also include all shift_defs (covers auto-generated PT shifts)
        all_codes.update(self.shift_defs.keys())
        self.shift_codes = sorted(all_codes)
        self.num_shifts = len(self.shift_codes)
        self.sc_idx = {sc: i for i, sc in enumerate(self.shift_codes)}

        # Per-day scenario and requirements
        self.day_scenarios = []
        self.day_requirements = []  # list of {shift_code, role, required}
        for d in range(self.num_days):
            date_str = self._date_for_day(d)
            scenarios = self.tenant_config.scenarios if self.tenant_config else None
            scen = get_scenario(date_str, self.package_dates, self.holidays, scenarios)
            self.day_scenarios.append(scen)
            self.day_requirements.append(get_day_requirements(scen, demand_profile))

        # Detect closure days (all employees have designated rest)
        self.closure_days = set()
        for d in range(self.num_days):
            if all(d in self.rest_days.get(h.employee_id, set())
                   for h in habits):
                self.closure_days.add(d)
        if self.closure_days:
            print(f"   🔒 公休日: {sorted(self.closure_days)} (所有員工休假)")

        self.model = cp_model.CpModel()
        self.vars = {}  # vars[e][d][sc_idx]

    def _date_for_day(self, day_idx: int) -> str:
        d = self.week_start + timedelta(days=day_idx)
        return d.strftime("%Y-%m-%d")

    def _shift_start_hour(self, shift_code: str) -> int:
        defn = self.shift_defs.get(shift_code)
        if defn:
            return int(defn["start"].split(":")[0])
        return 12  # default midday

    def _shift_hours(self, shift_code: str) -> float:
        defn = self.shift_defs.get(shift_code)
        return defn["hours"] if defn else 8.0

    def _shift_start_minutes(self, shift_code: str) -> int:
        """Return start time in minutes since midnight."""
        defn = self.shift_defs.get(shift_code)
        if defn:
            parts = defn["start"].split(":")
            return int(parts[0]) * 60 + int(parts[1])
        return 12 * 60

    def _shift_end_minutes(self, shift_code: str) -> int:
        """Return end time in minutes since midnight, adjusted +24h for overnight."""
        defn = self.shift_defs.get(shift_code)
        if not defn:
            return 20 * 60
        parts = defn["end"].split(":")
        end_m = int(parts[0]) * 60 + int(parts[1])
        start_m = self._shift_start_minutes(shift_code)
        if end_m <= start_m:
            end_m += 24 * 60
        return end_m

    def _is_late_shift(self, shift_code: str) -> bool:
        return self._shift_start_hour(shift_code) >= 17

    def _is_early_shift(self, shift_code: str) -> bool:
        return self._shift_start_hour(shift_code) <= 11

    def _shift_time_bucket(self, shift_code: str) -> str:
        """Classify a shift code into morning / afternoon / evening."""
        start_h = self._shift_start_hour(shift_code)
        if start_h < 12:
            return "morning"
        elif start_h < 17:
            return "afternoon"
        else:
            return "evening"

    @staticmethod
    def _prefs_are_shift_codes(preferred_shifts: list) -> bool:
        """Return True if preferred_shifts contains shift codes (B106, C102…),
        False if they are time-bucket labels (morning, afternoon, evening)."""
        if not preferred_shifts:
            return False
        time_labels = {"morning", "afternoon", "evening"}
        # If any entry looks like a shift code (starts with B/C or contains 櫃台),
        # treat the whole list as shift codes.
        for p in preferred_shifts:
            if p in time_labels:
                return False
            if re.match(r'^[BC]\d', p) or '櫃台' in p:
                return True
        # Default: treat unknown strings as shift codes
        return True

    def _generate_pt_shifts(self):
        """Auto-generate PT shift codes from pt_shift_generation config.
        Produces all (start, end) combos at the configured granularity,
        with duration in [min_hours, max_hours]. Injects into shift_defs
        and workstation_roles."""
        cfg = self.tenant_config.pt_shift_generation if self.tenant_config else {}
        if not cfg:
            return

        def _parse_hhmm(s):
            parts = s.split(":")
            return int(parts[0]) * 60 + int(parts[1])

        start_m = _parse_hhmm(cfg["earliest_start"])
        end_m = _parse_hhmm(cfg["latest_end"])
        gran = cfg.get("granularity_minutes", 30)
        min_dur = int(cfg["min_hours"] * 60)
        max_dur = int(cfg["max_hours"] * 60)
        role = cfg.get("role", "門市夥伴")

        count = 0
        for s in range(start_m, end_m, gran):
            for e in range(s + min_dur, min(s + max_dur, end_m) + 1, gran):
                code = f"PT_{s//60:02d}{s%60:02d}_{e//60:02d}{e%60:02d}"
                self.shift_defs[code] = {
                    "start": f"{s//60:02d}:{s%60:02d}",
                    "end": f"{e//60:02d}:{e%60:02d}",
                    "hours": (e - s) / 60,
                }
                self.workstation_roles[code] = role
                count += 1

        if count > 0:
            print(f"   🔧 PT 班次自動產生: {count} 個 "
                  f"({cfg['earliest_start']}–{cfg['latest_end']}, "
                  f"{cfg['min_hours']}–{cfg['max_hours']}h, {gran}min 粒度)")

    def _is_valid_assignment(self, e: int, d: int, i: int, sc: str) -> bool:
        """Check if employee e can be assigned shift sc (index i) on day d.
        Combines HC5 (skill), HC6 (rest days), HC13 (PT availability),
        HC15 (FT/PT separation) into a single build-time filter."""
        habit = self.habits[e]

        # HC6: designated rest day → no shifts
        if d in self.rest_days.get(habit.employee_id, set()):
            return False

        # HC15: FT/PT separation
        has_pt_gen = bool(self.tenant_config and self.tenant_config.pt_shift_generation)
        is_pt_shift = sc.startswith("PT_")
        if has_pt_gen:
            if habit.employee_type == "ft" and is_pt_shift:
                return False
            if habit.employee_type == "pt" and not is_pt_shift:
                return False
        else:
            # No pt_shift_generation — check ft_min_shift_hours
            ft_min = (self.tenant_config.constraints.get("ft_min_shift_hours", 0)
                      if self.tenant_config else 0)
            if ft_min > 0 and habit.employee_type == "ft":
                if self.shift_defs.get(sc, {}).get("hours", 0) < ft_min:
                    return False

        # HC5: skill match
        role = shift_code_to_role(sc, self.workstation_roles)
        if not employee_can_do_shift(habit, sc, role, self.workstation_roles):
            return False

        # HC13: PT availability window
        # When pt_shift_generation is active, PT without availability data = unavailable
        if habit.employee_type == "pt":
            avail = self.pt_availability.get(habit.employee_id)
            if avail is None:
                if has_pt_gen:
                    return False  # PT must have explicit availability
                # else: no pt_shift_generation → legacy behavior (allow all)
            else:
                if d not in avail:
                    return False
                avail_start, avail_end = avail[d]
                s_start = self._shift_start_minutes(sc)
                s_end = self._shift_end_minutes(sc)
                if s_start < avail_start or s_end > avail_end:
                    return False

        return True

    def build_variables(self):
        total_vars = 0
        total_possible = self.num_employees * self.num_days * self.num_shifts
        for e in range(self.num_employees):
            self.vars[e] = {}
            for d in range(self.num_days):
                self.vars[e][d] = {}
                for i, sc in enumerate(self.shift_codes):
                    if self._is_valid_assignment(e, d, i, sc):
                        self.vars[e][d][i] = self.model.NewBoolVar(
                            f"e{e}_d{d}_sc{i}"
                        )
                        total_vars += 1
        pruned = total_possible - total_vars
        print(f"   📊 變數: {total_vars} 個 (原 {total_possible}, 過濾 {pruned}, "
              f"減少 {pruned/total_possible*100:.0f}%)")

    def _working_sum(self, e: int, d: int):
        """Sum of all shift variables for employee e on day d."""
        vals = self.vars[e][d].values()
        return sum(vals) if vals else 0

    def _shift_sum(self, d: int, i: int, employees=None):
        """Sum of shift index i across employees on day d. Returns None if no vars."""
        emps = employees if employees is not None else range(self.num_employees)
        vars_list = [self.vars[e][d][i] for e in emps if i in self.vars[e][d]]
        return sum(vars_list) if vars_list else None

    def add_hard_constraints(self, relax_level: int = 0):
        # HC1: At most 1 shift per employee per day
        for e in range(self.num_employees):
            for d in range(self.num_days):
                if self.vars[e][d]:
                    self.model.AddAtMostOne(self.vars[e][d].values())

        # HC2: At most max_working_days per week (一例一休: guarantee 2 rest days)
        max_wd = 5
        if self.tenant_config:
            max_wd = self.tenant_config.constraints.get("max_working_days", 5)
        for e in range(self.num_employees):
            all_vars = [v for d in range(self.num_days)
                        for v in self.vars[e][d].values()]
            if all_vars:
                self.model.Add(sum(all_vars) <= max_wd)

        # HC2b: At least min_working_days per week (if configured)
        min_wd = 0
        if self.tenant_config:
            min_wd = self.tenant_config.constraints.get("min_working_days", 0)
        if min_wd > 0:
            available_roles = set(self.workstation_roles.values()) if self.workstation_roles else set()
            for e in range(self.num_employees):
                emp_skills = set(self.habits[e].workstation_skills or [])
                if not (emp_skills & available_roles):
                    continue
                all_vars = [v for d in range(self.num_days)
                            for v in self.vars[e][d].values()]
                if all_vars:
                    self.model.Add(sum(all_vars) >= min_wd)

        # HC3: Min rest between consecutive shifts (gap-based forbidden pairs)
        min_rest_mins = self.min_rest_hours * 60
        forbidden_pairs = []  # (shift_index_today, shift_index_tomorrow)
        for i, sc_i in enumerate(self.shift_codes):
            end_i = self._shift_end_minutes(sc_i)
            for j, sc_j in enumerate(self.shift_codes):
                start_j = self._shift_start_minutes(sc_j)
                gap = (24 * 60 + start_j) - end_i
                if gap < min_rest_mins:
                    forbidden_pairs.append((i, j))
        for e in range(self.num_employees):
            for d in range(self.num_days - 1):
                for i, j in forbidden_pairs:
                    vi = self.vars[e][d].get(i)
                    vj = self.vars[e][d+1].get(j)
                    if vi is not None and vj is not None:
                        self.model.Add(vi + vj <= 1)

        # HC3-cross: Forbid day-0 shifts that violate min rest with prev week's last shift
        if self.prev_tail:
            for e, habit in enumerate(self.habits):
                tail = self.prev_tail.get(habit.employee_id)
                if not tail or not tail.get("last_shift"):
                    continue
                prev_end = self._shift_end_minutes(tail["last_shift"])
                for j, sc_j in enumerate(self.shift_codes):
                    vj = self.vars[e][0].get(j)
                    if vj is None:
                        continue
                    start_j = self._shift_start_minutes(sc_j)
                    gap = (24 * 60 + start_j) - prev_end
                    if gap < min_rest_mins:
                        self.model.Add(vj == 0)

        # HC4: Weekly hours cap
        for e in range(self.num_employees):
            terms = [(v, int(self._shift_hours(self.shift_codes[i]) * 10))
                     for d in range(self.num_days)
                     for i, v in self.vars[e][d].items()]
            if terms:
                weekly_hours_x10 = sum(v * w for v, w in terms)
                self.model.Add(weekly_hours_x10 <= int(self.max_weekly_hours * 10))

        # HC5 + HC6 + HC13 + HC15: handled at build time by _is_valid_assignment()

        # HC7: Manager coverage constraints (at least N managers in early/late shifts)
        if self.manager_config and self.manager_config.get("member_ids"):
            mgr_ids = set(self.manager_config["member_ids"])
            daily_early = self.manager_config.get("daily_early_count", 1)
            daily_late = self.manager_config.get("daily_late_count", 1)

            # Find manager employee indices
            mgr_indices = [e for e, h in enumerate(self.habits)
                           if h.employee_id in mgr_ids]

            # Early/late shifts from config, fallback to hour-based threshold
            early_codes = set(self.manager_config.get("early_shifts", []))
            late_codes = set(self.manager_config.get("late_shifts", []))
            early_threshold = self.manager_config.get("early_hour_threshold", 12)
            late_threshold = self.manager_config.get("late_hour_threshold", 14)

            if early_codes:
                early_shift_indices = [i for i, sc in enumerate(self.shift_codes) if sc in early_codes]
            else:
                early_shift_indices = [i for i, sc in enumerate(self.shift_codes)
                                       if sc in self.shift_defs and self._shift_start_hour(sc) <= early_threshold]

            if late_codes:
                late_shift_indices = [i for i, sc in enumerate(self.shift_codes) if sc in late_codes]
            else:
                late_shift_indices = [i for i, sc in enumerate(self.shift_codes)
                                      if sc in self.shift_defs and self._shift_start_hour(sc) >= late_threshold]

            if mgr_indices:
                for d in range(self.num_days):
                    if relax_level < 2:
                        if early_shift_indices:
                            early_vars = [self.vars[e][d][i]
                                          for e in mgr_indices
                                          for i in early_shift_indices
                                          if i in self.vars[e][d]]
                            if early_vars:
                                self.model.Add(sum(early_vars) >= daily_early)
                        if late_shift_indices:
                            late_vars = [self.vars[e][d][i]
                                         for e in mgr_indices
                                         for i in late_shift_indices
                                         if i in self.vars[e][d]]
                            if late_vars:
                                self.model.Add(sum(late_vars) >= daily_late)

        # HC9: Forbidden same-day rest pairs
        no_same_rest = self.manager_config.get("no_same_rest", [])
        for pair in no_same_rest:
            idx_a = next((e for e, h in enumerate(self.habits) if h.employee_id == pair[0]), None)
            idx_b = next((e for e, h in enumerate(self.habits) if h.employee_id == pair[1]), None)
            if idx_a is not None and idx_b is not None:
                for d in range(self.num_days):
                    working_a = self._working_sum(idx_a, d)
                    working_b = self._working_sum(idx_b, d)
                    # At least one must work: working_a + working_b >= 1
                    self.model.Add(working_a + working_b >= 1)

        # HC11: Max consecutive working days (from tenant_config.constraints.max_consecutive_working_days)
        # Relaxed at level >= 2 (falls back to soft penalty in build_objective SC6)
        max_consec = 4  # default
        if self.tenant_config:
            max_consec = self.tenant_config.constraints.get("max_consecutive_working_days", 4)
        self._has_hard_rhythm = (relax_level < 2 and max_consec > 0)
        if self._has_hard_rhythm:
            window = max_consec + 1
            for e in range(self.num_employees):
                for start_d in range(self.num_days - window + 1):
                    window_vars = [v for d in range(start_d, start_d + window)
                                   for v in self.vars[e][d].values()]
                    if window_vars:
                        self.model.Add(sum(window_vars) <= max_consec)

        # HC12: No isolated rest days (forbid work-rest-work pattern)
        # Relaxed at level >= 2 (falls back to soft penalty in build_objective SC7)
        if relax_level < 2:
            for e in range(self.num_employees):
                for d in range(1, self.num_days - 1):
                    # If day d-1 working and day d+1 working, then day d must be working
                    # Equivalent: NOT(d-1 working AND d rest AND d+1 working)
                    w_prev = self.model.NewBoolVar(f"hc12_wp_{e}_{d}")
                    w_next = self.model.NewBoolVar(f"hc12_wn_{e}_{d}")
                    r_curr = self.model.NewBoolVar(f"hc12_rc_{e}_{d}")
                    self.model.Add(
                        self._working_sum(e, d-1) >= 1
                    ).OnlyEnforceIf(w_prev)
                    self.model.Add(
                        self._working_sum(e, d-1) == 0
                    ).OnlyEnforceIf(w_prev.Not())
                    self.model.Add(
                        self._working_sum(e, d) == 0
                    ).OnlyEnforceIf(r_curr)
                    self.model.Add(
                        self._working_sum(e, d) >= 1
                    ).OnlyEnforceIf(r_curr.Not())
                    self.model.Add(
                        self._working_sum(e, d+1) >= 1
                    ).OnlyEnforceIf(w_next)
                    self.model.Add(
                        self._working_sum(e, d+1) == 0
                    ).OnlyEnforceIf(w_next.Not())
                    # Forbid: w_prev AND r_curr AND w_next
                    self.model.AddBoolOr([w_prev.Not(), r_curr.Not(), w_next.Not()])

        # HC5/HC6/HC13/HC15: handled at build time by _is_valid_assignment()

        # HC14: Time-point coverage constraints (active_at mode)
        self._add_timepoint_coverage_constraints(relax_level)

        # HC16: Minimum FT employees per day (skip closure days)
        min_ft = 0
        if self.tenant_config:
            min_ft = self.tenant_config.constraints.get("min_ft_per_day", 0)
        if min_ft > 0:
            for d in range(self.num_days):
                if d in self.closure_days:
                    continue
                ft_working = []
                for e, habit in enumerate(self.habits):
                    if habit.employee_type == "ft":
                        ft_working.extend(self.vars[e][d].values())
                if ft_working:
                    self.model.Add(sum(ft_working) >= min_ft)

        # HC17: Daily total hours band (min/max)
        daily_h_min = 0
        daily_h_max = 0
        if self.tenant_config:
            daily_h_min = self.tenant_config.constraints.get("daily_total_hours_min", 0)
            daily_h_max = self.tenant_config.constraints.get("daily_total_hours_max", 0)
        if daily_h_min > 0 or daily_h_max > 0:
            for d in range(self.num_days):
                if d in self.closure_days:
                    continue
                # Sum hours × 10 (integer) for all employees on this day
                day_hours_x10 = sum(
                    v * int(self._shift_hours(self.shift_codes[i]) * 10)
                    for e in range(self.num_employees)
                    for i, v in self.vars[e][d].items()
                )
                if daily_h_max > 0:
                    if relax_level == 0:
                        self.model.Add(day_hours_x10 <= int(daily_h_max * 10))
                    else:
                        over = self.model.NewIntVar(
                            0, self.num_employees * 100,
                            f"dh_over_{d}")
                        self.model.Add(over >= day_hours_x10 - int(daily_h_max * 10))
                        if not hasattr(self, '_headcount_penalties'):
                            self._headcount_penalties = []
                        self._headcount_penalties.append(
                            (over, self.weights["W_HEADCOUNT"]))
                if daily_h_min > 0:
                    if relax_level == 0:
                        self.model.Add(day_hours_x10 >= int(daily_h_min * 10))
                    else:
                        under = self.model.NewIntVar(
                            0, self.num_employees * 100,
                            f"dh_under_{d}")
                        self.model.Add(under >= int(daily_h_min * 10) - day_hours_x10)
                        if not hasattr(self, '_headcount_penalties'):
                            self._headcount_penalties = []
                        self._headcount_penalties.append(
                            (under, self.weights["W_HEADCOUNT"]))

        # HC10: Minimum daily per-role coverage (e.g., 領檯早 >= 1, 領檯晚 >= 1)
        self._add_role_coverage_constraints(relax_level)

        # HC8: Minimum daily headcount
        self._add_headcount_constraints(relax_level)

    def _add_timepoint_coverage_constraints(self, relax_level: int = 0):
        """HC14: Time-point coverage constraints.
        For each coverage_target with match='active_at', find all shifts active
        at that time point (start <= T < end) and require >= min staff."""
        if not self.tenant_config or not self.tenant_config.coverage_targets:
            return

        W_HEADCOUNT = self.weights["W_HEADCOUNT"]

        for target_time, rules in self.tenant_config.coverage_targets.items():
            if rules.get("match") != "active_at":
                continue
            min_staff = rules.get("min", 1)
            label = rules.get("label", target_time)

            # Parse target time to minutes
            tp = target_time.split(":")
            t_minutes = int(tp[0]) * 60 + int(tp[1])

            # Find shift indices active at this time point
            active_indices = []
            for i, sc in enumerate(self.shift_codes):
                s_start = self._shift_start_minutes(sc)
                s_end = self._shift_end_minutes(sc)
                if s_start <= t_minutes < s_end:
                    active_indices.append(i)

            if not active_indices:
                print(f"   ⚠️ HC14: 時間點 {target_time} ({label}) 無對應 active 班次")
                continue

            for d in range(self.num_days):
                if d in self.closure_days:
                    continue
                total_vars = [self.vars[e][d][i]
                              for e in range(self.num_employees)
                              for i in active_indices
                              if i in self.vars[e][d]]
                if not total_vars:
                    continue  # no vars can cover this time point
                total = sum(total_vars)
                if relax_level == 0:
                    self.model.Add(total >= min_staff)
                else:
                    deficit = self.model.NewIntVar(
                        0, self.num_employees,
                        f"tp_deficit_{target_time.replace(':', '')}_{d}")
                    self.model.Add(deficit >= min_staff - total)
                    if not hasattr(self, '_headcount_penalties'):
                        self._headcount_penalties = []
                    self._headcount_penalties.append((deficit, W_HEADCOUNT))

    def _add_role_coverage_constraints(self, relax_level: int = 0):
        """Add minimum daily per-role coverage constraints (HC10).
        Reads min_role_per_day from tenant_config: {role: min_count}.
        For each role, finds all shift codes mapped to that role and ensures
        the daily sum of assignments meets the minimum."""
        if not self.tenant_config or not self.tenant_config.min_role_per_day:
            return

        for role, min_count in self.tenant_config.min_role_per_day.items():
            if min_count <= 0:
                continue
            # Find shift code indices that map to this role
            role_shift_indices = []
            for i, sc in enumerate(self.shift_codes):
                sc_role = self.workstation_roles.get(sc)
                if sc_role == role:
                    role_shift_indices.append(i)

            if not role_shift_indices:
                print(f"   ⚠️ HC10: 角色 '{role}' 在 min_role_per_day 中指定，"
                      f"但無對應的班次代碼")
                continue

            for d in range(self.num_days):
                if d in self.closure_days:
                    continue
                role_staff = sum(
                    self.vars[e][d][i]
                    for e in range(self.num_employees)
                    for i in role_shift_indices
                    if i in self.vars[e][d]
                )
                if relax_level == 0:
                    self.model.Add(role_staff >= min_count)
                else:
                    # Soft: allow deficit but penalize
                    deficit = self.model.NewIntVar(
                        0, self.num_employees,
                        f"role_deficit_{role}_d{d}")
                    self.model.Add(deficit >= min_count - role_staff)
                    if not hasattr(self, '_headcount_penalties'):
                        self._headcount_penalties = []
                    self._headcount_penalties.append((deficit, self.weights["W_HEADCOUNT"]))

    def _add_headcount_constraints(self, relax_level: int = 0):
        """Add minimum daily total headcount constraints (HC8).
        Supports differentiated headcounts: weekday, saturday, sunday, package.
        If manager_config.exclude_from_headcount is true, managers are excluded
        from the headcount (RULES.md §3: 值班主管不計算在每日人數編制內)."""
        if not self.min_daily_headcount:
            return

        W_HEADCOUNT = self.weights["W_HEADCOUNT"]
        weekday_min = self.min_daily_headcount.get("weekday", 0)
        saturday_min = self.min_daily_headcount.get("saturday",
                           self.min_daily_headcount.get("weekend", 0))
        sunday_min = self.min_daily_headcount.get("sunday",
                         self.min_daily_headcount.get("weekend", 0))
        package_min = self.min_daily_headcount.get("package", 0)

        # Determine which employees to exclude from headcount (Rule C)
        exclude_from_hc = set()
        if self.manager_config.get("exclude_from_headcount"):
            mgr_ids = set(self.manager_config.get("member_ids", []))
            exclude_from_hc = {e for e, h in enumerate(self.habits)
                               if h.employee_id in mgr_ids}

        countable_employees = [e for e in range(self.num_employees)
                               if e not in exclude_from_hc]

        for d in range(self.num_days):
            if d in self.closure_days:
                continue
            date_str = self._date_for_day(d)
            dt = self.week_start + timedelta(days=d)
            is_pkg = date_str in self.package_dates

            if dt.weekday() == 5:  # Saturday
                required = saturday_min
            elif dt.weekday() == 6 or date_str in self.holidays:  # Sunday / holiday
                required = sunday_min
            elif is_pkg:
                required = max(weekday_min, package_min)
            else:
                required = weekday_min
            if required <= 0:
                continue

            total_staff = sum(
                v for e in countable_employees
                for v in self.vars[e][d].values()
            )

            if relax_level == 0 and not self._has_hard_rhythm:
                # Hard constraint (only when rhythm constraints are not hard,
                # to avoid INFEASIBLE from HC8+HC11+HC12 combination)
                self.model.Add(total_staff >= required)
            else:
                # Soft penalty: headcount is best-effort when rhythm is hard,
                # or at relax_level >= 1
                deficit = self.model.NewIntVar(0, self.num_employees,
                                               f"hc_deficit_d{d}")
                self.model.Add(deficit >= required - total_staff)
                if not hasattr(self, '_headcount_penalties'):
                    self._headcount_penalties = []
                self._headcount_penalties.append((deficit, W_HEADCOUNT))

    def add_demand_constraints(self, relax: bool = False):
        """
        For each day, for each required shift code, ensure total assigned >= required.
        If relax=True, only enforce >= 1 (minimum viable).
        Closure days are skipped entirely.
        """
        for d in range(self.num_days):
            if d in self.closure_days:
                continue
            reqs = self.day_requirements[d]
            # Combine requirements by shift_code (sum across roles for same code)
            code_required = defaultdict(int)
            for req in reqs:
                code_required[req["shift_code"]] += req["required"]

            for sc, required in code_required.items():
                if sc not in self.sc_idx:
                    continue
                i = self.sc_idx[sc]
                staff_sum = self._shift_sum(d, i)
                if staff_sum is None:
                    continue  # no employee can take this shift
                min_required = 1 if relax else required
                self.model.Add(staff_sum >= min_required)

    def build_objective(self, relax_level: int = 0) -> list:
        penalties = []

        # ── Weight constants (from self.weights, overridable via weights_override) ──
        W_VAC = self.weights["W_VAC"]
        W_FAIRNESS = self.weights["W_FAIRNESS"]
        W_PREF = self.weights["W_PREF"]
        W_EMPLOYEE_SOFT = self.weights["W_EMPLOYEE_SOFT"]
        W_SHIFT = self.weights["W_SHIFT"]

        # ── SC1: Demand coverage (penalize both shortage and overstaffing) ──
        for d in range(self.num_days):
            if d in self.closure_days:
                continue
            reqs = self.day_requirements[d]
            code_required = defaultdict(int)
            for req in reqs:
                code_required[req["shift_code"]] += req["required"]

            for sc, required in code_required.items():
                if sc not in self.sc_idx:
                    continue
                if required <= 0:
                    continue
                i = self.sc_idx[sc]
                target = max(1, required) if relax_level >= 2 else required
                staff_sum = self._shift_sum(d, i)
                if staff_sum is None:
                    continue
                shortage = self.model.NewIntVar(0, self.num_employees,
                                               f"shortage_{sc}_d{d}")
                self.model.Add(shortage >= target - staff_sum)
                penalties.append((shortage, W_VAC))
                surplus = self.model.NewIntVar(0, self.num_employees,
                                              f"surplus_{sc}_d{d}")
                self.model.Add(surplus >= staff_sum - target)
                penalties.append((surplus, W_FAIRNESS))

            # Penalize assigning shifts that have zero demand on this day
            # Skip auto-generated PT shifts (PT_*) — they serve coverage, not demand
            codes_with_demand = {sc for sc, r in code_required.items() if r > 0}
            for i, sc in enumerate(self.shift_codes):
                if sc not in codes_with_demand and not sc.startswith("PT_"):
                    for e in range(self.num_employees):
                        v = self.vars[e][d].get(i)
                        if v is not None:
                            penalties.append((v, W_VAC))

        # ── SC2: Shift preference (fixed: supports shift codes & time labels) ──
        if relax_level < 1 and self.enforce_preferences:
            for e, habit in enumerate(self.habits):
                if not habit.preferred_shifts:
                    continue
                is_code = self._prefs_are_shift_codes(habit.preferred_shifts)

                for i, sc in enumerate(self.shift_codes):
                    if is_code:
                        # Direct shift-code matching
                        if sc in habit.preferred_shifts:
                            pref_rank = habit.preferred_shifts.index(sc)
                        else:
                            pref_rank = len(habit.preferred_shifts) + 1
                    else:
                        # Time-bucket matching (original logic)
                        bucket = self._shift_time_bucket(sc)
                        if bucket in habit.preferred_shifts:
                            pref_rank = habit.preferred_shifts.index(bucket)
                        else:
                            pref_rank = len(habit.preferred_shifts) + 1

                    if pref_rank > 0:
                        for d in range(self.num_days):
                            v = self.vars[e][d].get(i)
                            if v is not None:
                                penalties.append((v, pref_rank * W_PREF))

        # ── SC3: Fairness — personalised target from avg_shifts_per_week ──
        max_wd = self.tenant_config.constraints.get("max_working_days", 5) if self.tenant_config else 5
        for e, habit in enumerate(self.habits):
            avg = habit.avg_shifts_per_week
            if avg and avg > 0:
                # Values > 7 are likely biweekly totals → halve them
                target_raw = avg / 2.0 if avg > 7 else avg
                target_days = max(1, min(max_wd + 1, round(target_raw)))
            else:
                target_days = max_wd  # default for employees with no data

            total = sum(
                v for d in range(self.num_days)
                for v in self.vars[e][d].values()
            )
            # Penalise both over-scheduling and under-scheduling
            overwork = self.model.NewIntVar(0, self.num_days, f"overwork_{e}")
            self.model.Add(overwork >= total - target_days)
            penalties.append((overwork, W_FAIRNESS))

            underwork = self.model.NewIntVar(0, self.num_days, f"underwork_{e}")
            self.model.Add(underwork >= target_days - total)
            penalties.append((underwork, W_FAIRNESS))

        # ── SC4: Avoid late shifts for no-overtime employees ──
        if relax_level < 1:
            late_indices = [i for i, sc in enumerate(self.shift_codes)
                            if self._is_late_shift(sc)]
            for e, habit in enumerate(self.habits):
                if habit.overtime_willingness == "no_overtime":
                    for d in range(self.num_days):
                        for i in late_indices:
                            v = self.vars[e][d].get(i)
                            if v is not None:
                                penalties.append((v, W_EMPLOYEE_SOFT))

        # ── SC5: Shift frequency preference (new) ──
        if relax_level < 1 and self.enforce_preferences:
            for e, habit in enumerate(self.habits):
                freq = habit.shift_frequency
                if not freq:
                    continue
                max_freq = max(freq.values()) if freq else 0
                if max_freq <= 0:
                    continue

                # Detect whether keys are shift codes or time buckets
                freq_is_code = self._prefs_are_shift_codes(list(freq.keys()))

                for i, sc in enumerate(self.shift_codes):
                    if freq_is_code:
                        f_val = freq.get(sc, 0)
                    else:
                        bucket = self._shift_time_bucket(sc)
                        f_val = freq.get(bucket, 0)

                    # Penalty inversely proportional to historical frequency
                    penalty = int(W_SHIFT * 10 * (max_freq - f_val) / max_freq)
                    if penalty > 0:
                        for d in range(self.num_days):
                            v = self.vars[e][d].get(i)
                            if v is not None:
                                penalties.append((v, penalty))

        # ── SC6: 避免連上五天 (penalize 5 consecutive working days) ──
        W_CONSEC5 = self.weights["W_CONSEC5"]
        for e in range(self.num_employees):
            for start_d in range(self.num_days - 4):
                window_working = []
                for d in range(start_d, start_d + 5):
                    w = self.model.NewBoolVar(f"working_{e}_{d}_sc6_{start_d}")
                    self.model.Add(self._working_sum(e, d) >= 1).OnlyEnforceIf(w)
                    self.model.Add(self._working_sum(e, d) == 0).OnlyEnforceIf(w.Not())
                    window_working.append(w)
                # all_five = 1 only when all 5 days are working
                all_five = self.model.NewBoolVar(f"consec5_{e}_{start_d}")
                self.model.AddBoolAnd(window_working).OnlyEnforceIf(all_five)
                self.model.AddBoolOr([w.Not() for w in window_working]).OnlyEnforceIf(all_five.Not())
                penalties.append((all_five, W_CONSEC5))

        # ── SC6-cross: Cross-week consecutive 5 days (prev week tail + current week start) ──
        if self.prev_tail:
            for e, habit in enumerate(self.habits):
                tail = self.prev_tail.get(habit.employee_id)
                if not tail:
                    continue
                prev_working = tail["working"]  # [day-4, day-3, day-2, day-1]
                # 4 cross-week windows: offset 0..3
                # offset=0: prev[-4,-3,-2,-1] + curr[0] → need all 5
                # offset=1: prev[-3,-2,-1] + curr[0,1] → need all 5
                # offset=2: prev[-2,-1] + curr[0,1,2] → need all 5
                # offset=3: prev[-1] + curr[0,1,2,3] → need all 5
                for offset in range(4):
                    prev_start = offset  # index into prev_working (0-based)
                    prev_days_needed = 4 - offset  # how many prev days in the window
                    curr_days_needed = offset + 1  # how many curr days in the window

                    if curr_days_needed > self.num_days:
                        continue

                    # Check if all prev days in the window are working
                    all_prev_working = all(prev_working[prev_start + k]
                                           for k in range(prev_days_needed))
                    if not all_prev_working:
                        continue  # At least one prev day is rest, skip

                    # Build constraint for the curr days part
                    curr_working_vars = []
                    for cd in range(curr_days_needed):
                        w = self.model.NewBoolVar(f"working_cross_{e}_{offset}_{cd}")
                        self.model.Add(
                            self._working_sum(e, cd) >= 1
                        ).OnlyEnforceIf(w)
                        self.model.Add(
                            self._working_sum(e, cd) == 0
                        ).OnlyEnforceIf(w.Not())
                        curr_working_vars.append(w)

                    all_curr = self.model.NewBoolVar(f"consec5_cross_{e}_{offset}")
                    self.model.AddBoolAnd(curr_working_vars).OnlyEnforceIf(all_curr)
                    self.model.AddBoolOr(
                        [w.Not() for w in curr_working_vars]
                    ).OnlyEnforceIf(all_curr.Not())
                    penalties.append((all_curr, W_CONSEC5))

        # ── SC7: 避免做一休一 (penalize work-rest-work isolated pattern) ──
        W_ALTERNATE = self.weights["W_ALTERNATE"]
        for e in range(self.num_employees):
            for d in range(1, self.num_days - 1):
                # Detect: day d-1 working, day d rest, day d+1 working
                w_prev = self.model.NewBoolVar(f"wp_{e}_{d}")
                r_curr = self.model.NewBoolVar(f"rc_{e}_{d}")
                w_next = self.model.NewBoolVar(f"wn_{e}_{d}")

                self.model.Add(self._working_sum(e, d-1) >= 1).OnlyEnforceIf(w_prev)
                self.model.Add(self._working_sum(e, d-1) == 0).OnlyEnforceIf(w_prev.Not())
                self.model.Add(self._working_sum(e, d) == 0).OnlyEnforceIf(r_curr)
                self.model.Add(self._working_sum(e, d) >= 1).OnlyEnforceIf(r_curr.Not())
                self.model.Add(self._working_sum(e, d+1) >= 1).OnlyEnforceIf(w_next)
                self.model.Add(self._working_sum(e, d+1) == 0).OnlyEnforceIf(w_next.Not())

                alt_pattern = self.model.NewBoolVar(f"alt_{e}_{d}")
                self.model.AddBoolAnd([w_prev, r_curr, w_next]).OnlyEnforceIf(alt_pattern)
                self.model.AddBoolOr([w_prev.Not(), r_curr.Not(), w_next.Not()]).OnlyEnforceIf(alt_pattern.Not())
                penalties.append((alt_pattern, W_ALTERNATE))

        # ── SC7-cross: Cross-week work-rest-work pattern ──
        # Higher weight than intra-week SC7 because cross-week patterns are
        # harder for humans to spot and the solver has more freedom to avoid them
        W_ALT_CROSS = self.weights["W_ALT_CROSS"]
        if self.prev_tail:
            for e, habit in enumerate(self.habits):
                tail = self.prev_tail.get(habit.employee_id)
                if not tail:
                    continue
                prev_working = tail["working"]  # [day-4, day-3, day-2, day-1]

                # Triplet 1: prev[-2] working, prev[-1] rest → curr day0 working = penalty
                if len(prev_working) >= 2 and prev_working[-2] and not prev_working[-1]:
                    # day0 working is the only variable
                    w_day0 = self.model.NewBoolVar(f"sc7_cross1_w0_{e}")
                    self.model.Add(
                        self._working_sum(e, 0) >= 1
                    ).OnlyEnforceIf(w_day0)
                    self.model.Add(
                        self._working_sum(e, 0) == 0
                    ).OnlyEnforceIf(w_day0.Not())
                    penalties.append((w_day0, W_ALT_CROSS))

                # Triplet 2: prev[-1] working → curr day0 rest, curr day1 working = penalty
                if prev_working[-1] and self.num_days >= 2:
                    r_day0 = self.model.NewBoolVar(f"sc7_cross2_r0_{e}")
                    w_day1 = self.model.NewBoolVar(f"sc7_cross2_w1_{e}")

                    self.model.Add(
                        self._working_sum(e, 0) == 0
                    ).OnlyEnforceIf(r_day0)
                    self.model.Add(
                        self._working_sum(e, 0) >= 1
                    ).OnlyEnforceIf(r_day0.Not())

                    self.model.Add(
                        self._working_sum(e, 1) >= 1
                    ).OnlyEnforceIf(w_day1)
                    self.model.Add(
                        self._working_sum(e, 1) == 0
                    ).OnlyEnforceIf(w_day1.Not())

                    alt_cross = self.model.NewBoolVar(f"alt_cross2_{e}")
                    self.model.AddBoolAnd([r_day0, w_day1]).OnlyEnforceIf(alt_cross)
                    self.model.AddBoolOr([r_day0.Not(), w_day1.Not()]).OnlyEnforceIf(alt_cross.Not())
                    penalties.append((alt_cross, W_ALT_CROSS))

        # ── SC8: PT (兼職) prefer evening shifts ──
        # Penalize PT employees being assigned to non-evening shifts (start < 17:00)
        W_PT_EVENING = self.weights["W_PT_EVENING"]
        if relax_level < 1:
            pt_min_hour = self.tenant_config.constraints.get("pt_min_shift_hour", 17) if self.tenant_config else 17
            non_evening_indices = [i for i, sc in enumerate(self.shift_codes)
                                   if self._shift_start_hour(sc) < pt_min_hour]
            for e, habit in enumerate(self.habits):
                if habit.employee_type == "pt":
                    for d in range(self.num_days):
                        for i in non_evening_indices:
                            v = self.vars[e][d].get(i)
                            if v is not None:
                                penalties.append((v, W_PT_EVENING))

        # ── SC9: PT hours incentive — prefer longer PT shifts ──
        W_PT_HOURS = self.weights.get("W_PT_HOURS", 3)
        if W_PT_HOURS > 0 and any(sc.startswith("PT_") for sc in self.shift_codes):
            for e, habit in enumerate(self.habits):
                if habit.employee_type != "pt":
                    continue
                for d in range(self.num_days):
                    if d in self.closure_days:
                        continue
                    for i, v in self.vars[e][d].items():
                        sc = self.shift_codes[i]
                        if sc.startswith("PT_"):
                            hours_x10 = int(self._shift_hours(sc) * 10)
                            penalties.append((v, -W_PT_HOURS * hours_x10))

        return penalties

    def solve(self, time_limit_seconds: int = 60) -> tuple:
        """
        Solve with automatic retry at 3 relaxation levels.
        Returns (status, entries, stats, relax_level).
        """
        best_result = None

        for relax_level in range(3):
            if relax_level > 0:
                print(f"⚠️  求解失敗，嘗試放寬限制 (等級 {relax_level})...")
                self.model = cp_model.CpModel()
                self.vars = {}
                self._headcount_penalties = []

            self._headcount_penalties = []
            self.build_variables()
            self.add_hard_constraints(relax_level=relax_level)
            self.add_demand_constraints(relax=(relax_level >= 2))

            penalties = self.build_objective(relax_level)
            # Include headcount soft penalties (from HC8 at relax_level >= 1)
            penalties.extend(self._headcount_penalties)
            if penalties:
                self.model.Minimize(sum(w * v for v, w in penalties))

            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = time_limit_seconds
            solver.parameters.log_search_progress = False

            status = solver.Solve(self.model)

            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                entries = self._extract_schedule(solver)
                stats = self._compute_stats(solver)
                best_result = (status, entries, stats, relax_level)
                label = "最優解" if status == cp_model.OPTIMAL else "可行解"
                print(f"✅ 求解成功 ({label}, 放寬等級={relax_level})")
                break
            else:
                print(f"   ❌ 放寬等級 {relax_level} 仍無解")

        if best_result is None:
            print("❌ 無法找到可行解，請檢查班次設定或員工資料")
            return (cp_model.INFEASIBLE, [], {}, -1)

        return best_result

    def _extract_schedule(self, solver) -> list:
        entries = []
        for e, habit in enumerate(self.habits):
            for d in range(self.num_days):
                for i, v in self.vars[e][d].items():
                    if solver.Value(v) == 1:
                        sc = self.shift_codes[i]
                        defn = self.shift_defs.get(sc, {})
                        role = shift_code_to_role(sc, self.workstation_roles)
                        entry = ScheduleEntry(
                            date=self._date_for_day(d),
                            day_of_week=DAYS_TW[d],
                            employee_id=habit.employee_id,
                            employee_name=f"{habit.chinese_name} ({habit.english_name})",
                            shift_start=defn.get("start", ""),
                            shift_end=defn.get("end", ""),
                            workstation=sc,
                            workstation_role=role,
                        )
                        entries.append(entry)
        return entries

    def _compute_stats(self, solver) -> dict:
        emp_hours = {}
        shift_code_daily = {sc: [0] * self.num_days for sc in self.shift_codes}

        for e, habit in enumerate(self.habits):
            total_hours = 0.0
            for d in range(self.num_days):
                for i, v in self.vars[e][d].items():
                    if solver.Value(v) == 1:
                        sc = self.shift_codes[i]
                        total_hours += self._shift_hours(sc)
                        shift_code_daily[sc][d] += 1
            emp_hours[habit.employee_id] = total_hours

        return {
            "employee_weekly_hours": emp_hours,
            "daily_coverage_by_shift": shift_code_daily,
            "day_scenarios": self.day_scenarios,
        }


# ─── Output ───────────────────────────────────────────────────────────────────

def save_schedule_csv(entries: list, output_path: str):
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=[
            "date", "day_of_week", "employee_id", "employee_name",
            "shift_start", "shift_end", "workstation", "workstation_role",
            "leave_type"
        ])
        writer.writeheader()
        for e in entries:
            writer.writerow({
                "date": e.date,
                "day_of_week": e.day_of_week,
                "employee_id": e.employee_id,
                "employee_name": e.employee_name,
                "shift_start": e.shift_start,
                "shift_end": e.shift_end,
                "workstation": e.workstation or "",
                "workstation_role": e.workstation_role or "",
                "leave_type": e.leave_type or "",
            })
    print(f"✅ 班表已儲存至 {output_path} ({len(entries)} 筆)")


def save_schedule_json(entries: list, stats: dict, output_path: str):
    data = {
        "schedule": [
            {
                "date": e.date,
                "day_of_week": e.day_of_week,
                "employee_id": e.employee_id,
                "employee_name": e.employee_name,
                "shift_start": e.shift_start,
                "shift_end": e.shift_end,
                "workstation": e.workstation,
                "workstation_role": e.workstation_role,
                "leave_type": e.leave_type,
            }
            for e in entries
        ],
        "stats": stats,
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 詳細資料已儲存至 {output_path}")


# ─── Main Pipeline ─────────────────────────────────────────────────────────────

def run_scheduler(habits_path: str, demand_path: str,
                  output_prefix: str = "schedule",
                  week_start: str = None,
                  tenant_dir: str = None,
                  rest_days_path: str = None,
                  prev_schedule_path: str = None,
                  weights_override: dict = None):
    print("=" * 60)
    print("📅 排班求解器 (Demand-Aware Scheduler)")
    print("=" * 60)

    # Load tenant config (single source of truth for all tenant-specific settings)
    tenant_config = None
    if tenant_dir:
        tenant_config = load_tenant_config(tenant_dir)
        print(f"🏪 租戶: {tenant_config.display_name} ({tenant_config.tenant_id})")

    # Load habits
    habits = load_habits_json(habits_path)
    print(f"📂 載入 {len(habits)} 位員工習慣資料")

    # Load manager constraints from tenant_config + habits.is_manager
    manager_config = {}
    if tenant_config:
        manager_config = load_manager_constraints(tenant_config, habits)
        if manager_config.get("member_ids"):
            print(f"👔 主管設定: {manager_config['member_ids']}")

    # Load demand profile
    demand_profile = load_demand_profile(demand_path)
    print(f"📋 載入需求分析: {list(demand_profile.keys())}")

    # Load package dates
    package_dates = load_package_dates(tenant_dir) if tenant_dir else set()
    if package_dates:
        print(f"📅 包場日期: {sorted(package_dates)}")

    # Default week: next Monday
    if not week_start:
        today = datetime.today()
        days_ahead = (7 - today.weekday()) % 7 or 7
        week_start = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # Load availability (preferred) or fallback to rest_days.json
    rest_days = {}
    pt_availability = {}
    if tenant_dir:
        rest_days, pt_availability = load_availability(tenant_dir, week_start)
    elif rest_days_path:
        rest_days = load_rest_days(rest_days_path, week_start)
    if rest_days:
        print(f"🛌 指定劃休: {len(rest_days)} 位員工有指定休假日")
    if pt_availability:
        print(f"📋 PT 可用時段: {len(pt_availability)} 位兼職員工")

    # Load previous week schedule for cross-week constraints
    prev_tail = load_prev_tail(prev_schedule_path, habits) if prev_schedule_path else {}
    if prev_tail:
        print(f"📎 前週班表: 載入 {len(prev_tail)} 位員工跨週資料")

    # min_daily_headcount from tenant config, fallback to defaults
    min_daily_hc = (tenant_config.min_daily_headcount if tenant_config
                    else {"weekday": 0, "saturday": 0, "sunday": 0, "package": 0})

    solver = DemandScheduleSolver(
        habits=habits,
        demand_profile=demand_profile,
        week_start_date=week_start,
        package_dates=package_dates,
        rest_days=rest_days,
        manager_config=manager_config,
        min_daily_headcount=min_daily_hc,
        prev_tail=prev_tail,
        tenant_config=tenant_config,
        weights_override=weights_override,
        pt_availability=pt_availability,
    )

    print(f"\n🔧 開始求解...")
    print(f"   員工數:  {len(habits)}")
    print(f"   班次代碼: {len(solver.shift_codes)} 種 ({', '.join(solver.shift_codes[:6])}…)")
    print(f"   週起始:  {solver.week_start.strftime('%Y-%m-%d')} ({DAYS_TW[solver.week_start.weekday()]})")
    print(f"\n   每日情境:")
    for d in range(7):
        date_str = solver._date_for_day(d)
        print(f"   {DAYS_TW[d]} {date_str}: {solver.day_scenarios[d]}")

    status, entries, stats, relax_level = solver.solve(time_limit_seconds=60)

    if not entries:
        print("❌ 求解失敗，無法產出班表")
        return

    # Summary
    print(f"\n📊 班表摘要:")
    print(f"   總排班數: {len(entries)}")

    if stats.get("employee_weekly_hours"):
        hours_list = list(stats["employee_weekly_hours"].values())
        print(f"   平均週工時: {sum(hours_list)/len(hours_list):.1f}h")

    if stats.get("daily_coverage_by_shift"):
        print(f"\n   每日班次人數 (前8個班次代碼):")
        for sc in solver.shift_codes[:8]:
            daily = stats["daily_coverage_by_shift"][sc]
            counts = " ".join(f"{c:2}" for c in daily)
            print(f"   {sc:<8} [{counts}]  (週總 {sum(daily)})")

    save_schedule_csv(entries, f"{output_prefix}.csv")
    save_schedule_json(entries, stats, f"{output_prefix}.json")

    print(f"\n{'=' * 60}")
    print(f"✅ 排班完成！(放寬等級={relax_level})")
    print(f"{'=' * 60}")


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python ortools_solver.py <habits.json> <demand_shift.json> [輸出前綴] [週起始] [tenant目錄] [前週班表]")
        print("範例:")
        print("  python ortools_solver.py tenants/glod-pig/output/habits.json tenants/glod-pig/output/habits_demand_shift.json schedule_0302 2026-03-02 tenants/glod-pig")
        sys.exit(1)

    habits_path        = sys.argv[1]
    demand_path        = sys.argv[2]
    output_prefix      = sys.argv[3] if len(sys.argv) > 3 else "schedule"
    week_start         = sys.argv[4] if len(sys.argv) > 4 else None
    tenant_dir         = sys.argv[5] if len(sys.argv) > 5 else None
    prev_schedule_path = sys.argv[6] if len(sys.argv) > 6 else None

    # Auto-resolve output prefix to tenant output dir
    if tenant_dir and output_prefix == "schedule":
        out_dir = os.path.join(tenant_dir, "output")
        os.makedirs(out_dir, exist_ok=True)
        tag = week_start.replace("-", "")[4:] if week_start else "auto"
        output_prefix = os.path.join(out_dir, f"schedule_{tag}")

    run_scheduler(habits_path, demand_path, output_prefix, week_start, tenant_dir,
                  prev_schedule_path=prev_schedule_path)
