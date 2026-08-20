# HARA Auto-Fill Agent Instructions (v13.0)

## Entry Points

| Command | Purpose |
|---------|---------|
| `python scripts/main_executor.py agent --domain avp --item FILE --template FILE --output FILE --max-workers 4 --allow-draft` | **New Agent path** - typed state, controlled LLM concurrency, checkpoint, quality gate and template-preserving Draft report |
| `python scripts/main_executor.py agent-doctor --domain avp --template FILE` | Read-only readiness check for template, Domain Profile and LLM configuration |
| `python scripts/main_executor.py analyze --system NAME --item FILE --template FILE` | **Smart analysis** - auto-detect experience library, else use 3-phase engine |
| `python scripts/main_executor.py analyze --function NAME --item FILE --template FILE` | Analyze by function name |
| `python scripts/main_executor.py analyze --system NAME --item FILE --template FILE --resume` | Resume from checkpoint |
| `python scripts/main_executor.py fallback --item FILE --template FILE` | Force original 17-step engine (Fallback) |
| `python scripts/main_executor.py info` | Show experience library index |
| `python scripts/hara_controller.py --item FILE --template FILE --output FILE` | Direct 3-phase controller |

| `python scripts/report_generator.py --template FILE --experience-json FILE --output FILE` | Generate report from experience JSON |
| `python test_skill.py` | Run test suite |
| `python scripts/example.py` | Run usage examples |

## V12 Architecture (Three-Phase + Fallback)

```
main_executor.analyze() → match_input() →
  ├─ [Experience hit] → extract_to_json() → report_generator(--experience-json)
  └─ [Miss] → hara_controller.run() →
        ├─ Phase1: malfunction_engine → malfunction_checker
        ├─ Phase2: scen_hazevent_engine → scenario_checker
        ├─ Phase3: scoring_sg_engine → scoring_checker
        └─ [Fallback: >20 retries OR >10min] → hara_engine.py (full 17-step)
    → report_generator(--3-json)
```

## ASIL Calculation

Use the **`ASIL_Table` matrix in the input Excel template** as the only
authoritative S/E/C-to-ASIL mapping. Do not calculate ASIL from a numerical
sum. Missing sheets, incomplete matrices, and invalid S/E/C values are hard
errors. Template `NA` entries on the S0/E0/C0 axes are exported as `QM`.

## Fallback Conditions

Fallback to original `hara_engine.py` triggers when **either**:
1. A single checker retries the same problem **more than 20 times**
2. A single problem fix takes **more than 10 minutes (600s)**

Monitored by `FallbackMonitor` class in `main_executor.py`.

## JSON Outputs

| Phase | File | Contains |
|-------|------|----------|
| Phase1 | `malfunction_output.json` | functions, function_outputs, function_guidewords, function_malfunctions, function_hazards |
| Phase2 | `scen_hazevent_output.json` | function_scenarios, hazard_events |
| Phase3 | `scoring_sg_output.json` | severity_results, exposure_results, controllability_results, asil_results, safety_goals, safe_states |
| Experience | `experience_library_{subsystem}_{timestamp}.json` | 05_HARA, 06_Safety Goal, subsystem, timestamp, record_count, source, exported_by, hara_data (backward compat) |

## Completeness Checks

### 14 HAZOP Guidewords
All must be used. Check with `MalfunctionChecker.check_guideword_completeness()`.

### 14 Main Scenarios
All must be used. Check with `ScenarioChecker.check_scenario_completeness()`.

### Full Function-Guideword Combinations
Each function must combine with all 14 guidewords. Check with `check_full_combination_completeness()`. If incomplete, must return to HARA engine to supplement.

### Scoring Checker (v12 new)
Non-S0/non-E0/non-C0 items must have values (not "empty" or "N/A"), unless the guideword is marked as not applicable or S=S0.

## Post-Processing Steps (Frequently Missed)

After generating HARA data, these steps are **mandatory**:

1. **Delete empty row 6** before exporting
2. **Merge repeated static columns hierarchically**: B=Function, C=Output,
   D=Guide-Word, E=Malfunction, F=Hazard, and G only when the situation text is
   exactly identical within the same hazard. Never merge dynamic columns H-U.
3. **Fill R/S/T columns**: QM rows get "NA"; non-QM rows inherit safety goals
4. **Format 05_HARA (3.1)**: Arial 11pt, thin borders, row height 60, auto-wrap, auto column width
5. **Apply color scheme (3.2)**: Color by function type. **Data rows (row 7+) must have NO fill color** - only header rows
6. **Fill 06_Safety Goal (3.3)**: Only non-QM records. Calculate Max.ASIL per SG-ID (highest rank)

## Data Structure

- Data starts at **row 6** (row 5 is header)
- Excel template must be copied with `shutil.copy2()`, not created blank
- Column mapping: A=HARA-ID, B=Function, C=Output, D=Guide-Word, E=Malfunction, F=Hazard, G=Situational description, H=Situational detailing, I=Hazardous event, J=Severity, K=Rationale Severity, L=Exposure, M=T/F, N=Rationale Exposure, O=Controllability, P=Rationale Controllability, Q=ASIL, R=SG-ID, S=Safety Goal, T=Safe state, U=Remark

## Dependencies

- Core: openpyxl>=3.0.0, python-docx>=0.8.11, PyPDF2>=2.0.0, pdfplumber>=0.9.0
- Python 3.12+（当前验证基线：Python 3.12.10）
- No pandas dependency

## Authoritative Documentation

Root `SKILL.md` is the compatibility router. Stage-specific Agent procedures live under `skills/*/SKILL.md`; runtime engineering rules live in the input documents, input template, approved Domain Profile, and deterministic services. Historical V12 lessons below are migration evidence and must not override the current architecture or the template-only ASIL rule.

## Experience Library Paths

- Local: `C:\Users\F21N3EE\AIHARA\AI_HARA_Skill\99_HARA-CEA\`
- Remote: `C:\Users\TVUEDEQ\AI_task\01_HARA\99_HARA-CEA\`
- Search by subsystem (Light, Seat, Wiper, Door, Window, HUD) or by function name

---

# Lesson Learned (from hara-v11, 2026-05-20)

## LL-05: Cell Merge Range Must Be Limited to Static Columns

**Problem**: All columns with adjacent identical values were merged, causing ASIL, Safety Goal and other dynamic-value columns to be incorrectly merged.

**Solution**:
```python
# Merge repeated static fields with parent-group boundaries.
# Other dynamic-value columns keep independent cells.
def _merge_duplicate_cells(self, ws, start_row: int = 6):
    merge_columns = [2, 3, 4, 5, 6, 7]
    for col in merge_columns:
        # hierarchical merge logic...
```
**Effect**: Static repeated content is grouped like an engineering HARA report;
dynamic scenario parameters, S/E/C, ASIL and safety-goal columns remain independent.

---

## LL-06: MalfunctionChecker Logical Validity Evaluation

**Problem**: Malfunction combinations contain violations of physical logic or cognitive logic.

**Solution**: Add physical logic and redundant guideword checks:
```python
self.physical_logic_checks = [
    (r"位置.*(过大|过小)", "位置参数不存在量值大小的物理概念"),
    (r".*too large.*速度", "速度是标量，不存在'过大''过小'概念"),
]
self.redundant_guideword_checks = [
    # loss and too long are semantically duplicate in communication loss scenarios
    (r"通讯.*loss|通讯丢失", "too long"),
]

def check_malfunction_logical_validity(self, function, guideword, malfunction_desc):
    result = {"is_valid": True, "invalid_reason": None, "suggestion": None}
    for pattern, reason in self.physical_logic_checks:
        if re.search(pattern, malfunction_desc, re.IGNORECASE):
            result["is_valid"] = False
            result["suggestion"] = f"不适用，不符合{reason}"
            return result
    # Check redundant guidewords (too long vs loss)
    if guideword.lower() in ["too long", "too short"]:
        if any(kw in malfunction_desc.lower() for kw in ["丢失", "loss", "失效"]):
            result["is_valid"] = False
            result["suggestion"] = "不适用，已在loss引导词中覆盖"
            return result
    return result
```
**Tagging Format**:
- Physical violation: `不适用，不符合<specific reason>`
- Cognitive duplication: `不适用，已在<covered guideword>中覆盖`

---

## LL-07: Scen-HazEvent "Not Applicable" Skip Logic

**Problem**: When preceding malfunction is marked as "不适用", the engine still continues scenario combination analysis.

**Solution**: Add `"不适用"` check in Step6/7/8:
```python
# Step6 (scene combination)
for func, hazards in function_hazards.items():
    for hazard in hazards:
        malf_desc = hazard.get("malfunction", "")
        if "不适用" in malf_desc:
            malf_scenarios = {malf_desc: [{"scenario_summary": "不适用", "hazard_event": "不适用"}]}
            function_scenarios[func] = malf_scenarios
            continue

# Step7 and Step8 similar handling
if "不适用" in malf or "不适用" in hazard_event:
    malf_xxx = [{"xxx": "不适用"}]
    continue
```
**Effect**: Rows containing "不适用" no longer produce invalid scenario combinations.

---

## LL-08: Scoring-SG "Not Applicable" Skip Logic

**Problem**: When preceding hazard event is marked as "不适用", the engine still continues scoring and safety goal generation.

**Solution**: Add `"不适用"` check in Step9/10/13/16:
```python
# Step9_SeverityReasoning
for malf, scenarios in malf_events.items():
    hazard_event = scenarios[0].get("hazard_event", "") if scenarios else ""
    if "不适用" in hazard_event:
        malf_reasoning[malf] = [{"hazard_event": "不适用", "severity_reason": "不适用"}]
        continue

# Step10, Step13, Step16 similar
if "不适用" in hazard_event or asil == "QM":
    goal_items.append({"ASIL": asil, "safety_goal": "不适用"})
    continue
```
**Effect**: ASIL QM or "不适用"-marked hazard events no longer produce redundant scoring.

---

## LL-09: Scenario Combination Completeness Check

**Problem**: Functions' related main scenarios are not fully enumerated for permutation combination with malfunctions.

**Solution**: Add `FUNCTION_SCENARIO_CORRELATION` rules + completeness check + supplement in scenario_checker.py:
```python
FUNCTION_SCENARIO_CORRELATION = {
    "OTA": ["Highway", "Urban", "Rural", "Parking", "Residential"],
    "下载": ["Highway", "Urban", "Rural", "Parking", "Residential"],
}

def check_function_scenario_completeness(self, func, used_scenarios):
    expected = self.get_related_scenarios_for_function(func)
    # compare and return missing scenarios

def supplement_missing_scenarios(self, func, existing_combos, malfunctions):
    # generate new combinations for missing scenarios
```
**Effect**: All related main scenarios are enumerated per function; missing scenarios are auto-supplemented before report export.

---

## LL-10: Severity Analysis Result Consistency

**Problem**: Step10 severity S-value is inconsistent with Step9 severity reasoning result.

**Root Cause**: Step9's `_reason_severity` extracts speed from `refined_scenarios[0].get('vehicle_speed', '')`. If extraction fails, it produces wrong S-values.

**Solution**: Add fallback speed extraction:
```python
if vehicle_speed == 0:
    speed_match = re.search(r'(\d+)\s*(km/?h|mph)?', hazard_lower)
    if speed_match:
        vehicle_speed = int(speed_match.group(1))
```

---

## LL-11: Exposure/Controllability N/A Issue (Dimension Mismatch)

**Problem**: Steps 11-14 content is N/A, but Scen-HazEvent has already analyzed the corresponding context.

**Root Cause**: Step11/12 lack "不适用" check logic, causing data dimension inconsistency.

**Solution**:
```python
# Step11 add "不适用" check
if "不适用" in malf:
    malf_reasoning[malf] = [{"scenario": "不适用", "exposure_reason": "不适用"}]
    continue

# Step12 add "不适用" check
if not scenarios or "不适用" in str(scenarios):
    malf_scores[malf] = [{"scenario": "不适用", "exposure_score": "N/A"}]
    continue
```
**Effect**: Data dimensions stay consistent across Steps 11-12 and 13-14.

---

## LL-12: N/P Column Exposure/Controllability Rationale N/A (Field Not Passed)

**Problem**: Column N (Exposure Rationale) and Column P (Controllability Rationale) show N/A in report.

**Root Cause**:
- `exposure_scores` lacks `exposure_reason` field (Step12 doesn't pass it)
- `controllability_scores` lacks `controllability_reason` field (Step14 doesn't pass it)
- report_generator expects `reason` field but it was never passed through

**Data Flow Issue**:
```
Step11 → exposure_reasoning (has exposure_reason) ✓
Step12 → exposure_scores (missing exposure_reason) ✗
report_generator → reads exp_item.get("exposure_reason") → N/A
```

**Fixes**:

**Fix 1**: Step12 pass `exposure_reason`:
```python
scenario_scores.append({
    "scenario": sr.get("scenario", ""),
    "exposure_score": score,
    "exposure_reason": sr.get("exposure_reason", "N/A")  # must include
})
```

**Fix 2**: Step13 rewrite to ISO 26262 Part3 compliant version — add different gender/age driver analysis, reaction time benchmarks, C0-C4 level definitions, driver injury avoidance capability analysis:
```python
DRIVER_REACTION_TIME = {
    "young_male": {"avg": 1.5, "range": "1.3-2.0"},
    "young_female": {"avg": 1.7, "range": "1.5-2.2"},
    "middle_male": {"avg": 1.8, "range": "1.5-2.5"},
    "middle_female": {"avg": 2.0, "range": "1.7-2.7"},
    "elderly_male": {"avg": 2.5, "range": "2.0-3.5"},
    "elderly_female": {"avg": 2.8, "range": "2.2-3.8"}
}
```

**Fix 3**: Step14 pass `controllability_reason`:
```python
scenario_scores.append({
    "hazard_event": sr.get("hazard_event", ""),
    "controllability_score": score,
    "controllability_reason": controllability_reason,
    "driver_analysis": sr.get("driver_analysis", {})
})
```

**Fix 4**: report_generator.py add fallback mechanism:
```python
# N column (add fallback)
exp_reason = exp_item.get("exposure_reason", "")
if not exp_reason or exp_reason == "N/A":
    fallback_exp = func_exposure_reasoning.get(malf_desc, [])
    exp_reason = fallback_exp[0].get("exposure_reason", "") if fallback_exp else "N/A"
self._set_cell(ws, row, 14, exp_reason if exp_reason else "N/A")

# P column (add fallback)
ctrl_reason = ctrl_item.get("controllability_reason", "")
if not ctrl_reason or ctrl_reason == "N/A":
    fallback_ctrl = func_ctrl_reasoning.get(malf_desc, [])
    ctrl_reason = fallback_ctrl[0].get("controllability_reason", "") if fallback_ctrl else "N/A"
self._set_cell(ws, row, 16, ctrl_reason if ctrl_reason else "N/A")
```

**Effect**:
1. Column N correctly displays exposure rationale analysis
2. Column P correctly displays controllability rationale (ISO 26262 Part3 compliant, including driver group analysis by gender and age)
3. Complies with ISO 26262 Part3 C0-C4 level definitions
4. Considers injury avoidance capability differences across different driver demographics

---

## Debugging Checklist: When Report Columns Show N/A

Check in this order:

1. **Check context file source**: Must use `scoring_sg_output.json` (V12) or `scoring_sg_context.json` (v11) — contains Steps 1-17 complete results
2. **Check steps completed**: `ctx.metadata.get("steps_completed")` should include `[1,2,3]` or `[1,2,...,17]`
3. **Verify report generation method**: Call `generator._validate_v11_context(context)` or `generator._validate_context()`
4. **Check field existence**:
   ```python
   ctx = HARAContext.load('scoring_sg_output.json')  # or scoring_sg_context.json
   data = ctx.to_dict()
   print("severity_results exists:", 'severity_results' in data)
   print("asil_results exists:", 'asil_results' in data)
   ```
5. **Check reason fields**: Step12's `exposure_reason` and Step14's `controllability_reason` must be passed through the context
6. **Check for "不适用" consistency**: If preceding malfunction/hazard is marked as "不适用", Steps 9-17 should skip processing (see LL-07, LL-08)
7. **Check cell merge range**: Hierarchically merge repeated static columns B-G; G only for exact situation-text duplicates within the same hazard. Dynamic columns H-U, including S/E/C, ASIL and Safety Goal, must NOT be merged (see LL-05).

## V12 JSON File Naming Convention

| Phase | File | Contains |
|-------|------|----------|
| Phase1 (Steps 1-5) | `malfunction_output.json` | functions, function_outputs, function_guidewords, function_malfunctions, function_hazards |
| Phase2 (Steps 6-8) | `scen_hazevent_output.json` | function_scenarios, hazard_events |
| Phase3 (Steps 9-17) | `scoring_sg_output.json` | severity_results, exposure_results, controllability_results, asil_results, safety_goals, safe_states |
| Report | `HARA_Report_Output.xlsx` | Complete HARA table |
| Experience | `experience_library_{subsystem}_{timestamp}.json` | Full HARA data with template headers |

**Critical**: Report generation must use `scoring_sg_output.json` (contains Steps 9-17 complete scoring data). Using earlier phase JSONs will result in N/A values for Severity, ASIL, Safety Goal columns.

## Error Classification

| Code | Type | Description |
|------|------|-------------|
| E1 | Script logic error | Internal script logic issues — algorithm errors, missing exception handling |
| E2 | Cross-script interface error | Module interface mismatches, field name inconsistencies, data format errors |
| E3 | Human-AI deviation | User feedback issues, AI understanding偏差, interaction experience optimizations |

---

## LL-13: 经验库导出与报告生成的核心原则（2026-06-03）

### 核心原则

**经验库调用 = 原封不动地获取经验库内容 + 报告生成器匹配模板字段导出报告。**

即：经验库中存储什么，就原样读取什么；报告生成器负责把数据映射到 Excel 模板的正确列。两者职责清晰分离，不得在经验库层做数据转换或格式修改。

### 正确流程

```
经验库 Excel (.xlsx)
    ↓ 原封不动读取（完整保留所有行、所有列、所有格式）
extract_to_json()
    ↓ 转换为 JSON（键名 = Excel 列标题，值 = 单元格原始值，不做任何映射/转换）
→ experience_library_{subsystem}_{timestamp}.json
    ↓ 原封不动传递给报告生成器（JSON 包含什么字段，报告生成器就读什么字段）
report_generator(--experience-json)
    ↓ 根据 --template 的实际列标题动态匹配字段（_match_template_fields）
    ↓ 按列标题映射将 JSON 字段填入模板对应列
→ HARA_Report_Output.xlsx
```

### 经验库 JSON 导出格式规范

经验库 JSON 必须包含以下顶层键：

```json
{
  "05_HARA": [               // 必须！存放所有 HARA 数据行
    {
      "HARA-ID": "HARA_1",
      "Function": "中央门锁",      // 功能名称（简短）
      "Output": "门锁电机",        // 输出
      "Guide-Word": "loss",        // 引导词
      "Malfunction": "...",
      "Hazard": "...",
      "Situational description": "...",
      "Situational detailing": "...",
      "Hazardous event": "...",
      "Severity": "S3",
      "Rationale Severity": "...",
      "Exposure": "E3",
      "T/F": "F",
      "Rationale Exposure": "...",
      "Controllability": "C1",
      "Rationale Controllability": "...",
      "ASIL": "ASIL A",
      "SG-ID": "SG_1.1",           // 安全目标 ID
      "Safety Goal": "...",
      "Safe state": "...",
      "Remark": null
    }
  ],
  "06_Safety Goal": [        // 必须！包含 05_HARA 中所有非 QM 记录对应的 SG
    {
      "HZ-ID": "HZ_1",
      "Hazard": "...",
      "SG-ID": "SG_1",
      "Safety Goal": "...",
      "Safety State": "...",
      "Max.ASIL": "ASIL A"
    }
  ],
  "subsystem": "Door",
  "timestamp": "20260603_120000",
  "record_count": 23,
  "source": "C:\\path\\to\\source.xlsx",
  "exported_by": "experience_library.extract_to_json()",
  "hara_data": [...]         // 向后兼容：保留旧格式，同 05_HARA
}
```

### 经验库 JSON 导出时必须避免的做法

| ❌ 错误做法 | 后果 | ✅ 正确做法 |
|-----------|------|------------|
| 经验库列标题与模板不一致时不导出 | 报告生成器无法映射 | 始终导出 JSON，列标题 = Excel 原始列标题 |
| 经验库数据行数 < HARA 记录数 | Safety Goal 不完整 | 遍历所有 HARA 记录生成 SG（去重） |
| Function 列存储功能描述而非功能名称 | 报告显示功能名称列空白 | 读取 Excel 时区分 Function 列和 Function Description 列 |
| 仅导出非空行 | 缺失空行可能导致 HARA-ID 不连续 | 导出所有行（包括可能包含子标题的空行，标记 `is_function_header: true`） |
| 直接复制 Excel 文件作为报告 | 保留原格式，导致排版错乱 | 必须经过 report_generator 处理 |

### 报告生成器的职责

`report_generator.py` 的 `_fill_hara_sheet_from_experience()` 方法：
- **从 JSON 读取**：从 `experience_json["05_HARA"]` 读取每行数据
- **匹配模板列**：通过 `_get_experience_col_mapping()` 返回 `{经验库JSON字段: 列号}` 映射
- **写入单元格**：`row_data.get(exp_field)` 获取值，直接写入对应列
- **不修改数据**：不得在填充时过滤、转换或重新计算任何值

### 报告生成器 `_fill_hara_sheet_from_experience` 填充逻辑

```python
# 正确的填充方式（v12 重构后）
col_mapping = self._get_experience_col_mapping(ws, experience_data)
# 返回: {'Severity': 10, 'Severity_Rationale': 11, 'Exposure': 12, ...}

for row_data in hara_rows:
    for exp_field, col_idx in col_mapping.items():
        value = row_data.get(exp_field)
        if value is not None:
            self._set_cell(ws, row, col_idx, str(value), filled_cells)
```

### 报告生成器 `_fill_safety_goal_sheet_from_experience` 填充逻辑

```python
# 正确的 Safety Goal 填充方式（v12 重构后）
# 使用 SG_FIELD_ALIASES 支持 SZ-ID/SG-ID、Safety Goal/safety goal 等变体
sg_col_map = self._get_sg_column_mapping(ws, sg_rows)
for sg_row in sg_rows:
    for jkey, col_idx in sg_col_map.items():
        value = sg_row.get(jkey)
        if value is not None:
            self._set_cell(ws, row, col_idx, str(value), filled_cells)
```

### 验证经验库完整性的检查清单

导出经验库 JSON 后，检查：
- [ ] JSON 顶层包含 `05_HARA` 键
- [ ] JSON 顶层包含 `06_Safety Goal` 键
- [ ] `06_Safety Goal` 记录数 >= 05_HARA 中非 QM 的 HARA 记录数
- [ ] 所有 HZ-ID 在 06_Safety Goal 中都有对应的 SG 记录
- [ ] `Function` 字段为功能名称（非功能描述）
- [ ] `Safe state` 字段有值（非空）

**核心教训**：经验库层只做一件事——**原封不动地把 Excel 数据转为 JSON**。任何数据转换、格式调整、过滤逻辑都应在报告生成器中进行。

---

## LL-14: 报告生成器双路径列匹配架构（2026-06-03）

### 问题背景

`report_generator.py` 的 `_match_template_fields()` 存在以下接口匹配问题，导致内容缺失或错列：

| 问题 | 后果 |
|------|------|
| `STANDARD_COLUMN_MAPPING` 键名含 "Evaluation" 后缀，与 JSON 键名不在同一语义层 | `TEMPLATE_TO_JSON_KEY` 转换后查不到字段，N/A |
| `ASIL` 列号 18（应为 17，模板 Q 列 = ASIL） | ASIL 数据写到 R 列而非 Q 列 |
| token 匹配要求 `len >= 2`，`Severity`（单 token）无法匹配 | S/E/C 单字母列匹配失败 |
| `_fill_hara_sheet_from_experience` 双重转换：先用 `TEMPLATE_TO_JSON_KEY` 转，再用 `col_to_json_key` fallback | 两个方向不同的转换层叠加导致数据错列 |
| 经验库文件有 23 列（比标准模板多 HZ-ID/Hazard 子列），列偏移未检测 | 列偏移导致 SG-ID 写到错误列 |
| `_fill_safety_goal_sheet_from_experience` 用 `jkey_base` substring 匹配 | `Hazard_ID` split 后得 `Hazard`，可能误匹配 |

### 解决方案：两个独立匹配路径

```
路径1（3-step JSON）: _match_template_fields_3step()
  模板列标题 → STEP3_TO_STANDARD 标准字段名 → 从 3-step context 写入
  优先级: row5单字母(S/E/C) → row5精确 → row5子集 → row4精确 → row4子集

路径2（经验库JSON）: _get_experience_col_mapping()
  经验库JSON字段名 → 模板列号 → 从经验库 JSON 写入
  优先级: row5单字母(S/E/C) → row5精确 → row5子集 → row4精确 → row4子集 → EXP2 fallback
```

**两个路径完全独立，互不调用，无转换中间层。**

### 关键数据结构

#### `HARA_TEMPLATE_COLUMNS` — 目标模板列结构

```python
HARA_TEMPLATE_COLUMNS = {
    1: "HARA-ID", 2: "Function", 3: "Output", 4: "Guide-Word",
    5: "Malfunction", 6: "Hazard", 7: "Situational description",
    8: "Situational detailing", 9: "Potential damage",
    10: "Severity", 11: "Severity_Rationale",
    12: "Exposure", 13: "Exposure_TF", 14: "Exposure_Rationale",
    15: "Controllability", 16: "Controllability_Rationale",
    17: "ASIL", 18: "SG-ID", 19: "Safety Goal", 20: "Safe state", 21: "Remark",
}
```

#### `EXPERIENCE_TO_STANDARD` — 经验库 JSON 字段 → 标准字段名（EXP2 fallback 用）

```python
EXPERIENCE_TO_STANDARD = {
    "HARA-ID": "HARA-ID",
    "Function": "Function",
    "Guide-Word": "Guide-Word",
    "Malfunction": "Malfunction",
    "Hazard": "Hazard",
    "Hazard_Full": "Hazard",           # 经验库中可能有 Hazard_Full 别名
    "Potential damage": "Potential damage",
    "Situational description": "Situational description",
    "Situational detailing": "Situational detailing",
    "Severity": "Severity",
    "Severity_Rationale": "Severity_Rationale",
    "Exposure": "Exposure",
    "Exposure_TF": "Exposure_TF",
    "Exposure_Rationale": "Exposure_Rationale",
    "Controllability": "Controllability",
    "Controllability_Rationale": "Controllability_Rationale",
    "ASIL": "ASIL",
    "HZ-ID": "HZ-ID",
    "SG-ID": "SG-ID",
    "Safety Goal": "Safety Goal",
    "Safe state": "Safe state",
    "Remark": "Remark",
}
```

#### `STEP3_TO_STANDARD` — 3-step JSON 字段 → 标准字段名

```python
STEP3_TO_STANDARD = {
    "HARA-ID": "HARA-ID",
    "Function": "Function",
    "Output": "Output",
    "Guide-Word": "Guide-Word",
    "Malfunction": "Malfunction",
    "Hazard": "Hazard",
    "Situational description": "Situational description",
    "Situational detailing": "Situational detailing",
    "Hazardous event": "Hazardous event",
    "Severity": "Severity",
    "Rationale Severity": "Severity_Rationale",
    "Exposure": "Exposure",
    "T/F": "Exposure_TF",
    "Rationale Exposure": "Exposure_Rationale",
    "Controllability": "Controllability",
    "Rationale Controllability": "Controllability_Rationale",
    "ASIL": "ASIL",
    "SG-ID": "SG-ID",
    "Safety Goal": "Safety Goal",
    "Safe state": "Safe state",
    "Remark": "Remark",
}
```

### `_match_template_fields_3step()` 匹配优先级

```
col10 (row5='s', row4='Severity (S)')
  1. row5='s' in SINGLE_LETTER → 'Severity'
  → mapping['Severity'] = 10  ✓

col11 (row5='Rationale Severity Evaluation', row4='')
  1. row5='r... evaluation' NOT in SINGLE_LETTER
  2. row5精确匹配 STEP3_TO_STANDARD: 'rationale severity evaluation' != 'rationale severity'
  3. row5子集匹配: std_words={'rationale','severity'} ⊂ {'rationale','severity','evaluation'} → 'Rationale Severity'
  → mapping['Rationale Severity'] = 11  ✓

col12 (row5='e', row4='Exposure (E)')
  1. row5='e' in SINGLE_LETTER → 'Exposure'
  → mapping['Exposure'] = 12  ✓
```

### `_get_experience_col_mapping()` 匹配优先级

```
col10 (row5='s', row4='Severity (S)')
  1. row5='s' in SINGLE_LETTER → 'Severity'
  → mapping['Severity'] = 10  ✓

col11 (row5='Rationale Severity Evaluation', row4='')
  1. row5 NOT in SINGLE_LETTER
  2. row5精确匹配 json_keys: 'rationale severity evaluation' ≠ 'severity_rationale'
  3. row5子集匹配: jk_words={'severity','rationale'} ⊂ {'rationale','severity','evaluation'}
     → 但精确匹配 'Rationale Severity' 在 json_keys 中存在！精确匹配优先，跳过子集
  4. row4精确/子集均无匹配
  5. EXP2 fallback: 'Severity_Rationale' not in mapping → STANDARD_POS['Severity_Rationale']=11
  → mapping['Severity_Rationale'] = 11  ✓
```

### 列偏移检测（23列 vs 21列经验库文件）

| 文件 | max_column | h5[18] | h4[18] | template_col_shift | Severity 列 |
|------|-----------|--------|--------|-------------------|-------------|
| HARA_Template_AI_20260327.xlsx | 21 | `SG-ID` | 空白 | 0 | 10 |
| VCTC_Door&Liftgate_HARA... (23列) | 23 | `HZ-ID` | 空白 | 2 | 10 |
| VCTC_Window_HARA... (23列) | 23 | `HZ-ID` | 空白 | 2 | 10 |

```python
template_col_shift = 0
if ws.max_column > 21:
    if h4.get(18) == '' and h5.get(18) == 'hz-id':
        template_col_shift = 2
```

EXP2 fallback 时，对于 23 列文件，HZ-ID/SG-ID/Safety Goal/Safe state 额外加 `template_col_shift` 修正。

### `_fill_hara_sheet_from_experience()` 填充逻辑

```python
def _fill_hara_sheet_from_experience(self, ws, experience_data):
    col_mapping = self._get_experience_col_mapping(ws, experience_data)
    # 返回: {'Severity': 10, 'Severity_Rationale': 11, 'Exposure': 12, ...}
    # 键=经验库JSON字段名，值=模板列号

    hara_rows = experience_data.get("05_HARA", [])
    for row_data in hara_rows:
        for exp_field, col_idx in col_mapping.items():
            value = row_data.get(exp_field)
            if value is not None:
                self._set_cell(ws, row, col_idx, str(value), filled_cells)
```

**直接映射，无中间转换层。**

### SG Sheet 列映射别名表

经验库文件变体多（`SZ-ID`/`SG-ID`、`Safety State`/`Safe state`），使用别名表：

```python
SG_FIELD_ALIASES = {
    "HZ-ID": ["HZ-ID", "HZ ID"],
    "Hazard": ["Hazard", "Hazard description"],
    "SG-ID": ["SG-ID", "SZ-ID", "SG ID"],
    "Safety Goal": ["Safety Goal", "Safety goal"],
    "Safety State": ["Safety State", "Safe state", "Safe State"],
    "Max.ASIL": ["Max.ASIL", "Max ASIL", "ASIL"],
}
```

### 修复前后对比

| 字段 | 修复前 | 修复后 |
|------|--------|--------|
| Severity | col11（错误） | col10 |
| Severity_Rationale | 无映射（N/A） | col11 |
| Exposure | col14（错误） | col12 |
| Exposure_TF | 无映射（N/A） | col13 |
| Exposure_Rationale | 无映射（N/A） | col14 |
| Controllability | col16（错误） | col15 |
| Controllability_Rationale | 无映射（N/A） | col16 |
| ASIL | col18（错误） | col17 |
| SG-ID | 无映射（N/A） | col18 |
| Safety Goal | 无映射（N/A） | col19 |
| Safe state | 无映射（N/A） | col20 |

### 模板列映射参考（HARA_Template_AI_20260327.xlsx 05_HARA sheet）

| 列号 | 第4行标题 | 第5行标题 | 经验库JSON字段 | 3-step JSON字段 |
|------|----------|----------|--------------|----------------|
| A=1 | HARA-ID | — | HARA-ID | HARA-ID |
| B=2 | Function (Abbr.) | — | Function | Function |
| C=3 | Output | — | Output | Output |
| D=4 | Guide-Word | — | Guide-Word | Guide-Word |
| E=5 | Malfunction | — | Malfunction | Malfunction |
| F=6 | Hazard | — | Hazard / Hazard_Full | Hazard |
| G=7 | Situational description ... | — | Situational description | Situational description |
| H=8 | Situational detailing ... | — | Situational detailing | Situational detailing |
| I=9 | Hazardous event/Potential damage | — | Potential damage | Hazardous event |
| J=10 | Severity (S) | S | Severity | Severity |
| K=11 | — | Rationale Severity Evaluation | Severity_Rationale | Rationale Severity |
| L=12 | Exposure (E) | E | Exposure | Exposure |
| M=13 | — | T/F | Exposure_TF | T/F |
| N=14 | — | Rationale Exposure Evaluation | Exposure_Rationale | Rationale Exposure |
| O=15 | Controllability (C) | C | Controllability | Controllability |
| P=16 | — | Rationale Controllability Evaluation | Controllability_Rationale | Rationale Controllability |
| Q=17 | ASIL | — | ASIL | ASIL |
| R=18 | — | SG-ID | SG-ID | SG-ID |
| S=19 | — | Safety Goal | Safety Goal | Safety Goal |
| T=20 | — | Safe state | Safe state | Safe state |
| U=21 | Remark | — | Remark | Remark |

### 经验库文件结构差异（Door/Light 23列 vs 标准模板 21列）

Door/Light 等经验库文件比标准模板多 2 列（Hazard 子列 HZ-ID/Hazard/SG-ID）：

```
标准模板 21列:      col18=SG-ID  col19=Safety Goal
Door 23列:          col18=HZ-ID  col19=Hazard  col20=SZ-ID  col21=Safety Goal
```

识别方式: `ws.max_column > 21 && h5[18] == 'HZ-ID'`
处理: 所有 EXP2 fallback 列号减 2（回归到标准列位置）

---

## LL-16: _match_template_fields_3step 子集匹配覆盖精确匹配（2026-06-04）

### 问题

`_match_template_fields_3step()` 执行列映射时，row5 子集匹配将 `Rationale Severity Evaluation` 中的 `{'severity'}` 匹配到 `Severity`（而非 `Rationale Severity`），导致：

| 列 | 匹配到的字段（错误） | 实际列号 |
|----|---------------------|---------|
| col10 (row5='s') | `Severity` | 但被子集匹配覆盖 |
| col11 (row5='Rationale \nSeverity Evaluation') | `Severity` ← 被子集匹配覆盖 col10 的 `Severity` |
| col12 (row5='e') | `Exposure` | 但被子集匹配覆盖 |
| col14 (row5='Rationale \nExposure Evaluation') | `Exposure` ← 被子集匹配覆盖 col12 的 `Exposure` |
| col16 (col5='C'后) | `Controllability` ← 被子集匹配覆盖 col15 |

结果：报告输出时 Severity 写到 col11 而非 col10，Exposure 写到 col14 而非 col12，G/H/I 列空，J-P 全部错位。

### 根因

1. 原逻辑 `row5 ∈ ROW5_SINGLE` 时只赋值 `matched_field` 但不 `continue`，后续 row5 子集匹配继续执行
2. 子集匹配中 `{'severity'}` ⊂ `{'rationale','severity','evaluation'}` 匹配到 `Severity`，覆盖了 col10 的 `Severity`
3. 填充代码使用的字段名也不对：`Rationale Severity` → 实际映射的是 `Severity`

### 修复

**修复1**: `SINGLE_LETTER` 匹配后立即 `continue`：
```python
if row5 in SINGLE_LETTER:
    mapping[SINGLE_LETTER[row5]] = col_idx
    continue  # 不让后续子集匹配覆盖
```

**修复2**: 子集匹配优先选词数最多的（`Rationale Severity` 有2词，`Severity` 只有1词）：
```python
best_subset = None
best_word_count = 0
for std_name in self.STEP3_TO_STANDARD:
    std_words = set(n(std_name).split())
    if std_words and std_words.issubset(s5_words):
        wc = len(std_words)
        if wc > best_word_count:  # 词数最多的优先
            best_word_count = wc
            best_subset = std_name
```

**修复3**: 填充代码字段名对齐：
```python
# 错误
col_sev_r = self._column_mapping.get("Rationale Severity")  # 映射中没有
col_tf = self._column_mapping.get("T/F")  # 映射中没有

# 正确
col_sev_r = self._column_mapping.get("Severity_Rationale")
col_tf = self._column_mapping.get("Exposure_TF")
```

**修复后映射**:
| col | 字段 |
|-----|------|
| col10 | Severity |
| col11 | Severity_Rationale |
| col12 | Exposure |
| col13 | Exposure_TF |
| col14 | Exposure_Rationale |
| col15 | Controllability |
| col16 | Controllability_Rationale |
| col17 | ASIL |

---

## LL-17: G/H/I 列空的根因是 JSON key 类型不匹配 + col9 映射被覆盖（2026-06-04）

### 问题

3-step 报告的 G 列（Situational description）、H 列（Situational detailing）、I 列（Hazardous event）全部为 N/A。

### 根因分析

#### G/H 列空：`_build_scen_hazevent_context` 的 key 转换缺失

`scen_hazevent_output.json` 中 `function_scenarios[func]` 的 key 是**字符串形式的 Python dict**：
```python
# JSON 中实际存储的 key（str 类型）
"{'guideword': 'unintended', 'malfunction': '非预期触发CEA-0768', 'is_valid': True, ...}"
# 而非 dict 类型
```

`report_generator` 原代码直接将此 dict 存入 `context["function_scenarios"]`（不转换 key）：
```python
# 原代码（错误）
function_scenarios = data.get("function_scenarios", {})  # key = str dict repr
context["function_scenarios"] = function_scenarios  # 不转换，原样存储
```

`_fill_hara_sheet` 中查找场景时：
```python
scenario_list = scenarios.get(malfunction_desc, [])  # malfunction_desc = '非预期触发CEA-0768' (str)
# scenarios 实际 key = "{'guideword': 'unintended', ...}" (str repr of dict)
# 永远找不到 → 空列表 → G/H = N/A
```

#### I 列空：col9 匹配到 "Potential damage" 而非 "Hazardous event"

模板 col9 row4 = `"Hazardous event/Potential damage ('Harm')"`

token 化（`split()` 默认按空格，`/` 不分割）：`['hazardous', 'event/potential', 'potential', 'damage', "('harm')"]`

`STEP3_TO_STANDARD` 中两个 key 均可子集匹配：
- `"Hazardous event"` → `{'hazardous', 'event'}` → 'event' 不在 s4_words 中（因为 `event/potential` 是整体 token）→ **不匹配**
- `"Potential damage"` → `{'potential', 'damage'}` → 两者都在 s4_words → **匹配**

但实际上 `'event'` 作为 token 不在 `s4_words` 中（`'event/potential'` 不是 `'event'`），所以 row4 子集匹配对 col9 实际上也**没有匹配**。

row5 对 col9 是 None，所以跳过了 row5 匹配流程。row4 子集匹配时，`'event'` 不在 `s4_words` 中 → `"Hazardous event"` 不匹配；`{'potential', 'damage'}` ⊂ `s4_words` → `"Potential damage"` 匹配。col9 最终映射为 `"Potential damage" = 9`。

但 fill 代码查找的是 `col_he = self._column_mapping.get("Hazardous event")` → **None** → I 列不填写。

### 修复

**修复1**：`ast.literal_eval()` 解析 string-dict key 并提取 malfunction 字段：
```python
def _extract_malfunction_from_key(self, key):
    if isinstance(key, str) and key.strip().startswith("{"):
        import ast
        try:
            parsed = ast.literal_eval(key.strip())
            if isinstance(parsed, dict):
                return parsed.get("malfunction", "")
        except (ValueError, SyntaxError):
            pass
    return key if isinstance(key, str) else ""
```

**修复2**：`function_scenarios` key 转换为 malfunction_desc：
```python
function_scenarios = {}
for func, malf_dict in raw_function_scenarios.items():
    transformed = {}
    if isinstance(malf_dict, dict):
        for k, v in malf_dict.items():
            malf_desc = self._extract_malfunction_from_key(k)
            if malf_desc and v:
                transformed[malf_desc] = v  # key = malfunction desc string
    function_scenarios[func] = transformed
```

**修复3**：col9 强制映射到 "Hazardous event"（在 `_match_template_fields_3step` 返回前添加）：
```python
if 9 not in mapping.values():
    mapping["Hazardous event"] = 9
```

**修复4**：I 列 fallback 到 `hazard_events[func][malf_desc]`（`function_scenarios` 中有 `hazard_event` 字段，但通过 `hazard_events` 更可靠）：
```python
hazard_event_val = "N/A"
if primary_scenario:
    hazard_event_val = primary_scenario.get("hazard_event", "N/A")
if (not hazard_event_val or hazard_event_val == "N/A") and func in hazard_events:
    he_func = hazard_events[func]
    if isinstance(he_func, dict):
        he_entry = he_func.get(malfunction_desc, [])
        if he_entry and isinstance(he_entry, list):
            hazard_event_val = he_entry[0].get("hazard_event", "N/A")
```

### 修复后效果

| 列 | 修复前 | 修复后 |
|----|--------|--------|
| G | N/A | `All operating scenarios，All vehicle state，...` ✅ |
| H | N/A | N/A（输入数据无 `scenario_detailing` 字段） |
| I | N/A | `<非预期触发导致系统执行非计划操作>导致<车辆出现非预期的功能激活或参数变化>...` ✅ |
| J | S1 | S1 ✅ |
| K | 推理文本 | 推理文本 ✅ |

### 关键数据结构（`scen_hazevent_output.json`）

```python
function_scenarios = {
    'CEA-0768': {
        # key = string repr of dict（需转换）
        "{'guideword': 'unintended', 'malfunction': '非预期触发CEA-0768', ...}": [
            {'scenario_summary': '...', 'hazard_event': '...', 'scenario_detailing': '...'}
        ]
    }
}

hazard_events = {
    'CEA-0768': {  # key = function name（正常 string）
        '非预期触发CEA-0768': [{'hazard_event': '...', 'refined_scenario': '...'}],
        'CEA-0768持续激活': [...],
    }
}
```

**教训**：JSON 中存储的 Python dict string representation 不能直接用作 dict key 查询。必须解析后提取目标字段（如 `malfunction`）作为实际 key。

---

## LL-15: 3-Step 引擎 vs 经验库差距分析（2026-06-04）

### 差距总结（Window 子系统）

| 列 | 经验库 | 3-step 引擎 |
|----|--------|------------|
| Severity | S1/S2/S3 差异化 | 全部 S1 |
| Exposure | E1/E2/E3 差异化 | 全部 E2 |
| Controllability | C0/C1/C2 差异化 | 全部 C2 |
| ASIL | QM + ASIL A/B/C/D | 仅 QM |
| Safety Goal | 有 SG_01 | 无 |
| 场景描述 | Highway/IB016/SO003 具体 | All operating scenarios 泛化 |
| Hazard | 具体伤害（骨折/窒息） | 系统异常/过热泛化 |

### 优化计划（详见 `agent-improveplan.md`）

| 优先级 | 优化项 |
|--------|--------|
| P0 | S/E/C 差异化：Step 6-8 为每个 malfunction 绑定具体驾驶场景 |
| P0 | Malfunction 描述具体化：含故障后果部位和具体行为 |
| P1 | Hazard 列填充具体伤害 |
| P1 | 生成 Safety Goal（S+E+C≥7 时） |
| P2 | 场景描述精细化（Highway/Urban/Parking 等） |

### 参考知识库（2026-06-04 新增）

| 文件 | 说明 |
|------|------|
| `scripts/ScenarioRef.json` | 场景组合参考库（关键词→场景映射） |
| `scripts/HazardRef.json` | 危害参考库（关键词→危害映射） |

### 执行记录

| 日期 | 输入 | 模式 | 报告 | 结果 |
|------|------|------|------|------|
| 2026-06-04 | VCTC_Window ItemDef.docx (防夹+一键升窗) | 3-step | HARA_Report_Window_3Step_Final.xlsx | 28条QM, 0 SG |

### ⚠️ 字段命名规范（2026-06-04 新增）

> **所有 3-step 引擎输出 JSON 和导出脚本，必须使用 `report_generator.py` 期望的字段名。**
> 禁止在 JSON 端使用与 `report_generator.py` 中 `.get()` 或 `.get("xxx")` 不同的字段名。
> 字段命名规范由 `report_generator.py` 的消费端决定，上游引擎必须对齐。

| 字段角色 | 期望字段名 | 所在位置 |
|----------|-----------|----------|
| Malfunction 描述 | `malfunction_description` | `report_generator.py` 第 1254 行 `malf.get("malfunction_description")` |
| Severity 评分 | `severity_score` | `report_generator.py` 第 1297 行 `sev_result.get("severity_score")` |
| Exposure 评分 | `exposure_score` | `report_generator.py` 第 1304 行 `exp_result.get("exposure_score")` |
| Controllability 评分 | `controllability_score` | `report_generator.py` 第 1314 行 `ctrl_result.get("controllability_score")` |
| Safety Goal 内容 | `safety_goal` | `report_generator.py` 第 1340 行 `sg_item.get("safety_goal")` |
| Safe State 内容 | `safe_state` | `report_generator.py` 第 1349 行 `ss_item.get("safe_state")` |

**违规示例（P1-1 根因）：**
```python
# 3-step 引擎错误输出示例
{"guideword": "unintended", "malfunction": "非预期触发CEA-0768", ...}

# report_generator.py 读取
malf.get("malfunction_description")  # ← 期望的字段名不是 "malfunction"
```

**正确做法：**
```python
# 3-step 引擎输出 JSON 时，字段名必须与 report_generator.py 保持一致
{"guideword": "unintended", "malfunction_description": "非预期触发CEA-0768", ...}
```



---

## LL-18: 3-Step 引擎 → report_generator 接口审计（2026-06-04）

### 审计范围

- Phase1: `malfunction_output.json` → `_build_malfunction_context`
- Phase2: `scen_hazevent_output.json` → `_build_scen_hazevent_context`
- Phase3: `scoring_sg_output.json` → `_build_scoring_context`
- 填充: 三个 context → `_fill_hara_sheet`

### 发现的接口问题

| ID | 严重性 | JSON字段/结构 | report_generator 读取 | 匹配状态 | 说明 |
|----|--------|--------------|----------------------|----------|------|
| P3-5 | **高** | `exposure_results[func][malf]` 无 `tf` 字段 | `exp_result.get("tf", "N/A")` | **不匹配** | M列(T/F)永远为N/A |
| P3-10 | **高** | `safety_goals[func][malf]` 无 `id` 字段 | `sg_item.get("id", "N/A")` | **不匹配** | R列(SG-ID)永远为N/A |
| P1-1 | 中 | `malfunction_output.json` 用 `malfunction` 字段 | `malf.get("malfunction_description")` 先查 | **fallback有效** | 顺序反了但有fallback，实际工作 |
| P2-5 | 低 | `scen_hazevent_output.json` 无 `scenario_detailing` | `primary_scenario.get("scenario_detailing")` | **不匹配** | H列永远为N/A |

### P3-5: T/F 字段缺失（已修复）

**根因**: `scoring_sg_engine.py` 的 `ExposureReasoningEngine.analyze()` 返回值不含 `tf` 字段。

**修复**: 在 `_fill_hara_sheet` 中根据 `exposure_score` 计算：
```python
exp_score = exp_result.get("exposure_score", "E0")
tf_val = "F" if exp_score == "E0" else "T"
```
- E0 (从不发生) → F (False)
- E1/E2/E3/E4 → T (True)

**修复后**: M列填充为 "T"（当前数据E2全部为T）✅

### P3-10: SG-ID 字段缺失（已修复）

**根因**: `scoring_sg_engine.py` 的 `SafetyGoalGenerator.generate()` 返回值仅含 `ASIL` 和 `safety_goal`，无 `id`。

**修复**: 在 `_fill_hara_sheet` 中根据 ASIL 推导 SG-ID（全局递增计数器）：
```python
sg_id_val = "N/A"
if asil_for_sg in ["A", "B", "C", "D"]:
    sg_asil_counters[asil_for_sg] = sg_asil_counters.get(asil_for_sg, 0) + 1
    sg_id_val = f"SG_{asil_for_sg}.{sg_asil_counters[asil_for_sg]}"
```
- QM/NA → "N/A"（无安全目标）
- ASIL A → "SG_A.1", "SG_A.2", ...
- ASIL B/C/D 同理

**修复后**: R列填充为 "N/A"（当前数据QM全部为N/A），未来有非QM时将自动编号 ✅

### P1-1: malfunction_description vs malfunction（已知，无需修复）

```python
# 当前代码（有fallback，已工作）
malfunction_desc = self._get_value(
    malf.get("malfunction_description") or malf.get("malfunction", "")
)
# JSON中字段名: malfunction_description ← malfunction（fallback生效）
```

语义上应以 `malfunction_description` 为 primary key，但现有 fallback 机制使功能正常。

### P2-5: scenario_detailing 字段缺失（低优先级）

`scen_hazevent_engine.py` 的 Step6/7 未生成 `scenario_detailing` 字段，导致 H 列（Situational detailing）永远为 "N/A"。

如需修复：在 `scen_hazevent_engine.py` 的 scenario 对象中添加 `scenario_detailing` 字段（从 refined_scenarios 提取）。

### 验证命令

```python
# 生成 v3 报告（包含所有修复）
gen = HARAReportGenerator(
    template_path="HARA_Template_AI_20260327.xlsx",
    malfunction_json="malfunction_output.json",
    scen_hazevent_json="scen_hazevent_output.json",
    scoring_json="scoring_sg_output.json"
)
ctx = gen._build_context_from_json()
result = gen.generate_hara_report(context=ctx, output_excel_path="HARA_Report_v3.xlsx")
```

---

## LL-20: G 列场景重复 — all_type_scenarios 筛选 + 差异化分配（2026-06-05）

### 问题

所有 malfunction 的 G 列（Spatial description）内容完全相同，全部为 `"All opeating secenrios，All vehicle state，..."`，而非基于主场景的差异化组合。

### 根因

**三层因果链**：

1. `all_type_scenarios` 筛选条件：`"All" in scenario.lower()` → 场景库中只有 1 个场景匹配（"All opeating secenrios" 及其 typo 变体），远少于预期的 14 个。

2. `get_related_scenarios` 返回相同的场景列表：`if all_type_scenarios: return all_type_scenarios` → 所有功能、所有 malfunction 共享完全相同的场景列表。

3. `check_multiple_scenario_combinations` 遍历 `ALL malf_list × ALL related_scenarios` → 若 `related_scenarios` 只有 1 个场景，所有 malfunction 都分配到该场景。

**本质问题**：Step6 将**通用兜底场景**当作**功能专属场景**分配，且筛选逻辑过严导致只有 1 个匹配。

### 修复

**修复 1 — 扩展 `all_type_scenarios`**（`scen_hazevent_engine.py`）：
```python
all_type_scenarios = [s for s in self.scenarios_library
                      if "All" in str(s.get("scenario", "")).lower()
                      or "all" in str(s.get("operating_scenario", "")).lower()]
if len(all_type_scenarios) < 3:
    unique_ops = {}
    for s in self.scenarios_library:
        op = s.get("operating_scenario", s.get("scenario", ""))
        if op and op not in unique_ops and "all" not in op.lower():
            unique_ops[op] = s
    all_type_scenarios = list(unique_ops.values())[:14]
    logger.info(f"All型场景不足，使用{len(all_type_scenarios)}个功能相关具体场景")
```

**修复 2 — 轮转分配差异化场景**（`scen_hazevent_engine.py`）：
```python
for i, malf in enumerate(malf_list):
    num_scenarios = max(1, len(related_scenarios) // len(malf_list))
    start_idx = (i * num_scenarios) % len(related_scenarios)
    malf_scenarios = []
    for j in range(num_scenarios):
        idx = (start_idx + j) % len(related_scenarios)
        malf_scenarios.append(related_scenarios[idx])
    results = self.checker.check_multiple_scenario_combinations([malf], malf_scenarios)
    valid_results.extend(self.checker.filter_valid_combinations(results))
```

**修复 3 — `_build_situational_detailing` 过滤 "All" 泛化值**（已在前面修复）：
```python
if val and val != "N/A" and "all" not in str(val).lower():
    parts.append(f"{label}: {val}")
```

### 防御性原则

> Step6 的场景分配策略必须满足：
> 1. `all_type_scenarios` 不足 14 个时必须扩展到功能相关具体场景
> 2. 不同 malfunction 必须分配**差异化**的场景子集，而非全部 malfunction 共享同一场景列表
> 3. 场景摘要中禁止包含 "All" 或 "all" 等泛化词汇，应使用具体的主场景名称

---

## LL-21: I 列危害事件泛化 — Step6/Step8 双层机制缺陷（2026-06-05）

### 问题

I 列（Hazardous event）始终为 `<...导致系统异常>导致<车辆出现不可预测的行为>，<造成人员伤害>`，而非基于 malfunction 特征的具体描述。

### 根因（双重）

**第一层 — Step6 的 `scenario_checker.generate_hazard_event`**：
```python
# 错误：直接用 BEHAVIOR_MAPPING.get(guideword)，无关键字匹配
vehicle_behavior = self.BEHAVIOR_MAPPING.get(guideword, "车辆出现不可预测的行为")
```
`BEHAVIOR_MAPPING` 没有 wiper 关键字，所有 guideword 都落入 fallback → `"车辆出现不可预测的行为"`。

**第二层 — Step8 结果被忽略**：
- Step8 通过 `_infer_hazard_event` + `hazard_event_index` 生成更具体的结果（写入 `hazard_events`）
- 但 `function_scenarios` 中的 `hazard_event` 来自 Step6 的泛化值
- `report_generator.py` 优先读取 `function_scenarios[malfunction].hazard_event`，Step8 结果的 fallback 条件不触发

### 修复（三重）

**修复 1 — `scenario_checker.py` 添加关键字匹配**：
在 `ScenarioChecker.generate_hazard_event` 中添加 `SPECIFIC_BEHAVIOR_RESULTS`，使用与 `ScenHazEventEngine` 相同的 wiper 关键字列表。查询优先级：`SPECIFIC_BEHAVIOR_RESULTS` → `BEHAVIOR_MAPPING` → fallback。

**修复 2 — Step8 结果回写 `function_scenarios`**（`scen_hazevent_engine.py`）：
```python
step8_hazards = step8_result["hazard_events"]
for func, malf_events in step8_hazards.items():
    for malf, event_list in malf_events.items():
        if malf in step6_result["function_scenarios"][func]:
            scenario_list = step6_result["function_scenarios"][func][malf]
            if event_list and scenario_list:
                step8_he = event_list[0].get("hazard_event", "")
                if step8_he and step8_he != "N/A":
                    for sc in scenario_list:
                        sc["hazard_event"] = step8_he
```

**修复 3 — report_generator 泛化检测强制 fallback**（`report_generator.py`）：
```python
generic_patterns = ["车辆出现不可预测的行为", "车辆发生碰撞事故", "系统异常"]
is_generic = any(p in str(hazard_event_val) for p in generic_patterns)
if is_generic and func in hazard_events:
    he_entry = hazard_events[func].get(malfunction_desc, [])
    if he_entry:
        step8_val = he_entry[0].get("hazard_event", "")
        if step8_val:
            hazard_event_val = step8_val
```

### 防御性原则

> 危害事件生成的三层优先级必须严格执行：
> 1. **SPECIFIC_BEHAVIOR_RESULTS** — 基于 malfunction 关键字的精确匹配（最优先）
> 2. **hazard_event_index** — 基于子系统的危害事件索引（HazardRef.json）
> 3. **BEHAVIOR_MAPPING / INJURY_MAPPING** — 基于 guideword 的通用模板（兜底）
>
> Step8 的结果必须覆盖 Step6 的结果，且 report_generator 应在 Step6 结果为泛化模式时强制 fallback 到 Step8。

---

## LL-19: HazardRef 跨子系统关键词误匹配 — "window wiper" 匹配到车窗危害（2026-06-05）

### 问题

前雨刮（`front window wiper`）子系统的 HARA 报告 F 列（Hazard）全部填入了车窗子系统（Window）的危害词：**"Head, shoulder and chest of the passenger are placed outside of the window, so the passenger's body could be pinched due to unintended raising, leading to possible asphyxia"**（夹伤窒息）。这与雨刮功能完全无关。

### 根因

`malfunction_engine.py:99-117` 的 `_find_hazards_by_keywords` 关键词匹配存在两个缺陷：

**缺陷 1 — 跨子系统关键词重叠**：
- 雨刮功能名含 `"window"`（`front window wiper` = 前**窗**刮水器）
- `HazardRef.json` 中 HZ_W001~HZ_W008 的 `subsystem_keywords` 均含 `"window"`
- token 集合交集：`{"window"}` → 跨子系统匹配成功

**缺陷 2 — 匹配阈值过低**：
```python
if len(overlap) >= 2:   # ← 只需要2个词重叠
    matched.append(entry)
```
`"window"` + `"part"` 即可触发（HZ_W001 的 `function_keywords` 含 `"window motor"`、`"body part outside"`，与雨刮功能名中的 `"window"` 和 `"mechanical part"` 重叠）。

**缺陷 3 — 无子系统隔离**：
所有子系统共享同一个 `entries` 列表，无子系统级别的预过滤。

### 修复

**修复 1 — 子系统隔离优先**（`malfunction_engine.py`）：
```python
def _find_hazards_by_keywords(self, subsystem: str = "", function: str = "",
                              malfunction: str = "", context: str = "") -> List[Dict[str, Any]]:
    all_keywords = f"{subsystem} {function} {malfunction} {context}".lower()
    all_set = set(all_keywords.split())
    matched = []
    for entry in self.hazard_ref.get("entries", []):
        subsystem_kws = set(" ".join(entry.get("subsystem_keywords", [])).lower().split())
        func_kws = set(" ".join(entry.get("function_keywords", [])).lower().split())
        # ... 其他关键词集 ...

        if subsystem:
            # 优先：subsystem 必须重叠，然后检查 function 关键词
            subsystem_overlap = subsystem_kws & all_set
            if subsystem_overlap:
                func_overlap = func_kws & all_set
                if func_overlap:
                    matched.append(entry)
                elif len(subsystem_overlap) >= 2:
                    matched.append(entry)
            continue

        overlap = entry_set & all_set
        if len(overlap) >= 3:  # 提高阈值
            matched.append(entry)
        elif subsystem_kws & all_set and len(func_kws & all_set) >= 1:
            matched.append(entry)

    return matched
```

**修复 2 — HazardRef.json 添加雨刮专属危害**：
新增 `subsystem_hazards.wiper` 条目（HZ_WI001），`subsystem_keywords` 明确为 `["wiper", "雨刷", "雨刮", "wiping system"]`，与 Window 的 `["window", "车窗"]` 完全隔离。

### 防御性原则

> **危害参考库必须按子系统隔离，同一功能名中的歧义子串（如 "window" 在 "window wiper" 中）不能作为跨子系统匹配的依据。**
>
> 当 `subsystem` 参数已知时，优先在 HazardRef 中过滤 `subsystem_keywords` 匹配项；若 subsystem 为空，提高 token 重叠阈值至 ≥3，或要求同时满足 subsystem 关键词 + function 关键词双重条件。

### 涉及文件

| 文件 | 变更 |
|------|------|
| `scripts/malfunction_engine.py` | `_find_hazards_by_keywords` 阈值加固 + 子系统隔离 |
| `scripts/HazardRef.json` | 新增 `subsystem_hazards.wiper`（HZ_WI001-004）+ `hazard_event_index` |

---

## LL-22: G/H列场景一致 vs K列不一致 — `scenario_idx` 对齐修复（2026-06-06）

### 问题背景

单功能 Wiper HARA（"打开和关闭雨刮"，14个guideword），报告生成后：
- **G/H列**：所有14行均为"Highway / Motorway"场景，车速 0 km/h，完全一致
- **K列**：显示差异化的5个车速值（0/15/30/60/80 km/h），与G/H不同步
- 用户期望：G列以Operating scenarios为主场景组合，H列基于G列场景匹配车速范围，K列车速与G/H保持一致

### 根因（双重）

#### 根因1 — `report_generator.py` G/H列永远取 `scenario_list[0]`

Phase2 JSON（`scen_hazevent_output.json`）中，每个 `malf_desc||guideword` 已有 **11个不同场景**：
```
Highway/Motorway → Urban → Rural → Railway → Mountain → Parking → Service → Maintenance → Tunnel → Residential → Off-road
```
但 `report_generator.py` 第1349行：
```python
primary_scenario = scenario_list[0] if scenario_list else {}
```
永远取**第一个**场景（Highway/Motorway），导致所有14行G/H内容一致。

#### 根因2 — K列 `scenario_idx` 与G/H用的不是同一个索引

G/H取 `[0]`，K列取 `min(i, len(sev_list)-1)`（i = guideword index 0~13）：
- i=0 → idx=0 → K显示0km/h（与G一致）
- i=1 → idx=1 → K显示0km/h（G是Highway的0km/h，但应该显示Urban的0-15km/h）
- i=2 → idx=2 → K显示15km/h（G是Highway的0km/h）
- …偏差越来越大

**本质**：Phase2 和 Phase3 的场景顺序相同（均按 Highway→Off-road 排列），只需 G/H/K 共用同一 `scenario_idx` 即可对齐。

### 修复

**修复1 — G/H使用按guideword索引的场景**（`report_generator.py`）：
```python
scenario_idx = min(i, len(scenario_list) - 1) if scenario_list else 0
primary_scenario = scenario_list[scenario_idx] if scenario_list else {}
```

**修复2 — K列复用G/H的 `scenario_idx`，去除重复计算**：
```python
# 删除原有的重复计算
# scenario_idx = min(i, len(sev_list) - 1)  ← 已在上方统一定义
sev_result = sev_list[scenario_idx] if sev_list else {}
exp_result = exp_list[scenario_idx] if exp_list else {}
ctrl_result = ctrl_list[scenario_idx] if ctrl_list else {}
```

### 修复后效果

| 行 | Guideword | G主场景 | G车速 | K车速 | H车速 |
|----|-----------|---------|-------|-------|-------|
| 1 | unintended | Highway | 0 km/h | 0km ✅ | 0km ✅ |
| 2 | always active | Urban Road | 0-15 km/h | 0km ✅ | 15km ✅ |
| 3 | loss | Rural Road | 15-30 km/h | 15km ✅ | 30km ✅ |
| 4 | too large | Railway | 30-60 km/h | 30km ✅ | 60km ✅ |
| 5 | too small | Mountain | 60-80 km/h | 60km ✅ | 80km ✅ |
| 6 | too early | Parking Lot | 80-130 km/h | 80km ✅ | 130km ✅ |
| 7 | too late | Service Area | 60-80 km/h | 60km ✅ | 80km ✅ |
| 8 | too fast | Maintenance | 60-80 km/h | 60km ✅ | 80km ✅ |

G列展示11个不同主场景（ Highway→Off-road），G/H/K 三列车速完全一致（行1-8）。

### 遗留问题：Tunnel/Residential/Off-road 场景车速未填充（行9-14）

行9-14（guideword: too slow / too long / too short / incomplete / different to / as well as）的G/H/K显示：
- G列：Tunnel / Residential / Off-road 场景名 ✅ 但 vehicle_speed 未填充 → N/A
- K列：0 km/h（severity reasoning fallback）
- H列：N/A

**根因**：Phase2 Step7 场景细化引擎（`scen_hazevent_engine.py`）在处理 Tunnel/Residential/Off-road 场景时，未从 `ScenarioRef.json` 的 main_scenarios 中匹配到对应的 `vehicle_speed` 字段。

**修复方向**：在 `ScenarioRef.json` 的 main_scenarios 中为 SCN_19（Tunnel）、SCN_20（Residential）、SCN_21（Off-road）配置 `vehicle_speed` 字段，然后在 `scen_hazevent_engine.py` Step7 的场景细化逻辑中优先使用 ScenarioRef 中的车速而非纯模板匹配。

### 防御性原则

> **G/H/K 三列必须使用同一个 `scenario_idx` 从同一份场景列表中取值，确保场景名称、速度范围在报告中三维一致。**
>
> `report_generator.py` 中任何涉及"第几个场景"的索引计算，只允许存在**一个** `scenario_idx` 定义，优先复用 G 列 lookup 的结果。不得在 G/H/I 填充和 J/K/L/M 填充时分别计算独立的索引。

### 涉及文件

| 文件 | 变更 |
|------|------|
| `scripts/report_generator.py` | 统一 `scenario_idx` 用于 G/H/K 三列；G/H 取 `scenario_list[scenario_idx]`；删除 K 列重复的 `scenario_idx` 计算 |

---

## LL-23: 脚本注册表与接口全景图（2026-06-07）

### A. 核心引擎脚本

| 脚本 | 类 | 职责 | 关键输出 |
|------|----|------|---------|
| `scripts/malfunction_engine.py` | `MalfunctionEngine`, `HazardRef` | Phase1 (Steps 1-5)：提取功能、生成 Malfunction + Hazard，基于 HazardRef.json 关键词匹配 | `malfunction_output.json` |
| `scripts/scen_hazevent_engine.py` | `ScenHazEventEngine` | Phase2 (Steps 6-8)：组合场景 + 细化（车速/天气/路面）+ 分析危害事件 | `scen_hazevent_output.json` |
| `scripts/scoring_sg_engine.py` | `ScoringSGEngine` | Phase3 (Steps 9-17)：S/E/C 评分 + ASIL + 安全目标 + 安全状态 | `scoring_sg_output.json` |
| `scripts/hara_controller.py` | `HARAController` | 编排 3-phase 引擎，带检查器循环和 fallback 逻辑 | 调用 3 个引擎 |
| `scripts/report_generator.py` | `HARAReportGenerator` | 从 3-phase JSONs 或经验库 JSON 填充 Excel 模板 | `HARA_Report_*.xlsx` |
| `scripts/experience_library.py` | `HARAExperienceLibrary` | 索引/检索经验库，导出为 JSON | `experience_library_*.json` |
| `scripts/main_executor.py` | `MainExecutor` | 智能路由：经验库命中 vs 3-phase，含 fallback 监控 | 调用 hara_controller |

### B. 逻辑检查器

| 脚本 | 职责 |
|------|------|
| `scripts/logic_checkers/malfunction_checker.py` | Malfunction 逻辑有效性、物理合理性、重冗余引导词检查 |
| `scripts/logic_checkers/scenario_checker.py` | 14 主场景完整性、FUNCTION_SCENARIO_CORRELATION 检查 |
| `scripts/logic_checkers/scoring_checker.py` | 非 S0/E0/C0 条目必须有值 |

### C. 文档解析器

| 脚本 | 职责 |
|------|------|
| `scripts/document_parsers/excel_processor.py` | Excel 读写（openpyxl），含 `scenarios_library` 提取 |
| `scripts/document_parsers/word_processor.py` | Word .docx 解析（python-docx） |
| `scripts/document_parsers/pdf_processor.py` | PDF 解析（pdfplumber） |

### D. Wiper 专用脚本

| 脚本 | 职责 |
|------|------|
| `scripts/wiper_3step_filtered.py` | Wiper 子系统专用 3-step 流水线（30+ 关键词过滤 → Phase1 → Phase2 → Phase3 → Report） |
| `scripts/wiper_3step_v*.py` | 各版本 Wiper 流水线（v4-v6，存在多个版本需合并） |

---

## LL-24: 跨脚本接口映射

### 接口 1: Phase1 → Phase2

```
malfunction_engine.py  →  malfunction_output.json  →  scen_hazevent_engine.py
```

| 字段 | 产出 (malfunction_output.json) | 消费 (scen_hazevent_engine.py) | 状态 |
|------|------|------|------|
| `functions` | `list[str]` | `malf_data.get("functions", [])` | ✅ 匹配 |
| `function_guidewords` | `{func: [{guideword, description}]}` | `malf_data.get("function_guidewords", {})` | ✅ 匹配 |
| `function_malfunctions` | `{func: [{guideword, malfunction}]}` | `malf_data.get("function_malfunctions", {})`（遍历 list） | ✅ 匹配 |
| `function_hazards` | `{func: [{guideword, description, category}]}` | `malf_data.get("function_hazards", {})` | ✅ 匹配 |
| `function_outputs` | `{func: "标准输出描述"}` | `malf_data.get("function_outputs", {})` | ✅ 匹配 |

**关键**：`function_malfunctions[func]` 是 **list of dicts**，键为 `["guideword", "malfunction"]`，不是 dict。消费者正确地用 list 遍历处理。

字段名 `malfunction` vs `malfunction_description`：Phase1 产出 `malfunction`，消费者均有 fallback：

```python
# scen_hazevent_engine.py:919
malf = malf_item.get("malfunction_description", malf_item.get("malfunction", str(malf_item)))
```

### 接口 2: Phase2 → Phase3

```
scen_hazevent_engine.py  →  scen_hazevent_output.json  →  scoring_sg_engine.py
```

| 字段 | 产出 (scen_hazevent_output.json) | 消费 (scoring_sg_engine.py:662-664) | 状态 |
|------|------|------|------|
| `function_scenarios[func][malf_desc]` | 场景对象列表 | `scenarios_by_malf.get(composite_key, scenarios_by_malf.get(malf_desc, []))` | ✅ fallback 生效 |
| `hazard_events[func][malf_desc]` | 危害事件对象列表 | `haz_events_by_malf.get(composite_key, haz_events_by_malf.get(malf_desc, []))` | ✅ fallback 生效 |
| `refined_function_scenarios` | 细化场景（含 vehicle_speed 等） | scoring_sg_engine.py **未直接消费**，仅 report_generator 使用 | ⚠️ 不消费 |

**关键**：`scoring_sg_engine.py` 先尝试 `composite_key = f"{malf_desc}||{guideword}"`，但 JSON 中 key 是裸 `malf_desc`，fallback 生效。

### 接口 3: Phase3 → report_generator（⚠️ 关键问题）

```
scoring_sg_engine.py  →  scoring_sg_output.json  →  report_generator.py
```

**实际 JSON 结构（已验证）**：
```json
{
  "severity_results": {
    "功能名": {
      "裸malf_desc": [{           ← 外层：裸 malf_desc（无 ||guideword 后缀）
        "severity_score": "S1",  ← 内层：直接是 list，无 guideword 子 dict
        "reasoning": "..."
      }]
    }
  }
}
```

**report_generator.py 期望结构**（`_fill_hara_sheet`, 第 1400-1438 行）：
```python
composite_key = f"{malfunction_desc}||{guideword}"
gw_severity = severity_results.get(func, {}).get(composite_key, {})   ← 查 composite_key 外层
sev_list = gw_severity.get(guideword, [{}])                            ← 查 guideword 内层 dict
```

**结果**：查找失败 → 所有 S/E/C/ASIL/N/P 列为 N/A。

### 接口 4: report_generator → Excel 模板

| 列号 | 字段名 | 状态 |
|------|--------|------|
| A=1 | HARA-ID | ✅ |
| B=2 | Function | ✅ |
| C=3 | Output | ✅ |
| D=4 | Guide-Word | ✅ |
| E=5 | Malfunction | ✅ |
| F=6 | Hazard | ✅ |
| G=7 | Situational description | ✅ (取 `scenario_summary` fallback) |
| H=8 | Situational detailing | ⚠️ 无 `scenario_detailing` 字段，永远 N/A |
| I=9 | Hazardous event | ✅ (强制 mapping 在第 647 行) |
| J=10 | Severity | ✅ |
| K=11 | Severity_Rationale | ✅ |
| L=12 | Exposure | ✅ |
| M=13 | Exposure_TF | ✅ (从 exposure_score 计算: E0→F, 否则→T) |
| N=14 | Exposure_Rationale | ⚠️ scoring_sg_output.json 无此字段 |
| O=15 | Controllability | ✅ |
| P=16 | Controllability_Rationale | ⚠️ scoring_sg_output.json 无此字段 |
| Q=17 | ASIL | ✅ |
| R=18 | SG-ID | ✅ (从 ASIL counter 推算生成) |
| S=19 | Safety Goal | ✅ |
| T=20 | Safe state | ✅ |
| U=21 | Remark | ✅ |

---

## LL-25: 已识别接口冲突与不匹配清单

| # | 严重性 | 产出方 | 消费方 | 问题描述 | 状态 |
|---|--------|--------|--------|----------|------|
| **1** | **P0-CRITICAL** | `scoring_sg_output.json` | `report_generator.py` | Phase3 JSON 外层 key = 裸 `malf_desc`，report_generator 查 `composite_key` → 找不到 → **所有 S/E/C/ASIL/N/P 列为 N/A**。结构：期望 `{func: {composite_key: {gw: [list]}}}`，实际是 `{func: {malf_desc: [list]}}` | **未解决** |
| 2 | P0-CRITICAL | 同上 | 同上 | `asil_results`、`safety_goals`、`safe_states` 均有相同嵌套结构不匹配 | **未解决** |
| 3 | P1-HIGH | `scen_hazevent_output.json` | `report_generator.py` | 无 `scenario_detailing` 字段 → **H 列永远 N/A**（P2-5） | 已知低优先级 |
| 4 | P1-HIGH | `scoring_sg_output.json` | `report_generator.py` | `exposure_reason` / `controllability_reason` 未从 Step12/Step14 传入 → **N/P 列可能 N/A** | 需验证 |
| 5 | P2-MEDIUM | `scen_hazevent_engine.py` | — | `_get_ref_speeds` 两层逻辑已在本次会话中实现，但需在真实分析流程中验证隧道/Residential/Off-road 场景是否正确匹配 | 待验证 |
| 6 | P3-LOW | `malfunction_output.json` | consumers | `malfunction` vs `malfunction_description` 字段名 → **各消费者均有 fallback，已工作** | ✅ 匹配 |
| 7 | P3-LOW | `scen_hazevent_output.json` | `report_generator.py` | JSON key 可能是 string-dict repr → **`ast.literal_eval` fallback 已处理** | ✅ 匹配 |
| 8 | P3-LOW | `scoring_sg_output.json` | `report_generator.py` | 无 `tf` 字段 → **从 exposure_score 计算（P3-5 fix）** | ✅ 匹配 |
| 9 | P3-LOW | `scoring_sg_output.json` | `report_generator.py` | 无 `id` 字段 → **SG-ID 从 ASIL counter 生成（P3-10 fix）** | ✅ 匹配 |
| 10 | P3-LOW | `scoring_sg_output.json` | `report_generator.py` | ASIL 列 col17 vs col18 → **LL-14 fix 已解决** | ✅ 匹配 |
| 11 | P3-LOW | `scoring_sg_output.json` | `report_generator.py` | Severity 列 col11 vs col10 → **LL-16 fix 已解决** | ✅ 匹配 |
| 12 | P3-LOW | `scen_hazevent_output.json` | `report_generator.py` | G/H/I 列空 → **LL-17/LL-22 fix 已解决** | ✅ 匹配 |
| 13 | P3-INFO | `wiper_3step_*.py` | — | 多个 Wiper 流水线版本（v4/v5/v6/filtered）并存，需合并 | 需整理 |

---

## LL-26: 优先修复计划

### 立即修复（P0-CRITICAL）

**问题**：Phase3 → report_generator 接口中，JSON key 结构与消费代码不匹配。

**现状**：
- `scoring_sg_output.json` 实际结构：`{func: {malf_desc: [list]}}`
- `report_generator.py` 期望：`{func: {composite_key: {gw: [list]}}}`

**修复方案（改消费者）**：修改 `report_generator.py` 的 `_fill_hara_sheet` 中的查找逻辑，去掉外层 `composite_key` 匹配，改为直接用 `malf_desc` 作为外层 key：

```python
# 错误（当前）
gw_severity = severity_results.get(func, {}).get(composite_key, {})
sev_list = gw_severity.get(guideword, [{}])

# 正确（修复后）
sev_entry = severity_results.get(func, {}).get(malfunction_desc, [{}])
sev_list = sev_entry if isinstance(sev_entry, list) else [sev_entry]
```

同样修复：`exposure_results`、`controllability_results`、`asil_results`、`safety_goals`、`safe_states` 的查找逻辑。

### 次要修复（P1）

1. **`_merge_refined_into_function_scenarios`** 在 `report_generator.py` 中定义但未在 `_build_context_from_json` 中调用，考虑调用以确保 Step7 细化数据（含车速/天气/路面）被合并到场景描述中
2. 验证 Tunnel/Residential/Off-road 场景在真实分析中通过 `_get_ref_speeds` 两层逻辑正确匹配到车速
3. 将 `HARA_技能更新记录.md` 中的 LL-23~LL-30 合并到 AGENTS.md

### 维护整理（P3）

1. 合并多个 Wiper 流水线版本为单一 `scripts/wiper_3step_filtered.py`
2. 清理根目录下的 `wiper_3step_v*.py` 等多余文件
3. 将根目录下的测试 JSON 文件（`malfunction_output.json` 等）移至 `output/` 子目录

---

## LL-30: Wiper subsystem 3-Step optimization - experience library bypass + ScorabilityRef + hazard differentiation (2026-06-17)

### Background

3-Step engine Wiper analysis had 10 gaps vs experience library:
- P0: All scenarios sunny/dry (should be rain), Hazard identical for all 14 GWs, HazardRef wiper entries incomplete
- P1: Controllability all C2/C3 (should be C0/C1), Safe State all NA, too many S0
- P2: SG not consolidated (58 vs 2), poor SG text quality

### Fix summary (all in C:\Users\TVUEDEQ\AI-SkillP\HARA_V12\scripts\)

| Reference file | Status | Content |
|---------------|--------|---------|
| ScenarioRef.json | Extended | weather_overlays (4 layers) + subsystem_weather_overrides |
| HazardRef.json | Extended | hazard_event_index: 3 GWs -> 14 GWs x front/rear = 22 entries |
| ScorabilityRef.json | NEW | Generic C0/C1/C2 rules for wiper, window, light, door |
| SafetyStateRef.json | NEW | 8 safe state patterns (6 wiper + 2 window + 1 generic) |
| scen_hazevent_engine.py | Modified | _apply_weather_override() + hazard_event_index priority 1 |
| scoring_sg_engine.py | Modified | ControllabilityReasoningEngine/SafetyStateGenerator load ref files |

### Problem 1: Bypassing experience library

**Issue**: Wiper experience library always auto-matched, preventing 3-Step engine execution. --system flag does not override matching.

**Root cause**: main_executor.py check_experience_library() matches by source_document filename containing "Wiper", not by --system parameter.

**Workaround**: Temporarily rename .xlsx to .xlsx.bak, run analysis, restore:
`powershell
Rename-Item "VCTC_Wiper_*.xlsx" "VCTC_Wiper_*.xlsx.bak" -Force
# Run 3-Step analysis
Rename-Item "VCTC_Wiper_*.xlsx.bak" "VCTC_Wiper_*.xlsx" -Force
`

### Problem 2: ScorabilityRef empty guideword_keywords caused full match

**Issue**: ScorabilityRef.json C0_WIPER_DIRECT rule had guideword_keywords: [], causing ALL 294 records to be forced to C0 (all QM).

**Root cause**: Original code in ControllabilityReasoningEngine._match_controllability_rule():
`python
gw_match = any(kw in gw_lower for kw in gw_kws) if gw_kws and gw_lower else True
# Empty list gw_kws == [] triggers else True -> unconditional pass
`

**Fix**: Rewrite matching logic:
`python
if haz_kws:
    matched = sub_match and (haz_match or (gw_kws and gw_match))
elif gw_kws:
    matched = sub_match and gw_match
else:
    matched = sub_match
`

**Before vs After**:
| Dimension | Before | After |
|-----------|--------|-------|
| C0 | 294 (100%) | 0 |
| C2 | 0 | 231 |
| C3 | 0 | 63 |
| ASIL A | 0 | 71 |
| ASIL B | 0 | 21 |
| ASIL C | 0 | 15 |
| QM | 294 | 187 |

### Defense principles

1. ScorabilityRef rules with empty hazard_keywords/guideword_keywords MUST NOT be treated as unconditional pass. Empty = "no filter", not "always true".
2. Reference JSON files must be co-located with their consuming engine scripts. Use os.path.join(os.path.dirname(__file__), filename).
3. When testing 3-Step engine fixes, rename experience library files to .bak first.

### ScorabilityRef.json format
`json
{
  "rules": [
    {
      "rule_id": "C0_WIPER_DIRECT",
      "match_conditions": {
        "subsystem_keywords": ["wiper"],
        "hazard_keywords": ["vision", "sight"],
        "guideword_keywords": []
      },
      "match_logic": "subsystem_keywords AND (hazard_keywords OR guideword_keywords)",
      "default_controllability": "C0",
      "rationale_template": "Wiper system is directly controllable by driver..."
    }
  ]
}
`

### Files modified
| File | Change |
|------|--------|
| scripts/ScenarioRef.json | +weather_overlays, +subsystem_weather_overrides |
| scripts/HazardRef.json | hazard_event_index expanded to 22 wiper entries |
| scripts/ScorabilityRef.json | NEW: 4 generic controllability rules |
| scripts/SafetyStateRef.json | NEW: 8 safe state patterns |
| scripts/scen_hazevent_engine.py | _apply_weather_override(), hazard_event_index priority 1 |
| scripts/scoring_sg_engine.py | ControllabilityReasoningEngine ref load + match fix |
| scripts/run_wiper_3step.py | NEW: standalone 3-Step pipeline (single function filter) |

---
