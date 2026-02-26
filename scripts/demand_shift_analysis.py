#!/usr/bin/env python3
"""
demand_shift_analysis.py — 四種情境 × Workstation × 班次代碼 三維分析
"""
import csv, re, json, os, sys
from collections import defaultdict, Counter
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from data_loader import load_tenant_config


def load_package_dates(tenant_dir: str) -> set:
    """Load short-form package dates from events.json AND CSV 營運備註 rows."""
    pkg = set()

    # ── From events.json (manual) ──
    events_path = os.path.join(tenant_dir, "events.json")
    if os.path.exists(events_path):
        with open(events_path, encoding="utf-8") as f:
            data = json.load(f)
        for d in data.get("package_dates", []):
            d = d.strip()
            if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
                m, day = int(d[5:7]), int(d[8:10])
                d = f"{m}-{day}"
            pkg.add(d)

    # ── From 營運備註 row in each roster CSV ──
    for fname in os.listdir(tenant_dir):
        if not (fname.endswith(".csv") and "週班表" in fname):
            continue
        path = os.path.join(tenant_dir, fname)
        try:
            with open(path, encoding="utf-8") as f:
                rows = list(csv.reader(f))
        except Exception:
            continue

        # Get date column positions from header row
        date_cols = {}  # col_idx → short_date
        for row in rows[:8]:
            dates = [(j, c.strip().replace('/', '-')) for j, c in enumerate(row)
                     if re.match(r"^\d{1,2}[-/]\d{1,2}$", c.strip())]
            if len(dates) >= 5:
                date_cols = {j: d for j, d in dates}
                break

        if not date_cols:
            continue

        # Find 營運備註 row and check each date column for 包場 text
        for row in rows[:15]:
            if "營運備註" in row[2:5] or any("包場" in c for c in row):
                for col_idx, short_date in date_cols.items():
                    cell = row[col_idx].strip() if col_idx < len(row) else ""
                    if "包場" in cell:
                        pkg.add(short_date)
                break

    return pkg


def is_holiday(short_date: str, holidays: set = None) -> bool:
    try:
        m, d = map(int, short_date.split("-"))
        year = 2026 if m <= 6 else 2025
        full = f"{year}-{m:02d}-{d:02d}"
        dt = datetime.strptime(full, "%Y-%m-%d")
        return dt.weekday() >= 5 or full in (holidays or set())
    except Exception:
        return False


def get_scenario(short_date: str, package_dates: set, holidays: set = None) -> str:
    hol = is_holiday(short_date, holidays)
    pkg = short_date in package_dates
    if hol and pkg:  return "週末包場"
    if hol:          return "週末"
    if pkg:          return "平日包場"
    return "平日"


def get_date_cols(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    for row in rows[:8]:
        dates = [(j, c.strip().replace('/', '-')) for j, c in enumerate(row)
                 if re.match(r"^\d{1,2}[-/]\d{1,2}$", c.strip())]
        if len(dates) >= 5:
            return {d: j for j, d in dates}
    return {}


def run(tenant_dir: str, output_path: str = "habits_demand_shift.json"):
    from data_loader import parse_roster_csv

    # Load tenant config for workstation_roles, scenarios, and holidays
    tenant_config = load_tenant_config(tenant_dir)
    workstation_roles = tenant_config.workstation_roles
    holidays = tenant_config.region_holidays

    package_dates = load_package_dates(tenant_dir)
    roster_files = sorted([
        os.path.join(tenant_dir, f)
        for f in os.listdir(tenant_dir)
        if f.endswith(".csv") and "週班表" in f
    ])

    # matrix[scenario][role][shift_code] = count
    matrix = defaultdict(lambda: defaultdict(Counter))
    all_dates = set()

    for path in roster_files:
        # Get date → scenario mapping for this file
        date_col_map = get_date_cols(path)  # short_date → col_idx (unused here)
        all_dates.update(date_col_map.keys())
        date_scenarios = {d: get_scenario(d, package_dates, holidays) for d in date_col_map}

        employees = parse_roster_csv(path)
        for emp in employees:
            for s in emp.shifts:
                if s.leave_type or not s.start_time or not s.workstation:
                    continue
                scen = date_scenarios.get(s.date, "平日")
                role = workstation_roles.get(s.workstation, "烤手")
                matrix[scen][role][s.workstation] += 1

    # 計算每個情境的天數
    scenario_days = defaultdict(int)
    for d in all_dates:
        scen = get_scenario(d, package_dates, holidays)
        scenario_days[scen] += 1

    SCENARIOS = tenant_config.scenarios
    # Derive unique roles from workstation_roles values
    ROLES = list(dict.fromkeys(workstation_roles.values()))

    result = {}
    SEP = "=" * 58

    for scen in SCENARIOS:
        result[scen] = {}
        days = max(1, scenario_days[scen])
        print(f"\n{SEP}")
        print(f"  {scen}  （{days} 天，以下為每日平均）")
        print(SEP)
        for role in ROLES:
            codes = matrix[scen][role]
            if not codes:
                continue
            total = sum(codes.values())
            avg_total = round(total / days)
            print(f"\n  [{role}]  raw {total} 人次 → 每日 ~{avg_total}")
            daily = {k: round(v / days) for k, v in codes.most_common()}
            for code, avg in sorted(daily.items(), key=lambda x: -x[1]):
                print(f"    {code:<10}{avg:>4} 人/天")
            result[scen][role] = daily

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已儲存 {output_path}")
    return result


if __name__ == "__main__":
    tenant = sys.argv[1] if len(sys.argv) > 1 else "tenants/glod-pig"
    if len(sys.argv) > 2:
        out = sys.argv[2]
    else:
        out_dir = os.path.join(tenant, "output")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, "habits_demand_shift.json")
    run(tenant, out)
