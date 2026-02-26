# Schedule Agents — CLAUDE.md

> **定位**：本文件是整個 schedule-agents 專案的頂層導航。Claude（或任何 AI agent）進入專案時，應**第一時間讀取此文件**，再根據任務決定深入哪份 SKILL.md 或租戶設定。

---

## 1. 專案結構

```
schedule-agents/
├── CLAUDE.md                          ← 你正在讀的這份（頂層導航）
├── product-spec.md                    ← 產品規格書（概念層）
├── agent-settings-backend-spec.md     ← 設定後台規格（Layer 0-6 架構）
│
├── scripts/                           ← 五支核心腳本
│   ├── run.py                         ← 統一 CLI（--tenant / --all-tenants）
│   ├── data_loader.py                 ← 資料模型 + CSV 解析器 registry
│   ├── analyzer.py                    ← Analyzer Agent：身份識別 + 習慣計算
│   ├── demand_shift_analysis.py       ← 需求分析：四情境 × 角色 × 班次代碼
│   ├── ortools_solver.py              ← Scheduler Agent：CP-SAT 排班求解器
│   └── auditor_tools.py               ← Auditor Agent：P0/Hard/P1/P2 稽核
│
├── skills/                            ← 各 Agent 的操作手冊
│   ├── analyzer/SKILL.md
│   ├── scheduler/SKILL.md
│   └── auditor/SKILL.md
│
└── tenants/                           ← 多租戶目錄（每店一個子資料夾）
    ├── TEMPLATE/                      ← 空白租戶範本（複製此目錄建立新租戶）
    │   ├── tenant_config.json         ← 店面設定 schema（班次、角色、人力需求）
    │   ├── staff_roles.json           ← 員工技能與主管設定
    │   ├── events.json                ← 包場 / 特殊事件日期
    │   ├── rest_days.json             ← 指定劃休
    │   ├── RULES.md                   ← 店面商業規則（人類可讀）
    │   └── output/                    ← 所有產出檔（自動建立）
    ├── glod-pig/                      ← 金豬 燒肉（現有租戶）
    └── nara/                          ← 奈良（待建立）
```

---

## 2. 多租戶架構

### 2.1 核心原則

**所有店面特定的設定都在 `tenants/<tenant>/` 內，腳本本身不得 hard-code 任何租戶邏輯。**

| 層級 | 來源 | 說明 |
|------|------|------|
| 班次定義 (`SHIFT_DEFS`) | `tenant_config.json → shift_defs` | 每店的班次代碼、時間、工時不同 |
| 角色對應 (`WORKSTATION_ROLE_MAP`) | `tenant_config.json → workstation_roles` | 烤手 / 領檯 是 glod-pig 的角色名稱，其他店不同 |
| 每日最低人力 | `tenant_config.json → min_daily_headcount` | 平日 / 週六 / 週日 / 包場 各有不同門檻 |
| 情境分類 | `tenant_config.json → scenarios` | 預設四種，可自訂 |
| 假日表 | `tenant_config.json → region` | 依地區載入（TW / JP / ...） |
| 約束參數 | `tenant_config.json → constraints` | 最低休息時數、每週上限工時、最大工作天數 |
| CSV 解析器 | `tenant_config.json → csv_parser` | 不同店面可能有不同的班表匯出格式 |
| 主管配置 | `staff_roles.json → _manager_group` | 主管名單、每日早晚班最低人數 |
| 包場日期 | `events.json → package_dates` | 手動補充或從 CSV 營運備註自動偵測 |
| 指定劃休 | `rest_days.json → designated_rest` | 員工指定休假日 |
| 商業規則 | `RULES.md` | 人類可讀的排班規範（供 Claude 參考決策） |

### 2.2 tenant_config.json Schema

```json
{
  "tenant_id": "glod-pig",
  "display_name": "金豬 燒肉",
  "region": "TW",
  "timezone": "Asia/Taipei",

  "shift_defs": {
    "B001": { "start": "08:00", "end": "17:00", "hours": 8 },
    "B002": { "start": "09:00", "end": "18:00", "hours": 8 }
  },

  "workstation_roles": {
    "B001": "烤手",
    "B002": "烤手",
    "櫃台(早)": "領檯早",
    "櫃台(晚)": "領檯晚"
  },

  "scenarios": ["平日", "平日包場", "週末", "週末包場"],

  "min_daily_headcount": {
    "weekday": 18,
    "saturday": 23,
    "sunday": 22,
    "package": 19
  },

  "constraints": {
    "min_rest_hours": 11,
    "max_weekly_hours": 46,
    "max_working_days": 5,
    "std_weekly_hours": 40
  },

  "csv_parser": "gold_pig_v1"
}
```

| 欄位 | 必填 | 說明 |
|------|------|------|
| `tenant_id` | ✅ | 與目錄名稱一致 |
| `display_name` | ✅ | 顯示用名稱 |
| `region` | ✅ | 地區代碼，決定假日表（`TW` / `JP` / `US` / ...） |
| `timezone` | ✅ | IANA 時區 |
| `shift_defs` | ✅ | 所有班次定義 `{ code: { start, end, hours } }` |
| `workstation_roles` | ✅ | 班次代碼 → 崗位角色的對應表 |
| `scenarios` | ⬚ | 情境名稱列表，預設 `["平日", "平日包場", "週末", "週末包場"]` |
| `min_daily_headcount` | ⬚ | 各情境最低人力，預設全 0（不強制） |
| `constraints` | ⬚ | 勞動約束覆寫，未設定則用預設值 |
| `csv_parser` | ⬚ | CSV 解析器 ID，預設 `"generic"` |

---

## 3. 執行流程（Pipeline）

### 3.1 推薦：使用 run.py（統一 CLI）

```bash
# 單一租戶完整 pipeline（產出自動寫入 tenants/glod-pig/output/）
python scripts/run.py --tenant glod-pig --week 2026-03-02

# 只跑某一步驟
python scripts/run.py --tenant glod-pig --week 2026-03-02 --step scheduler

# 跨週排班（傳入前週班表）
python scripts/run.py --tenant glod-pig --week 2026-03-09 \
  --prev-schedule tenants/glod-pig/output/schedule_20260302.csv

# 所有租戶一次跑完
python scripts/run.py --all-tenants --week 2026-03-02
```

### 3.2 手動逐步執行

```bash
TENANT=glod-pig
WEEK=2026-03-02
TENANT_DIR=tenants/$TENANT
OUT=$TENANT_DIR/output

# Step 1: Analyzer — 分析歷史班表
python scripts/analyzer.py $TENANT_DIR/ $OUT/habits.json

# Step 2: Demand Analysis — 分析四情境需求分布
python scripts/demand_shift_analysis.py $TENANT_DIR $OUT/habits_demand_shift.json

# Step 3: Scheduler — CP-SAT 求解排班
python scripts/ortools_solver.py \
  $OUT/habits.json \
  $OUT/habits_demand_shift.json \
  $OUT/schedule_20260302 \
  $WEEK \
  $TENANT_DIR

# Step 4: Auditor — 稽核排班結果
python scripts/auditor_tools.py \
  $OUT/schedule_20260302.csv \
  $OUT/habits.json \
  $OUT/audit_20260302.json \
  $TENANT_DIR \
  $WEEK
```

### 3.3 跨週排班（含前週約束）

```bash
# 第二週排班時，傳入前週班表做跨週約束
python scripts/run.py --tenant glod-pig --week 2026-03-09 \
  --prev-schedule tenants/glod-pig/output/schedule_20260302.csv
```

### 3.4 閉環：Auditor → Scheduler 迭代

若 Auditor 回報 P0 / P1 違規，應：
1. 分析 `tenants/<t>/output/audit_<YYYYMMDD>_<TIMESTAMP>.json` 中的 violations
2. 調整 `tenant_config.json` 的 constraints 或 `rest_days.json`
3. 重新執行 Scheduler → Auditor
4. 重複直到 P0 = 0、P1 可接受

---

## 4. 新增租戶 SOP

```bash
# 1. 從 TEMPLATE 複製
cp -r tenants/TEMPLATE tenants/<new-tenant>

# 2. 編輯 tenant_config.json
#    - 填入 tenant_id, display_name, region, timezone
#    - 定義所有班次代碼 (shift_defs)
#    - 定義角色對應 (workstation_roles)
#    - 設定每日人力需求 (min_daily_headcount)

# 3. 編輯 staff_roles.json
#    - 列出所有員工的技能
#    - 定義 _manager_group（如有主管制度）
#    - 定義 _no_same_rest（禁同休配對）

# 4. 編輯 RULES.md
#    - 寫下店面的排班商業規則（供 Claude 參考）

# 5. 放入歷史班表 CSV

# 6. 執行 Pipeline 驗證
python scripts/analyzer.py tenants/<new-tenant>/ habits.json
```

---

## 5. 腳本依賴關係

```
data_loader.py ← 被所有腳本 import
    │
    ├── analyzer.py          (imports: data_loader)
    ├── demand_shift_analysis.py (imports: data_loader)
    ├── ortools_solver.py    (imports: data_loader)
    └── auditor_tools.py     (imports: data_loader, ortools_solver)
```

> **✅ 已完成**：所有腳本已改為從 `tenant_config.json` 動態載入設定（透過 `load_tenant_config()`），
> 不再依賴模組層級的 hard-coded 常數。`SHIFT_DEFS = {}` 仍保留為空 dict 佔位，
> 可在確認所有外部消費者都已遷移後移除。

---

## 6. 重要約束

1. **不得在 scripts/ 中 hard-code 租戶特定值**（班次、角色、人力需求、假日）
2. **所有產出檔寫入 `tenants/<tenant>/output/`**，檔名慣例：
   - 習慣檔：`output/habits.json`（另含 `habits_coverage.json`、`habits_demand.json`）
   - 需求分析：`output/habits_demand_shift.json`
   - 排班輸出：`output/schedule_<YYYYMMDD>_<TIMESTAMP>.csv` / `.json`
   - 稽核報告：`output/audit_<YYYYMMDD>_<TIMESTAMP>.json`
   - **不同週的排班和稽核檔並存**（以 `<YYYYMMDD>` 區分）
3. **tenant_config.json 是 single source of truth**，scripts 透過它取得所有店面設定
4. **RULES.md 是給 Claude 的決策參考**，不直接被腳本解析，但 Claude 在做排班決策或回答問題時應讀取

---

## 7. 常見任務索引

| 任務 | 讀什麼 | 跑什麼 |
|------|--------|--------|
| 分析新員工的排班習慣 | `skills/analyzer/SKILL.md` | `analyzer.py` |
| 產出新一週班表 | `skills/scheduler/SKILL.md` | `ortools_solver.py` |
| 稽核班表合規性 | `skills/auditor/SKILL.md` | `auditor_tools.py` |
| 分析店面人力需求分布 | — | `demand_shift_analysis.py` |
| 新增一個租戶 | 本文件 §4 | 手動建立 + `analyzer.py` 驗證 |
| 調整排班規則 | `tenants/<t>/RULES.md` + `tenant_config.json` | 重跑 Scheduler |
| 員工指定休假 | `tenants/<t>/rest_days.json` | 重跑 Scheduler |
