#!/usr/bin/env python3
"""
auditor_tools.py — Auditor Agent: Schedule validation & compliance checking.

Checks a generated schedule against four priority levels:
  P0 (勞基法): Labor law — hard legal requirements
  Hard:        System hard constraints (skill match, no double booking)
  P1 (租戶規則): Tenant business rules
  P2 (員工偏好): Employee preferences

Scenarios covered:
  S6: Schedule Audit and Feedback Loop
"""

import csv
import json
import sys
import os
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_loader import load_habits_json, WORKSTATION_CODES
from ortools_solver import SHIFT_DEFS, is_holiday, load_rest_days, load_manager_config


# ─── Violation Model ─────────────────────────────────────────────────────────

class Violation:
    def __init__(self, priority: str, rule_id: str, description: str,
                 employee_id: str = None, date: str = None, suggestion: str = None):
        self.priority = priority       # "P0", "Hard", "P1", "P2"
        self.rule_id = rule_id         # e.g. "P0-001"
        self.description = description
        self.employee_id = employee_id
        self.date = date
        self.suggestion = suggestion

    def to_dict(self):
        return {
            "priority": self.priority,
            "rule_id": self.rule_id,
            "description": self.description,
            "employee_id": self.employee_id,
            "date": self.date,
            "suggestion": self.suggestion,
        }


# ─── Schedule Loader ─────────────────────────────────────────────────────────

def load_schedule_csv(path: str) -> list:
    """Load schedule entries from CSV."""
    entries = []
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            entries.append(row)
    return entries


def load_schedule_json(path: str) -> list:
    """Load schedule entries from JSON."""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get("schedule", data) if isinstance(data, dict) else data


def group_by_employee(entries: list) -> dict:
    """Group schedule entries by employee_id, sorted by date."""
    grouped = defaultdict(list)
    for e in entries:
        eid = e.get("employee_id", "unknown")
        grouped[eid].append(e)
    for eid in grouped:
        grouped[eid].sort(key=lambda x: x.get("date", ""))
    return grouped


def group_by_date_shift(entries: list) -> dict:
    """Group entries by (date, shift_start) to check daily coverage."""
    grouped = defaultdict(list)
    for e in entries:
        key = (e.get("date", ""), e.get("shift_start", ""))
        grouped[key].append(e)
    return grouped


def parse_time(t: str) -> int:
    """Parse time string to minutes since midnight (handles +1 for next day)."""
    if not t:
        return -1
    next_day = "+1" in t
    t = t.replace("+1", "").strip()
    try:
        parts = t.split(":")
        mins = int(parts[0]) * 60 + int(parts[1])
        if next_day:
            mins += 24 * 60
        return mins
    except (ValueError, IndexError):
        return -1


# ─── P0: Labor Law Checks ────────────────────────────────────────────────────

def check_p0_labor_law(grouped_by_emp: dict, rest_days: dict = None) -> list:
    """
    P0 — 勞基法 compliance checks.
    These are legal requirements; violations must be fixed immediately.
    """
    violations = []
    rest_days = rest_days or {}

    for eid, entries in grouped_by_emp.items():
        working_entries = [e for e in entries if not e.get("leave_type")]

        # P0-001: Max 12 hours per single shift (use SHIFT_DEFS hours, which deduct breaks)
        for e in working_entries:
            sc = e.get("workstation", "")
            defn = SHIFT_DEFS.get(sc)
            if defn:
                hours = defn["hours"]
            else:
                start = parse_time(e.get("shift_start", ""))
                end   = parse_time(e.get("shift_end", ""))
                if start < 0 or end < 0:
                    continue
                if end < start:
                    end += 24 * 60
                hours = (end - start) / 60.0
            if hours > 12:
                violations.append(Violation(
                    priority="P0",
                    rule_id="P0-001",
                    description=(f"員工 {eid} 在 {e['date']} 單日班次超過 12 小時"
                                 f"（{hours:.1f}h）"),
                    employee_id=eid,
                    date=e["date"],
                    suggestion="縮短班次時間或拆分為兩段班",
                ))

        # P0-002: Minimum 11 hours rest between consecutive shifts
        for i in range(len(working_entries) - 1):
            curr = working_entries[i]
            nxt  = working_entries[i + 1]
            curr_end   = parse_time(curr.get("shift_end", ""))
            nxt_start  = parse_time(nxt.get("shift_start", ""))
            if curr_end < 0 or nxt_start < 0:
                continue
            # Adjust for overnight shifts
            if "<" in curr.get("shift_end", ""):
                curr_end += 24 * 60

            # Calculate actual gap (accounting for different dates)
            try:
                d1 = datetime.strptime(curr["date"], "%Y-%m-%d")
                d2 = datetime.strptime(nxt["date"], "%Y-%m-%d")
                day_diff = (d2 - d1).days
            except (ValueError, KeyError):
                day_diff = 0

            gap_mins = (day_diff * 24 * 60 + nxt_start) - curr_end
            if 0 < gap_mins < 11 * 60:
                gap_h = gap_mins / 60
                violations.append(Violation(
                    priority="P0",
                    rule_id="P0-002",
                    description=(f"員工 {eid} 在 {curr['date']} 與 {nxt['date']} "
                                 f"之間休息時間不足 11 小時（{gap_h:.1f}h）"),
                    employee_id=eid,
                    date=nxt["date"],
                    suggestion="避免晚班後接早班，或增加中間間隔",
                ))

        # P0-003: Weekly hours must not exceed 46h (including overtime)
        dates = sorted(set(e["date"] for e in working_entries))
        if dates:
            # Group by ISO week
            weeks = defaultdict(list)
            for e in working_entries:
                try:
                    d = datetime.strptime(e["date"], "%Y-%m-%d")
                    week_key = d.strftime("%Y-W%W")
                    weeks[week_key].append(e)
                except ValueError:
                    pass

            for week, week_entries in weeks.items():
                total_h = 0.0
                for e in week_entries:
                    sc = e.get("workstation", "")
                    defn = SHIFT_DEFS.get(sc)
                    if defn:
                        total_h += defn["hours"]
                    else:
                        s = parse_time(e.get("shift_start", ""))
                        end = parse_time(e.get("shift_end", ""))
                        if s < 0 or end < 0:
                            continue
                        if end < s:
                            end += 24 * 60
                        total_h += (end - s) / 60.0
                if total_h > 46:
                    violations.append(Violation(
                        priority="P0",
                        rule_id="P0-003",
                        description=(f"員工 {eid} 在週 {week} 累計工時超過 46 小時"
                                     f"（{total_h:.1f}h）"),
                        employee_id=eid,
                        suggestion="減少加班或調整班次",
                    ))

        # P0-004: 一例一休 — max 5 working days per week (2 rest days required)
        # Group by ISO week to handle combined multi-week data correctly
        weeks_days = defaultdict(set)
        for e in working_entries:
            try:
                d = datetime.strptime(e["date"], "%Y-%m-%d")
                week_key = d.strftime("%Y-W%W")
                weeks_days[week_key].add(e["date"])
            except ValueError:
                pass
        for week_key, dates in weeks_days.items():
            if len(dates) > 5:
                violations.append(Violation(
                    priority="P0",
                    rule_id="P0-004",
                    description=(f"員工 {eid} 在週 {week_key} 工作 {len(dates)} 天，"
                                 f"超過一例一休上限 5 天"),
                    employee_id=eid,
                    suggestion="減少至最多 5 天工作，確保至少 2 天休息（一例一休）",
                ))

        # P0-005: Designated rest days must not have shifts
        designated_dates = rest_days.get(eid, set())
        if designated_dates:
            working_dates = set(e["date"] for e in working_entries)
            for rest_date in designated_dates:
                if rest_date in working_dates:
                    violations.append(Violation(
                        priority="P0",
                        rule_id="P0-005",
                        description=(f"員工 {eid} 在指定劃休日 {rest_date} 被排班"),
                        employee_id=eid,
                        date=rest_date,
                        suggestion="移除該日班次，遵守指定劃休",
                    ))

    return violations


# ─── Hard Constraints ────────────────────────────────────────────────────────

def check_hard_constraints(entries: list, habits_map: dict) -> list:
    """Hard system constraints — should not occur in a valid solver output."""
    violations = []
    by_emp = group_by_employee(entries)

    # HC-001: No double-booking on same day
    for eid, emp_entries in by_emp.items():
        by_date = defaultdict(list)
        for e in emp_entries:
            by_date[e["date"]].append(e)
        for date, day_entries in by_date.items():
            working = [e for e in day_entries if not e.get("leave_type")]
            if len(working) > 1:
                violations.append(Violation(
                    priority="Hard",
                    rule_id="HC-001",
                    description=f"員工 {eid} 在 {date} 被排入多個班次",
                    employee_id=eid,
                    date=date,
                    suggestion="檢查排班邏輯，移除重複班次",
                ))

    return violations


# ─── P1: Tenant Rules ────────────────────────────────────────────────────────

def check_p1_tenant_rules(entries: list, coverage_targets: dict = None,
                          min_daily_headcount: dict = None,
                          manager_config: dict = None) -> list:
    """
    P1 — Tenant-defined business rules.
    Coverage minimums, role requirements, manager shift rules, etc.
    """
    violations = []

    if not coverage_targets:
        coverage_targets = {
            "10:00": {"min": 2, "label": "早班"},
            "15:00": {"min": 2, "label": "午班"},
            "19:00": {"min": 3, "label": "晚班"},
        }

    by_date_shift = group_by_date_shift(entries)

    for (date, shift_start), slot_entries in by_date_shift.items():
        if not shift_start:
            continue
        working = [e for e in slot_entries if not e.get("leave_type")]
        count = len(working)

        for target_time, rules in coverage_targets.items():
            # Match shift time prefix
            if shift_start.startswith(target_time[:2]):
                min_staff = rules.get("min", 1)
                label = rules.get("label", shift_start)
                if count < min_staff:
                    violations.append(Violation(
                        priority="P1",
                        rule_id="P1-001",
                        description=(f"{date} {label} 人力不足："
                                     f"需要 {min_staff} 人，僅有 {count} 人"),
                        date=date,
                        suggestion=f"增派 {min_staff - count} 名員工至 {label}",
                    ))

    # P1-002: Minimum daily total headcount (supports saturday/sunday/package)
    if min_daily_headcount:
        weekday_min = min_daily_headcount.get("weekday", 0)
        saturday_min = min_daily_headcount.get("saturday",
                           min_daily_headcount.get("weekend", 0))
        sunday_min = min_daily_headcount.get("sunday",
                         min_daily_headcount.get("weekend", 0))
        package_min = min_daily_headcount.get("package", 0)
        by_date = defaultdict(list)
        for e in entries:
            if not e.get("leave_type"):
                by_date[e["date"]].append(e)
        for date, day_entries in sorted(by_date.items()):
            try:
                dt = datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                continue
            if dt.weekday() == 5:  # Saturday
                required = saturday_min
                day_type = "週六"
            elif dt.weekday() == 6 or is_holiday(date):  # Sunday / holiday
                required = sunday_min
                day_type = "週日/假日"
            else:
                required = weekday_min
                day_type = "平日"
                # Package days on weekdays get higher headcount
                if package_min > 0 and required < package_min:
                    # TODO: detect package dates from events.json if needed
                    pass
            actual = len(day_entries)
            if actual < required:
                violations.append(Violation(
                    priority="P1",
                    rule_id="P1-002",
                    description=(f"{date} ({day_type}) 總人力不足："
                                 f"需要 {required} 人，僅有 {actual} 人"),
                    date=date,
                    suggestion=f"增派 {required - actual} 名員工",
                ))

    # P1-003: Manager early/late shift coverage (any early/late B-code)
    if manager_config and manager_config.get("member_ids"):
        mgr_ids = set(manager_config["member_ids"])
        daily_early = manager_config.get("daily_early_count", 1)
        daily_late = manager_config.get("daily_late_count", 1)

        def _shift_start_hour(ws):
            defn = SHIFT_DEFS.get(ws)
            return int(defn["start"].split(":")[0]) if defn else 12

        # Early/late shifts from config, fallback to hour-based threshold
        early_codes = set(manager_config.get("early_shifts", []))
        late_codes = set(manager_config.get("late_shifts", []))

        # Group manager entries by date
        by_date = defaultdict(lambda: {"early": 0, "late": 0})
        for e in entries:
            if e.get("employee_id") in mgr_ids and not e.get("leave_type"):
                ws = e.get("workstation", "")
                if ws.startswith("B"):
                    if early_codes:
                        if ws in early_codes:
                            by_date[e["date"]]["early"] += 1
                    else:
                        if _shift_start_hour(ws) <= 12:
                            by_date[e["date"]]["early"] += 1
                    if late_codes:
                        if ws in late_codes:
                            by_date[e["date"]]["late"] += 1
                    else:
                        if _shift_start_hour(ws) >= 14:
                            by_date[e["date"]]["late"] += 1

        # Check all dates in schedule
        all_dates = sorted(set(e["date"] for e in entries))
        for date in all_dates:
            counts = by_date.get(date, {"early": 0, "late": 0})
            if counts["early"] < daily_early:
                violations.append(Violation(
                    priority="P1",
                    rule_id="P1-003",
                    description=(f"{date} 主管早班 (start≤12) 未排滿："
                                 f"需要 {daily_early} 人，僅有 {counts['early']} 人"),
                    date=date,
                    suggestion="指派主管排早班 (start≤12:00) 的 B-code 班次",
                ))
            if counts["late"] < daily_late:
                violations.append(Violation(
                    priority="P1",
                    rule_id="P1-003",
                    description=(f"{date} 主管晚班 (start≥14) 未排滿："
                                 f"需要 {daily_late} 人，僅有 {counts['late']} 人"),
                    date=date,
                    suggestion="指派主管排晚班 (start≥14:00) 的 B-code 班次",
                ))

    # P1-004: No-same-rest pair violation
    no_same_rest = manager_config.get("no_same_rest", [])
    if no_same_rest:
        by_date = defaultdict(set)
        for e in entries:
            if not e.get("leave_type"):
                by_date[e["date"]].add(e.get("employee_id"))
        all_dates = sorted(set(e["date"] for e in entries))
        all_emp_ids = set(e.get("employee_id") for e in entries)
        for pair in no_same_rest:
            id_a, id_b = pair[0], pair[1]
            if id_a not in all_emp_ids or id_b not in all_emp_ids:
                continue
            for date in all_dates:
                working_ids = by_date.get(date, set())
                a_working = id_a in working_ids
                b_working = id_b in working_ids
                if not a_working and not b_working:
                    violations.append(Violation(
                        priority="P1",
                        rule_id="P1-004",
                        description=(f"{date} 禁休配對違規：員工 {id_a} 和 {id_b} 同天休假"),
                        date=date,
                        suggestion=f"調整排班使 {id_a} 和 {id_b} 不在同一天休假",
                    ))

    return violations


def check_p1_rhythm(grouped_by_emp: dict, current_week_start: str = None) -> list:
    """
    P1-SC6 / P1-SC7 — Cross-week attendance rhythm checks.
    Accepts grouped_by_emp that may span multiple weeks (combined entries).

    If current_week_start is provided, only report violations that overlap
    with the current week (7 days from current_week_start), avoiding
    re-reporting issues that are entirely within a previous week.

    P1-SC6: Consecutive >= 5 working days → one violation per streak
    P1-SC7: Work-rest-work (做一休一) isolated rest day → one violation per occurrence
    """
    violations = []

    # Determine current week date range for filtering
    curr_start = None
    curr_end = None
    if current_week_start:
        try:
            curr_start = datetime.strptime(current_week_start, "%Y-%m-%d")
            curr_end = curr_start + timedelta(days=6)
        except ValueError:
            pass

    def _overlaps_current_week(date_str_start, date_str_end=None):
        """Check if a date range overlaps with the current week."""
        if not curr_start:
            return True  # No filter, report all
        try:
            ds = datetime.strptime(date_str_start, "%Y-%m-%d")
            de = datetime.strptime(date_str_end, "%Y-%m-%d") if date_str_end else ds
        except ValueError:
            return True
        # Overlaps if streak end >= curr_start AND streak start <= curr_end
        return de >= curr_start and ds <= curr_end

    def _in_current_week(date_str):
        """Check if a single date falls within the current week."""
        if not curr_start:
            return True
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return True
        return curr_start <= d <= curr_end

    for eid, entries in grouped_by_emp.items():
        working_entries = [e for e in entries if not e.get("leave_type")]
        working_dates = set(e["date"] for e in working_entries)

        # Build a sorted list of all dates in the entries
        all_dates = sorted(set(e["date"] for e in entries))
        if not all_dates:
            continue

        # Build a continuous date range from min to max
        try:
            d_min = datetime.strptime(all_dates[0], "%Y-%m-%d")
            d_max = datetime.strptime(all_dates[-1], "%Y-%m-%d")
        except ValueError:
            continue

        date_range = []
        d = d_min
        while d <= d_max:
            date_range.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)

        # Boolean array: is this date a working day?
        is_working = [d in working_dates for d in date_range]

        # P1-SC6: Detect consecutive >= 5 working days
        streak_start = None
        streak_len = 0
        for idx, w in enumerate(is_working):
            if w:
                if streak_start is None:
                    streak_start = idx
                streak_len += 1
            else:
                if streak_len >= 5:
                    s_date = date_range[streak_start]
                    e_date = date_range[streak_start + streak_len - 1]
                    if _overlaps_current_week(s_date, e_date):
                        violations.append(Violation(
                            priority="P1",
                            rule_id="P1-SC6",
                            description=(
                                f"員工 {eid} 連續上班 {streak_len} 天"
                                f"（{s_date} ~ {e_date}）"
                            ),
                            employee_id=eid,
                            date=s_date,
                            suggestion="避免連續工作超過 4 天，安排中間休息日",
                        ))
                streak_start = None
                streak_len = 0
        # Check trailing streak
        if streak_len >= 5:
            s_date = date_range[streak_start]
            e_date = date_range[streak_start + streak_len - 1]
            if _overlaps_current_week(s_date, e_date):
                violations.append(Violation(
                    priority="P1",
                    rule_id="P1-SC6",
                    description=(
                        f"員工 {eid} 連續上班 {streak_len} 天"
                        f"（{s_date} ~ {e_date}）"
                    ),
                    employee_id=eid,
                    date=s_date,
                    suggestion="避免連續工作超過 4 天，安排中間休息日",
                ))

        # P1-SC7: Detect work-rest-work (isolated rest day)
        for idx in range(1, len(is_working) - 1):
            if is_working[idx - 1] and not is_working[idx] and is_working[idx + 1]:
                rest_date = date_range[idx]
                if _in_current_week(rest_date):
                    violations.append(Violation(
                        priority="P1",
                        rule_id="P1-SC7",
                        description=(
                            f"員工 {eid} 在 {rest_date} 出現做一休一"
                            f"（{date_range[idx-1]} 上班 → {rest_date} 休 → {date_range[idx+1]} 上班）"
                        ),
                        employee_id=eid,
                        date=rest_date,
                        suggestion="將相鄰的休息日合併，避免孤立休假",
                    ))

    return violations


# ─── P2: Employee Preferences ────────────────────────────────────────────────

def check_p2_preferences(entries: list, habits_map: dict) -> list:
    """
    P2 — Employee preference compliance.
    Soft violations: flag but do not block if necessary.
    """
    violations = []
    by_emp = group_by_employee(entries)

    for eid, emp_entries in by_emp.items():
        habit = habits_map.get(eid)
        if not habit:
            continue

        working = [e for e in emp_entries if not e.get("leave_type")]

        # P2-001: Preferred shift not respected (directly compare shift codes)
        if habit.preferred_shifts:
            most_preferred = habit.preferred_shifts[0]
            for e in working:
                actual_code = e.get("workstation", "")
                if not actual_code:
                    continue
                if actual_code not in habit.preferred_shifts:
                    violations.append(Violation(
                        priority="P2",
                        rule_id="P2-001",
                        description=(f"員工 {habit.chinese_name}({eid}) 在 {e['date']} "
                                     f"被排入非偏好班次（偏好: {', '.join(habit.preferred_shifts)}，"
                                     f"實際: {actual_code}）"),
                        employee_id=eid,
                        date=e["date"],
                        suggestion=f"優先考慮將此員工排入 {most_preferred} 班次",
                    ))

        # P2-002: No-overtime employees assigned too many shifts
        if habit.overtime_willingness == "no_overtime":
            if len(working) > 5:
                violations.append(Violation(
                    priority="P2",
                    rule_id="P2-002",
                    description=(f"員工 {habit.chinese_name}({eid}) 不願加班，"
                                 f"但本週被排 {len(working)} 個班次（超過 5）"),
                    employee_id=eid,
                    suggestion="減少此員工班次或改由加班意願較高的員工補充",
                ))

    return violations


# ─── Main Pipeline ────────────────────────────────────────────────────────────

def run_auditor(schedule_path: str, habits_path: str = None,
                output_path: str = "audit_report.json",
                coverage_targets: dict = None,
                tenant_dir: str = None,
                week_start: str = None,
                prev_schedule_path: str = None):
    print("=" * 60)
    print("🔍 稽核員 (Auditor Agent)")
    print("=" * 60)

    # Load schedule
    if schedule_path.endswith(".json"):
        entries = load_schedule_json(schedule_path)
    else:
        entries = load_schedule_csv(schedule_path)
    print(f"📂 載入 {len(entries)} 筆班表記錄")

    # Load habits for preference checking
    habits_map = {}
    if habits_path and os.path.exists(habits_path):
        habits = load_habits_json(habits_path)
        habits_map = {h.employee_id: h for h in habits}
        print(f"📂 載入 {len(habits_map)} 位員工習慣資料")

    # Load rest_days and manager_config from tenant dir
    rest_days_dates = {}   # {employee_id: set of date strings}
    manager_config = {}
    min_daily_headcount = {"weekday": 18, "saturday": 23, "sunday": 22, "package": 19}

    if tenant_dir:
        # Load manager config
        staff_roles_path = os.path.join(tenant_dir, "staff_roles.json")
        manager_config = load_manager_config(staff_roles_path)

        # Load rest days — need week_start; infer from schedule if not provided
        if not week_start and entries:
            all_dates = sorted(set(e.get("date", "") for e in entries))
            if all_dates:
                week_start = all_dates[0]

        if week_start:
            rest_days_path = os.path.join(tenant_dir, "rest_days.json")
            rest_day_indices = load_rest_days(rest_days_path, week_start)
            # Convert day indices back to date strings for auditor
            ws = datetime.strptime(week_start, "%Y-%m-%d")
            for emp_id, day_indices in rest_day_indices.items():
                rest_days_dates[emp_id] = set(
                    (ws + timedelta(days=d)).strftime("%Y-%m-%d")
                    for d in day_indices
                )

    # Load previous week schedule for cross-week detection
    prev_entries = []
    if prev_schedule_path and os.path.exists(prev_schedule_path):
        if prev_schedule_path.endswith(".json"):
            prev_entries = load_schedule_json(prev_schedule_path)
        else:
            prev_entries = load_schedule_csv(prev_schedule_path)
        print(f"📎 前週班表: 載入 {len(prev_entries)} 筆記錄")

    # Combined entries (prev + current) for cross-week checks
    combined_entries = prev_entries + entries
    combined_by_emp = group_by_employee(combined_entries)

    grouped_by_emp = group_by_employee(entries)

    # Run all checks
    print(f"\n🔎 開始稽核...")
    all_violations = []

    # P0: Use combined data for cross-week 11h rest detection
    p0 = check_p0_labor_law(combined_by_emp, rest_days=rest_days_dates)
    all_violations.extend(p0)
    hard = check_hard_constraints(entries, habits_map)
    all_violations.extend(hard)
    p1 = check_p1_tenant_rules(entries, coverage_targets,
                                min_daily_headcount=min_daily_headcount,
                                manager_config=manager_config)
    all_violations.extend(p1)
    # P1-SC6/SC7: Use combined data for cross-week rhythm detection
    p1_rhythm = check_p1_rhythm(combined_by_emp, current_week_start=week_start)
    all_violations.extend(p1_rhythm)
    p2 = check_p2_preferences(entries, habits_map) if habits_map else []
    all_violations.extend(p2)

    # Summary
    by_priority = defaultdict(list)
    for v in all_violations:
        by_priority[v.priority].append(v)

    passed = len(all_violations) == 0
    print(f"\n📊 稽核結果:")
    print(f"   整體狀態: {'✅ 通過' if passed else '❌ 有違規'}")
    for priority in ["P0", "Hard", "P1", "P2"]:
        count = len(by_priority.get(priority, []))
        icon = "🔴" if priority in ("P0", "Hard") else ("🟡" if priority == "P1" else "🔵")
        print(f"   {icon} {priority}: {count} 項{'違規' if count else ' (通過)'}")

    if all_violations:
        print(f"\n⚠️  違規詳情 (前5項):")
        for v in all_violations[:5]:
            print(f"   [{v.priority}] {v.rule_id}: {v.description}")
            if v.suggestion:
                print(f"            💡 建議: {v.suggestion}")

    # Output report
    report = {
        "generated_at": datetime.now().isoformat(),
        "schedule_path": schedule_path,
        "overall_pass": passed,
        "total_violations": len(all_violations),
        "summary": {p: len(vs) for p, vs in by_priority.items()},
        "violations": [v.to_dict() for v in all_violations],
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 稽核報告已儲存至 {output_path}")
    print(f"{'=' * 60}")
    return report


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python auditor_tools.py <schedule.csv|json> [habits.json] [輸出路徑] [tenant目錄] [週起始] [前週班表]")
        print("範例:")
        print("  python auditor_tools.py schedule.csv habits.json audit_report.json tenants/glod-pig 2026-03-02")
        print("  python auditor_tools.py schedule_0309.csv habits.json audit_0309.json tenants/glod-pig 2026-03-09 schedule_0302.csv")
        sys.exit(1)

    schedule_path      = sys.argv[1]
    habits_path        = sys.argv[2] if len(sys.argv) > 2 else None
    output_path        = sys.argv[3] if len(sys.argv) > 3 else "audit_report.json"
    tenant_dir         = sys.argv[4] if len(sys.argv) > 4 else None
    week_start         = sys.argv[5] if len(sys.argv) > 5 else None
    prev_schedule_path = sys.argv[6] if len(sys.argv) > 6 else None

    report = run_auditor(schedule_path, habits_path, output_path,
                         tenant_dir=tenant_dir, week_start=week_start,
                         prev_schedule_path=prev_schedule_path)
    sys.exit(0 if report["overall_pass"] else 1)
