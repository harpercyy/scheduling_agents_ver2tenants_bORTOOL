#!/usr/bin/env python3
"""
analyzer.py — Analyzer Agent: Identity Resolution + Habit Calculation.

Reads historical roster CSVs and produces a Habit Model (habits.json)
that captures each employee's scheduling patterns and preferences.

Scenarios covered:
  S1: Identity Resolution — map employee ID ↔ name ↔ nickname
  S2: Habit Calculation — compute per-person and per-shift statistics
"""

import csv
import json
import os
import re
import sys
import glob
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_loader import (
    parse_roster_csv, Habit, EmployeePreference,
    habits_to_json, SHIFT_LABELS, LEAVE_TYPES,
    merge_staff_roles,
)


# ─── Workstation Role Helper ──────────────────────────────────────────────────
# Workstation ROLE (員工技能): 烤手 / 領檯早 / 領檯晚
# Shift CODE (班次代碼):       B001-B012, B101-B109, 櫃台(早), 櫃台(晚)
# ALL B-codes → 烤手  |  櫃台(早) → 領檯早  |  櫃台(晚) → 領檯晚

def shift_code_to_role(shift_code: str) -> str:
    """Map a shift code to its workstation role (烤手 / 領檯早 / 領檯晚).

    Legacy '櫃台' (without 早/晚 suffix) maps to '烤手' so that historical
    CSV data doesn't accidentally grant 領檯 skills. The actual 領檯早/領檯晚
    assignments are controlled exclusively via staff_roles.json.
    """
    if not shift_code:
        return "烤手"
    sc = shift_code.strip()
    if sc == "櫃台(早)":
        return "領檯早"
    if sc == "櫃台(晚)":
        return "領檯晚"
    return "烤手"  # default for B-codes, C-codes, legacy 櫃台, etc.


# ─── Identity Resolution (S1) ────────────────────────────────────────────────

def resolve_identities(all_employees: list) -> dict:
    """
    Build a unified identity map across multiple roster files.
    Resolves: employee_id ↔ chinese_name ↔ english_name.
    Returns: dict keyed by employee_id with merged info.
    """
    identity_map = {}

    for emp in all_employees:
        eid = emp.employee_id
        if not eid:
            continue

        if eid not in identity_map:
            identity_map[eid] = {
                "employee_id": eid,
                "chinese_name": emp.chinese_name,
                "english_name": emp.english_name,
                "departure_note": emp.departure_note,
                "all_shifts": [],
                "preferences": [],
                "weekly_stats_list": [],
                "weeks_seen": 0,
            }

        entry = identity_map[eid]

        # Merge names (take non-empty)
        if emp.chinese_name and not entry["chinese_name"]:
            entry["chinese_name"] = emp.chinese_name
        if emp.english_name and not entry["english_name"]:
            entry["english_name"] = emp.english_name
        if emp.departure_note:
            entry["departure_note"] = emp.departure_note

        # Collect all shifts
        entry["all_shifts"].extend(emp.shifts)
        entry["weeks_seen"] += 1  # Count how many CSVs this employee appeared in

        # Collect preferences (may vary across weeks)
        if emp.preference:
            entry["preferences"].append(emp.preference)

        # Weekly stats
        if emp.weekly_stats:
            entry["weekly_stats_list"].append(emp.weekly_stats)

    print(f"🔍 身份識別: 共找到 {len(identity_map)} 位不重複員工")
    return identity_map


# ─── Habit Calculation (S2) ───────────────────────────────────────────────────

def calculate_habits(identity_map: dict) -> list:
    """
    From resolved identities and their historical shifts,
    compute the Habit Model for each employee.
    """
    habits = []

    for eid, info in identity_map.items():
        # Skip departed employees
        if info.get("departure_note"):
            continue

        habit = Habit(
            employee_id=eid,
            chinese_name=info["chinese_name"] or "",
            english_name=info["english_name"] or "",
        )

        shifts = info["all_shifts"]

        # ── Preferred shifts: use ACTUAL shift codes ranked by frequency ──
        shift_code_counter = Counter()  # shift_code (e.g. B004, C111) → count
        time_bucket_counter = Counter()  # legacy morning/afternoon/evening
        for s in shifts:
            if s.leave_type:
                continue
            if s.workstation and re.match(r'^[BC]\d', s.workstation):
                # Use workstation code as the shift code reference
                shift_code_counter[s.workstation] += 1
            if s.start_time:
                try:
                    hour = int(s.start_time.split(":")[0])
                    if hour < 12:
                        time_bucket_counter["morning"] += 1
                    elif hour < 17:
                        time_bucket_counter["afternoon"] += 1
                    else:
                        time_bucket_counter["evening"] += 1
                except (ValueError, IndexError):
                    pass

        # preferred_shifts = shift codes sorted by frequency (most-used first)
        if shift_code_counter:
            habit.preferred_shifts = [sc for sc, _ in shift_code_counter.most_common()]
        elif time_bucket_counter:
            # Fallback for employees with no workstation codes
            habit.preferred_shifts = [t for t, _ in time_bucket_counter.most_common()]

        # shift_frequency = {shift_code: count}
        habit.shift_frequency = dict(shift_code_counter) if shift_code_counter else dict(time_bucket_counter)

        # ── Workstation skills: store ROLES (烤手 / 領檯早 / 領檯晚) ──
        # Also compute shift_code used per workstation for reference
        role_counter = Counter()   # role → total appearances
        for s in shifts:
            if s.workstation:
                role = shift_code_to_role(s.workstation)
                role_counter[role] += 1
        habit.workstation_skills = list(role_counter.keys())       # e.g. ["烤手", "領檯早"]
        habit.workstation_frequency = dict(role_counter)           # e.g. {"烤手": 15, "領檯早": 3}

        # ── by_person: Average hours ──
        total_hours = 0.0
        shift_count = 0
        for s in shifts:
            if s.start_time and s.end_time and not s.leave_type:
                try:
                    sh, sm = map(int, s.start_time.split(":"))
                    eh, em = map(int, s.end_time.split(":"))
                    start_mins = sh * 60 + sm
                    end_mins = eh * 60 + em
                    if end_mins < start_mins:
                        end_mins += 24 * 60  # Overnight shift
                    hours = (end_mins - start_mins) / 60.0
                    total_hours += hours
                    shift_count += 1
                except (ValueError, IndexError):
                    pass

        # Calculate weekly averages using weeks_seen (number of CSV files)
        num_weeks = max(1, info.get("weeks_seen", 1))
        habit.avg_weekly_hours = round(total_hours / num_weeks, 1)
        habit.avg_shifts_per_week = round(shift_count / num_weeks, 1)

        # ── by_person: Preferences (merge across weeks) ──
        prefs = info["preferences"]
        if prefs:
            # Take the most recent / most common preference
            latest = prefs[-1]
            habit.available_hour_range = latest.available_hours

            # Map overtime policy to normalized values
            if latest.overtime_policy:
                ot = latest.overtime_policy
                if "固加" in ot:
                    habit.overtime_willingness = "fixed_overtime"
                elif "不加" in ot:
                    habit.overtime_willingness = "no_overtime"
                elif "可" in ot or "+" in ot:
                    habit.overtime_willingness = "flexible"
                else:
                    habit.overtime_willingness = ot

            # Rotation flexibility
            if latest.rotation_policy:
                rot = latest.rotation_policy
                if "可輪班" in rot:
                    habit.rotation_flexibility = "full"
                elif "可輪早午" in rot:
                    habit.rotation_flexibility = "morning_afternoon"
                else:
                    habit.rotation_flexibility = rot

        habits.append(habit)

    # ── Deduplicate by (chinese_name, english_name) — keep the lower employee_id ──
    seen_names = {}  # (chinese_name, english_name) → habit
    deduped = []
    for h in habits:
        key = (h.chinese_name, h.english_name)
        if key in seen_names:
            existing = seen_names[key]
            # Merge data from duplicate into the first occurrence
            for sc, cnt in h.shift_frequency.items():
                existing.shift_frequency[sc] = existing.shift_frequency.get(sc, 0) + cnt
            for ws, cnt in h.workstation_frequency.items():
                existing.workstation_frequency[ws] = existing.workstation_frequency.get(ws, 0) + cnt
            if h.avg_weekly_hours > existing.avg_weekly_hours:
                existing.avg_weekly_hours = h.avg_weekly_hours
            if h.avg_shifts_per_week > existing.avg_shifts_per_week:
                existing.avg_shifts_per_week = h.avg_shifts_per_week
            if h.preferred_shifts and not existing.preferred_shifts:
                existing.preferred_shifts = h.preferred_shifts
            if h.workstation_skills and not existing.workstation_skills:
                existing.workstation_skills = h.workstation_skills
            print(f"   ⚠️  去重: {h.chinese_name} ({h.english_name}) ID {h.employee_id} → 合併至 ID {existing.employee_id}")
        else:
            seen_names[key] = h
            deduped.append(h)
    habits = deduped

    # ── Ensure every employee has at least one workstation_skill ──
    # Employees without workstation data from CSV are 服務 (table service)
    for h in habits:
        if not h.workstation_skills:
            h.workstation_skills = ["服務"]

    # ── Sort by employee ID for consistent output ──
    habits.sort(key=lambda h: int(h.employee_id) if h.employee_id.isdigit() else 0)

    return habits





# ─── Workstation Role Mapping ──────────────────────────────────────────────────
# Workstation = employee ROLE/SKILL (兩種 + 領檯):
#   烤手 = induction grill area (ALL B-code shifts)
#   領檯早 = counter early shift (櫃台(早))
#   領檯晚 = counter late shift  (櫃台(晚))
# Shift code (Bxxx) is separate from workstation role.

WORKSTATION_ROLE_MAP = {
    **{f"B{n:03d}": "烤手" for n in range(1, 13)},   # B001-B012
    **{f"B1{n:02d}": "烤手" for n in range(1, 10)},   # B101-B109
    "櫃台(早)": "領檯早",
    "櫃台(晚)": "領檯晚",
}


# Taiwan public holidays in 2026 that are not weekends
TW_HOLIDAYS_2026 = {
    "2026-01-01",  # 元旦
    "2026-01-26", "2026-01-27", "2026-01-28", "2026-01-29",
    "2026-01-30", "2026-01-31", "2026-02-01",  # 春節
    "2026-02-28",  # 和平紀念日
    "2026-04-04",  # 兒童節/清明
    "2026-04-05",  # 清明補假
    "2026-05-01",  # 勞動節
    "2026-06-19",  # 端午節
    "2026-09-29",  # 中秋節
    "2026-10-10",  # 國慶日
}


def is_holiday(date_str: str) -> bool:
    """Return True if date is a weekend or public holiday."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        if d.weekday() >= 5:  # Sat=5, Sun=6
            return True
        return date_str in TW_HOLIDAYS_2026
    except ValueError:
        # Fallback for short date strings like '1-5'
        return False


def extract_package_dates(csv_path: str, date_columns: list) -> set:
    """
    Parse the 營運備註 row from a roster CSV and return the set of
    date strings that have 包場 (private event) annotations.

    date_columns: list of (col_index, date_str) tuples from the date header row.
    """
    package_dates = set()
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            rows = list(csv.reader(f))
    except Exception:
        return package_dates

    # Find the 營運備註 row (usually row 8, within first 15 rows)
    notes_row = None
    for i in range(min(15, len(rows))):
        if '營運備註' in rows[i][2:5] or any('包場' in c for c in rows[i]):
            notes_row = rows[i]
            break

    if notes_row is None:
        return package_dates

    # Check each date column for 包場 text
    for col_idx, date_str in date_columns:
        cell = notes_row[col_idx].strip() if col_idx < len(notes_row) else ""
        if '包場' in cell:
            package_dates.add(date_str)  # short form e.g. '1-5'

    return package_dates


# ─── Shift Coverage Analysis (Goal 1 output by shift slot) ───────────────────

def analyze_shift_coverage(identity_map: dict) -> dict:
    """
    Compute per-shift-slot headcount statistics across all weeks.
    Returns a summary of staffing levels by shift type.
    """
    coverage = defaultdict(lambda: {"counts": [], "workstations": Counter()})

    for eid, info in identity_map.items():
        if info.get("departure_note"):
            continue
        for s in info["all_shifts"]:
            if s.leave_type or not s.start_time:
                continue
            hour = int(s.start_time.split(":")[0])
            if hour < 12:
                slot = "morning"
            elif hour < 17:
                slot = "afternoon"
            else:
                slot = "evening"

            key = f"{s.date}_{slot}"
            coverage[slot]["counts"].append(key)
            if s.workstation:
                coverage[slot]["workstations"][s.workstation] += 1

    summary = {}
    for slot, data in coverage.items():
        date_counts = Counter(data["counts"])
        avg_headcount = sum(date_counts.values()) / max(len(set(
            k.split("_")[0] for k in date_counts.keys()
        )), 1)
        summary[slot] = {
            "avg_headcount": round(avg_headcount, 1),
            "top_workstations": dict(data["workstations"].most_common(5)),
        }

    return summary


# ─── Store Demand Analysis (Goal 2) ───────────────────────────────────────────

def analyze_store_demand(csv_paths: list, identity_map: dict) -> dict:
    """
    Analyze 4-scenario staffing demand:
      平日無包場 / 平日有包場 / 假日無包場 / 假日有包場

    For each scenario, compute the observed average headcount split by:
      - 烤手, 服務 (共用班次代碼, 分開計人數)
      - 櫃台早班, 櫃台晚班

    包場 detection: derived from 營運備註 row in each CSV file.
    Workstation role: mapped via WORKSTATION_ROLE_MAP.
    """
    from datetime import datetime

    # ── Step 1: build date → short_date and collect package dates ──
    all_package_short_dates = set()  # short-form dates like '1-5'

    # We need to parse each CSV's date columns independently
    import re

    def get_date_cols_from_csv(path):
        with open(path, 'r', encoding='utf-8') as f:
            rows = list(csv.reader(f))
        for row in rows[:8]:
            dates = [(j, c.strip().replace('/', '-')) for j, c in enumerate(row)
                     if re.match(r'^\d{1,2}[-/]\d{1,2}$', c.strip())]
            if len(dates) >= 5:
                return dates
        return []

    # Map short date → (full_date_str, is_holiday, is_package)
    date_context = {}  # key = short_date like '1-5'

    # ── Load manual package dates from events.json (if present) ──
    # Looks for events.json in the same directory as the CSVs
    tenant_dirs = set(os.path.dirname(p) for p in csv_paths)
    for tenant_dir in tenant_dirs:
        events_path = os.path.join(tenant_dir, "events.json")
        if os.path.exists(events_path):
            try:
                with open(events_path, 'r', encoding='utf-8') as f:
                    events = json.load(f)
                for d in events.get("package_dates", []):
                    d = d.strip()
                    # Convert YYYY-MM-DD → M-D short form
                    if re.match(r'^\d{4}-\d{2}-\d{2}$', d):
                        parts = d.split("-")
                        short = f"{int(parts[1])}-{int(parts[2])}"
                    else:
                        short = d  # already short form
                    all_package_short_dates.add(short)
                print(f"   📅 載入 events.json: {len(events.get('package_dates', []))} 個包場日期")
            except Exception as e:
                print(f"   ⚠️  events.json 解析失敗: {e}")

    for path in csv_paths:
        date_cols = get_date_cols_from_csv(path)
        pkg_dates = extract_package_dates(path, date_cols)
        all_package_short_dates.update(pkg_dates)

        for col_idx, short_date in date_cols:
            if short_date not in date_context:
                date_context[short_date] = {
                    "is_package": short_date in all_package_short_dates,
                    "is_holiday": None,  # will resolve below
                }

    # ── Step 2: collect per-day workstation headcounts ──
    # day_data[short_date][role] = count of staff with that role
    day_data = defaultdict(lambda: defaultdict(int))
    day_shift_slot = defaultdict(set)  # short_date → set of shift slots seen

    for eid, info in identity_map.items():
        if info.get("departure_note"):
            continue
        for s in info["all_shifts"]:
            if s.leave_type or not s.start_time:
                continue

            date_str = s.date  # short form '1-5'

            # Workstation role
            role = WORKSTATION_ROLE_MAP.get(s.workstation, None) if s.workstation else None

            if role in ("領檯早", "領檯晚"):
                pass  # already classified
            elif role == "烤手":
                pass  # keep as-is
            elif s.workstation and "櫃台" in s.workstation:
                # Legacy 櫃台 workstation — split by start time
                try:
                    hour = int(s.start_time.split(":")[0])
                    role = "領檯早" if hour < 15 else "領檯晚"
                except (ValueError, IndexError):
                    role = "領檯早"
            else:
                # No explicit workstation code — default to 烤手
                role = "烤手"

            day_data[date_str][role] += 1

    # ── Step 3: update package flags now that all CSVs are processed ──
    for short_date in date_context:
        date_context[short_date]["is_package"] = short_date in all_package_short_dates

    # ── Step 4: aggregate into 4 scenarios ──
    scenarios = {
        "平日": defaultdict(list),
        "平日包場": defaultdict(list),
        "週末": defaultdict(list),
        "週末包場": defaultdict(list),
    }

    for short_date, roles in day_data.items():
        ctx = date_context.get(short_date, {})
        is_pkg = ctx.get("is_package", False)

        # Determine if holiday: use weekday from short date
        # Short dates like '1-5' = Jan 5 = Monday (平日)
        # We need the year context — assume current CSV year from filename or default 2025/2026
        # Use a heuristic: if month <= 6 assume 2026, else assume 2025
        try:
            parts = short_date.split("-")
            month, day = int(parts[0]), int(parts[1])
            year = 2026 if month <= 6 else 2025
            full_date = f"{year}-{month:02d}-{day:02d}"
            is_hol = is_holiday(full_date)
        except (ValueError, IndexError):
            is_hol = False

        if is_hol and is_pkg:
            scenario = "週末包場"
        elif is_hol:
            scenario = "週末"
        elif is_pkg:
            scenario = "平日包場"
        else:
            scenario = "平日"

        for role, count in roles.items():
            scenarios[scenario][role].append(count)

    # ── Step 5: compute averages ──
    demand_profile = {}
    for scenario, role_counts in scenarios.items():
        if not role_counts:
            demand_profile[scenario] = {}
            continue
        profile = {}
        for role, counts in role_counts.items():
            avg = round(sum(counts) / len(counts), 1)
            mn = min(counts)
            mx = max(counts)
            profile[role] = {"avg": avg, "min": mn, "max": mx, "samples": len(counts)}
        demand_profile[scenario] = profile

    return demand_profile


def print_demand_profile(demand_profile: dict):
    """Pretty-print the demand profile to console."""
    roles_order = ["烤手", "領檯早", "領檯晚"]
    print("\n📊 店面人力需求分析 (Demand Profile):")
    print(f"{'情境':<12} ", end="")
    for r in roles_order:
        print(f"{r:>8}", end="")
    print()
    print("-" * 48)
    for scenario in ["平日", "平日包場", "週末", "週末包場"]:
        profile = demand_profile.get(scenario, {})
        samples = next((v["samples"] for v in profile.values()), 0)
        print(f"{scenario:<12} ", end="")
        for r in roles_order:
            val = profile.get(r, {})
            avg = val.get("avg", "-")
            print(f"{str(avg) if isinstance(avg, float) else avg:>8}", end="")
        print(f"  (n={samples})")
    print()


# ─── Main Pipeline ────────────────────────────────────────────────────────────

def run_analyzer(csv_paths: list, output_path: str = "habits.json"):
    """
    Full analyzer pipeline:
    1. Parse all CSV files
    2. Resolve identities across files
    3. Calculate habits
    4. Output habits.json
    """
    print("=" * 60)
    print("🔬 排班分析器 (Analyzer Agent)")
    print("=" * 60)

    # Step 1: Parse roster CSVs (skip non-roster files like 班次.csv)
    all_employees = []
    roster_paths = []
    for path in csv_paths:
        # Quick check: does this CSV have a date-header row (roster format)?
        try:
            with open(path, 'r', encoding='utf-8') as f:
                head = [next(f) for _ in range(8)]
            has_dates = any(
                re.search(r'\d{1,2}[-/]\d{1,2}', line) for line in head
            )
            if not has_dates:
                print(f"   ⏭️  略過 (非班表格式): {os.path.basename(path)}")
                continue
        except Exception:
            continue
        roster_paths.append(path)

    for path in roster_paths:
        print(f"\n📂 解析: {os.path.basename(path)}")
        try:
            employees = parse_roster_csv(path)
            print(f"   找到 {len(employees)} 筆員工記錄")
            all_employees.extend(employees)
        except Exception as e:
            print(f"   ⚠️  解析失敗: {e}")

    if not all_employees:
        print("❌ 沒有成功解析任何員工資料")
        return

    # Step 2: Identity Resolution
    identity_map = resolve_identities(all_employees)

    # Step 3: Habit Calculation
    habits = calculate_habits(identity_map)
    tenant_dir = os.path.dirname(csv_paths[0]) if csv_paths else "."
    merge_staff_roles(habits, tenant_dir)   # override workstation_skills from staff_roles.json
    print(f"\n📊 習慣計算完成: {len(habits)} 位在職員工")

    # Show summary
    for h in habits[:5]:
        print(f"   👤 {h.chinese_name} ({h.english_name})")
        print(f"      偏好班別: {', '.join(h.preferred_shifts)}")
        print(f"      工作站技能: {', '.join(h.workstation_skills[:3])}")
        print(f"      週均工時: {h.avg_weekly_hours}h")
        print(f"      加班意願: {h.overtime_willingness or '未設定'}")

    # Step 4: Shift coverage analysis (legacy)
    coverage = analyze_shift_coverage(identity_map)
    print(f"\n📈 班次覆蓋分析 (by shift slot):")
    for slot, data in coverage.items():
        slot_zh = {"morning": "早班", "afternoon": "午班", "evening": "晚班"}.get(slot, slot)
        print(f"   {slot_zh}: 平均 {data['avg_headcount']} 人")

    # Step 5: Store demand analysis — Goal 2
    demand_profile = analyze_store_demand(roster_paths, identity_map)
    print_demand_profile(demand_profile)

    # Step 6: Output habits.json
    habits_to_json(habits, output_path)

    # Save coverage analysis
    coverage_path = output_path.replace(".json", "_coverage.json")
    with open(coverage_path, 'w', encoding='utf-8') as f:
        json.dump(coverage, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 班次覆蓋分析已儲存至 {coverage_path}")

    # Save demand profile
    demand_path = output_path.replace(".json", "_demand.json")
    with open(demand_path, 'w', encoding='utf-8') as f:
        json.dump(demand_profile, f, ensure_ascii=False, indent=2)
    print(f"✅ 人力需求分析已儲存至 {demand_path}")

    print(f"\n{'=' * 60}")
    print(f"✅ 分析完成！")
    print(f"{'=' * 60}")


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python analyzer.py <CSV路徑或資料夾> [輸出路徑]")
        print("範例:")
        print("  python analyzer.py 'tenants/glod-pig/'")
        print("  python analyzer.py 'tenants/glod-pig/*.csv' habits.json")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "habits.json"

    # Resolve input to list of CSV files
    if os.path.isdir(input_path):
        csv_paths = sorted(glob.glob(os.path.join(input_path, "*.csv")))
    elif "*" in input_path:
        csv_paths = sorted(glob.glob(input_path))
    else:
        csv_paths = [input_path]

    if not csv_paths:
        print(f"❌ 找不到 CSV 檔案: {input_path}")
        sys.exit(1)

    print(f"📁 找到 {len(csv_paths)} 個 CSV 檔案")
    run_analyzer(csv_paths, output_path)
