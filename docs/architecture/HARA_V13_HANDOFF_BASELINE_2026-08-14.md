# HARA V13 开发交接与可复现基线（2026-08-14）

## 1. 文档定位

本文档是当前项目的可执行交接基线，补充 `HARA_AGENT_REFACTOR_PLAN.md` 的目标架构说明。
两类内容必须分开理解：

- 本文档记录当前工作区能够验证的事实、命令、阻断和下一步依赖关系。
- 重构方案记录目标边界和迁移方向，不代表所有组件已经进入默认生产路径。
- 项目事实以输入 Item Definition 为准；方法契约和 ASIL 以本次输入模板为准；
  Domain 规则只允许来自版本化且获批的 Domain Profile。

## 2. 基线标识

| 项目 | 当前基线 |
|---|---|
| 日期 | 2026-08-14 |
| Python | 3.12.10（项目要求 `>=3.12`） |
| 版本控制 | 当前交接目录不含 `.git`，无法提供 commit/branch；正式发布前必须补充 VCS 标识 |
| HARA 模板 | `references/HARA_Template_AI_20260327.xlsx` |
| 模板 SHA-256 | `F3D2E4F7A16CBFDA2D9E30C5F437ED94BB1B51AFFD635DC73215B8D307FB87EF` |
| AVP Profile | `config/domains/avp/profile.json`，版本 `1.3.0-atomic-scenario` |
| Profile SHA-256 | `5A953A0E3DF3DE6FDFAE6B72DDB3771C5D5CE750160DCC8F506CE28F3641F69A` |
| Profile 审批状态 | `migration_baseline`，未批准，只允许 Draft |
| Scenario assessment contract | `scenario-evidence-v1` |
| 测试基线 | `312 passed, 21 skipped` |

若模板或 Profile 内容变化，必须更新哈希、测试结果和本文件日期。当前目录缺少 Git 元数据时，
不得使用“最新工作树”作为可审计版本标识。

## 3. 当前运行路径

| 路径 | 命令 | 实际定位 | 是否默认生产路径 |
|---|---|---|---|
| 三阶段 Controller | `main_executor.py analyze` | 当前生产兼容主链；经验库命中后走经验导出，否则走 controller | 是 |
| Typed Agent | `main_executor.py agent` | 新状态、确定性服务、同步 `WorkflowGraph`、checkpoint 和 Draft 质量门 | 否，迁移期显式入口 |
| 17-step Engine | `main_executor.py fallback` | controller 超限后的兼容 fallback，也可手动触发 | 否 |
| LangGraph | 无 | 目标编排架构；当前未安装、未接入 | 否 |

`WorkflowGraph` 是依赖无关的同步 runner。不得把它描述为已使用 LangGraph，也不得把 typed Agent
的完成度描述为默认生产路由已经切换。

## 4. 可复现命令

### 4.1 环境与模板只读检查

```powershell
.\.venv\Scripts\python.exe .\scripts\main_executor.py agent-doctor `
  --domain avp `
  --template .\references\HARA_Template_AI_20260327.xlsx
```

当前无 LLM 环境变量的工作区预期结果：

- Template：通过；14 个 Guideword、5 类场景维度、完整 S/E/C 标准。
- Domain Profile：可加载，但 `migration_baseline` 未批准。
- LLM：失败；缺少 `HARA_LLM_BASE_URL`、`HARA_LLM_MODEL`、`HARA_LLM_API_KEY`。
- `ready_for_draft=false`，`ready_for_release=false`。

Scenario Harness 的 `READY` 只表示 Harness 基础能力可用，不等价于环境、Agent 或 Release READY。

### 4.2 全量测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

期望结果：

```text
312 passed, 21 skipped
```

`pytest.ini` 必须排除 `.pytest-tmp-*`、`pytest-cache-files-*`、`runtime/` 和 `tmp/`，避免运行产物
进入测试收集。任何基线更新必须记录完整命令，而不能只记录结果数字。

### 4.3 Typed Agent Draft

```powershell
.\.venv\Scripts\python.exe .\scripts\main_executor.py agent `
  --domain avp `
  --item .\input\ItemDef.docx `
  --template .\references\HARA_Template_AI_20260327.xlsx `
  --output .\output\HARA_Report_Output.xlsx `
  --run-id avp-current `
  --max-workers 4 `
  --allow-draft
```

该命令需要先配置 LLM。`--allow-draft` 不会把 `PENDING` 升级为已批准状态。

## 5. 已验证的当前实现

- Guideword 的具体值从 `04_HAZOP` 动态读取；当前方法契约显式要求 14 个唯一 Guideword。
  “模板驱动”不等于数量无限制，也不应把 14 散落成 AVP 业务魔数。
- ASIL 只通过输入模板 `ASIL_Table` 查表，不使用数值求和或 Domain 默认矩阵。
- 主 Item/Function 抽取主动排除 performance、driver 和 exposure 重证据字段，再由局部证据路由补抽取。
- `ItemDefinitionFacts` 已有 mode-specific `SpeedEnvelope[]`，全局 `speed_min_kph/speed_max_kph`
  仅保留为兼容聚合字段；performance/driver/exposure 仍是弱类型列表。
- `ProjectFactResolver` 已接入 Application：正式链路要求显式结构化 `operating_mode`，按模式选择
  `SpeedEnvelope.max_kph`；未知模式默认 Fail-Closed。aggregate/legacy fallback 均需显式授权，并保留
  machine-readable resolution diagnostics、SourceRef、approval 与 provenance。
- Extraction Integrity Harness 已支持稳定 Gap taxonomy 和 5 项字段/来源/上下文指标；
  `scripts/eval_hara.py --stage extraction` 是统一评估入口，不调用 LLM。
- `ItemEvidenceRouter` 已采用 `FactRetrievalSpec`、全局确定性评分和 coverage-first bounded assembly；
  保持 5000 字符预算，并输出结构化 diagnostics 和带 identity 的 source blocks；Supplement locator
  确定性回绑为 `SourceRef`，无法解析时显式计数而不猜测。
- Template Scenario Dimensions 已读取和审计，但尚未成为 Candidate Generator 的 deterministic input。
- Candidate Generator 当前遍历 AVP Profile 的 `risk_candidates`，再扩 atomic variants 与 driver contexts。
- `scenario-evidence-v1` 已实现 exact ref、basis kind、breakpoint、cross-field invariant 和 bounded TTC 校验。
- FactRegistry 已使用强类型 `EvidenceRecord`；kind 与 provenance 正交保存，`scenario.facts` 会继承项目输入或迁移候选的 provenance、approval 与 SourceRef。
- `scenario-evidence-v2` deterministic contract 已位于独立 `contracts` 层；per-support kind、authority、approval、mechanism version/premise binding 均由 Python 校验。生产仍使用 v1/v9。
- `scenario-feasibility-v10` Provider schema migration 已作为显式 evaluation mode 接入：结构化 mechanism catalog、
  authority-aware Evidence Registry snapshot、strict v2 parser、exact coverage、typed errors 与 contract/catalog
  fingerprint 均已覆盖；production 默认仍为 v9/v1。
- Scenario Evaluation Harness 已包含 machine-readable contract errors、有效样本不足时的 null 稳定性指标，
  以及按 `scenario_id + semantic_fingerprint` 对齐的比较 API。

## 6. 当前阻断

| 阻断 | 影响 | 完成判据 |
|---|---|---|
| Source/Context Integrity Harness | 已完成；可区分抽取、路由、归一化、方法、迁移与源缺失 | 继续保持独立 Stage 回归 |
| mode-specific ProjectFacts 生产接线 | 已完成；Parking/Search/Control 分别解析为 5/30/7，未知模式 Fail-Closed | 保持 production integration 与 mode-collapse regression |
| `project_evidence` 路由覆盖 | 已修复；真实七项后部证据均进入 2226 字符 context，ROUTING_MISSED 7→0 | 保持独立 routing regression |
| Fresh extraction 原子事实契约 | 已完成；fresh run 007 生成 Speed 3 + Numeric 5 + Driver 2，10/10 grounded 且 SourceRef accuracy=1.0 | 保持 atomic schema、exact SourceRef 与 extraction-only regression |
| Template Scenario Dimensions 未接入过滤 | Prompt 声称的 ODD 过滤与生产实现不完全一致 | 维度、ODD、模式和失效相关性进入确定性 lazy filtering |
| Evidence authority/provenance 缺失 | 已由 P0-2a 关闭；Profile candidate 保持 `LEGACY_MIGRATION/PENDING` | 保持 Registry/Harness 独立回归，禁止静默提权 |
| 每 hop 单一 `basis_type` | P0-2b contract 与 P0-2c1 Provider schema 已关闭；mixed support 在显式 v2 可表达，v1 默认行为保持 | P0-2c2 保持 real-provider differential regression |
| Causal mechanism application contract 缺失 | 已建立第一层 deterministic boundary：approved mechanism + valid premise bindings | 不得将其误称为任意工程因果真值证明 |
| AVP Profile 未批准 | 正式报告必须 Fail-Closed | 工程批准后的版本化 Profile，并通过 release doctor |
| Gold Benchmark 缺失 | 无法证明 95% | 多套客户输入、Engineer Gold、裁决协议和版本化指标规范 |

## 7. 下一阶段依赖顺序

P0 工作不按单纯编号并行展开，应遵循以下依赖：

1. **P0-1.5 Source/Context Integrity Harness**：已完成，状态 READY。
2. **P0-1.6 Schema-guided Evidence Routing**：已完成，状态 READY；ROUTING_MISSED 7→0。
3. **P0-1.7 Mode-specific ProjectFacts Wiring**：已完成，状态 READY；fresh typed fixture 的 production NORMALIZATION_LOSS=0。
4. **P0-1.8 Fresh Extraction Grounding Validation**：验证完成；历史 blocker 已重新归因为 3 项 missing target semantic + 7 项 atomicization failure，并由 P0-1.9 关闭。
5. **P0-1.9 Schema-guided extraction**：已完成，状态 READY。Fresh run `p0-1-9-fresh-20260817-007` 为 cache miss；targeted category 恰好 3 次；10/10 typed、grounded、SourceRef/context/normalization 全部 1.0，resolver 为 30/7/5，无 fallback。
6. **P0-2a Atomic ProjectFacts → Runtime FactRegistry**：已完成，状态 READY。原子 ProjectFacts 已进入生产 Registry；SCN provenance/approval/SourceRef 保真；Legacy 不提权；TTC 保持 DERIVED。
7. **P0-2b scenario-evidence-v2 deterministic contract**：已完成，状态 READY。A–P fixture、mixed v1/v2 differential、strict authority 和 mechanism binding 全部通过；全量 `347 passed, 21 skipped`。
8. **P0-2c1 Provider Schema Migration**：已完成，状态 READY。v10/v2 strict schema、synthetic positive/negative、
   invalid fixtures、versioning/fingerprint、CLI compatibility 与 stub Harness 已通过；全量
   `372 passed, 21 skipped, 0 failed`；未调用真实 Provider。
9. **P0-2c2 Real-provider Semantic Evaluation**：已评估，状态 NOT_READY。Synthetic negative 0/3
   schema/contract valid（`INVALID_V2_ASSESSMENT_SHAPE`）；positive 0/3（`ScenarioAdaptiveBatchError`）；
   Order A/B 无 valid cross-order sample，agreement 指标为 null。固定 AVP runner 的本地 batch 上限错误已修复，
   但修复后的 v2/v1 调用被外部额度阻断，Real AVP observational differential 尚未完成。Primary blocker 为
   `PROVIDER_SCHEMA_INSTABILITY`；生产仍为 v9/v1。
10. **P0-2c3**：独立进行 Prompt/Provider contract optimization，并补齐固定 AVP v1/v2 observational run；
    不得把本阶段失败样本修复后混用。
11. **P0-3/P0-5**：继续扩展 MethodContract/DomainPolicySnapshot 和经批准的 provider；不得创建未批准的生产规则。
12. **P0-4 Scenario Expansion**：接入 Template Dimensions + Project ODD 的 deterministic lazy filtering。
13. **P1/P2**：扩大真实 Provider 稳定性测试，最后使用客户 Gold 做 Stage + E2E 验收。

P0-2c2 的机器可读合并报告为 `runtime/evaluation/p0-2c2-summary-20260819-final.json`；本轮共纳入
12 个 completed logical calls、18 个 Provider wrapper calls、32 个 transport attempts 和 14 个 retries，
全量回归为 `380 passed, 21 skipped, 0 failed`。后续补跑必须保持相同 identity、fingerprint、ProjectFacts、
Provider/model 配置与独立 run ID，并在批准来源缺失时拒绝将 TEST_ONLY mechanism 用于生产。

## 8. 95% 验收规范最低要求

“95% 工程师还原”与“合理新增场景”必须分成两条 Track，且在运行前冻结评估规范。

### 8.1 Gold Restoration Track

- 定义比较单位：Function、Guideword、Malfunction、Atomic Scenario、Hazard、S/E/C、ASIL、Safety Goal。
- 定义稳定匹配键和语义等价裁决规则，禁止只用数组位置或自由文本完全相等。
- 分别报告 precision、recall、exact agreement；不得用单一 accuracy 掩盖漏项。
- S/E/C 与 ASIL 必须报告 exact agreement，ASIL 不允许“接近正确”。
- 明确 Stage Harness 与 E2E Harness 的分母、样本量、项目覆盖和置信区间。

### 8.2 Novel Coverage Track

- AI-only 场景必须由至少一名独立 HARA 工程师裁决；争议项进入复核而不是自动计分。
- 分别报告 Valid Novel Precision、Valid Novel Count 和覆盖的新增风险机制。
- Novel Track 不得反向降低 Gold Restoration 的分数，也不得把 Gold 标签写入 Domain Policy。

在指标、样本量和裁决协议未版本化前，不得宣称项目已经达到 95%。

## 9. 交接检查清单

- [ ] 提供 Git commit/tag；若只能交付压缩包，提供文件名、SHA-256 和生成时间。
- [ ] 运行 `agent-doctor`，保存 JSON 输出并区分 Draft/Release readiness。
- [ ] 运行完整 pytest 命令并记录 Python、模板/Profile 哈希和结果。
- [ ] 确认 `scenario-evidence-v1` 仍是 production semantic baseline；v2 未静默替换。
- [ ] 确认 AVP Profile 是否仍为 `migration_baseline`。
- [ ] 不把旧 AVP Item Definition、Profile candidate 或 migration fixture 当作新项目 Truth Source。
- [ ] 出现 evidence missing 时先完成 Gap 分类，不直接向客户追加输入。
- [ ] 出现 causal failure 时先查看 typed error、有效样本数和上下游来源，再决定改 Contract、Rule 或 Prompt。
- [ ] 更新本文档的日期、哈希、测试基线、当前阻断和 P0 依赖顺序。
