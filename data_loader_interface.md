# Data Loader Output Interface

本文件提供 [scripts/data_loader.py](file:///Users/harper_yeh/schedule-agents/scripts/data_loader.py) 輸出的資料結構定義（Schema），供後續系統開發與串接使用。

[data_loader.py](file:///Users/harper_yeh/schedule-agents/scripts/data_loader.py) 的核心職責是將：
1. [tenant_config.json](file:///Users/harper_yeh/schedule-agents/tenants/glod-pig/tenant_config.json) (租戶設定檔)
2. `*.csv` (歷史排班資料)
3. [habits.json](file:///Users/harper_yeh/schedule-agents/tenants/glod-pig/output/habits.json) (分析後的員工習慣與需求)
轉換為 Python 物件與標準化的資料結構，供 Analyzer、Demand Analysis、Scheduler 與 Auditor 使用。

---

## 1. 歷史資料解析輸出 (CSV Parser Output)

**主要進入點**: [parse_roster_csv(csv_path: str) -> list[Employee]](file:///Users/harper_yeh/schedule-agents/scripts/data_loader.py#358-502)

將原始的排班 CSV 轉換為 [Employee](file:///Users/harper_yeh/schedule-agents/scripts/data_loader.py#227-238) 物件列表。這是 Analyzer 進行習慣分析的**原始訓練資料**。

### [Employee](file:///Users/harper_yeh/schedule-agents/scripts/data_loader.py#227-238) 物件結構
代表一位員工在**單一週次（或單一檔案）**中的歷史排班與偏好紀錄。

```python
class Employee:
    employee_id: Optional[str]        # 員工編號 (職工號)，例如 "1"
    chinese_name: Optional[str]      # 中文姓名，例如 "林靜宜"
    english_name: Optional[str]      # 英文姓名 (或別名)，例如 "Sherry"
    default_shift: Optional[str]     # 預設班次標籤 (來自 CSV A 欄)
    departure_note: Optional[str]    # 離職註記 (若有)
    shifts: list[ShiftEntry]         # 該週內的實際排班紀錄列表
    preference: Optional[EmployeePreference] # 員工偏好設定 (來自 CSV 右側欄位)
    weekly_stats: dict               # 該週統計資料 (休假天數、總工時等)
```

### [ShiftEntry](file:///Users/harper_yeh/schedule-agents/scripts/data_loader.py#203-214) 物件結構
代表員工在**單一天**的一個班次紀錄。若員工有雙頭班，會有兩個或多個 [ShiftEntry](file:///Users/harper_yeh/schedule-agents/scripts/data_loader.py#203-214)，透過 `is_secondary` 區分。

```python
class ShiftEntry:
    date: str                        # 日期，格式: "YYYY-MM-DD"
    day_of_week: str                 # 星期字串，例如 "週一"
    start_time: Optional[str]        # 上班時間，正規化為 "HH:MM" (e.g. "10:00")
    end_time: Optional[str]          # 下班時間，正規化為 "HH:MM"
    workstation: Optional[str]       # 工作站代碼，例如 "B003", "櫃台(早)"
    leave_type: Optional[str]        # 休假類型 (事假, 病假, 特休等)，如果不是休假則為 None
    leave_note: Optional[str]        # 備註說名
    is_secondary: bool = False       # 是否為同一天的第二個（雙頭班）班次
```

### [EmployeePreference](file:///Users/harper_yeh/schedule-agents/scripts/data_loader.py#216-225) 物件結構
萃取自 CSV 最右側的偏好設定欄位。

```python
class EmployeePreference:
    available_hours: Optional[str]   # 可用時段 (e.g. "6-24", "10-23")
    preferred_shift: Optional[str]   # 偏好班別 (e.g. "早", "晚", "全")
    break_preference: Optional[str]  # 休息偏好 (e.g. "兩頭班", "不限")
    overtime_policy: Optional[str]   # 加班意願 (e.g. "固定加班", "不加班")
    rotation_policy: Optional[str]   # 輪班配合度 (e.g. "輪早中", "全配合")
    notes: Optional[str]             # 其他備註
```

---

## 2. 員工屬性與習慣模型 (Habit Model)

**主要進入點**: 
- [load_habits_json(input_path: str) -> list[Habit]](file:///Users/harper_yeh/schedule-agents/scripts/data_loader.py#712-721)
- 此資料由 Analyzer 產出，並儲存為 `{tenant_dir}/output/habits.json`。

這是 Scheduler (排班) 和 Auditor (稽核) 使用的**核心員工配置檔案**。

### [Habit](file:///Users/harper_yeh/schedule-agents/scripts/data_loader.py#240-261) 物件結構

```python
class Habit:
    employee_id: str                 # 員工唯一識別碼 (Primary Key)
    chinese_name: str                # 中文姓名
    english_name: str                # 英文名/別名
    
    # --- 歷史資料統計特徵 (由 Analyzer 產出) ---
    preferred_shifts: list[str]      # 最常上的 M 個班次代碼 (e.g. ["B104", "B103"])
    available_hour_range: Optional[str] # 推算的可用時段區間
    overtime_willingness: Optional[str] # 推算的加班彈性
    rotation_flexibility: Optional[str] # 推算的輪班彈性
    avg_weekly_hours: float          # 歷史平均每週工時
    avg_shifts_per_week: float       # 歷史平均每週排班天數
    shift_frequency: dict[str, int]  # 歷史上班次數統計 (e.g. {"B104": 6, "B003": 2})
    workstation_frequency: dict[str, int] # 歷史工作站/角色統計 (e.g. {"烤手": 8})

    # --- 租戶設定覆蓋屬性 (由 RULES.md/tenant_config 等定義) ---
    workstation_skills: list[str]    # 該員工具備的技能清單 (e.g. ["烤手"], ["領檯早", "烤手"])
    employee_type: str = "ft"        # "ft" (正職 full-time) 或 "pt" (兼職 part-time)
    is_manager: bool = False         # 是否為管理職/幹部
```

---

## 3. 租戶系統設定檔 (Tenant Configuration)

**主要進入點**: [load_tenant_config(tenant_dir: str) -> TenantConfig](file:///Users/harper_yeh/schedule-agents/scripts/data_loader.py#117-199)

讀取並驗證 [tenant_config.json](file:///Users/harper_yeh/schedule-agents/tenants/glod-pig/tenant_config.json) 的內容。

### [TenantConfig](file:///Users/harper_yeh/schedule-agents/scripts/data_loader.py#91-115) 物件結構

```python
class TenantConfig:
    tenant_id: str                   # 租戶 ID (e.g. "glod-pig")
    display_name: str                # 顯示名稱 (e.g. "金豬 燒肉")
    region: str                      # 節假日地區 (e.g. "TW")
    timezone: str                    # 時區 (e.g. "Asia/Taipei")
    
    # 班次對應設定
    shift_defs: dict[str, dict]      # 所有班次的時間定義 
                                     # { "B003": {"start": "10:00", "end": "19:00", "hours": 8} }
    workstation_roles: dict[str, str] # 班次(或原始代碼) 對應 技能角色 
                                     # { "B003": "烤手", "櫃台(早)": "領檯早" }
    csv_code_aliases: dict[str, list[str]] # CSV 模糊代碼展開規則
                                     # { "櫃台": ["櫃台(早)", "櫃台(晚)"] }
    
    # 需求配置
    scenarios: list[str]             # 需求分析支援的情境 (e.g. ["平日", "平日包場", "週末"])
    min_daily_headcount: dict        # 各情境每日基準總人數 {"weekday": 18, "saturday": 23}
    min_role_per_day: dict           # 各角色每日硬性最低人數配置 {"領檯早": 1, "領檯晚": 1}
    coverage_targets: dict           # 稽核用：特定時間點的最低保障人力
                                     # { "10:00": {"min": 2, "label": "早班"} }
    
    # 排班引擎限制與勞基法參數
    constraints: dict                # {
                                     #   "min_rest_hours": 11,
                                     #   "max_weekly_hours": 46,
                                     #   "max_working_days": 5, ...
                                     # }   
    
    # 特殊業務邏輯規則 (衍生自 RULES.md)
    manager_constraints: dict        # 主管排班規則
                                     # {
                                     #   "daily_early_count": 1, 
                                     #   "early_shifts": ["B003"],
                                     #   "exclude_from_headcount": true ...
                                     # }
    no_same_rest: list[list[str]]    # 不允許同一天休假的員工配對 ID list (e.g. [["4", "6"]])
    
    # 系統執行期衍生
    region_holidays: set[str]        # 依地區年份快取的假日日期字串 (e.g. {"2026-01-01"})
    csv_parser: str                  # 使用的 CSV 解析器類型 (預設 "generic" / "gold_pig_v1")
```

---

## 4. 排班結果輸出 (Schedule Output)

**主要進入點**: [run.py](file:///Users/harper_yeh/schedule-agents/scripts/run.py) 或 Scheduler 生成的 `schedule.csv` / `schedule.json`

輸出符合約定格式的班表，提供前端 UI 渲染或系統整合。

### [ScheduleEntry](file:///Users/harper_yeh/schedule-agents/scripts/data_loader.py#263-275) 物件結構 (序列化為 JSON / CSV)

```python
class ScheduleEntry:
    date: str                        # 日期: "YYYY-MM-DD"
    day_of_week: str                 # 星期: "週一"
    employee_id: str                 # 員工 ID: "1"
    employee_name: str               # 員工姓名: "林靜宜 (Sherry)"
    shift_start: str                 # 班次開始時間 (若為休假則為 "--:--")
    shift_end: str                   # 班次結束時間 (若為休假則為 "--:--")
    workstation: Optional[str]       # 實際排入的班次代碼 (e.g. "B104")，修假為預設班次
    workstation_role: Optional[str]  # 對應系統判定的角色/技能 (e.g. "烤手")
    leave_type: Optional[str]        # "休假", "病假" 或為空字串 (如果是上班)
```

## JSON 序列化輔助函數

如果後端 API 需要直接吐出 JSON 字串或回傳 dict，可使用內建函數：

- [employees_to_json(employees: list, output_path: str)](file:///Users/harper_yeh/schedule-agents/scripts/data_loader.py#693-702)
- [habits_to_json(habits: list, output_path: str)](file:///Users/harper_yeh/schedule-agents/scripts/data_loader.py#704-710)

這些函數內部利用 `__dict__` 進行轉換，生成的 JSON 結構與上述 Python 物件結構 1:1 對應。
