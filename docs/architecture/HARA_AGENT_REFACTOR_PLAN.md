# HARA Agent 架构重构方案（Historical Migration Baseline）

> 本文记录 Template-Driven 决策之前的迁移方案，不再定义未来工程真值源。
> 当前决策与 Governance 冲突清单见
> `TEMPLATE_DRIVEN_GOVERNANCE_MIGRATION.md`。下文 Domain Profile、Legacy 与
> Fallback 内容只描述保留用于对照的 `MIGRATION_ONLY` Runtime，不得新增规则。

> 文档状态：目标架构 + 迁移记录。当前可执行事实、环境检查、测试命令、哈希和阻断以
> [HARA V13 开发交接与可复现基线（2026-08-14）](HARA_V13_HANDOFF_BASELINE_2026-08-14.md)
> 为准。本文中的“目标”不表示默认生产路由已经完成切换。

## 0. 当前路径与目标边界

| 路径 | 当前状态 | 入口 | 说明 |
|---|---|---|---|
| 三阶段 Controller | 生产兼容主链 | `main_executor.py analyze` | 当前默认路由；仍使用阶段 JSON 和兼容脚本 |
| Typed Agent / `WorkflowGraph` | 迁移验证主链 | `main_executor.py agent` | 强类型状态、checkpoint、确定性服务和 Draft 质量门 |
| 17-step Engine | Legacy Fallback | `main_executor.py fallback` | 仅用于显式或超限 fallback |
| LangGraph | 目标状态 | 尚无入口 | 当前未安装、未接入；不能将 `WorkflowGraph` 误称为 LangGraph |

迁移目标是让三条现存路径逐步复用同一组 typed models 与 deterministic services，最后再切换默认入口。

## 1. 重构目标

当前 V13 的默认生产兼容路径仍是“脚本流水线 + JSON 中间文件”；同时已经形成可显式运行的
Typed Agent 迁移路径。目标架构是“Agent 编排 + 强类型状态 + Domain Policy Snapshot +
确定性工程服务 + 人工审核”的混合系统，而不是把当前迁移实现描述为已经完成的生产切换。

核心原则：

1. LangGraph 只负责状态、节点路由、重试、暂停、回滚和检查点，不承载具体 HARA 算法。
2. LLM 负责语义任务：文档理解、候选生成、适用性解释、危害语义和文本表达。
3. Python 确定性服务负责矩阵查表、公式、ID、外键、归并、质量门和报告渲染。
4. 项目参数必须来自输入文档、输入模板、批准的 Domain Profile 或工程师填回；不得以代码默认值伪装成项目事实。
5. ASIL 只读取本次输入模板的 `ASIL_Table`，不得复制到 YAML 或代码中。
6. 任何未知 Domain、缺少规则版本或使用未批准参数的结果必须进入 `PENDING/DRAFT`，不能生成正式报告。

## 2. 目标运行链路

```text
START
  → initialize_run
  → extract_document
  → review_item_definition
  → identify_functions
  → assess_guideword_applicability
  → derive_malfunctions_and_hazards
  → review_hazop
  → generate_scenario_candidates
  → judge_scenario_feasibility
  → score_s_e_c_ftti
  → determine_asil_from_template
  → aggregate_safety_goals
  → review_risk_results
  → quality_gate
  → render_report
END
```

建议在以下位置设置 Human-in-the-Loop：

- Item Definition：确认Function、Output、ODD、运行模式及来源。
- HAZOP：确认不适用Guideword和车辆级Hazard。
- S/E/C/FTTI：处理`PENDING`、项目参数和证据不足项。
- 正式发布：确认所有必需判定均为`FINALIZED`。

## 3. 目标目录结构

```text
HARA_V13/
├─ pyproject.toml
├─ AGENTS.md                    # Codex/OpenCode使用说明，不包含业务规则
├─ SKILL.md                     # Agent调用入口，不作为运行时规则源
├─ src/
│  └─ hara_agent/
│     ├─ __init__.py
│     ├─ cli.py                 # 唯一CLI入口
│     ├─ application.py         # 构造依赖并启动工作流
│     ├─ workflow/
│     │  ├─ graph.py            # LangGraph拓扑
│     │  ├─ state.py            # HARAState
│     │  ├─ routing.py          # approve/reject/pending/fail路由
│     │  ├─ checkpoints.py
│     │  └─ nodes/
│     │     ├─ initialize.py
│     │     ├─ extraction.py
│     │     ├─ functions.py
│     │     ├─ hazop.py
│     │     ├─ hazards.py
│     │     ├─ scenarios.py
│     │     ├─ scoring.py
│     │     ├─ safety_goals.py
│     │     ├─ quality_gate.py
│     │     └─ render.py
│     ├─ models/
│     │  ├─ common.py           # Source/Judgement/ReviewStatus
│     │  ├─ item_definition.py
│     │  ├─ malfunction.py
│     │  ├─ hazard.py
│     │  ├─ scenario.py
│     │  ├─ risk.py
│     │  └─ safety_goal.py
│     ├─ domain/
│     │  ├─ protocol.py         # DomainProfile接口
│     │  ├─ registry.py         # 显式注册和Fail-Closed检测
│     │  ├─ loader.py
│     │  └─ profiles/
│     │     └─ avp_low_speed/
│     │        ├─ profile.yaml
│     │        ├─ scenario_atoms.yaml
│     │        ├─ applicability.yaml
│     │        ├─ scoring.yaml
│     │        └─ safety_goals.yaml
│     ├─ services/
│     │  ├─ extraction/
│     │  │  ├─ document_reader.py
│     │  │  ├─ typed_extractor.py
│     │  │  └─ fact_extractor.py
│     │  ├─ analysis/
│     │  │  ├─ function_service.py
│     │  │  ├─ malfunction_service.py
│     │  │  ├─ hazard_service.py
│     │  │  ├─ scenario_generator.py
│     │  │  ├─ scenario_selector.py
│     │  │  ├─ scoring_service.py
│     │  │  ├─ ftti_service.py
│     │  │  └─ safety_goal_service.py
│     │  ├─ validation/
│     │  │  ├─ item_validator.py
│     │  │  ├─ hazop_validator.py
│     │  │  ├─ scenario_validator.py
│     │  │  ├─ scoring_validator.py
│     │  │  └─ release_gate.py
│     │  └─ reporting/
│     │     ├─ template_contract.py
│     │     ├─ excel_renderer.py
│     │     └─ text_renderer.py
│     ├─ infrastructure/
│     │  ├─ llm/
│     │  │  ├─ protocol.py
│     │  │  └─ provider_factory.py
│     │  ├─ experience/
│     │  │  ├─ repository.py
│     │  │  └─ matcher.py
│     │  └─ persistence/
│     │     └─ run_repository.py
│     └─ compatibility/
│        └─ v13_json.py          # 迁移期旧JSON读写适配器
├─ config/
│  ├─ standards/
│  │  └─ hazop_guidewords.yaml   # 14个Guideword及版本
│  └─ templates/
│     └─ hara_v13.yaml           # Sheet/字段契约，不复制ASIL矩阵
├─ legacy/
│  └─ v13_17step/                # 只读兼容，不允许承接新规则
├─ integrations/
│  └─ mcp/README.md              # 仅放实际接入HARA Agent的MCP适配器
├─ tests/
│  ├─ unit/
│  ├─ contract/
│  ├─ regression/avp/
│  ├─ regression/cross_domain/
│  └─ fixtures/
├─ runtime/                       # 每次运行的中间状态，不进入源码目录
├─ output/                        # 固定正式输出，可按要求覆盖
└─ docs/architecture/
```

文件不使用`step1_`、`step2_`编号维持执行顺序；执行顺序由`workflow/graph.py`唯一声明。文件名只表达职责。

## 4. 当前文件迁移映射

| 当前文件 | 目标位置 | 处理方式 |
|---|---|---|
| `scripts/main_executor.py` | `cli.py`、`application.py`、`workflow/routing.py` | 拆分；最终保留薄兼容入口 |
| `scripts/hara_controller.py` | `workflow/graph.py`、`checkpoints.py` | 用LangGraph节点和状态替代subprocess串联 |
| `scripts/malfunction_engine.py` | `services/analysis/function_service.py`、`malfunction_service.py`、`hazard_service.py` | 按职责拆分 |
| `scripts/scen_hazevent_engine.py` | `scenario_generator.py`、`scenario_selector.py`、`hazard_service.py` | 删除内置Checker副本和文本兜底 |
| `scripts/scoring_sg_engine.py` | `scoring_service.py`、`ftti_service.py`、`safety_goal_service.py` | 评分规则迁入Domain Profile/标准服务 |
| `scripts/logic_checkers/*` | `services/validation/*` | 分离结构校验与工程语义校验 |
| `scripts/report_generator.py` | `template_contract.py`、`excel_renderer.py`、`text_renderer.py` | 拆分数据映射、Excel格式和文案 |
| `scripts/hara_schema.py` | `models/*` | 改为Pydantic运行时模型 |
| `scripts/asil_matrix.py` | `services/analysis/asil_service.py` | 保留模板查表核心 |
| `scripts/ftti.py` | `services/analysis/ftti_service.py` | 公式/参数外置并保留批准状态 |
| `scripts/experience_library.py` | `infrastructure/experience/*` | 取消默认绝对路径，禁止静默命中 |
| `scripts/hara_engine.py` | `legacy/v13_17step/engine.py` | 只读兼容，完成迁移后删除 |
| `scripts/*Ref.json` | Domain Profile或批准的知识库 | 禁止全局混合多个子系统规则 |

## 5. Agent、自动化和工程判断的边界

### 5.1 可完全自动化的确定性逻辑

- 文档读取、表格单元格展开和来源定位。
- Pydantic Schema验证、ID生成、外键和维度一致性。
- 14个Guideword覆盖矩阵记录。
- 输入模板`ASIL_Table`查表及复核。
- 明确公式和批准参数下的运动学计算。
- SG的Max.ASIL/最严FTTI聚合。
- Excel字段映射、格式、合并和质量水印。

### 5.2 Agent可以提出候选，但必须保留证据和置信度

- Function/Output/Precondition/Trigger语义抽取。
- Guideword适用性候选及理由。
- Malfunction、车辆级Hazard和Hazardous Event表达。
- ODD内场景候选、可行性和风险差异判断。
- Safety Goal语义归并和文本生成。

### 5.3 强输入依赖、必须工程批准的逻辑

- ODD速度、坡度、天气、车位和目标物范围。
- Exposure运行占比、目标物出现频次和触发状态分布。
- Severity伤害模型、碰撞构型和适用速度区间。
- Controllability接管位置、通信延迟、反应时间和避让能力。
- FTTI公式、制动减速度、诊断/决策/执行时序预算。
- Safe State的独立执行能力、冗余路径和车辆级约束。

这些字段统一使用：

```text
value + source + confidence + status + rule_version + review_reason
```

状态限定为`FINALIZED / PENDING / REJECTED / NOT_APPLICABLE`。质量门只允许全部必要工程字段为`FINALIZED`的结果进入正式报告。

## 6. 场景组合的目标 Agent 化策略

1. 从模板场景库和Domain Profile加载维度，不在Python写道路/天气/行为列表。
2. 用Item ODD先执行确定性排除，保留排除审计。
3. 对剩余维度构造候选，不直接生成全量HARA记录。
4. Agent判定物理可行性、功能相关性和危害因果链，低置信度进入人工审核。
5. 所有可行候选先完成S/E/C/FTTI评估。
6. 评估后按风险签名归并；数量只作为告警，不作为删除条件。
7. 每个代表场景保存`covered_scenario_ids`和被归并原因。

## 7. 分阶段迁移（历史快照）

以下内容保留为 2026-08-07 历史快照；当前状态见第 11 章：

- 阶段A：已完成第一轮边界、运行路径和死代码清理。
- 阶段B：进行中；已建立 `RunConfig`、`HARAState`、`EvidenceValue`、
  `SourceRef`、`ScenarioCandidate`、`RiskAssessment`、`RiskGroup`和`SafetyGoal`审核状态模型；
  `compatibility/v13_json.py`可将现有Phase 3 JSON转换为强类型Agent状态，其中RiskAssessment
  使用独立`assessment_id`，`scenario_id`仅作场景外键，避免同一场景被多个失效复用时覆盖。
  `workflow/nodes/compatibility.py`已作为首个工作流节点接入，自动汇总待审证据并路由至质量门；
  旧控制器已开始使用RunConfig。
- 阶段C：进行中；AVP Safety Goal、功能归类与对应 Safe State 已迁入
  `config/domains/avp/profile.json`；AVP检测、基础场景参数、对象候选、相对速度/距离、
  碰撞几何以及结构化场景的S/E/C判据已由 `AVPDomainPolicy` 加载。Severity按对象类别、
  碰撞几何和相对速度判定，Exposure读取场景证据，Controllability按驾驶员接管状态判定；
  均携带规则ID、版本和工程审核状态。非结构化/非AVP输入暂时仍走旧文本兼容路径，通用危害
  规则及 `ScorabilityRef.json` 已标记为兼容路径：仅在非结构化输入时按需加载，结果带
  `legacy_text_scoring_compatibility` 来源并被正式质量门阻断。结构化AVP场景若缺少Domain
  Policy判据将直接失败，不再静默回退。结构化评分主路径已经迁入
  `src/hara_agent/services/analysis/scoring_service.py`；`scoring_sg_engine.py` 只负责迁移期
  编排并在非结构化输入时调用旧类。ASIL模板查表主路径也已迁入
  `src/hara_agent/services/analysis/asil_service.py`，强制显式接收本次运行模板且不提供默认矩阵。
  Safety Goal分类、Max.ASIL、事件关联和最严FTTI聚合已迁入
  `src/hara_agent/services/analysis/safety_goal_service.py`。`scripts/asil_matrix.py` 暂仅供
  17步
  兼容引擎和旧回归测试使用。场景级FTTI主路径已迁入
  `src/hara_agent/services/analysis/ftti_service.py`，保持TTC只作筛查、候选公式保持
  `NEEDS_REVIEW`、显式值必须同时具有批准状态和来源才能`FINALIZED`。`scripts/ftti.py`
  暂仅供17步兼容引擎和旧测试使用。AVP Safety State已确认完全来自Domain Profile中的SG
  定义；主链不再预加载`SafetyStateRef.json`，且AVP功能缺少SG映射时直接失败，不允许回退
  到通用文案。评分后的风险签名归并已迁入
  `src/hara_agent/services/analysis/risk_aggregation_service.py`；对象类别、碰撞几何、运动关系、
  接管状态、S/E/C/ASIL、SG及FTTI均保留在签名中，数量仍不作为删除条件。下一步删除脚本中
  已失去调用的旧归并副本，并迁移剩余报告前处理逻辑。
- 阶段D：已开始；首个兼容导入节点已接入强类型状态，但当前完整运行仍由三阶段subprocess控制器执行。
- 阶段E：已开始；已建立供应商无关的LLM协议和首个Function Extraction语义Agent，并提供
  OpenAI-compatible Provider。实际Endpoint、模型和密钥通过`HARA_LLM_*`环境变量注入，未配置时Fail-Closed。
- 阶段F：未开始。

### 阶段A：边界和清理

- 建立本方案和Legacy清单。
- 删除全仓库无调用的死代码。
- 禁止在`scripts/`写运行JSON。
- 将MCP、示例、一次性工具和17步引擎从主链标识中隔离。
- 冻结当前AVP回归基线。

### 阶段B：强类型核心

- 引入Pydantic模型和统一`HARAState`。
- 建立Source/Judgement/AuditTrail。
- 给旧三阶段JSON增加兼容适配器。

### 阶段C：Domain Profile

- 把AVP场景、适用性、S/E/C、SG和Safe State常量移出Python。
- Domain未知时Fail-Closed。
- Profile加载时记录版本和批准状态。
- 结构化AVP场景优先使用Domain Policy的S/E/C规则；旧关键词评分只能作为显式兼容路径。
- Profile中的迁移判据保持`PENDING`，批准前只允许生成Draft。

### 阶段D：LangGraph工作流

- 节点调用服务层，不再通过subprocess传JSON。
- 增加checkpoint、interrupt、reject和rollback。
- 保留旧CLI为薄适配器。

### 阶段E：LLM语义节点

- 双轨typed/facts文档抽取。
- Guideword适用性、Hazard和场景可行性使用结构化输出。
- 所有LLM输出必须经过Schema和确定性Checker。
- 不在业务服务中绑定OpenAI、Doubao或其他供应商；通过`infrastructure/llm/protocol.py`注入。
- Function Extraction Agent输出必须携带来源、置信度、Prompt版本、模型和请求审计。
- `FunctionValidator`在LLM之后确定性拦截段落残片、编号条目、条件句、章节标题、重复功能和
  缺少定位/原文的候选；规则只判断文本角色，不包含AVP或其他子系统业务关键词。
- `services/extraction/document_reader.py`按段落、表格行或PDF页建立可追溯Block；不在读取层做功能判断，
  也不静默截断全文。分块和Token预算由后续语义节点显式管理。
- `GuidewordApplicabilityAgent`必须逐项覆盖模板Guideword并记录适用/不适用理由；覆盖完整性与
  Malfunction生成相互分离，只有适用项进入后续候选生成，从机制上避免无条件笛卡尔积。
- `MalfunctionHazardAgent`只接收适用Guideword，输出失效、功能影响、车辆级Hazard和至少两段
  因果链；确定性校验禁止遗漏适用Guideword、混入不适用项、重复候选及把人员伤害直接当Hazard。
- `ScenarioFeasibilityAgent`对每个ODD内候选记录物理、功能和因果可行性及可能改变的风险维度；
  必须覆盖所有候选，不设置保留数量上限，最终归并仍在完成S/E/C/FTTI之后进行。

### 阶段F：移除Legacy

- 主路径、经验库路径和Agent路径使用同一服务层。
- 对比回归通过后删除`legacy/v13_17step`及旧JSON适配器。

## 8. 不应直接照搬参考项目的内容

- 不复制其8个Guideword口径；本项目继续评估模板要求的14个Guideword。
- 不把ASIL矩阵迁入YAML；继续以输入模板`ASIL_Table`为唯一权威。
- 不硬限制每个失效2～5个场景。
- 不统一删除零速场景；驻车保持、错误激活和坡道溜车必须保留。
- 不让LLM直接给出不可追溯的最终S/E/C/FTTI。

## 9. 多 Skill 规划

项目采用“一个主 Agent 编排、多个职责型 Skill 协作”的结构。Skill 不保存项目参数，也不替代运行时代码：

| Skill | 职责 | 不负责 |
|---|---|---|
| `hara-orchestrate` | 全链路路由、状态、暂停、恢复和发布决策 | 具体HARA算法 |
| `extract-item-definition` | 文档语义抽取、类型分类和来源追踪 | 用默认值补项目事实 |
| `analyze-hara` | Guideword适用性、失效/危害、场景和风险语义分析 | ASIL矩阵复制或Excel渲染 |
| `review-hara` | 工程审计、Checker、问题分级和质量门 | 修改分析结果以绕过质量门 |
| `render-hara-report` | 模板映射、格式、合并、跨Sheet和文件完整性 | 重算或改写工程判断 |

运行时关系：

```text
Main Agent
  -> hara-orchestrate
       -> extract-item-definition
       -> analyze-hara
       -> review-hara
       -> render-hara-report

Skills -> typed services -> Domain Profile / input template / source documents
```

其中：

- Skill回答“这一类任务应如何完成、何时暂停、输出什么证据”。
- Domain Profile回答“当前领域有哪些获批规则和参数”。
- 输入文档回答“当前项目的事实是什么”。
- Python服务回答“如何确定性校验、查表、聚合和渲染”。
- `ASIL_Table`始终只从本次输入Excel读取。

## 10. 当前实现进度补充（历史快照：2026-08-07）

- 当前环境未安装 `langgraph`、`pydantic` 或 `langchain`。迁移期先提供无第三方依赖的
  `WorkflowGraph`，其节点签名和状态边界可继续被 LangGraph 适配，不能将其误报为已使用 LangGraph。
- `CheckpointRepository` 已支持 `HARAState` 原子化保存与强类型恢复；工作流已支持质量门中断、
  计划中断、未注册节点中断、无进展阻断和最大步数保护。
- 已增加独立的 `MALFUNCTIONS` 阶段，避免 Guideword 适用性和失效候选生成共用同一状态而形成自循环。
- `build_semantic_frontend_graph()` 已贯通：实际文档读取 → Function Agent → Guideword Agent →
  Malfunction/Hazard Agent → Scenario Feasibility Agent → `SCORING` 边界。
- 场景筛选按物理、功能、因果可行性和风险维度差异保留，不使用固定 `1~3` 条或总数上限；
  被保留场景及完整评估审计均写入状态。
- 结构化S/E/C评分、输入模板`ASIL_Table`查表、FTTI候选及评审状态、Safety Goal聚合和质量门
  已接入`build_hara_agent_graph()`；风险记录显式关联`scenario_id`和`malfunction_id`，禁止靠文本或ID拆解回查。
- 迁移基线Domain Profile未批准或FTTI仍待评审时，完整图会在`QUALITY_GATE`以
  `pending_engineering_review`中断，不得进入正式渲染。
- Excel渲染已接入可选`RENDER → COMPLETE`节点。渲染器直接修改模板OOXML包中的两个目标Sheet，
  不通过常规重存破坏`customXml`、标签元数据、自定义存储或扩展数据验证；Q列写入已查表ASIL并覆盖旧求和公式。
- B-G仅按父级边界层级合并，H-U保持独立；模板T/F表示Exposure采用的判定方法：
  `T`为平均运行时间占比，`F`为场景发生频率。T/F由Exposure证据方法直接输出，禁止由E等级反推。
- 新CLI尚未接管生产入口，因此旧CLI仍是生产兼容入口，`scripts/`现在不能删除。
- `TemplateInputReader`从本次模板读取并校验14个Guideword、场景维度、S0-S3、E0-E4
  （T/F两套判据）和C0-C3标准；读取层不构造场景组合，
  因此模板中的高速公路等通用值不会绕过Item ODD/Domain Policy直接进入AVP HARA。
- 核心Agent单元/契约验证包含使用实际`input/ItemDef.docx`和真实模板
  `HARA_Template_AI_20260327.xlsx`运行至质量门的端到端图契约测试。
- 模板保真回归确认输出与输入均为39个OOXML部件，37个非目标Sheet/部件逐字节保持一致，
  两个目标Sheet的`x14:dataValidations`仍存在，ZIP及Excel重开校验通过。
- `AVPScenarioCandidateService`按Domain Profile中的6类风险类别及ItemDef驾驶员控制上下文生成候选，
  不与模板通用维度做无条件笛卡尔积；缺少ItemDef驾驶员上下文时同时保留Profile中的车内/车外候选并
  标记`DOMAIN_CANDIDATE_PENDING`，不再把全部场景固定为驾驶员车外。未提供项目车速时使用迁移候选值并
  明确标记`MIGRATION_FALLBACK/PENDING`。
- 新增`HARAApplication`与`hara_agent.cli`装配入口，Domain必须显式提供；支持run-id、checkpoint恢复、
  项目车速及来源参数。当前仍需通过`PYTHONPATH=src`运行，安装包/旧入口切换尚未完成。
- 独立CLI导入路径已验证，核心Agent测试41项、全量回归71项通过。
- Draft模式保持`QUALITY_GATE/PENDING`状态并在`05_HARA!A3`与`06_Safety Goal!A2`写入
  `DRAFT — NOT FOR RELEASE`可见水印；应用审计记录`draft_excel_report_rendered`，不执行状态升级。
- `scripts/main_executor.py agent`已成为新链路的显式兼容入口；旧`analyze`尚未默认切换。
- Guideword适用性、Malfunction生成和Scenario可行性已从逐项串行改为受控并发，
  默认4 workers，可通过`--max-workers 1..32`调整；并发结果按输入索引恢复顺序，避免完成时序改变ID关联和报告排列。
- 工作流在每个Stage开始、完成或失败时输出耗时，批量语义节点同时输出`completed/total`，
  解决长时间运行无可见进度的问题。Checkpoint仍以Stage为原子边界。
- Item Definition与Function改为一次整文主抽取（初始16384 tokens），随后由确定性Checker识别核心缺口；
  仅当服务端明确返回`finish_reason=length`时，才按全局上限扩容重试一次，不对超时和业务校验错误扩容；
  ODD缺口以及性能参数、驾驶员控制、Exposure证据分别通过语义路由选取相关文档块，再按需执行最多
  两个局部补抽取（各4096 tokens）。筛选依据是字段缺口和证据块相关性，不以目标行数控制结果。
- Item/Function Artifact仅在类型构造、Function Checker及补抽取合并成功后进行SHA-256内容寻址缓存；
  不缓存原始LLM响应，缓存键不包含API Key，支持off、readonly、readwrite和refresh四种模式。
  Prompt、路由器、模型或文档内容变化均产生新键；旧`ITEM_DEFINITION`检查点仍可按原节点恢复。
- 后续LLM输出预算按任务收敛：Guideword=4096、Malfunction=8192、Scenario=8192；Prompt同时限制
  数组规模和来源摘录长度。
- `agent-doctor`提供只读生产条件检查，分别报告Draft与Release readiness；当前真实环境检查结果为：
  模板通过（14 Guideword、5类场景维度），AVP Profile未批准，LLM环境变量未配置，因此两种readiness均为false。
- 新增独立`ItemDefinitionExtractionAgent`，抽取系统描述、Item边界、运行模式、ODD和全局速度包络；
  场景候选速度优先级为CLI显式项目值→文档全局最大速度→迁移候选值，来源状态写入审计。
- Hazard、场景化Hazardous Event与Potential Harm已分离建模，报告不再把车辆级Hazard直接复制为伤害字段。
- Domain加载改为显式`DomainRegistry`，未知Domain Fail-Closed；当前仅注册AVP，新增子系统需注册独立Policy。
- LLM传输层对读取超时、HTTP 429及5xx执行有界指数退避重试；默认单次超时180秒、最多重试2次，
  DNS、鉴权、Schema和业务校验错误不重试，避免掩盖永久性配置或数据问题。
- 全量测试结果为`95 passed, 21 skipped`；兼容测试`test_skill.py`为`9 passed`。
  跳过项仅为缺少已清理历史生成物的Legacy回归。

## 11. 当前实现与迁移决策（2026-08-14）

### 11.1 已验证实现

- `scripts/main_executor.py analyze` 仍是默认生产兼容入口；`agent` 是显式迁移入口。
- 当前 `WorkflowGraph` 是同步、无 LangGraph 依赖的 runner；已支持 checkpoint、计划停止、
  质量门中断、无进展阻断和步数保护。
- Scenario Evaluation Harness 已落地 typed evidence error、有效样本不足时的 null 稳定性指标，
  以及按 `scenario_id + semantic_fingerprint` 对齐的 comparison API。
- 全量测试基线已更新为 `312 passed, 21 skipped`；根目录 `pytest -q` 是唯一推荐复现命令。
- Guideword 值从模板动态读取；当前 HARA 方法契约显式要求 14 个唯一 Guideword。
  因此禁止硬编码具体 Guideword 内容，但允许模板入口校验当前版本的方法契约数量。
- Template Scenario Dimensions 当前只完成读取、校验和审计计数，尚未进入 Candidate Generator。
- Candidate Generator 仍以 AVP Profile 的 `risk_candidates` 为候选源，再扩展 atomic variants 和
  driver contexts；未实现 Template Dimensions + Project ODD 的约束驱动 lazy filtering。
- `ItemDefinitionFacts` 已新增 mode-specific `SpeedEnvelope[]`，同时保留全局速度 min/max 作为
  兼容聚合字段；performance、driver 和 exposure 仍是弱类型 dict 列表，尚未完成 P0-3。
- `ProjectFactResolver` 已接管 Scenario Candidate 生产链：Application 要求显式结构化 `operating_mode`，
  按 Search/Parking/Control 选择同模式 `SpeedEnvelope`，未知模式 Fail-Closed；aggregate 与 legacy fallback
  均只能显式授权，并在 Candidate state 中保留 resolution diagnostics、SourceRef、approval 与 provenance。
- Source / ProjectFacts Integrity Harness 已进入现有 `src/hara_agent/evaluation/`，统一 CLI 支持
  `--stage extraction`；指标包括 field recall、grounded recall、SourceRef accuracy、上下文保持率和
  normalization accuracy，并输出稳定 Gap taxonomy。
- 事实 provenance/authority 使用同一 `FactProvenance` 枚举，与 evidence kind、`ReviewStatus`
  正交；未批准的 `migration_baseline` Domain Profile 显式映射为 `LEGACY_MIGRATION`。
- `scenario-evidence-v1` 的 FactRegistry 仍会把多数 `scenario.facts` 统一登记为
  `DIRECT_FACT`；evidence kind、authority/provenance、approval 必须在 v2 中保持正交。
- `agent-doctor` 当前工作区结果为：模板通过、AVP Profile 未批准、LLM 配置缺失，
  因而 Draft/Release readiness 均为 false。Harness READY 不等价于运行或发布 READY。

### 11.2 P0-1.5 Source / ProjectFacts Integrity Harness（已完成）

真实调用链审阅结果：

```text
input/ItemDef.docx
  -> DocumentReader: DocumentBlock(block_id, location, text)
  -> HARAState.item_definition.blocks/text/source_id
  -> ItemArtifactExtractionAgent: 核心 Item/Function，全局 odd.speed_range_kph
  -> ItemEvidenceRouter + ItemSupplementAgent: performance/driver/exposure 弱类型事实
  -> item_artifacts._merge_supplements()
  -> HARAState.item_definition.typed
  -> HARAApplication.prepare_candidates(): typed.speed_max_kph
  -> DomainScenarioCandidateService.generate(ego_speed_kph=全局最大值)
```

原始 ItemDef 已核验的关键事实包括：Search `0–30 kph`（`T-014-R-0015`）、Control
`0–7 kph`（同一行）、Parking `≤5 km/h`（`T-012-R-0003`）、正常制动 `≤3 m/s²`、
紧急制动 `>5 m/s²`、制动响应 `≤50 ms`、转向稳态误差 `≤0.1 deg`、转向响应
`≤150 ms (TBD)` 以及驾驶员在/不在驾驶位。它们已进入 `legacy_avp` grounded fixture，
每项保存 value、unit、context 和真实 block/location/excerpt；该 fixture 仅用于回归，不是新客户事实源。

对已有 `avp-source-trace-fix-v1` checkpoint 的确定性评估结果为：10 项事实全部能在原文定位，
但得到 `3 × NORMALIZATION_LOSS` 和 `7 × ROUTING_MISSED`。前者来自多模式速度压成全局
`0–30`；后者来自 `project_evidence` 路由在 5000 字符上限内先收集前部段落，未覆盖后部
性能/驾驶员表。P0-1.5 只暴露并分类问题，未修改抽取 Prompt、token budget 或 Scenario 行为。

### 11.3 P0-1.6 Schema-guided Item Evidence Routing（已完成）

旧 `ItemEvidenceRouter` 的 root cause 是：所有 performance/driver/exposure 共用一个宽泛关键词集合；
命中后只扩展前一块/本块/后一块；候选没有 field-level score；最后按文档原始顺序装入，达到
5000 字符即停止。检索虽然先遍历了全部 blocks，但装配止于 `T-010-R-0005`，因此后部表格中的：

1. 正常巡航纵向减速度 `T-011-R-0007`；
2. 紧急制动最大减速度 `T-011-R-0008`；
3. 泊车制动响应时间 `T-013-R-0007`；
4. 转向稳态角度误差 `T-013-R-0020`；
5. 转向响应时间 `T-013-R-0023`；
6. 驾驶员在驾驶位 `T-014-R-0013`；
7. 驾驶员不在驾驶位 `T-014-R-0013`；

均未进入 supplement context。这七项是 retrieval/assembly 丢失，不是源文档缺失。

新 Router 使用 `FactRetrievalSpec(fact_type, aliases, unit_hints, context_hints,
section_hints, required)`；模型不能保存 expected value 或 gold locator，因此 fixture 无法反向控制生产检索。
处理顺序固定为：全局 lexical/structural scoring → 每个 required fact 先选一个 top candidate →
补充 table header 与 ±1 邻块 → 剩余预算按全局分数填充。输出保留 block ID、kind、location、text，
并提供 candidate count、selected IDs、selected chars、coverage status 和 top scores 的结构化 diagnostics。
Supplement 后处理会把返回的 source locator 确定性解析回已选择 block，并附加结构化 `SourceRef`；
无法解析时保留空 sources 并计入 `unresolved_source_ref_count`，不猜测来源。

真实 ItemDef 的七项目标证据在 2226 字符上下文内全部覆盖，原 `5000` 字符预算和 Supplement
`4096` token budget 均未增加；Item Artifact/Supplement Prompt、Provider 参数和 Scenario 语义路径均未修改。
Router 版本进入 artifact cache key，避免旧 routed supplement 被错误复用。

旧 `avp-source-trace-fix-v1` checkpoint 的确定性 Before/After：

| 指标 | Before | After |
|---|---:|---:|
| ROUTING_MISSED | 7 | 0 |
| NORMALIZATION_LOSS | 3 | 3 |
| EXTRACTOR_MISSED | 0 | 7 |
| field_recall | 0.0 | 0.0 |
| grounded_field_recall | 0.0 | 0.0 |
| source_ref_accuracy | null | null |
| context_preservation_rate | null | null |
| normalization_accuracy | 0.0 | 0.0 |

`EXTRACTOR_MISSED=7` 是旧 checkpoint 已在旧 Router 下完成补抽取的结果；After Harness 证明新版 Router
已覆盖原文块，但不会伪造抽取事实。必须在配置真实 LLM 后用新 cache key 重跑 extraction，才能测量字段召回改善。

### 11.4 P0-1.7 Mode-specific ProjectFacts Production Wiring（已完成）

原生产链在没有 CLI speed override 时直接读取 `typed.speed_max_kph`，再把全局最大值写入所有
Scenario Candidate 的 `ego_speed_kph`。现在正式链路改为：

```text
RunConfig.operating_mode（显式结构化输入）
  -> HARAApplication.resolve_project_speed_context()
  -> ProjectFactResolver.resolve_speed_context()
  -> matching SpeedEnvelope.max_kph
  -> DomainScenarioCandidateService
  -> ScenarioCandidate.facts.ego_speed_kph + context_resolution + fact_provenance
```

不从 Scenario 名称、ID 或自由文本推断 mode。Parking/Search/Control 的确定性结果分别为 `5/30/7 km/h`；
未知/缺失 mode 输出 typed `UNRESOLVED_PROJECT_CONTEXT`。多于一个 contextual envelope 时禁止 aggregate
collapse；aggregate-only 兼容输入需 `allow_aggregate_fallback=True`，Domain Profile 迁移速度需显式
legacy fallback，且始终标记 `LEGACY_MIGRATION`，不会升级为 `PROJECT_INPUT`。两种 fallback 均保持待评审，
不能静默进入正式发布。

Fresh typed fixture 的 production resolution 检查为 `NORMALIZATION_LOSS=0`；旧 checkpoint 缺少
`speed_envelopes[]` 时仍保留历史 `3 × NORMALIZATION_LOSS`，不迁移、不猜测。Scenario semantic contract、
Prompt、Provider 参数、Router 和 token budget 均未修改，也未调用真实 LLM。

### 11.5 P0-1.8 Fresh Extraction Grounding Validation（验证完成）

受控 fresh run `p0-1-8-fresh-20260817-001` 使用真实 AVP Item Definition、当前 Provider、独立
`refresh` cache 和 extraction-only task 白名单完成。cache miss，Provider 逻辑调用 3 次：
`extract_core_item_artifacts`、`supplement_odd_repair`、`supplement_project_evidence`；未调用
Guideword、Malfunction、Scenario、S/E/C、ASIL、FTTI 或 Safety Goal。

严格 grounded fixture 的 Before/After：

| 指标 | 旧 checkpoint | Fresh extraction |
|---|---:|---:|
| ROUTING_MISSED | 0 | 0 |
| EXTRACTOR_MISSED | 7 | 7 |
| NORMALIZATION_LOSS | 3 | 3 |
| field_recall | 0.0 | 0.0 |
| grounded_field_recall | 0.0 | 0.0 |
| source_ref_accuracy | null | null |
| context_preservation_rate | null | null |
| normalization_accuracy | 0.0 | 0.0 |

Fresh output 的细粒度诊断显示：project-evidence Router 选中 35 个 blocks，并覆盖六个要求的后部表格 block；
LLM 返回 12 个 performance、2 个 driver、1 个 exposure 条目。10 项 grounded source 全部存在；七个
performance/driver fixture 都找到 raw candidate 且 SourceRef 正确（raw candidate SourceRef accuracy=1.0），
但模型把多个原子事实合并为描述字符串，并把驾驶员在/不在驾驶位合成一个上下文，因此未满足严格 typed
value/context contract，现有 Harness 归类为 `EXTRACTOR_MISSED`。这不是 Router 或 SourceRef 丢失。

速度只保留 aggregate `0–30 km/h`，没有生成 `speed_envelopes[]`，因此 Search/Control/Parking 均为
`NORMALIZATION_LOSS`，`ProjectFactResolver` 对三种模式全部 `UNRESOLVED_PROJECT_CONTEXT`。主抽取收到完整
Item Definition，所以三个速度 source 均进入主 Prompt；optional odd-repair route 没有选到后部速度行，不影响
“source/prompt available”判定。没有 `SOURCE_NOT_PROVIDED`，没有加载 Domain Profile，也没有
`LEGACY_MIGRATION` leakage。

因此 **P0-1.8 Validation READY**，但 **Extraction Production NOT_READY**。下一阻断是 P0-1.9：在不扩大
Router budget 的前提下，引入 schema-guided/targeted extraction 与 deterministic atomic normalization；本轮没有
修改任何 Prompt、Router ranking、Provider 参数或 Scenario semantic behavior。完整 machine-readable report：
`runtime/evaluation/p0-1-8-fresh-20260817-001.json`。

### 11.6 P0-1.9 Schema-guided Targeted Extraction + Atomic ProjectFacts（已完成）

P0-1.8 的十项重新审计将根因收敛为：三个 mode-specific speed 目标语义未形成上下文候选；其余七项
performance/driver 已有 grounded raw semantic，但分别发生复合合并或数值/operator/unit 未原子化；没有
已成型 typed fact 被 downstream 丢失。Gap taxonomy 因此新增 `ATOMICIZATION_FAILED`：仅无目标语义时使用
`EXTRACTOR_MISSED`，raw grounded semantic 存在但未满足 typed contract 时使用 `ATOMICIZATION_FAILED`，
`NORMALIZATION_LOSS` 仅表示 typed/context fact 已存在却被后续映射丢失。

生产 extraction 新增以下契约，不改变主抽取、Scenario semantics、Router ranking 或 5000 字符预算：

- `NumericConstraintFact`：typed operator（LT/LE/EQ/GE/GT/RANGE）、单一数值/range、canonical unit、context、
  exact SourceRef、`PROJECT_INPUT` provenance、approval 与独立 `extracted_by`；
- `DriverContextFact`：typed driver location、control mode/condition、exact SourceRef、provenance/approval；
- 继续复用 `SpeedEnvelope`，生成 search/control/parking 三个独立 contextual envelope；
- `RequiredProjectFactSpec` 只保存 schema semantics、aliases、unit/context hints 和 required fields，类型上不能保存
  expected value、Gold locator 或 Gold source block；
- 三个 bounded targeted batches：speed、performance/timing、driver。每个 spec 均有 FOUND/NOT_FOUND coverage；
  NOT_FOUND 只有经确定性 source verification 后才可升级为 SOURCE_NOT_PROVIDED；
- normalizer 只接受 JSON scalar number、canonical operator/unit 和 exact routed block ID；未知/邻行 support-only
  SourceRef、compound value、重复 fact、非法 enum 一律 fail-closed，不拆分自由中文，也不包含 AVP Gold 值解析器；
- legacy `performance_parameters` / `driver_contexts` 只保留兼容读取，production truth 使用
  `numeric_constraints` / `driver_context_facts` / `speed_envelopes`；cache schema 已升级并保存 validated typed payload。

最终 extraction-only fresh run 为 `p0-1-9-fresh-20260817-007`，使用独立 refresh cache，cache miss。Provider
逻辑调用 6 次（主抽取 1、ODD/project compatibility supplement 2、targeted category 恰好 3），transport attempt
同为 6，无重试、无 forbidden downstream call，未执行 Guideword、Malfunction、Scenario、S/E/C、ASIL、FTTI
或 Safety Goal。严格结果：

| 指标 | Fresh result |
|---|---:|
| typed fact count | 10/10（Speed 3 + Numeric 5 + Driver 2） |
| field_recall / grounded_field_recall | 1.0 / 1.0 |
| source_ref_accuracy | 1.0 |
| context_preservation_rate / normalization_accuracy | 1.0 / 1.0 |
| EXTRACTOR_MISSED / ATOMICIZATION_FAILED | 0 / 0 |
| ROUTING_MISSED / NORMALIZATION_LOSS / SOURCE_NOT_PROVIDED | 0 / 0 / 0 |
| ProjectFactResolver | search=30、control=7、parking=5 km/h；均来自 SpeedEnvelope，无 fallback |

完整 machine-readable report：`runtime/evaluation/p0-1-9-fresh-20260817-007.json`。全量回归为
`312 passed, 21 skipped`。因此 **P0-1.9 Atomic ProjectFacts READY**、**Extraction Production READY**，
Source / Context Quality 状态为 **READY_FOR_SCENARIO_INTEGRATION**。

### 11.7 P0-2a Atomic ProjectFacts → Runtime FactRegistry Integration（已完成）

P0-2a 在不改变 `scenario-evidence-v1` schema、basis values、因果判定语义、Prompt 或 Provider 参数的
前提下，将 P0-1.9 的原子 `SpeedEnvelope`、`NumericConstraintFact` 和 `DriverContextFact` 注册到生产
Scenario FactRegistry。Registry 内部真源改为强类型 `EvidenceRecord`，kind 与 provenance 是正交维度，
并保留 approval、精确 SourceRef 和 metadata。支持的稳定命名空间为 `MF.*`、`SCN.*`、`PROJECT.*`、
`METHOD.*`、`DOMAIN_RULE.*` 与 `DERIVED.*`；标识不包含 fact value，重复 ref fail-closed，禁止静默覆盖。

场景候选的 `fact_provenance` 现在贯穿 Candidate Generator → ScenarioCandidate → FactRegistry：项目速度和
驾驶员上下文保持 `PROJECT_INPUT`，未批准 Domain Profile 候选保持 `LEGACY_MIGRATION/PENDING`，不会因
`DIRECT_FACT` kind 被误升级为项目事实。TTC 继续以 `DERIVED.ttc_s` 注册为
`DERIVED_PHYSICS/DERIVED`，并记录 canonical inputs。Approved Rule 仅提供 provider interface 和确定性
测试 adapter；生产没有新增 domain rule。

新增 Evidence Integrity Harness 报告 `project_fact_registration_rate`、
`provenance_preservation_rate`、`source_ref_preservation_rate`、`legacy_authority_leak_count`、
`duplicate_ref_count`，snapshot 默认不包含 fact value。四个独立测试文件覆盖 Speed 3/Numeric 5/Driver 2
的结构性接入契约、SCN 继承、Legacy 不提权、TTC、命名空间、测试 rule provider、重复 ref 和 Harness。
全量回归为 `322 passed, 21 skipped`；未调用 LLM。状态：**P0-2a Evidence Substrate READY**。
下一阶段是 **P0-2b**。

### 11.8 P0-2b scenario-evidence-v2 + Per-support Evidence + Mechanism Application（已完成）

P0-2b 在新的 `src/hara_agent/contracts/` 中建立独立 deterministic contract。依赖方向保持为
semantic/evaluation → contracts；contracts 仅依赖 typed models，不依赖 Provider、workflow 或 evaluation。
`CausalSupport` 为每一个 exact evidence ref 独立保存 canonical `EvidenceKind`，因此同一 edge 可以同时
使用 `DIRECT_FACT` 与 `DERIVED_PHYSICS`，同时 provenance/approval 仍由 P0-2a `EvidenceRecord` 提供。

严格发布策略接受带 SourceRef 的 `PROJECT_INPUT` direct fact；DERIVED 只接受带 canonical inputs 和
derivation type 的 `DERIVED_PHYSICS`；`APPROVED_RULE` 必须为 `FINALIZED/DOMAIN_POLICY`。
`LEGACY_MIGRATION`、`LLM_INFERENCE` 与 `ASSUMPTION` 不能支撑严格 positive causal edge。显式
`MIGRATION_EVALUATION` 仅用于迁移回归，不会批准 pending rule 或非法 derived record。

`CausalMechanismDefinition` 保存 id/version、typed premises、result state、SourceRef、approval 与 provenance；
`CausalMechanismApplication` 保存 `premise_id → evidence_ref` exact bindings。内存 Catalog 验证 mechanism
存在、版本、批准状态、required premise、unknown binding、exact resolution、kind、authority 和 binding 是否
已在 edge supports 中声明。P0-2b 仅包含 `TEST_ONLY` fixture mechanism，没有生产 AVP 因果规则。

Breakpoint 和 positive/negative cross-field invariant 均保留。Differential Harness 的核心 mixed fixture：
v1 返回 `DERIVED_PHYSICS_KIND_MISMATCH`，v2 PASS。生产仍为 `scenario-feasibility-v9` +
`scenario-evidence-v1`；未修改 Prompt、Provider 或 ProjectFacts/Router，也未调用 LLM。四个独立测试文件
覆盖 A–P fixture，全量为 `347 passed, 21 skipped`。状态：
**P0-2b scenario-evidence-v2 deterministic contract READY**。其后续 **P0-2c1** 已在下一节完成。

### 11.9 P0-2c1 Provider Schema Migration（已完成）

P0-2c1 在不覆盖 v9/v1 的前提下，为 `ScenarioFeasibilityAgent` 增加显式 contract mode。默认 `v1`
继续选择 `scenario-feasibility-v9`、`scenario-evidence-v1` 与
`ScenarioFeasibilityAssessmentList`；仅显式 evaluation `v2` 选择 `scenario-feasibility-v10`、
`scenario-evidence-v2` 与 `ScenarioFeasibilityAssessmentV2List`。生产 `HARAApplication` 未切换默认值。

v10 保留 v9 的 Atomic Scenario、structured-fact authority、No New World State、M→B→I→H→Harm、
counterfactual、breakpoint 与 risk-dimension 方法论。唯一主要 schema 迁移是从 hop-level
`basis_type/evidence_refs` 变为有序 `edges[]`、per-record `supports[]` 和
`mechanism_application`。Prompt 中的 `AVAILABLE_CAUSAL_MECHANISMS` 是无 gold bindings 的 canonical JSON；
Evidence Registry snapshot 只暴露 ref、value、kind、provenance、approval 与 metadata，不重新注入完整
Item Definition。

v2 Provider parser 要求精确 envelope、精确 assessment/edge/support/application/risk shape 与 exact batch
coverage；v1/v2 hybrid、missing/duplicate/unknown scenario、unknown/version-mismatched mechanism、非法 binding
全部 typed fail-closed，不做 semantic repair。Positive 必须返回四条有序 edge、非空 dimension/hazard/harm；
negative 只允许 breakpoint 之前的完整 edge prefix，且 dimension/hazard/harm 为空。最终 authority、approval、
derived metadata、premise binding 与 cross-field invariant 仍由 P0-2b deterministic validator 裁决。

Prompt version、assessment contract version 与 deterministic mechanism-catalog SHA-256 组成 contract cache
fingerprint，并写入 Provider request metadata、Scenario evaluation report 与 workflow checkpoint audit。项目当前
没有独立 Scenario semantic cache，因此不存在旧 cache 被复用；将来如建立 cache，必须使用该 fingerprint。
`scenario_smoke.py` 与 `eval_hara.py` 都新增 `--scenario-contract {v1,v2}`，默认保持 v1。

本阶段只使用 synthetic negative、mixed-support positive、invalid fixtures 和 stub Provider；机制均为
`TEST_ONLY`，未新增正式 AVP/domain rule，未读取 `.env`，未调用真实 Provider，也未运行 Full HARA。
全量回归为 `372 passed, 21 skipped, 0 failed`。状态：**P0-2c1 Provider Schema Migration READY**。
其后的 **P0-2c2 Real-provider Semantic Evaluation** 已作为独立阶段执行，结果见下一节。

### 11.10 P0-2c2 Real-provider Semantic Evaluation（已评估，NOT_READY）

P0-2c2 使用 `.env` 中的真实 Provider 做受控 Scenario-only 评估，没有运行 Full HARA。受控 negative
在 3 次相同的 `INVALID_V2_ASSESSMENT_SHAPE` 后按规则提前停止；原始 causal=false/breakpoint 虽与 Gold
一致，但 0/3 schema-valid、0/3 contract-valid，不能作为语义通过。受控 positive 在 3 次
`ScenarioAdaptiveBatchError` 后提前停止，没有可解析 verdict。Order A/B 各 3 次，两个 identity 均无
contract-valid 样本，所有 order agreement 指标均为 `null`，没有用默认 0/1 制造稳定性结论。

有效合并样本共 12 个 logical calls；由于 existing adaptive split，共记录 18 个 Provider wrapper calls、
32 个 transport attempts、14 个 retries，finish reason 为 error 13 / stop 5；另有 1 个中止且未计入正式样本的
logical call。固定 AVP pair 的首次尝试在 Provider 调用前暴露 evaluation-only batching wiring bug：完整
P0-1.9 EvidenceRegistry snapshot 估算 16,362 chars，超过 runner 错用的 12,000-char 上限。该本地上限已提高到
30,000，不修改 Prompt、temperature、thinking、timeout、max_tokens 或 retry policy。修复后重跑被外部调用额度
拒绝，因此 Real AVP v2/v1 与 differential 保持 `NOT_COMPLETED_EXTERNAL_LIMIT`，相关稳定性指标为 null。

最终机器可读报告为 `runtime/evaluation/p0-2c2-summary-20260819-final.json`。结论为
**Provider Schema Stability NOT_READY**、**Controlled Semantic Stability NOT_READY**、Real AVP
**OBSERVATIONAL_ONLY**；Primary blocker 是 `PROVIDER_SCHEMA_INSTABILITY`。当前 catalog 仍为 0 个正式 AVP
mechanism，因此 `KNOWLEDGE_SUBSTRATE_BLOCKED=true`，但没有真实 AVP Provider 样本可用于判断
`CONTRACT_EXPRESSIVENESS_GAP`。生产默认继续保持 v9/v1。全量回归为 `380 passed, 21 skipped, 0 failed`。
下一步候选是 P0-2c3 Prompt/Provider contract optimization，但本阶段没有自动修改 Prompt。

### 11.11 P0 依赖顺序

1. P0-1.5：Source/Context Integrity Harness。**READY，已完成。**
2. P0-1.6：Schema-guided Item Evidence Routing。**READY，已完成。**
3. P0-1.7：Mode-specific ProjectFacts Production Wiring。**READY，已完成。**
4. P0-1.8：Fresh Extraction Grounding Validation。**Validation READY；历史 production blocker 已由 P0-1.9 关闭。**
5. P0-1.9：Schema-guided/targeted extraction 与原子事实规范化。**READY，已完成。**
6. P0-2a：Atomic ProjectFacts → Runtime FactRegistry Integration。**READY，已完成。**
7. P0-2b：Evidence v2/per-support 与 mechanism application deterministic contract。**READY，已完成。**
8. P0-2c1：最小 Provider Schema Migration 与 scenario-feasibility-v10 deterministic/stub validation。**READY，已完成。**
9. P0-2c2：真实 Provider controlled synthetic 与 batch/order evaluation。**NOT_READY；Real AVP v1/v2 因外部额度阻断尚未完成。**
10. P0-2c3：在独立阶段处理 Prompt/Provider contract optimization，并补跑固定 AVP v1/v2 observational differential。
11. P0-3：继续扩展 MethodContract/DomainPolicySnapshot 等最小强类型上下文。
12. P0-5：继续补齐 METHOD_CONTRACT 与批准后的 DOMAIN_POLICY provider；不得伪造生产规则。
13. P0-4：接入 Template Dimensions + Project ODD 的 deterministic lazy filtering。
14. P1/P2：扩大真实 Provider 稳定性测试，最后使用客户 Gold 做 Stage + E2E 验收。

P0-2c2 不得绕过 Provider schema versioning，也不得在没有批准来源时把 TEST_ONLY mechanism 升级为生产规则。

### 11.12 验收与交接

- “95% 工程师还原”和“合理新增场景”分成 Gold Restoration 与 Novel Coverage 两条 Track。
- 验收前必须冻结匹配键、语义等价规则、分母、样本量、置信区间、裁决协议和各 Stage 门槛。
- 无客户多套 Item Definition、Engineer Gold 和版本化指标规范时，不得宣称已经达到 95%。
- 当前目录缺少 `.git`，不能用“最新工作树”作为版本标识；正式交接必须提供 commit/tag，
  或至少提供压缩包文件名、SHA-256、模板/Profile 哈希、Python 版本和完整测试命令。
- 完整可复现命令、哈希、阻断矩阵和交接检查清单见
  [HARA V13 开发交接与可复现基线（2026-08-14）](HARA_V13_HANDOFF_BASELINE_2026-08-14.md)。
