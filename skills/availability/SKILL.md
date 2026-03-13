---
name: availability
description: Collect employee availability for scheduling. FT employees declare rest days, PT employees declare available date+time windows. Produces availability.json consumed by solver (HC13) and auditor (P0-006). Supports fallback to rest_days.json.
---

# Availability Collection — SKILL.md

> **前置條件**：先讀取 `CLAUDE.md` 了解多租戶架構。

## 1. Overview

每輪排班前，店長透過 LINE 記事本搜集員工可用性：
- **正職 (FT)**：指定休假日期（哪幾天不上班）
- **兼職 (PT)**：可上班日期 + 時間區段（如 3/10 17:00-22:00）

資料流：

```
LINE 記事本 → (Phase 1: 手動填寫 / Phase 2: OCR) → availability.json → solver + auditor
```

## 2. Schema Reference — `availability.json`

```json
{
  "_comment": "每週可用性搜集",
  "week": "2026-03-09",
  "designated_rest": {
    "6": ["2026-03-11", "2026-03-12"],
    "10": ["2026-03-14"]
  },
  "pt_availability": {
    "25": {
      "2026-03-09": {"start": "17:00", "end": "22:00"},
      "2026-03-10": {"start": "17:00", "end": "23:00"},
      "2026-03-12": {"start": "18:00", "end": "22:00"}
    }
  }
}
```

| 欄位 | 型別 | 說明 |
|------|------|------|
| `week` | string | 週起始日 `YYYY-MM-DD`（參考用） |
| `designated_rest` | `{emp_id: [date, ...]}` | FT 員工指定休假日期 |
| `pt_availability` | `{emp_id: {date: {start, end}}}` | PT 員工可上班時段 |

### PT 可用性規則

- **未列日期** → 視為該日不可上班，solver 禁止排班
- **有列日期** → 僅可排入完全落在 `start`~`end` 區段內的班次
- 時間格式：`HH:MM`（24 小時制）
- 支援跨日班次（`end` < `start` 自動 +24h）

## 3. Quick Start（Phase 1 手動填寫）

```bash
# 1. 建立本週 availability.json
cat > tenants/<tenant>/availability.json << 'EOF'
{
  "week": "YYYY-MM-DD",
  "designated_rest": {
    "<emp_id>": ["YYYY-MM-DD", "YYYY-MM-DD"]
  },
  "pt_availability": {
    "<emp_id>": {
      "YYYY-MM-DD": {"start": "17:00", "end": "22:00"},
      "YYYY-MM-DD": {"start": "17:00", "end": "23:00"}
    }
  }
}
EOF

# 2. 執行排班
python3 scripts/run.py --tenant <tenant> --week YYYY-MM-DD

# 3. 稽核確認無 P0-006 違規
python3 scripts/run.py --tenant <tenant> --week YYYY-MM-DD --step auditor
```

## 4. FT vs PT 差異

| | FT（正職） | PT（兼職） |
|---|---|---|
| 輸入 | `designated_rest`: 不上班的日期 | `pt_availability`: 可上班的日期+時段 |
| 預設行為 | 未指定 → 可上班 | 未指定 → 不可上班 |
| Solver 約束 | HC6: 指定休假日禁止排班 | HC13: 僅在宣告時段內排班 |
| Auditor 檢查 | P0-005: 指定休假被排班 | P0-006: 超出宣告時段 |

## 5. Solver 整合

### HC6（不變）— 指定休假

FT 員工的 `designated_rest` 日期轉換為 day_index，HC6 禁止該日排班。

### HC13（新增）— PT 可用性時段

```
對每位 PT 員工 e:
  如果 pt_availability 中有 e 的資料:
    對每天 d:
      如果 d 不在可用清單 → 所有班次 = 0
      如果 d 在可用清單 → 僅允許時間完全落在 [avail_start, avail_end] 的班次
```

## 6. 從 rest_days.json 遷移

`availability.json` 向下相容 `rest_days.json`：
- 若 `availability.json` 存在 → 使用它
- 若不存在 → 自動 fallback 到 `rest_days.json`（PT 可用性為空）
- `designated_rest` 格式與 `rest_days.json` 完全一致

遷移步驟：
1. 將 `rest_days.json` 的 `designated_rest` 複製到 `availability.json`
2. 加入 `pt_availability` 欄位
3. 驗證排班結果一致
4. （可選）刪除 `rest_days.json`
