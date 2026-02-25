# AI 排班助手 — 產品規格書

> **文件定位**：本文件是排班助手的完整產品規格，定義「習慣」為核心資料模型，串連分析（Analyzer）、排班（Scheduler）、檢核（Auditor）三大 Agent 的閉環流程。
>
> **前置文件**：`SKILL.md`（Meta Skill 規格）、`agent-settings-backend-spec.md`（設定後台規格）

---

## 1. 產品概述

### 1.1 一句話描述

排班助手透過「習慣」作為核心記憶，讓 AI 從歷史班表中學習、從便利貼中微調、在排班時運用、在檢核時驗證，形成一個不斷進化的排班閉環。

### 1.2 閉環全景圖

```
                    ┌──────────────────────────────────────────────────────────┐
                    │                    習 慣 模 型                            │
                    │         ┌─────────────────────────────┐                  │
                    │         │  habits_by_person            │                  │
                    │         │  (staff_priorities +          │                  │
                    │         │   staff_workstations)         │                  │
                    │         ├─────────────────────────────┤                  │
                    │         │  habits_by_shift             │                  │
                    │         │  (staffing_requirements +     │                  │
                    │         │   shift_patterns)             │                  │
                    │         └──────────┬──────────────────┘                  │
                    └───────────────────┼──────────────────────────────────────┘
                                        │
              ┌─────────────────────────┼─────────────────────────┐
              │                         │                         │
              ▼                         ▼                         ▼
     ┌─────────────┐          ┌─────────────┐          ┌─────────────┐
     │   Analyzer   │          │  Scheduler  │          │   Auditor   │
     │   分析習慣    │          │  根據習慣排班  │          │  根據習慣檢核  │
     │              │◀─────────│              │─────────▶│              │
     │  · 舊班表推論  │  回饋更新  │  · 目標確認    │  檢核結果  │  · P0 勞基法  │
     │  · 便利貼修改  │─────────▶│  · 求解排班    │◀─────────│  · P1 租戶規則 │
     │  · HITL 確認  │          │  · 迭代重排    │          │  · P2 偏好違反 │
     └─────────────┘          └─────────────┘          └─────────────┘
              ▲                         │                         │
              │                         ▼                         │
              │                ┌─────────────┐                    │
              └────────────────│  完成班表    │────────────────────┘
                               │  → 回寫習慣  │
                               └─────────────┘
```

### 1.3 六大場景總覽

| # | 場景 | 觸發 | 讀取 | 寫入 | Agent |
|---|------|------|------|------|-------|
| S1 | 分析舊班表產出習慣 | 上傳歷史班表 CSV | historical_rosters | habits_by_person + habits_by_shift | Analyzer |
| S2 | 完成班表後回寫習慣 | 班表狀態 → published | 已發佈班表 | habits（增量更新） | Analyzer |
| S3 | 雇主便利貼修改習慣 | 雇主貼規則便利貼 | sticky_note (employer) | habits_by_shift（規則面） | Analyzer |
| S4 | 員工便利貼修改習慣 | 員工貼偏好便利貼 | sticky_note (employee) | habits_by_person（偏好面） | Analyzer |
| S5 | 根據習慣排班 | 用戶觸發排班 | habits + constraints | draft_schedule | Scheduler → Auditor |
| S6 | 根據習慣檢核 | 排班完成 / 手動觸發 | draft_schedule + habits | audit_report | Auditor |

---

## 2. 核心概念：習慣模型（Habit Model）

「習慣」是整個系統的記憶層。它不是靜態設定，而是隨著每次分析、每張便利貼、每次排班結果動態演進的資料結構。

### 2.1 習慣 by 人（habits_by_person）

對應 Analyzer 的 `staff_priorities` + `staff_workstations` 輸出。

```json
{
  "staff_id": "S001",
  "staff_name": "王大明",

  "shift_preferences": [
    {
      "shift_code": "D1",
      "priority_level": 1,
      "frequency": 48,
      "source": "historical_analysis",
      "confidence": 0.92
    },
    {
      "shift_code": "D2",
      "priority_level": 2,
      "frequency": 22,
      "source": "historical_analysis",
      "confidence": 0.78
    }
  ],

  "station_skills": [
    {
      "station": "櫃檯",
      "frequency": 45,
      "is_qualified": true,
      "source": "historical_analysis"
    },
    {
      "station": "備料區",
      "frequency": 12,
      "is_qualified": true,
      "source": "sticky_note",
      "note_id": "note_003"
    }
  ],

  "personal_constraints": [
    {
      "type": "prefer_off",
      "rule": "每月第二個週六希望休假",
      "source": "employee_sticky_note",
      "level": "soft",
      "note_id": "note_010"
    }
  ],

  "metadata": {
    "last_analyzed": "2026-02-13T10:00:00+08:00",
    "data_points": 70,
    "analysis_period": "2025-11-01 ~ 2026-01-31"
  }
}
```

### 2.2 習慣 by 班（habits_by_shift）

對應系統的 `staffing_requirements` + `shift_patterns`。

```json
{
  "shift_code": "D1",
  "shift_display": "早班 08:00-16:00",

  "typical_staffing": {
    "weekday": {
      "櫃檯": { "min": 2, "avg": 2.3, "source": "historical_analysis" },
      "備料區": { "min": 1, "avg": 1.5, "source": "historical_analysis" },
      "外場": { "min": 1, "avg": 1.2, "source": "historical_analysis" }
    },
    "weekend": {
      "櫃檯": { "min": 3, "avg": 3.1, "source": "historical_analysis" },
      "備料區": { "min": 2, "avg": 2.0, "source": "employer_sticky_note" },
      "外場": { "min": 2, "avg": 2.4, "source": "historical_analysis" }
    }
  },

  "shift_rules": [
    {
      "rule": "早班至少一位資深員工（年資 > 2 年）",
      "source": "employer_sticky_note",
      "level": "hard",
      "note_id": "note_020"
    },
    {
      "rule": "週五早班多排一人（備貨日）",
      "source": "employer_sticky_note",
      "level": "soft",
      "note_id": "note_021"
    }
  ],

  "metadata": {
    "last_analyzed": "2026-02-13T10:00:00+08:00",
    "weeks_of_data": 12
  }
}
```

### 2.3 習慣的四種來源與優先序

```
┌───────────────────────────────────────────────────────────┐
│  習慣來源優先序（衝突時，高優先覆蓋低優先）                     │
├───────────────────────────────────────────────────────────┤
│                                                           │
│  Priority 1 ▓▓▓▓▓▓▓▓  雇主便利貼（employer_sticky_note）  │
│             規則面修改，如「資深排早班」「週末多一人」           │
│                                                           │
│  Priority 2 ▓▓▓▓▓▓    員工便利貼（employee_sticky_note）   │
│             偏好面修改，如「我希望週六休」「我想學外場」         │
│                                                           │
│  Priority 3 ▓▓▓▓      完成班表回寫（schedule_feedback）     │
│             實際執行結果增量更新頻率統計                       │
│                                                           │
│  Priority 4 ▓▓        歷史分析推論（historical_analysis）   │
│             初始批量推論，信度隨時間遞減                       │
│                                                           │
└───────────────────────────────────────────────────────────┘
```

### 2.4 習慣的信度衰減

歷史推論的 confidence 隨時間衰減，近期資料權重更高：

```
confidence = base_confidence × decay_factor^(weeks_since_data)

decay_factor = 0.95（每週衰減 5%）

示例：
  - 1 週前的資料：0.92 × 0.95^1  = 0.874
  - 4 週前的資料：0.92 × 0.95^4  = 0.754
  - 12 週前的資料：0.92 × 0.95^12 = 0.497
```

便利貼來源的 confidence 不衰減（人工明確指定 = 1.0），直到被新的便利貼覆寫或刪除。

---

## 3. 場景 S1：分析舊班表 → 產出習慣

### 3.1 流程

```
用戶上傳歷史班表 CSV
        │
        ▼
┌──────────────────────┐
│  Step 1: 載入與驗證    │
│  · 偵測 CSV 編碼 / 分隔│
│  · 驗證必要欄位存在     │
│  · 回報資料筆數與範圍   │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│  Step 2: Identity Resolution             │
│                                          │
│  staff_name_raw → staff.staff_id         │
│  · 完全匹配 → 直接對應                    │
│  · 模糊匹配 → 去空格 / 括號 → 建議對應    │
│  · 無法匹配 → unmapped_names → HITL      │
│                                          │
│  shift_code_raw → shifts.shift_code      │
│  · 比對 shifts 表已定義的班次              │
│  · ⚠ 防幽靈班次：                         │
│    若 shift_code_raw 同時出現在            │
│    station_raw 欄 → 可能是站點名誤歸       │
│  · 未匹配 → 收集回報，絕不自動建立          │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│  Step 3: 習慣 by 人 — Preference Calc    │
│                                          │
│  SELECT staff_id, shift_code_raw,        │
│         COUNT(*) as frequency            │
│  FROM historical_rosters hr              │
│  INNER JOIN shifts s                     │
│    ON hr.shift_code_raw = s.shift_code   │
│  GROUP BY staff_id, shift_code_raw       │
│                                          │
│  → 頻率排序 → priority_level             │
│  → 寫入 staff_priorities                 │
│  → FK 安全檢查：shift_code 必須存在        │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│  Step 4: 習慣 by 人 — Skill Extraction   │
│                                          │
│  SELECT staff_id, station_raw,           │
│         COUNT(*) as frequency            │
│  FROM historical_rosters                 │
│  GROUP BY staff_id, station_raw          │
│                                          │
│  → 次數 >= 閾值 → is_qualified = true    │
│  → 寫入 staff_workstations              │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│  Step 5: 習慣 by 班 — Pattern Extract    │
│                                          │
│  統計各班次 × 各站點的歷史人力配置          │
│  區分平日 / 週末 / 特殊日                  │
│  → 寫入 staffing_requirements 基線值      │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│  Step 6: 產出習慣摘要報告                  │
│                                          │
│  {                                       │
│    "status": "success|partial|failed",   │
│    "staff_updated": 42,                  │
│    "priorities_inferred": 126,           │
│    "skills_inferred": 68,               │
│    "unmapped_names": [...],             │
│    "unmapped_shifts": [...],            │
│    "warnings": [...]                    │
│  }                                       │
└──────────────────────────────────────────┘
```

### 3.2 HITL 觸發點

| 觸發條件 | Agent 行為 | 用戶操作 |
|---------|-----------|---------|
| unmapped_names > 0 | 暫停，展示未對應姓名 + 建議 | 確認對應 / 建立新員工 / 忽略 |
| unmapped_shifts > 0 | 暫停，展示未對應班次 + 是否為站點誤歸 | 確認是站點 / 建立新班次 / 忽略 |
| 某員工資料點 < 5 | 標記低信度，但不暫停 | 可追加上傳更多歷史資料 |

---

## 4. 場景 S2：完成班表後 → 回寫習慣

### 4.1 觸發條件

班表狀態從 `draft` → `published`（排班專員按下「發佈」）。

### 4.2 流程

```
班表發佈
    │
    ▼
Analyzer 增量更新模式
    │
    ├── 習慣 by 人：更新 shift_preferences 頻率
    │   · 將新班表的 (staff, shift) 配對計入 frequency
    │   · 重新計算 priority_level 排序
    │   · confidence 根據時間權重更新
    │
    ├── 習慣 by 人：更新 station_skills 頻率
    │   · 將新班表的 (staff, station) 配對計入
    │   · 低頻站點是否仍 qualified 重新評估
    │
    ├── 習慣 by 班：更新 typical_staffing
    │   · 將實際人力配置計入統計
    │   · 更新 avg 值
    │
    └── 標記 metadata.last_analyzed = now()
```

### 4.3 增量 vs 全量

| 模式 | 觸發 | 範圍 | 效能 |
|------|------|------|------|
| 增量 | 每次發佈班表 | 僅處理本次班表涉及的員工和班次 | 快，秒級 |
| 全量 | 手動觸發 / 首次上傳 | 全部歷史資料重新統計 | 慢，可能分鐘級 |

---

## 5. 場景 S3：雇主便利貼 → 修改習慣（規則面）

### 5.1 便利貼類型對應

雇主便利貼主要影響 **habits_by_shift**（班次規則面）：

| 便利貼範例 | 影響目標 | 修改方式 |
|-----------|---------|---------|
| 「早班至少一位資深」 | habits_by_shift.shift_rules | 新增 hard constraint |
| 「週五備料多一人」 | habits_by_shift.typical_staffing | 調整特定日的 min 值 |
| 「活動日全站加人」 | habits_by_shift.typical_staffing | 特殊日模式新增 |
| 「小明不排大夜」 | habits_by_person.personal_constraints | 新增個人限制 |
| 「取消週末輪班」 | habits_by_shift.shift_rules | 移除/停用規則 |

### 5.2 處理流程

```
雇主輸入便利貼（自然語言）
        │
        ▼
┌──────────────────────────────────────────┐
│  Step 1: 解析便利貼                        │
│                                          │
│  Agent 解析：                             │
│  · 影響對象：by_person or by_shift？        │
│  · 約束等級：hard or soft？                │
│  · 有效期：permanent or one_time？          │
│  · 是否與現有規則衝突？                     │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│  Step 2: 確認理解                          │
│                                          │
│  Agent 回覆：                             │
│  「我理解為：早班（D1）需至少一位              │
│    年資 > 2 年的員工，這是硬性規則，           │
│    長期有效。正確嗎？」                      │
└──────────┬───────────────────────────────┘
           │ 用戶確認
           ▼
┌──────────────────────────────────────────┐
│  Step 3: 寫入習慣                          │
│                                          │
│  · 寫入 habits_by_shift.shift_rules       │
│    或 habits_by_person.personal_constraints│
│  · source = "employer_sticky_note"        │
│  · confidence = 1.0（人工指定不衰減）        │
│  · 記錄 note_id 供溯源                     │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│  Step 4: 影響預估                          │
│                                          │
│  Agent 回報：                             │
│  「這條規則會影響 3 位員工的排班，             │
│    其中王小明目前最常被排早班但年資不足。       │
│    下次排班時會自動套用。」                   │
└──────────────────────────────────────────┘
```

---

## 6. 場景 S4：員工便利貼 → 修改習慣（偏好面）

### 6.1 便利貼類型對應

員工便利貼主要影響 **habits_by_person**（個人偏好面）：

| 便利貼範例 | 影響目標 | 修改方式 |
|-----------|---------|---------|
| 「我希望每月第二個週六休」 | personal_constraints | 新增 soft prefer_off |
| 「我想學外場」 | station_skills | 新增技能意願（待帶訓） |
| 「我不想排大夜」 | shift_preferences | 調低大夜 priority |
| 「下週三有事」 | personal_constraints | 新增 one_time 約束 |
| 「我可以多加班」 | personal_constraints | 放寬工時彈性 |

### 6.2 處理流程

```
員工輸入便利貼（自然語言）
        │
        ▼
┌──────────────────────────────────────────┐
│  Step 1: 身份驗證                          │
│  · 確認便利貼作者 = 員工本人                 │
│  · 員工只能修改自己的 habits_by_person       │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│  Step 2: 解析 + 確認                       │
│  · 判斷是偏好修改還是約束新增                 │
│  · 確認有效期（長期 / 單次）                 │
│  · 回覆理解結果，等待確認                    │
└──────────┬───────────────────────────────┘
           │ 員工確認
           ▼
┌──────────────────────────────────────────┐
│  Step 3: 寫入習慣                          │
│  · source = "employee_sticky_note"        │
│  · level = "soft"（員工偏好永遠是 soft）      │
│  · confidence = 1.0                       │
│                                          │
│  ⚠ 若與雇主規則衝突：                      │
│    · 不覆蓋雇主規則                         │
│    · 標記衝突，通知排班專員                   │
│    · 排班時雇主規則 > 員工偏好               │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│  Step 4: 通知排班專員                       │
│  · 若偏好影響排班可行性，通知排班專員          │
│  · 「小美提交了偏好：每月第二個週六休假，       │
│     目前該時段覆蓋率偏緊。」                  │
└──────────────────────────────────────────┘
```

---

## 7. 場景 S5：根據習慣排班

### 7.1 完整排班流程

```
用戶觸發排班
        │
        ▼
┌──────────────────────────────────────────────────────────┐
│  Phase 1: 確認排班目標                                     │
│                                                          │
│  Agent 提問：                                             │
│  ┌────────────────────────────────────────────────┐       │
│  │  排班期間：[2026-02-16] ~ [2026-02-22]          │       │
│  │                                                │       │
│  │  目標排班人員（全選 / 部分）：                     │       │
│  │  ☑ 王大明  ☑ 林靜宜  ☑ 陳小美                   │       │
│  │  ☐ 李大華（本週請假）                            │       │
│  │                                                │       │
│  │  目標排班班次（全選 / 部分）：                     │       │
│  │  ☑ D1 早班  ☑ D2 中班  ☑ N1 晚班               │       │
│  │  ☐ N2 大夜（暫不排）                            │       │
│  │                                                │       │
│  │  排班情境：◉ 平日  ○ 活動日  ○ 自訂              │       │
│  │                                                │       │
│  │          [確認開始排班]                          │       │
│  └────────────────────────────────────────────────┘       │
│                                                          │
│  Agent 根據 habits 預填建議：                               │
│  · 請假員工自動排除                                        │
│  · 無技能匹配的班次自動排除                                  │
│  · 顯示各班次預估人力缺口                                   │
└──────────────────────────┬───────────────────────────────┘
                           │ 用戶確認
                           ▼
┌──────────────────────────────────────────────────────────┐
│  Phase 2: 執行排班（Scheduler）                            │
│                                                          │
│  CP-SAT Solver 模型：                                     │
│  · 決策變數：x[staff_id, date, shift_code] ∈ {0, 1}       │
│                                                          │
│  Hard Constraints（不可違反）：                             │
│  · 每人每天最多一班                                        │
│  · 技能檢核：來自 habits_by_person.station_skills           │
│  · 假表禁排：leave_type ≠ PreferOff 的不排                 │
│  · 七休一：連續 7 天內至少 1 天休                            │
│  · 輪班間隔 ≥ 11 小時                                     │
│  · 雇主便利貼 hard 約束                                    │
│                                                          │
│  Soft Objectives（目標函式最小化）：                         │
│  · vacancy_penalty × W_vac（人力缺口懲罰）                 │
│  · prefer_off_violation × W_pref（員工偏好違反懲罰）         │
│  · non_top_shift × W_shift（非首選班次懲罰）                │
│  · employer_soft_violation × W_employer（雇主 soft 違反）   │
│  · employee_soft_violation × W_employee（員工 soft 違反）   │
│                                                          │
│  權重來自 solver_config（見 §10 Agent 設定預設值）           │
└──────────────────────────┬───────────────────────────────┘
                           │ 求解完成
                           ▼
┌──────────────────────────────────────────────────────────┐
│  Phase 3: 排班檢核（Auditor）                               │
│                                                          │
│  Level 1 — P0 勞基法檢核：                                 │
│  · 七休一 ✅                                              │
│  · 輪班間隔 ≥ 11hr ✅                                     │
│  · 單日工時上限 ✅                                         │
│  → 若 P0_count > 0 → status: FAIL → 必須重排              │
│                                                          │
│  Level 2 — Hard Constraint 檢核：                         │
│  · 技能匹配 ✅（by habits_by_person.station_skills）        │
│  · 假表禁排 ✅                                            │
│  → 若有違反 → status: FAIL → 必須重排                      │
│                                                          │
│  Level 3 — P1 租戶規則（雇主便利貼）：                       │
│  · 逐條檢查 habits_by_shift.shift_rules                   │
│  → 若 P1_count > 0 → status: WARN → 人工確認              │
│                                                          │
│  Level 4 — P2 員工偏好：                                   │
│  · PreferOff 被違反次數                                    │
│  · 非偏好班次指派次數                                       │
│  → 統計報告，不阻擋                                        │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│  Phase 4: 結果展示 + 迭代決策                               │
│                                                          │
│  Agent 展示排班結果：                                       │
│  ┌────────────────────────────────────────────────┐       │
│  │  ✅ 排班完成   status: optimal                  │       │
│  │                                                │       │
│  │  📊 摘要：                                      │       │
│  │  · 總指派: 42 班次  · 人力缺口: 1               │       │
│  │  · P0 違規: 0  · P1 違規: 2  · P2 違反: 5      │       │
│  │                                                │       │
│  │  ⚠ P1 不符項目：                                │       │
│  │  1. 週三早班無資深員工（規則：早班至少一位資深）    │       │
│  │  2. 週五備料區只排 1 人（規則：備貨日需 2 人）     │       │
│  │                                                │       │
│  │  💡 建議調整：                                   │       │
│  │  · 將林靜宜週三從中班移至早班可解決項目 1         │       │
│  │  · 週五備料區需從外場調一人或加班                  │       │
│  │                                                │       │
│  │  [查看完整班表]  [查看員工工時]  [查看覆蓋率]     │       │
│  │                                                │       │
│  │  [✅ 接受並發佈]  [🔄 針對不符項目重排]           │       │
│  └────────────────────────────────────────────────┘       │
│                                                          │
└──────────────────────────┬───────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              │                         │
         [接受並發佈]              [重排]
              │                         │
              ▼                         ▼
    → 場景 S2（回寫習慣）    ┌────────────────────┐
                            │  重排：針對不符項目   │
                            │  · 鎖定已確認的指派   │
                            │  · 僅對 P1 不符的    │
                            │    人員+班次重新求解  │
                            │  · 回到 Phase 2      │
                            └────────────────────┘
```

### 7.2 迭代重排機制

重排不是全部重來，而是「鎖定已 OK 的部分，只對有問題的子集重新求解」：

```json
{
  "retry_scope": {
    "locked_assignments": [
      { "staff_id": "S001", "date": "2026-02-16", "shift": "D1", "locked": true },
      { "staff_id": "S002", "date": "2026-02-16", "shift": "D2", "locked": true }
    ],
    "retry_targets": {
      "staff_ids": ["S003", "S005"],
      "dates": ["2026-02-19"],
      "shift_codes": ["D1"],
      "violations_to_fix": ["P1_001", "P1_002"]
    }
  }
}
```

Scheduler 在重排時：
- 已鎖定的 assignment → 設為 `x[s, d, shift] = 1`（固定值）
- retry_targets 範圍內 → 重新求解
- 其他不在範圍內 → 保持不變

---

## 8. 場景 S6：根據習慣檢核（合規閉環）

### 8.1 檢核觸發時機

| 時機 | 自動 / 手動 | 檢核範圍 |
|------|-----------|---------|
| 排班完成後 | 自動 | Phase 3 的一部分 |
| 班表被手動修改後 | 自動 | 僅被修改的班次 |
| 用戶主動觸發 | 手動 | 指定範圍或全部 |
| 便利貼新增後 | 自動 | 回溯檢查現有班表是否仍合規 |

### 8.2 Auditor 檢核規格

#### P0: 勞基法（Global Policy）— 不可覆蓋

| 規則 | 檢查方式 | 違反結果 |
|------|---------|---------|
| 七休一 | 每位員工任意連續 7 天內至少 1 天休 | FAIL → 必須重排 |
| 輪班間隔 | 相鄰班次間隔 ≥ 11 小時 | FAIL → 必須重排 |
| 每日工時上限 | 單日工時 ≤ 12 小時（含加班） | FAIL → 必須重排 |
| 每月加班上限 | 加班 ≤ 46 小時/月 | FAIL → 必須重排 |

#### Hard Constraints — 資料層面

| 規則 | 檢查方式 | 違反結果 |
|------|---------|---------|
| 技能檢核 | staff 是否有 station skill | FAIL → 不具資格 |
| 假表禁排 | staff 在請假日被排班 | FAIL → 必須移除 |

#### P1: 租戶規則（雇主便利貼 hard） — 可由店長覆蓋

| 規則來源 | 檢查方式 | 違反結果 |
|---------|---------|---------|
| habits_by_shift.shift_rules (hard) | 逐條驗證 | WARN → 人工確認 |
| habits_by_person.personal_constraints (hard, employer) | 逐條驗證 | WARN → 人工確認 |

#### P2: 偏好統計（員工便利貼 soft） — 不阻擋

| 規則來源 | 檢查方式 | 違反結果 |
|---------|---------|---------|
| PreferOff 被違反 | 統計次數 | 記錄，不阻擋 |
| 非偏好班次指派 | 統計次數 | 記錄，不阻擋 |
| habits_by_person.personal_constraints (soft) | 統計次數 | 記錄，不阻擋 |

### 8.3 Auditor 輸出

```json
{
  "audit_result": {
    "status": "pass | warn | fail",
    "checked_at": "2026-02-13T15:30:00+08:00",

    "summary": {
      "total_assignments_checked": 42,
      "P0_violations": 0,
      "hard_constraint_violations": 0,
      "P1_violations": 2,
      "P2_violations": 5,
      "pass_rate": 0.95
    },

    "violations": [
      {
        "id": "V001",
        "level": "P1",
        "rule_source": "employer_sticky_note",
        "note_id": "note_020",
        "rule": "早班至少一位資深員工",
        "staff_id": "S003",
        "date": "2026-02-19",
        "shift_code": "D1",
        "detail": "週三早班無年資 > 2 年的員工",
        "suggested_fix": "將林靜宜（年資 3 年）從 D2 移至 D1"
      }
    ],

    "decision": {
      "P0_count_gt_0": "→ status=fail → 強制重排",
      "P1_count_gt_0": "→ status=warn → 展示不符清單 → 用戶選擇重排或覆蓋",
      "all_pass": "→ status=pass → 可直接發佈"
    }
  }
}
```

### 8.4 便利貼新增後的回溯檢核

當雇主新增便利貼後，Auditor 需回溯檢查已排定（draft 或 published）的班表：

```
雇主新增便利貼：「早班至少一位資深」
        │
        ▼
Auditor 回溯檢查
        │
        ├── 檢查所有 draft 班表 → 標記新增違規
        ├── 檢查所有 published 但未執行的班表 → 發出警示通知
        └── 已執行的班表不追溯修改
```

---

## 9. 閉環完整時序

將所有場景串在一起，展示系統的完整生命週期：

```
═══════════════════════════════════════════════════════════════════
                        初始化階段
═══════════════════════════════════════════════════════════════════

① Agent 設定（§10 預設值自動帶入）
   └── Gmail 連結 → 角色 → 門市 → 模板選擇

② 上傳歷史班表（S1: Analyzer）
   └── CSV → Identity Resolution → 習慣 by 人 + 習慣 by 班
   └── HITL: 未映射姓名 / 幽靈班次確認

③ 雇主設定規則便利貼（S3）
   └── 自然語言 → 解析 → 寫入 habits_by_shift.shift_rules

═══════════════════════════════════════════════════════════════════
                        排班循環（每週 / 每月）
═══════════════════════════════════════════════════════════════════

④ 員工提交偏好便利貼（S4）
   └── 自然語言 → 解析 → 寫入 habits_by_person

⑤ 排班專員觸發排班（S5）
   ├── Phase 1: 確認目標人 + 目標班
   ├── Phase 2: Scheduler 求解（讀取全部 habits）
   ├── Phase 3: Auditor 檢核（P0 → Hard → P1 → P2）
   └── Phase 4: 展示結果
         ├── [接受] → 發佈 → S2（回寫習慣） → ② 更新 habits
         └── [重排] → 鎖定 OK 部分 → 回到 Phase 2

⑥ 班表運行中
   ├── 雇主新增便利貼 → S3 → Auditor 回溯檢核
   ├── 員工新增偏好 → S4 → 標記下次排班套用
   └── 臨時換班 → Auditor 即時驗證合規

═══════════════════════════════════════════════════════════════════
                        持續進化
═══════════════════════════════════════════════════════════════════

⑦ 下一次排班時，habits 已被更新：
   · 歷史推論 + 上次排班結果回寫 + 便利貼修改
   · confidence 時間衰減 → 近期行為權重更高
   · 便利貼 > 回寫 > 歷史推論（優先序）
```

---

## 10. 基本 Agent 設定（預設值）

首次進入系統時，以下設定自動帶入，用戶可隨時調整。

### 10.1 Identity 預設

```json
{
  "identity_defaults": {
    "gmail": null,
    "role": "scheduling_specialist",
    "timezone": "Asia/Taipei",
    "locale": "zh-TW",
    "date_format": "YYYY-MM-DD",
    "time_format": "HH:mm",
    "week_start": "monday"
  }
}
```

> Gmail 為必填第一步，其餘預設帶入。

### 10.2 Analyzer 預設

```json
{
  "analyzer_defaults": {
    "identity_resolution": {
      "fuzzy_match_threshold": 0.8,
      "auto_strip_parentheses": true,
      "auto_strip_whitespace": true,
      "hitl_on_unmapped": true
    },
    "preference_calculation": {
      "min_data_points_for_confidence": 5,
      "confidence_decay_factor": 0.95,
      "decay_unit": "week",
      "skill_qualification_threshold": 3
    },
    "ghost_shift_detection": {
      "enabled": true,
      "cross_check_station_raw": true,
      "auto_create_shifts": false
    }
  }
}
```

### 10.3 Scheduler 預設

```json
{
  "scheduler_defaults": {
    "solver": "cp_sat",
    "solver_config": {
      "max_solve_time_seconds": 120,
      "num_workers": 4,
      "solution_limit": 5,
      "log_search_progress": false
    },
    "weights": {
      "W_vac": 100,
      "W_pref": 10,
      "W_shift": 5,
      "W_employer_soft": 30,
      "W_employee_soft": 8,
      "W_fairness": 15
    },
    "hard_constraints": {
      "one_shift_per_day": true,
      "skill_check": true,
      "leave_block": true,
      "seven_day_rest": true,
      "shift_gap_hours": 11,
      "employer_hard_notes": true
    },
    "retry": {
      "max_retries": 3,
      "lock_confirmed_assignments": true,
      "relax_soft_on_infeasible": true
    }
  }
}
```

### 10.4 Auditor 預設

```json
{
  "auditor_defaults": {
    "check_levels": {
      "P0_labor_law": { "enabled": true, "blocking": true },
      "hard_constraints": { "enabled": true, "blocking": true },
      "P1_tenant_rules": { "enabled": true, "blocking": false },
      "P2_preferences": { "enabled": true, "blocking": false }
    },
    "labor_standards": {
      "jurisdiction": "taiwan",
      "max_consecutive_work_days": 6,
      "min_rest_between_shifts_hours": 11,
      "max_daily_hours": 12,
      "max_monthly_overtime_hours": 46
    },
    "decision_logic": {
      "P0_violation": "fail",
      "hard_violation": "fail",
      "P1_violation": "warn",
      "P2_violation": "info"
    },
    "retroactive_check_on_new_rule": true,
    "auto_suggest_fix": true,
    "max_suggestions_per_violation": 3
  }
}
```

### 10.5 Output 預設

```json
{
  "output_defaults": {
    "schedule_format": "table_date_x_employee",
    "export_format": "xlsx",
    "color_coding": true,
    "reports_enabled": {
      "coverage_matrix": true,
      "employee_hours": true,
      "sticky_note_status": true,
      "audit_summary": true,
      "optimization_suggestions": true
    },
    "notification": {
      "channel": "gmail",
      "on_schedule_ready": true,
      "on_audit_fail": true,
      "on_audit_warn": true,
      "on_sticky_note_conflict": true,
      "on_employee_sticky_note": true
    }
  }
}
```

### 10.6 Mind 預設

```json
{
  "mind_defaults": {
    "store_priority": {
      "station_ranking": [],
      "leave_policy": "employee_priority",
      "weekend_rotation": true,
      "rotation_cycle_weeks": 4
    },
    "optimization_goal": "balanced",
    "constraint_priority_order": [
      "P0_labor_law",
      "hard_constraints",
      "employer_sticky_notes_hard",
      "store_priority",
      "staffing_requirements",
      "employer_sticky_notes_soft",
      "employee_preferences",
      "employee_sticky_notes",
      "fairness_balance"
    ],
    "ai_behavior": {
      "proactiveness": "high",
      "explanation_detail": "medium",
      "show_alternatives": true,
      "max_alternatives": 3,
      "compare_with_history": true
    }
  }
}
```

---

## 11. 設定頁面結構

```
/settings
│
├── /settings/identity              ← 身份（Gmail 為首要設定）
│   ├── Gmail 連結狀態
│   ├── 角色與門市
│   └── 語系與時區
│
├── /settings/analyzer              ← Analyzer Agent 設定
│   ├── 模糊比對閾值
│   ├── 信度衰減係數
│   ├── 幽靈班次偵測
│   └── HITL 行為
│
├── /settings/scheduler             ← Scheduler Agent 設定
│   ├── 求解器參數（超時/工人數）
│   ├── 權重調整（W_vac / W_pref / W_shift / ...）
│   ├── 硬約束開關
│   └── 重排行為
│
├── /settings/auditor               ← Auditor Agent 設定
│   ├── 檢核層級開關（P0/Hard/P1/P2）
│   ├── 勞基法版本（預設台灣）
│   ├── 決策邏輯（fail/warn/info）
│   └── 回溯檢核開關
│
├── /settings/output                ← 輸出設定
│   ├── 班表格式
│   ├── 匯出偏好
│   └── 通知管道
│
├── /settings/habits                ← 習慣管理（唯讀 + 微調）
│   ├── 習慣 by 人 總覽
│   ├── 習慣 by 班 總覽
│   ├── 便利貼管理
│   └── 信度統計
│
└── /settings/templates             ← 模板管理
    ├── 系統模板
    ├── 自訂模板
    └── 匯出入
```

---

## 12. 與前置文件對應表

| 本文件章節 | SKILL.md 對應 | agent-settings-backend-spec 對應 | 附件對應 |
|-----------|-------------|-------------------------------|---------|
| §2 習慣模型 | §2 資料輸入模組 | L3 Mind Layer | analyzer.md output |
| §3 S1 分析舊班表 | §4.1 批次匯入 | L2 Data Layer | analyzer.md 全部 |
| §4 S2 完成回寫 | — (新增) | — (新增) | analyzer.md Step 2-3 |
| §5 S3 雇主便利貼 | §4.3 便利貼 | L3 Mind - Sticky Notes | — |
| §6 S4 員工便利貼 | §4.3 便利貼 | L3 Mind - Sticky Notes | — |
| §7 S5 根據習慣排班 | §3 Core Agent | L3 Mind - Constraint Engine | scheduler.md 全部 |
| §8 S6 根據習慣檢核 | §7 錯誤處理 | L3 Mind - Constraints | auditor.md 全部 |
| §10 Agent 設定預設 | — | L1~L5 全部 Layer | solver_config 對應 |

---

## 13. 名詞對照表

| 本文件用語 | 附件 / DB 對應 | 說明 |
|-----------|--------------|------|
| 習慣 by 人 | staff_priorities + staff_workstations | 員工排班偏好 + 站點技能 |
| 習慣 by 班 | staffing_requirements + shift_patterns | 各班次人力需求 + 班次規則 |
| 雇主便利貼 | tenant_rules / constraints (employer) | 雇主設定的排班規則 |
| 員工便利貼 | PreferOff / constraints (employee) | 員工個人偏好 |
| P0 | labor_standards | 勞基法硬限制 |
| P1 | tenant_rules | 租戶自訂規則 |
| P2 | soft_constraints | 員工偏好統計 |
| HITL | Human-In-The-Loop | 人工介入確認 |
| confidence | 信度 | 習慣推論的可信程度 |
| 幽靈班次 | ghost shift | shift_code_raw 實為站點名的誤歸 |
