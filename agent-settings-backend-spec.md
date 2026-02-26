# Agent 通用設定後台規格 v2

> 本文件定義 AI 排班助手的通用設定後台架構。系統以「習慣」為核心記憶層，串連 Analyzer、Scheduler、Auditor 三大 Agent 的閉環流程。所有設定皆帶預設值，用戶可隨時調整。
>
> **前置文件**：`SKILL.md`、`product-spec.md`

---

## 1. 設定架構總覽 — Layer Model

```
┌────────────────────────────────────────────────────────────┐
│                    Layer 6: Template                        │
│          排班專員模板 · 場景預設 · 一鍵套用                    │
├────────────────────────────────────────────────────────────┤
│                    Layer 5: Output                          │
│          輸出格式 · 報告模板 · 通知管道                        │
├────────────────────────────────────────────────────────────┤
│                    Layer 4: Agents                          │
│          Analyzer 設定 · Scheduler 設定 · Auditor 設定       │
├────────────────────────────────────────────────────────────┤
│                    Layer 3: Mind                            │
│          P0/P1/P2 約束 · Solver 權重 · 優先序 · 便利貼       │
├────────────────────────────────────────────────────────────┤
│                    Layer 2: Habit                           │
│          習慣 by 人 · 習慣 by 班 · 信度衰減 · 來源優先序      │
├────────────────────────────────────────────────────────────┤
│                    Layer 1: Data                            │
│          資料來源 · 解析策略 · 欄位映射 · Identity Resolution │
├────────────────────────────────────────────────────────────┤
│                    Layer 0: Identity                        │
│          Gmail · 角色 · 門市歸屬 · 權限 · 偏好語言            │
└────────────────────────────────────────────────────────────┘
```

### 層級依賴關係

| 層級 | 名稱 | 職責 | 依賴 | product-spec 對應 |
|------|------|------|------|-------------------|
| L0 | Identity | 誰在用、用在哪 | 無 | §10.1 |
| L1 | Data | 資料怎麼進來、怎麼解析 | L0 | §3 S1 Step 1-2 |
| L2 | Habit | 系統的記憶層 | L0 + L1 | §2 習慣模型 |
| L3 | Mind | Agent 怎麼想、怎麼決策 | L0 + L2 | §7 S5 Phase 2, §8 S6 |
| L4 | Agents | 三大 Agent 各自的參數 | L1 + L2 + L3 | §10.2~10.4 |
| L5 | Output | 結果怎麼出去 | L0 + L3 | §10.5 |
| L6 | Template | 打包好的預設組合 | L0 ~ L5 全部 | — |

---

## 2. Layer 0: Identity（身份層）

Identity 是整個設定體系的起點。Agent 需要先知道「我是誰、服務誰、在哪裡」才能載入後續設定。

### 2.1 資料模型

```json
{
  "identity": {
    "agent_id": "agent_scheduling_001",
    "agent_name": "排班小助手",
    "version": "2.0",

    "owner": {
      "user_id": "usr_thomas_001",
      "display_name": "Thomas",
      "gmail": "thomas@company.com",
      "role": "scheduling_manager",
      "permissions": ["read", "write", "approve", "configure"]
    },

    "store": {
      "store_id": "store_taipei_001",
      "store_name": "台北信義店",
      "company": "SunMart",
      "timezone": "Asia/Taipei",
      "locale": "zh-TW"
    },

    "preferences": {
      "language": "zh-TW",
      "date_format": "YYYY-MM-DD",
      "time_format": "HH:mm",
      "week_start": "monday",
      "notification_channel": "gmail"
    }
  }
}
```

### 2.2 Gmail 設定（Identity 首要配置）

```json
{
  "gmail_config": {
    "primary_email": "thomas@company.com",
    "verified": true,
    "connected_at": "2026-02-13T10:00:00+08:00",

    "usage": {
      "login_auth": true,
      "notification_receiver": true,
      "calendar_sync": true,
      "schedule_export": true
    },

    "notification_preferences": {
      "schedule_generated": true,
      "audit_fail_alert": true,
      "audit_warn_alert": true,
      "sticky_note_conflict": true,
      "employee_sticky_note_received": true,
      "approval_request": true,
      "daily_digest": false,
      "quiet_hours": {
        "enabled": true,
        "start": "22:00",
        "end": "07:00"
      }
    },

    "calendar_integration": {
      "enabled": true,
      "calendar_id": "primary",
      "sync_direction": "agent_to_calendar",
      "event_prefix": "[排班]",
      "include_station": true
    }
  }
}
```

**Gmail 設定流程**：

```
用戶首次進入設定後台
    ↓
Step 1: 輸入 Gmail → OAuth 驗證
    ↓
Step 2: 選擇 Gmail 用途（通知 / 日曆 / 匯出）
    ↓
Step 3: 設定通知偏好
    ↓
Step 4: （可選）連結 Google Calendar
    ↓
Identity 層建立完成 → 解鎖後續 Layer 設定
```

### 2.3 角色與權限矩陣

| 角色 | 代碼 | 查看班表 | 編輯班表 | 核准 | 設定 Agent | 管理模板 | 貼便利貼 | 貼偏好便利貼 |
|------|------|---------|---------|------|-----------|---------|---------|------------|
| 門市店長 | `store_manager` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ (employer) | ❌ |
| 排班主管 | `scheduling_manager` | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ (employer) | ❌ |
| 排班專員 | `scheduling_specialist` | ✅ | ✅ | ❌ | 部分 | ❌ | ✅ (employer) | ❌ |
| 一般員工 | `employee` | 僅自己 | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ (employee) |
| 系統管理員 | `admin` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |

> **product-spec §5/§6 對應**：雇主便利貼（employer）影響 habits_by_shift 規則面；員工便利貼（employee）影響 habits_by_person 偏好面。員工只能修改自己的偏好。

---

## 3. Layer 1: Data（資料層）

定義 Agent 從哪裡取得排班相關資料、如何解析、如何做 Identity Resolution。

### 3.0 多租戶設定檔（tenant_config.json）

每個租戶目錄 `tenants/<tenant>/` 必須包含 `tenant_config.json`，它是該店面所有設定的 **single source of truth**。腳本在執行時透過此檔案取得班次定義、角色對應、約束參數等。

```json
{
  "tenant_id": "glod-pig",
  "display_name": "金豬 燒肉",
  "region": "TW",
  "timezone": "Asia/Taipei",
  "shift_defs": { "<shift_code>": { "start": "HH:MM", "end": "HH:MM", "hours": <number> } },
  "workstation_roles": { "<shift_code>": "<role_name>" },
  "scenarios": ["平日", "平日包場", "週末", "週末包場"],
  "min_daily_headcount": { "weekday": 18, "saturday": 23, "sunday": 22, "package": 19 },
  "constraints": { "min_rest_hours": 11, "max_weekly_hours": 46, "max_working_days": 5 },
  "csv_parser": "gold_pig_v1"
}
```

**與其他租戶檔案的關係**：

| 檔案 | 職責 | 被誰讀取 |
|------|------|---------|
| `tenant_config.json` | 班次、角色、約束、人力需求 | Scheduler, Auditor, Analyzer |
| `staff_roles.json` | 員工技能、主管配置、禁同休配對 | Scheduler, Auditor, Analyzer |
| `events.json` | 包場 / 特殊事件日期 | Scheduler, Auditor |
| `rest_days.json` | 員工指定劃休（每週更新） | Scheduler, Auditor |
| `RULES.md` | 商業規則（人類 & AI 可讀） | Claude（決策參考） |

> **詳細 schema**：請參考 `CLAUDE.md` §2.2 或 `tenants/TEMPLATE/tenant_config.json`。

### 3.1 資料來源設定

```json
{
  "data_sources": {
    "input_mode": "csv_upload",

    "modules": {
      "historical_rosters": {
        "enabled": true,
        "source_type": "csv_upload",
        "auto_detect": true,
        "parse_strategy": "per_person_summary",
        "description": "歷史班表原始資料，Analyzer 的主要輸入",
        "field_mapping": {
          "staff_name_raw": "姓名",
          "shift_code_raw": "班次",
          "station_raw": "站點",
          "date": "日期"
        },
        "validation_rules": {
          "required_fields": ["staff_name_raw", "shift_code_raw", "date"],
          "date_format": "auto_detect",
          "encoding": "auto_detect"
        }
      },

      "shifts": {
        "enabled": true,
        "source_type": "csv_upload",
        "description": "班次定義表（shift_code → 時間區間）",
        "field_mapping": {
          "shift_code": "ShiftCode",
          "display_format": "DisplayFormat",
          "start_time": "Start",
          "end_time": "End",
          "work_minutes": "TotalWorkMinutes"
        }
      },

      "staff": {
        "enabled": true,
        "source_type": "csv_upload_or_prd_api",
        "prd_api_endpoint": "/api/v1/employees/active",
        "sync_frequency": "daily",
        "description": "在職員工清單",
        "field_mapping": {
          "staff_id": "ID",
          "staff_name": "Name",
          "position": "Position",
          "seniority_years": "Seniority"
        }
      },

      "leaves": {
        "enabled": true,
        "source_type": "csv_upload",
        "description": "假表（含 PreferOff）",
        "field_mapping": {
          "staff_id": "ID",
          "date": "Date",
          "leave_type": "Type"
        }
      },

      "demand_scenarios": {
        "enabled": true,
        "source_type": "csv_upload",
        "description": "人力需求情境",
        "field_mapping": {
          "scenario_name": "Scenario",
          "shift_code": "ShiftCode",
          "station": "WorkStation",
          "required_workers": "RequiredWorkers"
        }
      }
    },

    "global_parse_settings": {
      "csv_delimiter": "auto_detect",
      "encoding_priority": ["UTF-8", "Big5", "Shift_JIS"],
      "header_row": 1,
      "skip_empty_rows": true,
      "trim_whitespace": true
    }
  }
}
```

### 3.2 Identity Resolution 設定

對應 product-spec §3 S1 Step 2 和 analyzer.md Step 1。

```json
{
  "identity_resolution": {
    "staff_name_matching": {
      "exact_match": true,
      "fuzzy_match": true,
      "fuzzy_threshold": 0.8,
      "auto_strip_parentheses": true,
      "auto_strip_whitespace": true,
      "hitl_on_unmapped": true
    },

    "shift_code_matching": {
      "strict_join_with_shifts_table": true,
      "ghost_shift_detection": {
        "enabled": true,
        "cross_check_station_raw": true,
        "auto_create_shifts": false,
        "report_unmapped": true
      }
    }
  }
}
```

> **analyzer.md 防護對應**：`ghost_shift_detection` 直接源自 gold-pig 租戶事件 — `shift_code_raw = '櫃台'` 實為站點名。`auto_create_shifts: false` 確保不會自動插入幽靈班次。

### 3.3 欄位映射配置介面

```
┌────────────────────────────────────────────────┐
│  欄位映射設定                                    │
├────────────────────────────────────────────────┤
│                                                │
│  CSV 欄位           →    系統欄位               │
│  ─────────              ─────────              │
│  [姓名]             →    staff_name_raw         │
│  [班次]             →    shift_code_raw         │
│  [站點]             →    station_raw            │
│  [日期]             →    date                   │
│  [未映射: 備註]       →    [點擊選擇...]          │
│                                                │
│  ☑ 記住此映射（下次自動套用）                     │
│  ☑ 同公司其他門市共用此映射                       │
│                                                │
│         [重置]    [預覽解析結果]    [確認儲存]     │
└────────────────────────────────────────────────┘
```

---

## 4. Layer 2: Habit（習慣層）

**Habit 是系統的記憶層**，也是三大 Agent 閉環的核心資料。對應 product-spec §2 的完整定義。

### 4.1 習慣 by 人（habits_by_person）設定

```json
{
  "habits_by_person": {
    "data_structure": {
      "shift_preferences": "staff_priorities 表",
      "station_skills": "staff_workstations 表",
      "personal_constraints": "來自員工/雇主便利貼"
    },

    "inference_settings": {
      "min_data_points_for_confidence": 5,
      "skill_qualification_threshold": 3,
      "preference_max_priority_levels": 5
    },

    "update_triggers": {
      "on_historical_analysis": "全量更新",
      "on_schedule_published": "增量更新",
      "on_employer_sticky_note": "手動覆寫（confidence=1.0）",
      "on_employee_sticky_note": "偏好新增（confidence=1.0, level=soft）"
    }
  }
}
```

### 4.2 習慣 by 班（habits_by_shift）設定

```json
{
  "habits_by_shift": {
    "data_structure": {
      "typical_staffing": "staffing_requirements 表",
      "shift_rules": "來自雇主便利貼 + 歷史分析"
    },

    "inference_settings": {
      "weekday_weekend_split": true,
      "special_day_detection": true,
      "min_weeks_for_reliable_avg": 4
    },

    "update_triggers": {
      "on_historical_analysis": "全量統計各班次人力配置",
      "on_schedule_published": "增量更新 avg 值",
      "on_employer_sticky_note": "規則新增/修改"
    }
  }
}
```

### 4.3 信度衰減（Confidence Decay）

對應 product-spec §2.4。

```json
{
  "confidence_decay": {
    "enabled": true,
    "decay_factor": 0.95,
    "decay_unit": "week",
    "min_confidence": 0.3,
    "sticky_note_confidence": 1.0,
    "sticky_note_decays": false,
    "schedule_feedback_base_confidence": 0.85,
    "historical_analysis_base_confidence": 0.92
  }
}
```

衰減公式：`confidence = base × decay_factor ^ weeks_since_data`

| 資料年齡 | base=0.92, decay=0.95 |
|---------|----------------------|
| 1 週前 | 0.874 |
| 4 週前 | 0.754 |
| 12 週前 | 0.497 |
| 24 週前 | 0.269 → 切到 min 0.3 |

### 4.4 習慣來源優先序

對應 product-spec §2.3。衝突時高優先覆蓋低優先。

```json
{
  "habit_source_priority": {
    "order": [
      {
        "rank": 1,
        "source": "employer_sticky_note",
        "label": "雇主便利貼",
        "confidence": 1.0,
        "decays": false,
        "affects": "habits_by_shift + habits_by_person（規則面）"
      },
      {
        "rank": 2,
        "source": "employee_sticky_note",
        "label": "員工便利貼",
        "confidence": 1.0,
        "decays": false,
        "affects": "habits_by_person（偏好面）",
        "conflict_rule": "不覆蓋雇主規則，標記衝突通知排班專員"
      },
      {
        "rank": 3,
        "source": "schedule_feedback",
        "label": "班表回寫",
        "confidence": 0.85,
        "decays": true,
        "affects": "habits_by_person + habits_by_shift（增量統計）"
      },
      {
        "rank": 4,
        "source": "historical_analysis",
        "label": "歷史推論",
        "confidence": 0.92,
        "decays": true,
        "affects": "全部（初始批量推論）"
      }
    ]
  }
}
```

### 4.5 Habit Layer UI 概念

```
┌──────────────────────────────────────────────────────────┐
│  📊 習慣管理                                               │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ┌─ 習慣 by 人 ─────────────────────────────────────┐     │
│  │                                                  │     │
│  │  王大明 (S001)  信度: ██████████ 0.92             │     │
│  │  ├─ 偏好班次: D1(早班)▓▓▓▓ 48次  D2(中班)▓▓ 22次  │     │
│  │  ├─ 站點技能: 櫃檯✅ 備料區✅ 外場❌               │     │
│  │  └─ 個人約束: 「每月第二週六休」(員工便利貼, soft)   │     │
│  │                                                  │     │
│  │  林靜宜 (S002)  信度: ██████████ 0.88             │     │
│  │  ├─ 偏好班次: D2(中班)▓▓▓▓ 35次  D1(早班)▓▓ 20次  │     │
│  │  ├─ 站點技能: 櫃檯✅ 外場✅ 庫存✅                 │     │
│  │  └─ 個人約束: (無)                                │     │
│  │                                                  │     │
│  │  [展開全部 42 人...]                               │     │
│  └──────────────────────────────────────────────────┘     │
│                                                          │
│  ┌─ 習慣 by 班 ─────────────────────────────────────┐     │
│  │                                                  │     │
│  │  D1 早班 08:00-16:00                              │     │
│  │  ├─ 平日人力: 櫃檯 min:2 avg:2.3 | 備料 min:1     │     │
│  │  ├─ 週末人力: 櫃檯 min:3 avg:3.1 | 備料 min:2     │     │
│  │  └─ 規則: 「早班至少一位資深」(雇主便利貼, hard)     │     │
│  │                                                  │     │
│  │  [展開全部班次...]                                 │     │
│  └──────────────────────────────────────────────────┘     │
│                                                          │
│  ┌─ 信度設定 ────────────────────────────────────────┐     │
│  │  衰減係數: [0.95] /週   最低信度: [0.3]            │     │
│  │  便利貼信度: 1.0 (固定)  班表回寫信度: [0.85]       │     │
│  └──────────────────────────────────────────────────┘     │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

---

## 5. Layer 3: Mind（認知層）

Mind Layer 定義 Agent 的決策邏輯。對應 product-spec §7 S5 Phase 2（Scheduler 約束）和 §8 S6（Auditor 檢核層級）。

### 5.1 約束引擎 — P0 / Hard / P1 / P2 層級

對應 auditor.md 的檢查優先序和 product-spec §8.2。

```json
{
  "mind": {
    "constraints": {
      "P0_labor_law": {
        "label": "勞基法",
        "source": "rules/labor_standards.md",
        "level": "hard",
        "blocking": true,
        "overridable": false,
        "decision_on_violation": "fail",
        "rules": {
          "max_consecutive_work_days": 6,
          "min_rest_between_shifts_hours": 11,
          "max_daily_hours": 12,
          "max_weekly_regular_hours": 40,
          "max_weekly_hours_with_overtime": 48,
          "overtime_cap_monthly_hours": 46,
          "min_rest_per_week_hours": 24,
          "minor_worker_restrictions": true
        }
      },

      "hard_constraints": {
        "label": "資料層硬約束",
        "level": "hard",
        "blocking": true,
        "decision_on_violation": "fail",
        "rules": {
          "skill_check": {
            "enabled": true,
            "source": "habits_by_person.station_skills",
            "description": "員工必須具備站點技能才能被指派"
          },
          "leave_block": {
            "enabled": true,
            "source": "leaves 表",
            "description": "非 PreferOff 的假別不可排班",
            "exclude_types": ["PreferOff"]
          },
          "one_shift_per_day": {
            "enabled": true,
            "description": "每人每天最多一班"
          }
        }
      },

      "P1_tenant_rules": {
        "label": "租戶規則（雇主便利貼 hard）",
        "source": "habits_by_shift.shift_rules + habits_by_person.personal_constraints (employer, hard)",
        "level": "mixed",
        "blocking": false,
        "decision_on_violation": "warn",
        "overridable": true,
        "override_requires": "store_manager",
        "rules": []
      },

      "P2_soft_preferences": {
        "label": "員工偏好",
        "source": "habits_by_person (employee_sticky_note + historical)",
        "level": "soft",
        "blocking": false,
        "decision_on_violation": "info",
        "rules": {
          "prefer_off_tracking": true,
          "non_preferred_shift_tracking": true,
          "employee_sticky_note_soft": true
        }
      }
    },

    "constraint_priority_order": [
      "P0_labor_law",
      "hard_constraints.leave_block",
      "hard_constraints.one_shift_per_day",
      "hard_constraints.skill_check",
      "P1_tenant_rules (hard)",
      "store_priority",
      "staffing_requirements",
      "P1_tenant_rules (soft)",
      "P2_soft_preferences",
      "fairness_balance"
    ]
  }
}
```

### 5.2 Store Priority 設定

```json
{
  "mind": {
    "store_priority": {
      "station_ranking": {
        "enabled": true,
        "order": ["櫃檯", "備料區", "外場", "庫存"],
        "description": "人力不足時，按此順序優先填補"
      },

      "leave_policy": {
        "mode": "employee_priority",
        "options": {
          "employee_priority": "以員工規劃休優先，系統填補空缺",
          "system_unified": "系統統一安排規休，員工不可自選",
          "public_holiday": "僅公休日統一放假，其他由員工申請",
          "custom": "自定義混合規則"
        }
      },

      "weekend_policy": {
        "rotation": true,
        "rotation_cycle_weeks": 4,
        "min_weekends_off_per_month": 2
      },

      "overtime_policy": {
        "max_monthly_hours": 46,
        "alert_threshold_hours": 30,
        "distribution_strategy": "even",
        "voluntary_first": true
      }
    }
  }
}
```

### 5.3 Solver 權重（Objective Function）

對應 scheduler.md 的 Objective Function 和 product-spec §10.3。

```json
{
  "mind": {
    "solver_weights": {
      "W_vac": {
        "value": 100,
        "label": "人力缺口懲罰",
        "description": "每一個未填的需求格子的懲罰分數",
        "range": [50, 500]
      },
      "W_pref": {
        "value": 10,
        "label": "PreferOff 違反懲罰",
        "description": "違反員工偏好休假的懲罰分數",
        "range": [1, 50]
      },
      "W_shift": {
        "value": 5,
        "label": "非首選班次懲罰",
        "description": "排到非 priority_level=1 班次的懲罰",
        "range": [1, 30]
      },
      "W_employer_soft": {
        "value": 30,
        "label": "雇主 soft 規則違反懲罰",
        "description": "違反雇主便利貼 soft 規則的懲罰",
        "range": [10, 100]
      },
      "W_employee_soft": {
        "value": 8,
        "label": "員工 soft 偏好違反懲罰",
        "description": "違反員工便利貼 soft 偏好的懲罰",
        "range": [1, 30]
      },
      "W_fairness": {
        "value": 15,
        "label": "公平性懲罰",
        "description": "工時分配不均的懲罰",
        "range": [5, 50]
      }
    },

    "optimization_goal": {
      "selected": "balanced",
      "options": {
        "cost_minimize": { "W_vac": 50, "W_pref": 5, "W_shift": 2 },
        "coverage_maximize": { "W_vac": 500, "W_pref": 5, "W_shift": 2 },
        "fairness_maximize": { "W_vac": 100, "W_fairness": 50, "W_pref": 10 },
        "preference_maximize": { "W_vac": 100, "W_pref": 50, "W_shift": 30 },
        "balanced": { "W_vac": 100, "W_pref": 10, "W_shift": 5, "W_fairness": 15 }
      }
    }
  }
}
```

> 用戶選擇 `optimization_goal` 時，自動帶入對應的權重預設。進階用戶可手動微調個別權重。

### 5.4 便利貼設定

對應 product-spec §5/§6（雇主 vs 員工雙來源）。

```json
{
  "mind": {
    "sticky_notes": {
      "employer_notes": {
        "who_can_create": ["store_manager", "scheduling_manager", "scheduling_specialist"],
        "affects": "habits_by_shift (rules) + habits_by_person (constraints)",
        "default_level": "hard",
        "allowed_levels": ["hard", "soft"],
        "requires_confirmation": true
      },

      "employee_notes": {
        "who_can_create": ["employee"],
        "affects": "habits_by_person (preferences) only",
        "forced_level": "soft",
        "conflict_with_employer": "employer_wins_notify_specialist",
        "requires_confirmation": true,
        "notify_specialist_on_submit": true
      },

      "lifecycle": {
        "effective_options": ["permanent", "one_time", "date_range"],
        "auto_expire_one_time": true,
        "allow_edit_after_applied": true,
        "keep_history": true
      }
    }
  }
}
```

### 5.5 AI 行為設定

```json
{
  "mind": {
    "ai_behavior": {
      "personality": {
        "tone": "professional_friendly",
        "proactiveness": "high",
        "explanation_detail": "medium",
        "language": "zh-TW"
      },

      "decision_making": {
        "ambiguity_handling": "ask_user",
        "conflict_resolution": "present_options",
        "auto_optimize": true
      },

      "reporting": {
        "show_reasoning": true,
        "show_alternatives": true,
        "max_alternatives": 3,
        "highlight_risks": true,
        "compare_with_history": true
      }
    }
  }
}
```

### 5.6 Mind Layer UI 概念

```
┌──────────────────────────────────────────────────────────┐
│  🧠 Agent 認知設定                                        │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ┌─ 約束層級 ────────────────────────────────────────┐    │
│  │                                                  │    │
│  │  🔴 P0 勞基法 (不可覆蓋, 違反=FAIL)               │    │
│  │     七休一: 6天  間隔: 11hr  月加班: 46hr          │    │
│  │                                                  │    │
│  │  🟡 Hard 約束 (違反=FAIL)                         │    │
│  │     ☑ 技能檢核  ☑ 假表禁排  ☑ 每日一班            │    │
│  │                                                  │    │
│  │  🟠 P1 租戶規則 (違反=WARN, 店長可覆蓋)            │    │
│  │     雇主便利貼 (hard):                            │    │
│  │     ┌──────────────────────────────────────┐      │    │
│  │     │ 「早班至少一位資深」        [hard] [永久]│      │    │
│  │     │ 「週五備料多排一人」        [soft] [永久]│      │    │
│  │     │ 「小明不排大夜」           [hard] [永久]│      │    │
│  │     │                    [+ 新增雇主便利貼]  │      │    │
│  │     └──────────────────────────────────────┘      │    │
│  │                                                  │    │
│  │  🔵 P2 員工偏好 (違反=INFO, 不阻擋)               │    │
│  │     員工便利貼:                                   │    │
│  │     ┌──────────────────────────────────────┐      │    │
│  │     │ 王大明「每月第二週六休」     [soft] [永久]│      │    │
│  │     │ 陳小美「下週三有事」        [soft] [單次]│      │    │
│  │     │                  (員工自行提交, 唯讀)   │      │    │
│  │     └──────────────────────────────────────┘      │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  ┌─ Solver 權重 ────────────────────────────────────┐    │
│  │                                                  │    │
│  │  最佳化目標  ◉ 綜合平衡  ○ 覆蓋優先  ○ 公平優先    │    │
│  │                                                  │    │
│  │  W_vac (缺口)      ──────────●── [100]           │    │
│  │  W_employer_soft    ────●──────── [30]            │    │
│  │  W_fairness         ──●────────── [15]            │    │
│  │  W_pref (偏好休)    ─●─────────── [10]            │    │
│  │  W_employee_soft    ─●─────────── [8]             │    │
│  │  W_shift (非首選)   ●──────────── [5]             │    │
│  │                                                  │    │
│  │  ⓘ 選擇最佳化目標會自動調整權重，                   │    │
│  │    也可手動微調個別數值                             │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  ┌─ 門市優先 ───────────────────────────────────────┐    │
│  │  站區排序 (拖拉): 1.櫃檯 2.備料區 3.外場 4.庫存    │    │
│  │  休假策略: ◉ 員工優先  ○ 系統統一  ○ 公休          │    │
│  │  週末輪班: [✅]  週期: [4] 週                     │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

---

## 6. Layer 4: Agents（三大 Agent 設定）

每個 Agent 有獨立的可調參數。對應 product-spec §10.2 ~ §10.4。

### 6.1 Analyzer Agent 設定

```json
{
  "analyzer": {
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
    },

    "update_mode": {
      "default": "incremental",
      "options": {
        "incremental": "僅處理新資料（班表發佈時）",
        "full": "全部歷史資料重新統計（手動觸發）"
      }
    },

    "script": "scripts/analyzer.py"
  }
}
```

### 6.2 Scheduler Agent 設定

```json
{
  "scheduler": {
    "solver": {
      "engine": "cp_sat",
      "max_solve_time_seconds": 120,
      "num_workers": 4,
      "solution_limit": 5,
      "log_search_progress": false
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
      "enabled": true,
      "max_retries": 3,
      "lock_confirmed_assignments": true,
      "retry_scope": "violation_subset_only",
      "relax_soft_on_infeasible": true,
      "relax_order": ["W_employee_soft", "W_shift", "W_pref"]
    },

    "target_confirmation": {
      "require_user_confirm_before_solve": true,
      "auto_exclude_on_leave": true,
      "auto_exclude_no_skill_match": true,
      "show_estimated_coverage_gap": true
    },

    "script": "scripts/ortools_solver.py"
  }
}
```

> **product-spec §7.2 對應**：`retry.lock_confirmed_assignments = true` 代表重排時鎖定已確認的指派，僅對 P1 不符的人員+班次子集重新求解。

### 6.3 Auditor Agent 設定

```json
{
  "auditor": {
    "check_levels": {
      "P0_labor_law": { "enabled": true, "blocking": true },
      "hard_constraints": { "enabled": true, "blocking": true },
      "P1_tenant_rules": { "enabled": true, "blocking": false },
      "P2_preferences": { "enabled": true, "blocking": false }
    },

    "labor_standards": {
      "jurisdiction": "taiwan",
      "rules_file": "rules/labor_standards.md"
    },

    "decision_logic": {
      "P0_violation": "fail → 強制重排",
      "hard_violation": "fail → 強制重排",
      "P1_violation": "warn → 展示不符清單，用戶選擇重排或覆蓋",
      "P2_violation": "info → 統計報告，不阻擋"
    },

    "retroactive_check": {
      "enabled": true,
      "on_new_employer_note": "回溯檢查所有 draft 班表",
      "on_schedule_manual_edit": "即時檢查被修改的班次",
      "on_user_trigger": "指定範圍或全部"
    },

    "suggestions": {
      "auto_suggest_fix": true,
      "max_suggestions_per_violation": 3
    },

    "script": "scripts/auditor_tools.py"
  }
}
```

---

## 7. Layer 5: Output（輸出層）

### 7.1 輸出格式設定

```json
{
  "output": {
    "schedule_format": {
      "primary": "table_by_date_employee",
      "export_formats": ["csv", "xlsx", "pdf", "google_calendar"],
      "table_orientation": "date_rows_employee_cols",
      "show_station": true,
      "show_shift_code": true,
      "color_coding": {
        "enabled": true,
        "morning_shift": "#4CAF50",
        "afternoon_shift": "#2196F3",
        "night_shift": "#9C27B0",
        "day_off": "#E0E0E0",
        "training": "#FF9800",
        "overtime": "#F44336"
      }
    },

    "reports": {
      "coverage_matrix": { "enabled": true, "detail": "by_station_by_hour" },
      "employee_hours_summary": { "enabled": true, "compare_previous": true },
      "sticky_note_status": {
        "enabled": true,
        "format": "per_note_with_status",
        "status_icons": { "satisfied": "✅", "partial": "⚠️", "unsatisfied": "❌" }
      },
      "audit_summary": {
        "enabled": true,
        "include_P0": true,
        "include_P1": true,
        "include_P2": true,
        "include_pass_rate": true
      },
      "optimization_suggestions": { "enabled": true, "max_items": 5 }
    },

    "notifications": {
      "channel": "gmail",
      "recipients": {
        "schedule_ready": ["owner"],
        "audit_fail": ["owner", "store_manager"],
        "audit_warn": ["owner"],
        "sticky_note_conflict": ["owner"],
        "employee_note_received": ["owner"],
        "shift_change": ["affected_employees"]
      },
      "gmail_template": {
        "subject_prefix": "[排班系統]",
        "include_attachment": true,
        "attachment_format": "xlsx"
      }
    }
  }
}
```

---

## 8. Layer 6: Template（模板層）

### 8.1 模板系統架構

模板是 L0 ~ L5 設定的「快照打包」，現在包含三大 Agent 的各自設定。

```json
{
  "template": {
    "template_id": "tmpl_retail_standard",
    "name": "零售門市標準排班",
    "description": "適用於一般零售門市，含早中晚三班制",
    "category": "retail",
    "created_by": "system",
    "is_default": true,
    "inherits_from": null,

    "layers": {
      "identity_defaults": {
        "role": "scheduling_specialist",
        "timezone": "Asia/Taipei",
        "locale": "zh-TW"
      },
      "data_defaults": {
        "modules_enabled": ["historical_rosters", "shifts", "staff", "leaves", "demand_scenarios"]
      },
      "habit_defaults": {
        "confidence_decay_factor": 0.95,
        "skill_qualification_threshold": 3
      },
      "mind_defaults": {
        "store_priority": {
          "leave_policy": "employee_priority",
          "weekend_rotation": true
        },
        "optimization_goal": "balanced",
        "P0_jurisdiction": "taiwan"
      },
      "agent_defaults": {
        "analyzer": {
          "fuzzy_match_threshold": 0.8,
          "ghost_shift_detection": true
        },
        "scheduler": {
          "max_solve_time_seconds": 120,
          "max_retries": 3
        },
        "auditor": {
          "retroactive_check": true,
          "auto_suggest_fix": true
        }
      },
      "output_defaults": {
        "schedule_format": "table_by_date_employee",
        "export_format": "xlsx",
        "notification_channel": "gmail"
      }
    }
  }
}
```

### 8.2 預設排班專員模板

#### 模板 A：零售門市標準班

```json
{
  "template_id": "tmpl_retail_standard",
  "name": "零售門市標準排班",
  "shifts": [
    { "code": "A", "display": "早班 08:00-16:00", "hours": 8 },
    { "code": "B", "display": "中班 12:00-20:00", "hours": 8 },
    { "code": "C", "display": "晚班 16:00-00:00", "hours": 8 }
  ],
  "default_stations": ["櫃檯", "備料區", "外場", "庫存"],
  "min_staff_per_shift": 2,
  "weekend_rotation_weeks": 4,
  "solver_weights": { "W_vac": 100, "W_pref": 10, "W_shift": 5, "W_fairness": 15 },
  "employer_notes_preset": [
    { "content": "每班至少一位資深員工", "level": "soft", "effective": "permanent" },
    { "content": "新人前五天搭配資深帶訓", "level": "hard", "effective": "permanent" }
  ]
}
```

#### 模板 B：餐飲業排班

```json
{
  "template_id": "tmpl_restaurant",
  "name": "餐飲門市排班",
  "inherits_from": "tmpl_retail_standard",
  "overrides": {
    "shifts": [
      { "code": "M", "display": "早市 06:00-14:00", "hours": 8 },
      { "code": "A", "display": "午市 10:00-14:00", "hours": 4 },
      { "code": "D", "display": "晚市 16:00-22:00", "hours": 6 },
      { "code": "F", "display": "全日 10:00-22:00", "hours": 10, "break": 2 }
    ],
    "default_stations": ["內場", "外場", "吧檯", "備料", "收銀"],
    "solver_weights": { "W_vac": 150 },
    "employer_notes_preset": [
      { "content": "午市尖峰至少 3 位外場", "level": "hard", "effective": "permanent" },
      { "content": "內場需有持證廚師", "level": "hard", "effective": "permanent" }
    ]
  }
}
```

#### 模板 C：醫療 / 護理排班

```json
{
  "template_id": "tmpl_healthcare",
  "name": "醫療護理排班",
  "shifts": [
    { "code": "D", "display": "白班 08:00-16:00", "hours": 8 },
    { "code": "E", "display": "小夜 16:00-00:00", "hours": 8 },
    { "code": "N", "display": "大夜 00:00-08:00", "hours": 8 }
  ],
  "default_stations": ["護理站", "急診", "門診", "加護病房"],
  "solver_weights": { "W_vac": 200, "W_fairness": 30 },
  "P0_extensions": {
    "max_consecutive_night_shifts": 3,
    "rest_after_night_shift_hours": 24
  },
  "employer_notes_preset": [
    { "content": "大夜班後至少休息 24 小時", "level": "hard", "effective": "permanent" },
    { "content": "連續大夜不超過 3 天", "level": "hard", "effective": "permanent" },
    { "content": "每站至少一位護理長或代理", "level": "hard", "effective": "permanent" }
  ]
}
```

### 8.3 模板繼承與覆寫機制

```
┌───────────────────────────────────────────────────┐
│  模板繼承鏈                                         │
├───────────────────────────────────────────────────┤
│                                                   │
│  System Base Template                             │
│    └── 零售門市標準排班 (tmpl_retail_standard)       │
│          ├── 台北信義店自訂 (tmpl_store_001)         │
│          └── 餐飲門市排班 (tmpl_restaurant)          │
│                └── 火鍋店特殊排班                    │
│                                                   │
│  覆寫規則：                                         │
│  - 子模板繼承父模板所有設定                           │
│  - 子模板可覆寫任意欄位                              │
│  - P0 勞基法約束不可被子模板降級                      │
│  - solver_weights 可局部覆寫（僅指定要改的 key）      │
│  - 覆寫時記錄 diff，方便回溯                         │
│                                                   │
└───────────────────────────────────────────────────┘
```

### 8.4 模板管理 API

| 操作 | Method | Endpoint | 權限 |
|------|--------|----------|------|
| 列出所有模板 | GET | `/api/v1/templates` | all |
| 取得單一模板 | GET | `/api/v1/templates/{id}` | all |
| 建立模板 | POST | `/api/v1/templates` | store_manager |
| 更新模板 | PUT | `/api/v1/templates/{id}` | store_manager |
| 複製模板 | POST | `/api/v1/templates/{id}/clone` | scheduling_manager |
| 刪除模板 | DELETE | `/api/v1/templates/{id}` | admin |
| 套用模板到門市 | POST | `/api/v1/stores/{store_id}/apply-template` | store_manager |
| 匯出模板 | GET | `/api/v1/templates/{id}/export` | scheduling_manager |
| 匯入模板 | POST | `/api/v1/templates/import` | store_manager |

---

## 9. Agent System Architecture（SA）

### 9.1 系統架構總覽

```
┌─────────────────────────────────────────────────────────────────┐
│                          Client Layer                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐   │
│  │ Web UI   │  │ Mobile   │  │ LINE Bot │  │ Gmail Add-on  │   │
│  │ (React)  │  │   App    │  │          │  │               │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬────────┘   │
│       └──────────────┴─────────────┴───────────────┘            │
└──────────────────────────┬──────────────────────────────────────┘
                           │ REST / WebSocket
┌──────────────────────────┴──────────────────────────────────────┐
│                        API Gateway                              │
│                (Auth · Rate Limit · Routing)                     │
└─┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬───────┘
  │      │      │      │      │      │      │      │      │
  ▼      ▼      ▼      ▼      ▼      ▼      ▼      ▼      ▼
┌─────┐┌─────┐┌─────┐┌─────┐┌──────┐┌──────┐┌──────┐┌─────┐┌─────┐
│Iden-││Data ││Habit││Mind ││Analy-││Sched-││Audi- ││Outp-││Noti-│
│tity ││Parse││Store││Engi-││zer   ││uler  ││tor   ││ut   ││fica-│
│Svc  ││ Svc ││ Svc ││ne   ││Agent ││Agent ││Agent ││Rendr││tion │
└──┬──┘└──┬──┘└──┬──┘└──┬──┘└──┬───┘└──┬───┘└──┬───┘└──┬──┘└──┬──┘
   │      │      │      │      │       │       │       │      │
   └──────┴──────┴──────┴──────┴───────┴───────┴───────┴──────┘
                              │
┌─────────────────────────────┴───────────────────────────────────┐
│                         Data Layer                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐   │
│  │PostgreSQL│  │  Redis   │  │  S3 /    │  │   Google      │   │
│  │ (主資料) │  │ (快取/   │  │  MinIO   │  │  Calendar API │   │
│  │          │  │  Queue)  │  │ (檔案)   │  │               │   │
│  └──────────┘  └──────────┘  └──────────┘  └───────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 9.2 核心服務拆解

#### Service 1: Identity Service

```
職責：用戶身份、Gmail 連結、角色權限、門市歸屬
DB Table：users, stores, user_store_roles, gmail_connections
API：
  POST   /auth/gmail/connect       — OAuth 連結 Gmail
  GET    /auth/gmail/status        — 查詢連結狀態
  GET    /users/me                 — 取得當前用戶資訊
  PUT    /users/me/preferences     — 更新偏好設定
  GET    /stores/{id}/members      — 列出門市成員
```

#### Service 2: Data Parse Service

```
職責：CSV/Excel 上傳、自動偵測、欄位映射、Identity Resolution
DB Table：uploads, field_mappings, historical_rosters
Queue：parse_jobs (非同步解析大檔案)
API：
  POST   /data/upload              — 上傳 CSV/Excel
  GET    /data/uploads/{id}/status — 解析進度
  GET    /data/uploads/{id}/preview— 預覽解析結果
  PUT    /data/uploads/{id}/mapping— 修改欄位映射
  POST   /data/uploads/{id}/confirm— 確認解析結果
  GET    /data/unmapped            — 列出 unmapped names / shifts
  POST   /data/unmapped/resolve    — 人工確認 mapping（HITL）
```

#### Service 3: Habit Store ⭐ 新增

```
職責：習慣的持久化、信度計算、來源衝突處理、增量/全量更新
DB Table：staff_priorities, staff_workstations, staffing_requirements, habit_metadata
API：
  GET    /habits/by-person                 — 列出所有員工的習慣
  GET    /habits/by-person/{staff_id}      — 取得單人習慣詳情
  GET    /habits/by-shift                  — 列出所有班次的習慣
  GET    /habits/by-shift/{shift_code}     — 取得單班次習慣詳情
  POST   /habits/analyze                   — 觸發全量分析（S1）
  POST   /habits/feedback                  — 班表發佈回寫（S2）
  PUT    /habits/by-person/{id}/override   — 手動修改個人習慣
  PUT    /habits/by-shift/{code}/override  — 手動修改班次習慣
  GET    /habits/confidence-report         — 信度衰減報告
```

#### Service 4: Mind Engine

```
職責：約束管理、P0/P1/P2 層級、solver 權重、便利貼 → 約束轉換
DB Table：constraint_sets, sticky_notes, store_priorities
API：
  GET    /mind/constraints                 — 取得所有約束（P0+Hard+P1+P2）
  PUT    /mind/store-priority              — 更新門市優先規則
  GET    /mind/solver-weights              — 取得 solver 權重
  PUT    /mind/solver-weights              — 更新 solver 權重
  POST   /mind/sticky-notes               — 新增便利貼（自動判斷 employer/employee）
  PUT    /mind/sticky-notes/{id}           — 修改便利貼
  DELETE /mind/sticky-notes/{id}           — 刪除便利貼
  POST   /mind/validate                   — 驗證約束是否互相衝突
  GET    /mind/sticky-notes/conflicts      — 列出便利貼間的衝突
```

#### Service 5: Analyzer Agent ⭐ 拆分自 Agent Core

```
職責：Identity Resolution、Preference Calculation、Skill Extraction、HITL
依賴：Data Parse (取 historical_rosters) → Habit Store (寫習慣)
觸發：S1 上傳分析 / S2 班表回寫 / S3-S4 便利貼修改
API：
  POST   /analyzer/run                 — 執行全量分析
  POST   /analyzer/incremental         — 執行增量更新（班表回寫）
  POST   /analyzer/apply-note          — 便利貼 → 修改習慣
  GET    /analyzer/status/{job_id}     — 分析進度
  GET    /analyzer/report/{job_id}     — 分析報告
腳本：scripts/analyzer.py
```

#### Service 6: Scheduler Agent ⭐ 拆分自 Agent Core

```
職責：CP-SAT 模型建構、求解、草稿輸出、迭代重排
依賴：Habit Store (讀習慣) + Mind Engine (讀約束+權重)
觸發：S5 用戶觸發排班
API：
  POST   /scheduler/confirm-targets    — 確認排班目標人+目標班
  POST   /scheduler/solve              — 執行求解
  POST   /scheduler/retry              — 鎖定已確認 + 子集重排
  GET    /scheduler/status/{job_id}    — 求解進度
  GET    /scheduler/result/{job_id}    — 排班結果
腳本：scripts/ortools_solver.py
```

#### Service 7: Auditor Agent ⭐ 拆分自 Agent Core

```
職責：P0/Hard/P1/P2 逐層檢核、回溯檢查、修正建議
依賴：Habit Store (讀習慣做 P1/P2 檢核) + Mind Engine (讀約束)
觸發：S5 Phase 3 排班後檢核 / S6 手動觸發 / 便利貼新增後回溯
API：
  POST   /auditor/check                — 檢核班表
  POST   /auditor/retroactive          — 回溯檢查（新規則 vs 現有班表）
  GET    /auditor/report/{job_id}      — 檢核報告
  GET    /auditor/violations/{schedule_id} — 特定班表的違規清單
腳本：scripts/auditor_tools.py
```

#### Service 8: Output Render + Notification Service

```
職責：班表格式化、匯出、Gmail 通知、報告生成
依賴：所有 Agent 的產出
API：
  POST   /output/render                — 格式化班表
  POST   /output/export                — 匯出 CSV/XLSX/PDF
  POST   /notifications/send           — 發送通知
  GET    /notifications/history        — 通知歷史
  PUT    /notifications/preferences    — 更新通知偏好
```

### 9.3 閉環資料流時序圖

```
排班專員    Gateway    DataParse  HabitStore  MindEngine  Analyzer  Scheduler  Auditor  Notification
    │          │          │          │           │          │          │         │          │
    │          │          │          │           │          │          │         │          │
    │ ── S1: 上傳歷史 CSV ──────────▶│          │           │          │          │         │          │
    │          │          │──parse──▶│           │          │          │         │          │
    │◀─preview─│◀─────────│          │           │          │          │         │          │
    │──confirm─│─────────▶│          │           │          │          │         │          │
    │          │          │──────────│───────────│─trigger─▶│          │         │          │
    │          │          │          │           │          │          │         │          │
    │          │          │          │◀──write───│──────────│          │         │          │
    │          │          │          │  habits   │          │          │         │          │
    │◀─report──│◀─────────│──────────│───────────│──────────│          │         │          │
    │          │          │          │           │          │          │         │          │
    │ ── S3: 雇主便利貼 ────────────────────────▶│          │          │         │          │
    │          │          │          │           │──parse──▶│          │         │          │
    │◀─confirm─│◀─────────│──────────│───────────│──────────│          │         │          │
    │──yes────▶│          │          │◀──write───│──────────│          │         │          │
    │          │          │          │           │          │          │         │          │
    │          │          │          │           │          │          │─retro──▶│          │
    │◀─impact──│◀─────────│──────────│───────────│──────────│──────────│─────────│          │
    │          │          │          │           │          │          │         │          │
    │ ── S5: 觸發排班 ─────────────────────────────────────────────────│         │          │
    │          │          │          │           │          │          │         │          │
    │◀ Phase1: 確認目標 ──▶│          │           │          │          │         │          │
    │──confirm─│──────────│──────────│◀──read────│──────────│──solve──▶│         │          │
    │          │          │          │  habits   │◀─read────│──────────│         │          │
    │          │          │          │           │ weights  │          │         │          │
    │          │          │          │           │          │──draft──▶│──check─▶│          │
    │          │          │          │◀──read────│──────────│──────────│─────────│          │
    │          │          │          │  habits   │          │          │         │          │
    │◀Phase4:result──────────────────│───────────│──────────│◀─────────│◀────────│          │
    │          │          │          │           │          │          │         │          │
    │──[accept]│          │          │           │          │          │         │          │
    │          │──S2: feedback───────│◀──incr────│──────────│          │         │          │
    │          │──notify──│──────────│───────────│──────────│──────────│─────────│─────────▶│
    │          │          │          │           │          │          │         │    Gmail─▶│
```

### 9.4 DB Schema

```sql
-- ===== Layer 0: Identity =====

CREATE TABLE users (
    id UUID PRIMARY KEY,
    display_name VARCHAR(100) NOT NULL,
    gmail VARCHAR(255) UNIQUE,
    gmail_verified BOOLEAN DEFAULT false,
    gmail_connected_at TIMESTAMP,
    preferences JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE stores (
    id UUID PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    company VARCHAR(100),
    timezone VARCHAR(50) DEFAULT 'Asia/Taipei',
    locale VARCHAR(10) DEFAULT 'zh-TW',
    config JSONB DEFAULT '{}'
);

CREATE TABLE user_store_roles (
    user_id UUID REFERENCES users(id),
    store_id UUID REFERENCES stores(id),
    role VARCHAR(50) NOT NULL,
    PRIMARY KEY (user_id, store_id)
);

-- ===== Layer 1: Data =====

CREATE TABLE uploads (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES stores(id),
    uploaded_by UUID REFERENCES users(id),
    module_type VARCHAR(50) NOT NULL,
    file_path VARCHAR(500),
    parse_status VARCHAR(20) DEFAULT 'pending',
    parsed_result JSONB,
    field_mapping JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE historical_rosters (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES stores(id),
    upload_id UUID REFERENCES uploads(id),
    staff_name_raw VARCHAR(100),
    staff_id UUID REFERENCES staff(id),
    shift_code_raw VARCHAR(50),
    station_raw VARCHAR(50),
    roster_date DATE NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE staff (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES stores(id),
    staff_name VARCHAR(100) NOT NULL,
    position VARCHAR(50),
    seniority_years NUMERIC(4,1) DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE shifts (
    shift_code VARCHAR(20) PRIMARY KEY,
    tenant_id UUID REFERENCES stores(id),
    display_format VARCHAR(50),
    start_time TIME,
    end_time TIME,
    work_minutes INT,
    break_minutes INT DEFAULT 0,
    is_one_day BOOLEAN DEFAULT true
);

CREATE TABLE leaves (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES stores(id),
    staff_id UUID REFERENCES staff(id),
    leave_date DATE NOT NULL,
    leave_type VARCHAR(30) NOT NULL
);

-- ===== Layer 2: Habit =====

CREATE TABLE staff_priorities (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES stores(id),
    staff_id UUID REFERENCES staff(id),
    shift_code VARCHAR(20) REFERENCES shifts(shift_code),
    priority_level INT NOT NULL,
    frequency INT DEFAULT 0,
    confidence NUMERIC(4,3) DEFAULT 0.5,
    source VARCHAR(30) NOT NULL,
    note_id UUID,
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (tenant_id, staff_id, shift_code)
);

CREATE TABLE staff_workstations (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES stores(id),
    staff_id UUID REFERENCES staff(id),
    station VARCHAR(50) NOT NULL,
    frequency INT DEFAULT 0,
    is_qualified BOOLEAN DEFAULT false,
    confidence NUMERIC(4,3) DEFAULT 0.5,
    source VARCHAR(30) NOT NULL,
    note_id UUID,
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (tenant_id, staff_id, station)
);

CREATE TABLE staffing_requirements (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES stores(id),
    scenario_name VARCHAR(50) NOT NULL,
    shift_code VARCHAR(20) REFERENCES shifts(shift_code),
    station VARCHAR(50) NOT NULL,
    min_workers INT NOT NULL,
    avg_workers NUMERIC(4,1),
    source VARCHAR(30) NOT NULL,
    note_id UUID,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE habit_metadata (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES stores(id),
    staff_id UUID,
    shift_code VARCHAR(20),
    last_analyzed TIMESTAMP,
    data_points INT DEFAULT 0,
    analysis_period_start DATE,
    analysis_period_end DATE,
    analysis_mode VARCHAR(20) DEFAULT 'full'
);

-- ===== Layer 3: Mind =====

CREATE TABLE store_priorities (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES stores(id),
    station_ranking JSONB DEFAULT '[]',
    leave_policy VARCHAR(50) DEFAULT 'employee_priority',
    weekend_policy JSONB DEFAULT '{}',
    overtime_policy JSONB DEFAULT '{}',
    solver_weights JSONB DEFAULT '{}',
    optimization_goal VARCHAR(30) DEFAULT 'balanced',
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE sticky_notes (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES stores(id),
    created_by UUID REFERENCES users(id),
    author_role VARCHAR(30) NOT NULL,
    source_type VARCHAR(20) NOT NULL CHECK (source_type IN ('employer', 'employee')),
    content TEXT NOT NULL,
    parsed_constraint JSONB,
    affects VARCHAR(30) NOT NULL,
    constraint_level VARCHAR(10) NOT NULL CHECK (constraint_level IN ('hard', 'soft')),
    effective VARCHAR(20) DEFAULT 'permanent' CHECK (effective IN ('permanent', 'one_time', 'date_range')),
    valid_from DATE,
    valid_until DATE,
    is_active BOOLEAN DEFAULT true,
    applied_to_habits BOOLEAN DEFAULT false,
    conflict_with JSONB DEFAULT '[]',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE constraint_sets (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES stores(id),
    check_level VARCHAR(10) NOT NULL CHECK (check_level IN ('P0', 'hard', 'P1', 'P2')),
    source VARCHAR(50) NOT NULL,
    rules JSONB NOT NULL,
    blocking BOOLEAN DEFAULT false,
    overridable BOOLEAN DEFAULT false,
    override_requires VARCHAR(50)
);

-- ===== Schedule Output =====

CREATE TABLE schedules (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES stores(id),
    generated_by UUID REFERENCES users(id),
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    scenario_name VARCHAR(50),
    assignments JSONB NOT NULL,
    status VARCHAR(20) DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'archived')),
    solver_status VARCHAR(20),
    objective_value NUMERIC,
    solve_time_seconds NUMERIC(8,2),
    template_id UUID REFERENCES templates(id),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE schedule_versions (
    id UUID PRIMARY KEY,
    schedule_id UUID REFERENCES schedules(id),
    version_number INT NOT NULL,
    change_reason TEXT,
    locked_assignments JSONB DEFAULT '[]',
    retry_targets JSONB DEFAULT '{}',
    assignments JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE audit_reports (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES stores(id),
    schedule_id UUID REFERENCES schedules(id),
    schedule_version INT,
    status VARCHAR(10) NOT NULL CHECK (status IN ('pass', 'warn', 'fail')),
    P0_count INT DEFAULT 0,
    hard_count INT DEFAULT 0,
    P1_count INT DEFAULT 0,
    P2_count INT DEFAULT 0,
    violations JSONB DEFAULT '[]',
    suggestions JSONB DEFAULT '[]',
    total_checked INT DEFAULT 0,
    pass_rate NUMERIC(5,4) DEFAULT 0,
    checked_at TIMESTAMP DEFAULT NOW()
);

-- ===== Layer 6: Template =====

CREATE TABLE templates (
    id UUID PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    category VARCHAR(50),
    inherits_from UUID REFERENCES templates(id),
    layers JSONB NOT NULL,
    created_by UUID REFERENCES users(id),
    is_system BOOLEAN DEFAULT false,
    is_default BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

---

## 10. 設定後台頁面結構

```
/settings
│
├── /settings/identity                  ← L0 身份設定
│   ├── Gmail 連結與驗證（首要）
│   ├── 個人資訊與偏好
│   └── 門市歸屬管理
│
├── /settings/data                      ← L1 資料設定
│   ├── 上傳管理
│   ├── 欄位映射規則
│   ├── Identity Resolution 設定
│   └── 幽靈班次偵測開關
│
├── /settings/habits                    ← L2 習慣管理
│   ├── 習慣 by 人 總覽（含信度指標）
│   ├── 習慣 by 班 總覽
│   ├── 信度衰減設定
│   └── 來源優先序
│
├── /settings/mind                      ← L3 認知設定
│   ├── P0/P1/P2 約束層級管理
│   ├── Solver 權重調整
│   ├── 最佳化目標選擇
│   ├── 門市優先規則
│   ├── 雇主便利貼管理
│   └── 員工便利貼總覽（唯讀）
│
├── /settings/agents                    ← L4 Agent 設定
│   ├── /settings/agents/analyzer
│   │   ├── 模糊比對閾值
│   │   ├── 技能認定門檻
│   │   └── 幽靈班次防護
│   ├── /settings/agents/scheduler
│   │   ├── 求解器參數（超時/工人數）
│   │   ├── 重排行為（鎖定/重試次數）
│   │   └── 目標確認流程
│   └── /settings/agents/auditor
│       ├── 檢核層級開關
│       ├── 勞基法版本
│       ├── 回溯檢核開關
│       └── 修正建議設定
│
├── /settings/output                    ← L5 輸出設定
│   ├── 班表格式
│   ├── 匯出偏好
│   ├── 報告內容選擇
│   └── 通知管道與收件人
│
└── /settings/templates                 ← L6 模板管理
    ├── 系統模板瀏覽
    ├── 自訂模板建立
    ├── 模板套用紀錄
    └── 匯出入模板
```

---

## 11. 設定初始化流程（Onboarding）

```
Step 1: Identity
┌────────────────────────────────────┐
│  歡迎使用 AI 排班助手！              │
│                                    │
│  請先連結您的 Gmail：                │
│  [      your@gmail.com        ]    │
│  [🔗 連結 Google 帳號]              │
│                                    │
│  您的角色：                         │
│  ◉ 排班主管  ○ 排班專員  ○ 店長      │
│                                    │
│  所屬門市：                         │
│  [  台北信義店  ▼ ]                 │
│                                    │
│              [下一步 →]             │
└────────────────────────────────────┘

Step 2: Template Selection
┌────────────────────────────────────┐
│  選擇排班模板：                      │
│                                    │
│  🏪 零售門市    🍽️ 餐飲門市          │
│  🏥 醫療護理    ⚙️ 從零開始          │
│                                    │
│   [← 上一步]        [下一步 →]      │
└────────────────────────────────────┘

Step 3: Mind Quick Setup
┌────────────────────────────────────┐
│  快速設定：                         │
│                                    │
│  休假策略  ◉ 員工優先  ○ 系統統一     │
│  站區排序  ≡ 櫃檯 ≡ 備料 ≡ 外場 ≡ 庫存│
│  週末輪班  [✅]  週期: [4 週]        │
│  最佳化    ◉ 平衡  ○ 覆蓋優先        │
│                                    │
│   [← 上一步]        [下一步 →]      │
└────────────────────────────────────┘

Step 4: Upload Historical Data ⭐ 新增
┌────────────────────────────────────┐
│  上傳歷史班表 (可選)：                │
│                                    │
│  拖放 CSV / Excel 至此              │
│  ┌──────────────────────────┐      │
│  │                          │      │
│  │     📁 拖放檔案至此       │      │
│  │                          │      │
│  └──────────────────────────┘      │
│                                    │
│  ⓘ 上傳歷史班表可讓 AI 學習         │
│    您的排班習慣，產出更好的建議。      │
│    也可以之後再上傳。                │
│                                    │
│   [← 上一步]    [跳過]  [完成 ✓]    │
└────────────────────────────────────┘
```

---

## 12. 變更對照表

### 與前版 agent-settings-backend-spec v1 的差異

| 變更項 | v1 | v2 | 原因 |
|--------|----|----|------|
| Layer 數量 | 5 層 (L1~L5) | 7 層 (L0~L6) | 新增 Habit + Agents 層 |
| 習慣層 | 不存在 | L2 Habit（含信度衰減、來源優先序） | product-spec §2 核心概念 |
| Agent 設定 | 單一 Agent Core | 拆為 Analyzer / Scheduler / Auditor | product-spec 三 Agent 閉環 |
| 約束結構 | legal / company / store / sticky | P0 / Hard / P1 / P2 四層級 | 對齊 auditor.md 檢查優先序 |
| 便利貼 | 單一來源 | 雇主(employer) + 員工(employee) 雙來源 | product-spec §5/§6 |
| Solver 權重 | 不存在 | W_vac / W_pref / W_shift 等 6 個可調參數 | 對齊 scheduler.md Objective Function |
| 角色權限 | 5 欄 | 8 欄（新增便利貼權限） | 員工可貼偏好便利貼 |
| DB Schema | 9 表 | 16 表（+audit_reports, habit_metadata 等） | 三 Agent + Habit 資料需求 |
| SA 服務 | 6 個 | 9 個（Habit Store + 3 Agent 拆分） | 職責更明確 |
| 時序圖 | 單線程 | 閉環（S1→S3→S5→S6→S2→循環） | product-spec 閉環設計 |
| Onboarding | 3 步 | 4 步（+上傳歷史班表） | 習慣初始化 |

### 與 product-spec 的對應

| product-spec 章節 | 本文件對應 |
|-------------------|----------|
| §2 習慣模型 | §4 Layer 2: Habit |
| §2.3 來源優先序 | §4.4 habit_source_priority |
| §2.4 信度衰減 | §4.3 confidence_decay |
| §3 S1 分析舊班表 | §3.2 Identity Resolution + §6.1 Analyzer |
| §5 S3 雇主便利貼 | §5.4 sticky_notes.employer_notes |
| §6 S4 員工便利貼 | §5.4 sticky_notes.employee_notes |
| §7 S5 排班 | §5.3 solver_weights + §6.2 Scheduler |
| §7.2 迭代重排 | §6.2 scheduler.retry |
| §8 S6 檢核 | §5.1 P0/P1/P2 + §6.3 Auditor |
| §8.4 回溯檢核 | §6.3 auditor.retroactive_check |
| §10 Agent 設定預設 | §6 Layer 4: Agents (全部) |
