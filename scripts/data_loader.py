#!/usr/bin/env python3
"""
data_loader.py — Shared data models & CSV parser for schedule-agents.

Handles the complex Excel-exported CSV format from tenants like Gold Pig (金豬),
where each employee spans 2 rows (main shift + secondary shift), with Chinese
leave types, workstation codes, and embedded preferences.
"""

import csv
import json
import re
import os
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path


# ─── Constants ────────────────────────────────────────────────────────────────

LEAVE_TYPES = {
    "例假": "regular_day_off",     # Statutory day off (1/week)
    "休假": "rest_day",            # Rest day (1/week)
    "必休": "mandatory_rest",      # Mandatory rest (cannot be called in)
    "放休": "release_rest",        # Release / long-term rest
    "特":   "special_leave",       # Special leave (annual, etc.)
    "病假": "sick_leave",          # Sick leave
    "請假": "personal_leave",      # Personal leave
    "國":   "national_holiday",    # National holiday
    "國+":  "national_holiday_ot", # National holiday + overtime
    "出差": "business_trip",       # Business trip
}

SHIFT_LABELS = {
    "早": "morning",
    "中": "afternoon",
    "晚": "evening",
}

# Workstation codes found in the real data
WORKSTATION_CODES = [
    "B001", "B002", "B003", "B004", "B005",
    "B006", "B007", "B008", "B009", "B010", "B011", "B012",
    "B101", "B102", "B103", "B104", "B105", "B106", "B107", "B108", "B109",
    "櫃台",  # Front counter
]


# ─── Region Holidays ─────────────────────────────────────────────────────────

REGION_HOLIDAYS = {
    "TW": {
        2026: {
            "2026-01-01",                                             # 元旦
            "2026-01-26", "2026-01-27", "2026-01-28", "2026-01-29",  # 春節
            "2026-01-30", "2026-01-31", "2026-02-01",                # 春節
            "2026-02-28",                                             # 和平紀念日
            "2026-04-04", "2026-04-05",                               # 兒童節/清明
            "2026-05-01",                                             # 勞動節
            "2026-06-19",                                             # 端午節
            "2026-09-29",                                             # 中秋節
            "2026-10-10",                                             # 國慶日
        },
    },
}


def get_region_holidays(region: str) -> set:
    """Return all holiday date strings for a region (all years merged)."""
    region_data = REGION_HOLIDAYS.get(region, {})
    holidays = set()
    for year_holidays in region_data.values():
        holidays.update(year_holidays)
    return holidays


def is_holiday_for_region(date_str: str, holidays: set) -> bool:
    """Return True if date is a weekend or in the holidays set."""
    from datetime import datetime
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        if d.weekday() >= 5:  # Sat=5, Sun=6
            return True
        return date_str in holidays
    except ValueError:
        return False


# ─── Tenant Configuration ────────────────────────────────────────────────────

@dataclass
class TenantConfig:
    """Multi-tenant configuration loaded from tenants/<name>/tenant_config.json."""
    tenant_id: str
    display_name: str
    region: str
    timezone: str
    shift_defs: dict           # {code: {start, end, hours}}
    workstation_roles: dict    # {code: role_name}
    scenarios: list = field(default_factory=lambda: ["平日", "平日包場", "週末", "週末包場"])
    min_daily_headcount: dict = field(default_factory=lambda: {
        "weekday": 0, "saturday": 0, "sunday": 0, "package": 0
    })
    constraints: dict = field(default_factory=lambda: {
        "min_rest_hours": 11, "max_weekly_hours": 46,
        "max_working_days": 5, "std_weekly_hours": 40,
    })
    csv_parser: str = "generic"
    region_holidays: set = field(default_factory=set)  # populated by load_tenant_config


def load_tenant_config(tenant_dir: str) -> TenantConfig:
    """Load and validate tenant_config.json from a tenant directory."""
    config_path = os.path.join(tenant_dir, "tenant_config.json")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"tenant_config.json not found in {tenant_dir}")

    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)

    # Strip doc comments (keys starting with _doc)
    data = {k: v for k, v in data.items() if not k.startswith("_doc")}
    for key in ["shift_defs", "workstation_roles", "min_daily_headcount", "constraints"]:
        if isinstance(data.get(key), dict):
            data[key] = {k: v for k, v in data[key].items() if not k.startswith("_doc")}

    # Required fields
    for req in ("tenant_id", "display_name", "region", "timezone", "shift_defs", "workstation_roles"):
        if req not in data:
            raise ValueError(f"tenant_config.json missing required field: {req}")

    # Merge defaults for optional nested dicts
    default_constraints = {"min_rest_hours": 11, "max_weekly_hours": 46,
                           "max_working_days": 5, "std_weekly_hours": 40}
    constraints = {**default_constraints, **(data.get("constraints") or {})}

    default_headcount = {"weekday": 0, "saturday": 0, "sunday": 0, "package": 0}
    headcount = {**default_headcount, **(data.get("min_daily_headcount") or {})}

    # Load region holidays
    holidays = get_region_holidays(data["region"])

    config = TenantConfig(
        tenant_id=data["tenant_id"],
        display_name=data["display_name"],
        region=data["region"],
        timezone=data["timezone"],
        shift_defs=data["shift_defs"],
        workstation_roles=data["workstation_roles"],
        scenarios=data.get("scenarios", ["平日", "平日包場", "週末", "週末包場"]),
        min_daily_headcount=headcount,
        constraints=constraints,
        csv_parser=data.get("csv_parser", "generic"),
        region_holidays=holidays,
    )

    print(f"📋 租戶設定: {config.display_name} ({config.tenant_id})")
    print(f"   班次定義: {len(config.shift_defs)} 個")
    print(f"   角色對應: {len(config.workstation_roles)} 個")
    print(f"   地區假日: {config.region} ({len(config.region_holidays)} 天)")

    return config


# ─── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class ShiftEntry:
    """A single shift assignment for one employee on one day."""
    date: str                           # e.g. "1-5" or "2025-01-05"
    day_of_week: str                    # e.g. "週一" or "monday"
    start_time: Optional[str] = None    # e.g. "11:00" or "1100"
    end_time: Optional[str] = None      # e.g. "15:00" or "1500"
    workstation: Optional[str] = None   # e.g. "B104", "櫃台"
    leave_type: Optional[str] = None    # e.g. "例假", "休假"
    leave_note: Optional[str] = None    # e.g. "請假", "病假" (mid-column notes)
    is_secondary: bool = False          # True if this is the 2nd row (split shift)


@dataclass
class EmployeePreference:
    """Employee preferences extracted from the rightmost columns."""
    available_hours: Optional[str] = None    # e.g. "6-24", "6-28"
    preferred_shift: Optional[str] = None    # e.g. "早", "午、晚", "晚"
    break_preference: Optional[str] = None   # e.g. "希空1H"
    overtime_policy: Optional[str] = None    # e.g. "固加", "不加班", "一周可兩三天加班"
    rotation_policy: Optional[str] = None    # e.g. "可輪班", "可輪早午班"
    notes: Optional[str] = None              # Additional free-text notes


@dataclass
class Employee:
    """An employee parsed from the roster CSV."""
    employee_id: Optional[str] = None    # 職工號
    chinese_name: Optional[str] = None   # 姓名
    english_name: Optional[str] = None   # English nickname
    default_shift: Optional[str] = None  # 早/中/晚 (from column A)
    shifts: list = field(default_factory=list)  # List[ShiftEntry]
    preference: Optional[EmployeePreference] = None
    departure_note: Optional[str] = None  # e.g. "7/13離職"
    weekly_stats: dict = field(default_factory=dict)  # 例假/休假 counts


@dataclass
class Habit:
    """Habit model — the memory layer for an employee."""
    employee_id: str
    chinese_name: str
    english_name: str

    # by_person habits
    preferred_shifts: list = field(default_factory=list)   # ["morning", "afternoon"]
    available_hour_range: Optional[str] = None              # "6-24"
    overtime_willingness: Optional[str] = None              # "fixed_ot" / "no_ot" / "flexible"
    rotation_flexibility: Optional[str] = None              # "full" / "morning_afternoon" / "none"
    workstation_skills: list = field(default_factory=list)  # ["B104", "B003", "櫃台"]
    avg_weekly_hours: float = 0.0
    avg_shifts_per_week: float = 0.0

    # by_shift aggregated stats (filled by analyzer)
    shift_frequency: dict = field(default_factory=dict)    # {"morning": 5, "afternoon": 3}
    workstation_frequency: dict = field(default_factory=dict)  # {"B104": 4, "B003": 2}


@dataclass
class ScheduleEntry:
    """A single entry in the output schedule."""
    date: str
    day_of_week: str
    employee_id: str
    employee_name: str
    shift_start: str
    shift_end: str
    workstation: Optional[str] = None
    leave_type: Optional[str] = None
    workstation_role: Optional[str] = None


# ─── CSV Parser ───────────────────────────────────────────────────────────────

def _normalize_time(raw: str) -> Optional[str]:
    """Normalize time strings like '1100', '11:00', '1000' → 'HH:MM'."""
    if not raw:
        return None
    raw = raw.strip().replace(" ", "")
    if not raw or raw == "-" or raw == "0":
        return None

    # Already in HH:MM format
    if re.match(r'^\d{1,2}:\d{2}$', raw):
        parts = raw.split(":")
        return f"{int(parts[0]):02d}:{parts[1]}"

    # Pure digits like "1100", "800", "1500"
    if re.match(r'^\d{3,4}$', raw):
        raw = raw.zfill(4)
        return f"{raw[:2]}:{raw[2:]}"

    return None


def _is_leave_type(val: str) -> Optional[str]:
    """Check if a cell value is a recognized leave type."""
    val = val.strip()
    for zh, en in LEAVE_TYPES.items():
        if val == zh or val.startswith(zh):
            return zh
    return None


def _is_employee_row(row: list, col_offset: int = 0) -> bool:
    """
    Check if a row is a main employee data row.
    Employee rows have: col C = 職工號 (or departure note), col D = Chinese name, col E = number, col F = English name.
    Based on observed format: columns [2]=note, [3]=Chinese name, [4]=number, [5]=English name.
    """
    if len(row) < col_offset + 6:
        return False

    name_col = row[col_offset + 3].strip()
    id_col = row[col_offset + 4].strip()
    nick_col = row[col_offset + 5].strip()

    # Must have a Chinese name (at least 2 Chinese characters)
    if not name_col or len(name_col) < 2:
        return False

    # Employee ID should be a number
    if id_col and id_col.isdigit():
        return True

    return False


def _extract_day_columns(header_row: list) -> list:
    """
    Extract the date columns from header row 3 (the one with dates like 1-5, 1-6, ...).
    Returns list of (col_index, date_string) tuples.
    Accepts both '-' and '/' separators, normalizes to '-'.
    """
    dates = []
    for i, cell in enumerate(header_row):
        cell = cell.strip()
        # Match date patterns like "1-5", "1/6", "12-29", "2/16"
        if re.match(r'^\d{1,2}[-/]\d{1,2}$', cell):
            dates.append((i, cell.replace('/', '-')))
    return dates


def _extract_weekday_row(row: list) -> dict:
    """Extract weekday labels from header row 4 (週一, 週二, ...)."""
    weekdays = {}
    for i, cell in enumerate(row):
        cell = cell.strip()
        if cell in ("週一", "週二", "週三", "週四", "週五", "週六", "週日"):
            weekdays[i] = cell
    return weekdays


def parse_roster_csv(csv_path: str) -> list:
    """
    Parse a Gold Pig style weekly roster CSV.

    The CSV structure (per-week block for 7 days):
    - Row 1: 午包 counts
    - Row 2: blank
    - Row 3: Header with tenant name, dates (1-5, 1-6, ...)
    - Row 4: Weekday labels (週一, 週二, ...)
    - Row 5-6: Shift time headers (早/中/晚 times, staffing info)
    - Row 7+: Employee data (2 rows per employee)

    Each day-block has ~6 columns: start_time, leave_note, end_time, workstation, productivity, hours

    Returns: list of Employee objects.
    """
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)

    if len(rows) < 10:
        raise ValueError(f"CSV too short ({len(rows)} rows), expected at least 10")

    # ── Find header rows ──
    # Row index 2 (0-based) typically has dates
    date_row_idx = None
    weekday_row_idx = None

    for i in range(min(10, len(rows))):
        row = rows[i]
        dates_found = _extract_day_columns(row)
        if len(dates_found) >= 5:  # At least 5 dates for a valid week
            date_row_idx = i
            break

    if date_row_idx is None:
        raise ValueError("Could not find date header row in CSV")

    dates = _extract_day_columns(rows[date_row_idx])

    # Weekday row is typically the next row
    if date_row_idx + 1 < len(rows):
        weekday_map = _extract_weekday_row(rows[date_row_idx + 1])
        weekday_row_idx = date_row_idx + 1

    # ── Determine per-day column structure ──
    # Each day has a block of columns. The first date tells us the starting column.
    # Typical block width = 6 columns (start, note, end, workstation, productivity, hours)
    if len(dates) >= 2:
        day_col_width = dates[1][0] - dates[0][0]
    else:
        day_col_width = 6  # Default

    # ── Find the employee name header row (職工號/姓名 row) ──
    emp_start_row = None
    for i in range(date_row_idx + 2, min(date_row_idx + 10, len(rows))):
        row_text = ",".join(rows[i][:10])
        if "職工號" in row_text or "姓名" in row_text:
            emp_start_row = i
            break

    if emp_start_row is None:
        # Fallback: look for first row with a numeric employee ID
        for i in range(date_row_idx + 2, len(rows)):
            if _is_employee_row(rows[i]):
                emp_start_row = i
                break

    if emp_start_row is None:
        raise ValueError("Could not find employee data rows in CSV")

    # ── Parse employees ──
    employees = []
    i = emp_start_row

    while i < len(rows):
        row = rows[i]

        # Skip completely empty rows
        if not any(cell.strip() for cell in row[:20]):
            i += 1
            continue

        # Check if this is an employee header row
        if not _is_employee_row(row):
            # Could be a summary row, section header, or the secondary shift row
            # (secondary rows don't have employee info in cols 3-5)
            i += 1
            continue

        # ── Extract employee info ──
        default_shift_label = row[0].strip() if len(row) > 0 else ""
        departure_note_col = row[2].strip() if len(row) > 2 else ""
        chinese_name = row[3].strip() if len(row) > 3 else ""
        emp_id = row[4].strip() if len(row) > 4 else ""
        english_name = row[5].strip() if len(row) > 5 else ""

        departure_note = None
        if "離職" in departure_note_col:
            departure_note = departure_note_col

        emp = Employee(
            employee_id=emp_id if emp_id.isdigit() else None,
            chinese_name=chinese_name,
            english_name=english_name,
            default_shift=SHIFT_LABELS.get(default_shift_label, default_shift_label),
            departure_note=departure_note,
        )

        # ── Extract shifts for each day (main row) ──
        for date_col_idx, date_str in dates:
            shift = _parse_shift_from_row(row, date_col_idx, date_str, day_col_width,
                                         weekday_map, is_secondary=False)
            if shift:
                emp.shifts.append(shift)

        # ── Check for secondary row (split shift, next row) ──
        if i + 1 < len(rows):
            next_row = rows[i + 1]
            # Secondary row: no employee info in cols 3-5, but has shift data
            next_has_name = (len(next_row) > 3 and next_row[3].strip() and
                            len(next_row[3].strip()) >= 2 and
                            len(next_row) > 4 and next_row[4].strip().isdigit())

            if not next_has_name and any(cell.strip() for cell in next_row[6:40]):
                # This is the split-shift continuation row
                for date_col_idx, date_str in dates:
                    shift = _parse_shift_from_row(next_row, date_col_idx, date_str,
                                                 day_col_width, weekday_map,
                                                 is_secondary=True)
                    if shift:
                        emp.shifts.append(shift)
                i += 1  # Skip the secondary row

        # ── Extract preferences from rightmost columns ──
        emp.preference = _parse_preferences(row, dates)

        # ── Extract weekly stats ──
        emp.weekly_stats = _parse_weekly_stats(row, dates)

        employees.append(emp)
        i += 1

    return employees


def _parse_shift_from_row(row: list, date_col: int, date_str: str,
                          col_width: int, weekday_map: dict,
                          is_secondary: bool) -> Optional[ShiftEntry]:
    """
    Parse a shift entry from a row for a given date column.

    Day block layout (6 cols typically):
    [date_col+0]=start_time, [+1]=leave_note, [+2]=end_time,
    [+3]=workstation, [+4]=productivity, [+5]=hours
    """
    if date_col + 3 >= len(row):
        return None

    start_raw = row[date_col].strip() if date_col < len(row) else ""
    note_raw = row[date_col + 1].strip() if date_col + 1 < len(row) else ""
    end_raw = row[date_col + 2].strip() if date_col + 2 < len(row) else ""
    ws_raw = row[date_col + 3].strip() if date_col + 3 < len(row) else ""

    # Check if the whole block is a leave type
    leave = None
    for val in [start_raw, note_raw]:
        lt = _is_leave_type(val) if val else None
        if lt:
            leave = lt
            break

    # Determine weekday from the map
    weekday = ""
    for col_i, wd in weekday_map.items():
        # The weekday label column should be near the date column
        if abs(col_i - date_col) <= col_width:
            weekday = wd
            break

    if leave:
        return ShiftEntry(
            date=date_str,
            day_of_week=weekday,
            leave_type=leave,
            is_secondary=is_secondary,
        )

    start_time = _normalize_time(start_raw)
    end_time = _normalize_time(end_raw)

    if not start_time and not end_time:
        return None  # Empty day block

    # Leave note in the middle column (e.g., 請假, 病假, 報到)
    leave_note = None
    if note_raw and note_raw not in (",", "-", " "):
        lt = _is_leave_type(note_raw)
        if lt:
            leave_note = note_raw

    # Workstation
    workstation = None
    if ws_raw and ws_raw in WORKSTATION_CODES:
        workstation = ws_raw

    return ShiftEntry(
        date=date_str,
        day_of_week=weekday,
        start_time=start_time,
        end_time=end_time,
        workstation=workstation,
        leave_type=None,
        leave_note=leave_note,
        is_secondary=is_secondary,
    )


def _parse_preferences(row: list, dates: list) -> Optional[EmployeePreference]:
    """
    Extract employee preferences from tail columns (far right of the CSV).

    In the Gold Pig format, after the 7-day roster + monthly calendar sections,
    the last non-empty columns contain preference data in this order:
      available_hours (e.g. '6-24') | preferred_shift | break_pref | overtime | rotation | notes
    These start around column 117-130+ depending on file width.
    """
    if not dates:
        return None

    pref = EmployeePreference()

    # Preference columns are deep in the row — search from col 100 onward,
    # but skip the monthly calendar (cols ~54-116 which repeat 例/休 labels).
    # We look for the clustered non-empty values at the very end.
    tail_values = []
    for i in range(100, len(row)):
        val = row[i].strip()
        if not val or val in ("-", "0", ","):
            continue
        # Skip single-char calendar markers (例, 休, 國, 特, etc.)
        if len(val) <= 1:
            continue
        # Skip cells that are purely numeric (calendar day numbers)
        if val.isdigit():
            continue
        tail_values.append(val)

    for val in tail_values:
        # Available hours: '6-24', '6-28'
        if re.match(r'^\d{1,2}-\d{2}$', val) and not pref.available_hours:
            pref.available_hours = val
        # Shift preference: '早', '午', '晚', '早、午', '午、晚'
        elif any(s in val for s in ["早", "午", "晚"]) and not pref.preferred_shift:
            pref.preferred_shift = val
        # Break preference: '希空1H', '希偶爾午班'
        elif ("希" in val or ("空" in val and "班" not in val)) and not pref.break_preference:
            pref.break_preference = val
        # Overtime: '固加', '不加班', '一周可兩三天加班', '可+'
        elif any(k in val for k in ["加班", "固加", "不加", "可+"]) and not pref.overtime_policy:
            pref.overtime_policy = val
        # Rotation: '可輪班', '可輪早午班'
        elif "輪" in val and not pref.rotation_policy:
            pref.rotation_policy = val
        # Free-text notes (longer strings not yet captured)
        elif len(val) > 3 and not pref.notes:
            pref.notes = val

    if all(v is None for v in [pref.available_hours, pref.preferred_shift,
                                pref.break_preference, pref.overtime_policy,
                                pref.rotation_policy]):
        return None

    return pref


def _parse_weekly_stats(row: list, dates: list) -> dict:
    """Extract weekly leave statistics and hours from the stats columns."""
    stats = {}
    if not dates:
        return stats

    # 'hours' marker is at last_date_col + day_col_width (typically +6)
    last_date_col = max(d[0] for d in dates)

    # Search wider range to find 'hours' marker
    for i in range(last_date_col + 4, min(last_date_col + 15, len(row))):
        val = row[i].strip() if i < len(row) else ""
        if val == "hours":
            # After 'hours': [例假, 休假, total_rest, +rest, monthly_rest]
            for offset, key in enumerate(["例假_count", "休假_count", "total_rest_days"], 1):
                j = i + offset
                cell = row[j].strip() if j < len(row) else ""
                # Skip dashes
                if cell in ("-", " - ", ""):
                    continue
                try:
                    stats[key] = int(cell)
                except ValueError:
                    pass
            break

    return stats


# ─── CSV Parser Registry ─────────────────────────────────────────────────────

_CSV_PARSERS = {
    "gold_pig_v1": parse_roster_csv,   # Gold Pig style multi-row per employee
    "generic": parse_roster_csv,       # Alias — defaults to gold_pig_v1 for now
}


def get_csv_parser(parser_id: str):
    """Return the CSV parser function for a given parser ID.
    Raises ValueError if the parser ID is not registered.
    """
    parser = _CSV_PARSERS.get(parser_id)
    if parser is None:
        available = ", ".join(sorted(_CSV_PARSERS.keys()))
        raise ValueError(f"Unknown CSV parser: '{parser_id}'. Available: {available}")
    return parser


def register_csv_parser(parser_id: str, parser_func):
    """Register a new CSV parser function.
    Args:
        parser_id: Unique identifier for the parser.
        parser_func: Callable that takes a CSV path and returns list of Employee.
    """
    _CSV_PARSERS[parser_id] = parser_func


# ─── Serialization helpers ────────────────────────────────────────────────────

def employees_to_json(employees: list, output_path: str):
    """Serialize a list of Employee objects to JSON."""
    data = []
    for emp in employees:
        d = asdict(emp)
        data.append(d)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 已儲存 {len(employees)} 位員工資料至 {output_path}")


def habits_to_json(habits: list, output_path: str):
    """Serialize a list of Habit objects to JSON."""
    data = [asdict(h) for h in habits]
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 已儲存 {len(habits)} 位員工習慣資料至 {output_path}")


def load_habits_json(input_path: str) -> list:
    """Load habits from JSON file back into Habit objects."""
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    habits = []
    for d in data:
        h = Habit(**{k: v for k, v in d.items() if k in Habit.__dataclass_fields__})
        habits.append(h)
    return habits


def merge_staff_roles(habits: list, tenant_dir: str):
    """
    Load tenants/<name>/staff_roles.json (if exists) and override
    workstation_skills for each employee listed in it.

    staff_roles.json format:
      {
        "_manager_group": { ... },          // metadata keys (start with '_')
        "3":  { "_name": "史曜誠 (Money)", "skills": ["烤手", "櫃台"] },
        "5":  { "_name": "吳亭穎 (Ting)",  "skills": ["烤手", "服務", "櫃台"] }
      }
    Keys starting with '_' are metadata and are skipped during merge.
    Skills are ordered by priority (first = highest priority).
    """
    roles_path = os.path.join(tenant_dir, "staff_roles.json")
    if not os.path.exists(roles_path):
        return

    with open(roles_path, encoding="utf-8") as f:
        staff_roles = json.load(f)

    overridden = 0
    for habit in habits:
        entry = staff_roles.get(habit.employee_id)
        if entry and isinstance(entry.get("skills"), list):
            habit.workstation_skills = entry["skills"]
            overridden += 1

    print(f"   📋 staff_roles.json: 覆蓋 {overridden} 位員工技能")


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python data_loader.py <CSV路徑> [輸出JSON路徑]")
        print("範例: python data_loader.py tenants/glod-pig/'row 1月週班表(外場) _0105-0111.csv'")
        sys.exit(1)

    csv_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "parsed_roster.json"

    employees = parse_roster_csv(csv_path)
    print(f"\n📊 解析結果:")
    print(f"   員工數: {len(employees)}")

    active = [e for e in employees if not e.departure_note]
    print(f"   在職員工: {len(active)}")
    print(f"   離職員工: {len(employees) - len(active)}")

    for emp in employees[:3]:
        print(f"\n   👤 {emp.chinese_name} ({emp.english_name}) - #{emp.employee_id}")
        print(f"      預設班別: {emp.default_shift}")
        print(f"      班次數: {len(emp.shifts)}")
        if emp.preference:
            print(f"      偏好: {emp.preference.available_hours or '-'} / "
                  f"{emp.preference.preferred_shift or '-'} / "
                  f"{emp.preference.overtime_policy or '-'}")

    employees_to_json(employees, output_path)
