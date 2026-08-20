# HARA V13

## Template compiler status

P0-2 now compiles the hash-bound `TemplateRoleContract` into a typed,
source-traceable `MethodContract`. The current template compiles offline as
`READY_WITH_WARNINGS`: executable rules are structurally complete, while
unresolved workbook semantics remain explicit and fail closed. The legacy
Domain/runtime path remains `MIGRATION_ONLY`. P0-3a now compiles one contract
per Application run and uses it for Guidewords, scenario metadata, scoring
scale evidence, ASIL lookup, doctor output, and checkpoint identity. Remaining
Domain candidate/scoring/Safety Goal dependencies are explicit release
blockers; no second computation chain was added. See
[`FULL_TEMPLATE_COMPILER.md`](docs/architecture/FULL_TEMPLATE_COMPILER.md).

HARA V13 已确定 Template-Driven 目标架构：

```text
HARA Template → Template Role Discovery → MethodContract
Project Input → grounded ProjectFacts
MethodContract + ProjectFacts → deterministic HARA evaluation
  → 工程质量门
  → Report Role Contract驱动的模板保真Excel渲染
```

Template Role Contract 是系统按 `template_hash` 自动发现和缓存的派生产物，
只保存角色位置，不复制工程规则，也不是客户需要维护的第三份业务输入。
只有角色存在歧义时才需要一次人工确认；Workbook hash 改变后必须重新验证。

当前旧路径保留为 `MIGRATION_ONLY` 基线，不能把它们误写成目标生产架构：

| 路径 | 入口 | 当前定位 | 默认生产路由 |
|---|---|---|---|
| 三阶段 Controller | `main_executor.py analyze` | MIGRATION_ONLY基线 | 是（切换前） |
| Typed Agent / `WorkflowGraph` | `main_executor.py agent` | MIGRATION_ONLY验证/Draft | 否 |
| 17-step Engine | `main_executor.py fallback` | MIGRATION_ONLY回归 | 否 |
| Template-Driven Runtime | P0-2 MethodContract编译已建立，尚未切换 | 目标架构 | 切换后唯一 |

`WorkflowGraph` 是项目自实现的同步 runner，不是 LangGraph。当前事实、可复现命令、
已知阻断和下一阶段依赖关系见
[开发交接与可复现基线](docs/architecture/HARA_V13_HANDOFF_BASELINE_2026-08-14.md)。

## 开发环境

- Python：`>=3.12`，当前验证版本为 `3.12.10`。
- 安装依赖以 `requirements.txt` / `setup.py` 为准；核心运行不依赖 pandas。
- 新 Agent 需要配置 `HARA_LLM_BASE_URL`、`HARA_LLM_MODEL`、`HARA_LLM_API_KEY`。
- AVP Domain Profile 当前为 `migration_baseline`，只服务旧 Runtime 对照；不得新增工程规则，也不是目标架构的发布权威。

## 当前 Agent 入口（MIGRATION_ONLY）

通过旧总入口调用新链路，无需配置 `PYTHONPATH`：

```powershell
.\.venv\Scripts\python.exe .\scripts\main_executor.py agent `
  --domain avp `
  --item .\input\ItemDef.docx `
  --template .\references\HARA_Template_AI_20260327.xlsx `
  --output .\output\HARA_Report_Output.xlsx `
  --run-id avp-current `
  --operating-mode parking `
  --max-workers 4 `
  --allow-draft
```

`--operating-mode` 是 Scenario Candidate 的结构化运行模式输入；生产链不会从 Scenario 名称、ID 或自由文本猜测模式。
Application 使用该值通过 `ProjectFactResolver` 选择同模式的 `SpeedEnvelope`，未知或缺失模式默认
`UNRESOLVED_PROJECT_CONTEXT`。仅旧 ProjectFacts 没有 contextual envelope 时，才可显式使用
`--allow-aggregate-speed-fallback`；Draft/迁移回归如需 Domain Profile 旧速度，还必须显式使用
`--allow-legacy-speed-fallback`。两类 fallback 都保留 diagnostics/provenance，并保持待评审状态。

`--max-workers`控制局部补抽取、Guideword、Malfunction及Scenario语义任务的受控并发，
范围1～32、默认4。设为1可执行确定性顺序回归；运行时会在stderr输出阶段耗时和批次进度。
并发不会改变结果排序，输出仍按输入Function/Malfunction顺序稳定组织。
LLM单次读取默认超时180秒；超时、HTTP 429及5xx默认最多重试2次。可通过
`HARA_LLM_TIMEOUT_SECONDS`、`HARA_LLM_MAX_RETRIES`和`HARA_LLM_RETRY_BACKOFF_SECONDS`调整。
首个语义阶段只执行一次整文主抽取，同时返回核心Item Definition与Functions，初始输出预算为16384；
仅当服务端明确返回`finish_reason=length`时，才按`HARA_LLM_MAX_TOKENS`配置的全局上限扩容重试一次。
可用`HARA_ITEM_ARTIFACT_MAX_TOKENS`调整主抽取初始预算；
确定性Checker发现核心字段缺失时，仅把相关文档块送入ODD补抽取。性能参数、驾驶员控制和Exposure
证据也只读取关键词命中的局部块，单个补抽取预算为4096。后续Guideword、Malfunction、Scenario
分别使用4096、8192、8192，避免两次长文请求并发竞争和无条件使用最大输出预算。

校验后Artifact缓存默认采用`readwrite`，可用`HARA_ARTIFACT_CACHE_MODE=off`禁用，并用
`HARA_ARTIFACT_CACHE_DIR`指定目录。缓存只保存已经完成类型构造、Function Checker和局部合并的
Item/Function Artifact；不保存原始LLM响应，也不包含API Key。使用`refresh`可忽略旧缓存并重新生成。

运行前可先做只读检查：

```powershell
.\.venv\Scripts\python.exe .\scripts\main_executor.py agent-doctor `
  --domain avp `
  --template .\references\HARA_Template_AI_20260327.xlsx
```

该命令分别报告 Draft 与正式发布 readiness，不调用模型，也不生成报告。
只有 `ready_for_draft=true` 才说明当前环境具备运行新 Agent 的最低条件；
该 Doctor 仍报告旧 Runtime 的 Profile 状态；它不代表未来 MethodContract 的
release readiness。Harness 的 `READY` 不等价于环境或正式发布 READY。

如果项目提供了经确认的自车速度，可增加：

```powershell
--ego-speed-kph 5 --ego-speed-source "ItemDef.docx#AVP-speed"
```

也可直接运行 `src` 包：

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe -m hara_agent analyze --help
```

## 当前迁移 Runtime 发布状态

- 当前 AVP Domain Profile 为 `migration_baseline`，不是工程批准版本，也不是未来工程真值源。
- 正式模式默认 Fail-Closed；存在 `PENDING` 时不会生成正式报告。
- `--allow-draft` 只生成带可见水印的评审稿，不会把待评审项改成已批准。
- ASIL 仅从本次输入模板的 `ASIL_Table` 查表，禁止默认矩阵和数值求和。
- 当前旧链仍由 Domain Profile 给出 S/E/C 映射候选；目标链必须直接执行从 Template 编译的规则，缺失或歧义时保持 `PENDING`。
- Exposure的`T/F`分别表示平均运行时间占比/场景发生频率，禁止由E等级机械推导。

## MIGRATION_ONLY 旧入口

旧三阶段链路仍保留，尚未切换默认生产路由：

```powershell
.\.venv\Scripts\python.exe .\scripts\main_executor.py analyze `
  --system AVP `
  --item .\input\ItemDef.docx `
  --template .\references\HARA_Template_AI_20260327.xlsx `
  --output .\output\HARA_Report_Output.xlsx `
  --allow-draft
```

显式 Legacy Fallback：

```powershell
.\.venv\Scripts\python.exe .\scripts\main_executor.py fallback `
  --item .\input\ItemDef.docx `
  --template .\references\HARA_Template_AI_20260327.xlsx `
  --output .\output\HARA_Report_Output.xlsx `
  --allow-draft
```

## 目录职责

- `src/hara_agent/`：新 Agent、工作流、强类型模型和确定性服务。
- `config/domains/`：版本化 Domain Policy；不得包含 ASIL 矩阵。
- `skills/`：按职责拆分的 Agent Skills，不保存项目参数。
- `scripts/`：迁移期旧实现和兼容入口；新业务规则不得继续写入。
- `references/`：本次运行模板及标准参考。
- `runtime/`：Checkpoint 和审计状态。
- `output/`：Excel 交付物；同一路径覆盖写入。
- `docs/architecture/`：架构方案、旧路径和硬编码审计。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

2026-08-19 当前全量结果：`372 passed, 21 skipped`。`pytest.ini` 已排除项目生成的
`.pytest-tmp-*`、`pytest-cache-files-*`、`runtime/` 和 `tmp/`，因此上述命令可直接从项目根目录复现。
跳过项为依赖已清理历史生成物的 Legacy 回归，测试会明确说明缺失的 fixture，不再误报业务代码失败。

Source / ProjectFacts Integrity Harness 使用统一入口：

```powershell
.\.venv\Scripts\python.exe .\scripts\eval_hara.py --stage extraction `
  --item .\input\ItemDef.docx `
  --run-id RUN_ID `
  --output .\runtime\agent\RUN_ID.extraction-eval.json
```

该命令只评估 checkpoint 或 `--project-facts` JSON，不调用 LLM、不改 Prompt、不写回项目事实。

P0-1.9 extraction-only fresh Provider 验证使用：

```powershell
.\.venv\Scripts\python.exe .\scripts\fresh_extraction_grounding.py
```

该脚本自动加载根目录 `.env`，使用独立 `refresh` cache，并拒绝任何非 extraction LLM task。
脚本在兼容主抽取/补抽取之外只允许 speed、performance/timing、driver 三个 bounded targeted category，
并将 raw candidates 交给 fail-closed deterministic atomic normalizer。2026-08-17 的最终真实 AVP 报告保存在
`runtime/evaluation/p0-1-9-fresh-20260817-007.json`：cache miss，10/10 typed/grounded，SourceRef、context、
normalization 均为 1.0，search/control/parking 分别解析为 30/7/5 km/h，无 fallback。状态为
**P0-1.9 Atomic ProjectFacts READY**、**Extraction Production READY**、
**READY_FOR_SCENARIO_INTEGRATION**。

P0-2a 已将上述原子 ProjectFacts 接入生产 Scenario FactRegistry。Registry 现在使用强类型
`EvidenceRecord`，分别保存 evidence kind、provenance、approval、SourceRef 和 metadata；支持
`MF.*`、`SCN.*`、`PROJECT.*`、`METHOD.*`、`DOMAIN_RULE.*`、`DERIVED.*` 命名空间，重复 ref
直接拒绝。Scenario facts 会继承 Project/Legacy provenance，Legacy 候选保持
`LEGACY_MIGRATION/PENDING`，TTC 保持 `DERIVED_PHYSICS/DERIVED`。`scenario-evidence-v1` schema、
basis values、因果语义、Prompt 与 Provider 参数均未改变。Evidence Integrity Harness 的确定性
fixture 结果为 registration/provenance/source preservation 全部 1.0，legacy authority leak 和 duplicate
均为 0。状态：**P0-2a Evidence Substrate READY**。

P0-2b 新增独立 `contracts` 层的 `scenario-evidence-v2` deterministic contract：每个 causal support
独立声明 canonical evidence kind，严格校验 exact ref、provenance、approval 与 derived metadata；版本化
Mechanism Definition/Application 使用精确 premise bindings，未知、版本不匹配、PENDING、漏绑定或错 kind
全部 fail-closed。v1/v2 Differential Harness 已确认 mixed `DIRECT_FACT + DERIVED_PHYSICS` 在 v2 PASS、
在未修改的 v1 中仍为 `DERIVED_PHYSICS_KIND_MISMATCH`。生产继续使用 `scenario-feasibility-v9` 和
`scenario-evidence-v1`，未修改 Prompt/Provider，也未调用 LLM。状态：
**P0-2b scenario-evidence-v2 deterministic contract READY**。

P0-2c1 已增加显式 `scenario-feasibility-v10` / `scenario-evidence-v2` Provider contract 路径。v10 保留
v9 的 Atomic Scenario、structured-fact authority、No New World State、M→B→I→H→Harm、counterfactual
与 breakpoint 方法论，仅将输出升级为 `edges[]`、per-support `supports[]` 和
`mechanism_application`。Provider 只能从结构化 `AVAILABLE_CAUSAL_MECHANISMS` 选择 mechanism，strict
parser 不做 semantic repair，对 hybrid payload、exact coverage、unknown mechanism 和错误 binding 全部
fail-closed。Prompt/assessment/catalog fingerprint 已进入 request、evaluation report 和 checkpoint audit。
`scenario_smoke.py` 与 `eval_hara.py` 支持显式 `--scenario-contract v2`，但 production 默认仍是
v9/v1。本阶段仅使用 stub + TEST_ONLY fixture，未读取 `.env`、未调用真实 Provider。
全量回归为 `372 passed, 21 skipped, 0 failed`。状态：**P0-2c1 Provider Schema Migration READY**；
其后的 P0-2c2 real-provider semantic evaluation 结果见下段。

P0-2c2 已执行受控的 Scenario-only 真实 Provider 评估。Synthetic negative 在连续 3 次
`INVALID_V2_ASSESSMENT_SHAPE` 后提前停止：原始 Provider verdict 为 false 3/3，breakpoint 也与 Gold
一致，但 schema/contract valid 均为 0/3，因此不能计为通过。Synthetic positive 连续 3 次
`ScenarioAdaptiveBatchError`，没有可解析 verdict。Order A/B 各运行 3 次，均没有 contract-valid
交叉样本，所以 causal/mechanism/binding/support order agreement 保持 `null`。固定 AVP pair 暴露了
evaluation runner 的本地 12,000-char batch 上限错误；该上限已修为 30,000（不属于 Provider 生成参数），
但修复后的 v2/v1 调用因外部额度耗尽未能启动，Real AVP 与 v1/v2 differential 明确标记为未完成，
没有把本地错误伪装成 Provider 观测。合并报告位于
`runtime/evaluation/p0-2c2-summary-20260819-final.json`。状态：**Provider Schema Stability NOT_READY**、
**Controlled Semantic Stability NOT_READY**、Real AVP **OBSERVATIONAL_ONLY / NOT_COMPLETED**；Primary blocker
为 `PROVIDER_SCHEMA_INSTABILITY`。Prompt、Provider 参数、正式 AVP mechanism 与 production v9/v1 默认值均未修改。
全量回归为 `380 passed, 21 skipped, 0 failed`。

## 架构文档

- [Agent重构方案](docs/architecture/HARA_AGENT_REFACTOR_PLAN.md)
- [开发交接与可复现基线（2026-08-14）](docs/architecture/HARA_V13_HANDOFF_BASELINE_2026-08-14.md)
- [Scenario Evidence v2 Deterministic Contract](docs/architecture/SCENARIO_EVIDENCE_V2_CONTRACT.md)
- [旧路径与清理清单](docs/architecture/LEGACY_CODE_INVENTORY.md)
- [硬编码与可扩展性审计](docs/architecture/HARDCODED_RULE_AUDIT.md)
