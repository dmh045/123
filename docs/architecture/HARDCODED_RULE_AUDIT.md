# HARA V13 硬编码与可扩展性审计（Historical Findings）

> 将规则迁入 Domain Profile 的旧整改方向已被
> `TEMPLATE_DRIVEN_GOVERNANCE_MIGRATION.md` 取代。问题证据仍有效；未来方法规则
> 只能来自 active Template/MethodContract。

## 1. 结论

当前项目同时混合了四类逻辑：

1. **通用确定性逻辑**：模板读取、ASIL 查表、ID、字段关联、质量门、Excel 渲染。
2. **项目输入事实**：ODD、功能、速度、对象、相对距离、运行模式、FTTI 假设。
3. **Domain 工程规则**：AVP 场景维度、碰撞对象、S/E/C 判据、安全状态和 SG 分类。
4. **文本启发式/兜底模板**：关键词匹配、跨子系统示例、默认车速、默认危害文本。

问题不在于“存在规则”，而在于第 2～4 类目前散落在核心 Python 文件中，且缺少
domain、版本、来源和审批状态。换一个 Item 后，程序仍可能运行，但会产生“格式正确、
工程语义错误”的结果。

## 2. 高优先级硬编码

| 优先级 | 位置 | 当前行为 | 风险 | 迁移目标 |
|---|---|---|---|---|
| P0 | `logic_checkers/scenario_checker.py:30` | 默认读取个人电脑绝对路径下的场景库 | 换机器或换项目后静默失效 | 删除默认绝对路径；场景库必须由 RunConfig/Domain Profile 显式注入 |
| P0 | `scenario_checker.py:71-119` | 在 Checker 内写死道路、速度、天气兼容关系和失效严重度类别 | Checker 同时生成工程判断，无法跨 Domain | Checker 只校验结构与证据；兼容规则迁入版本化 Domain Profile |
| P0 | `scenario_checker.py:733-905` | 写死制动、转向、雨刮等危害事件文本 | 已发生跨子系统 Wiper 污染；换项目会继续误配 | 删除跨子系统文本表；危害候选由 Agent + domain tools 生成，Checker 仅检查主体/行为/伤害链 |
| P0 | `scen_hazevent_engine.py:1194-1643` | 核心引擎直接分支到 AVP，并写死 5 km/h、车尾泊入、垂直车位、对象速度及 E 假设 | AVP 规则无法版本化；非 AVP Item 不可复用；输入缺失时默认值会伪装成事实 | 拆为 `domains/avp/scenario_rules`；所有默认值带 source/confidence/status，未批准时为 PENDING |
| P0 | `scoring_sg_engine.py:780-895` | Python 中固定 7 个 AVP SG 与安全状态 | SG 归并口径与项目架构强耦合，变更需改代码 | 迁入版本化 AVP policy；保留 Python 聚合算法，定义由批准配置/知识库提供 |
| P0 | `scoring_sg_engine.py:936-980` | Safe State 先关键词匹配共享规则，再用 AVP 文本兜底 | 共享规则仍可跨 Domain 污染 | Domain 必须先确定；只允许在同 Domain 规则集中匹配；无匹配进入 PENDING |
| P0 | `main_executor.py:277-442` | 命中经验库后直接追加修改全局 ScenarioRef/HazardRef | 一次项目运行会改变后续项目行为，结果不可复现 | 经验库只读；增量经验进入候选区，经审批、版本化后发布 |
| P0 | `main_executor.py:642-705` | 控制器失败后自动进入旧 17 步 Fallback | 新旧口径可能在一次运行中切换，报告来源不透明 | Agent Graph 显式路由；旧引擎只允许用户显式 `--legacy`，不得静默接管正式输出 |

迁移进度（2026-08-07）：

- `scoring_sg_engine.py` 中 7 个 AVP Safety Goal、功能映射和对应 Safe State
  已迁入 `config/domains/avp/profile.json`，逐字段回归一致。
- AVP 检测关键词、基础场景名称、默认速度、天气、路面、车位、距离、候选驾驶员控制上下文、
  加减速度及转向误差已迁入同一 Profile；不再把单一“驾驶员车外”状态写入全部Agent场景。
- 行人、10 km/h车辆、30 km/h车辆、固定障碍物和安全静止候选已迁入
  `AVPDomainPolicy`；相对速度由声明式 `zero/ego/ego_plus_target` 模式计算。
- Template的S/E/C等级定义已从`Severity`、`Exposure`和`Controllability`读取；Profile仅保留
  AVP风险事实到等级的`risk_mapping_policy`。Exposure显式区分`T`时间占比法和`F`频率法，
  Controllability使用结构化驾驶员位置/直接控制权限，不再从自由文本关键词给分。
- 上述场景参数和映射仍标记为`PENDING/migration_baseline`；缺少项目Exposure数据或Profile未批准时，
  不能据此发布正式报告。

## 3. S/E/C 与工程事实耦合

| 优先级 | 位置 | 问题 | 正确边界 |
|---|---|---|---|
| P0 | `document_parsers/excel_processor.py:563-564` | 用“高速/城市”等关键词直接给 E4/E3 | Parser 只能抽取场景事实，不能给工程结论 |
| P0 | `scoring_sg_engine.py:46-76` | 碰撞类型和速度区间直接映射 S | 可保留为某个已批准 injury model，但必须有模型版本、适用范围和证据；否则 PENDING |
| P0 | `scoring_sg_engine.py:306-520` | 道路/天气/速度关键词直接推断 E，并包含固定暴露比例 | E 应来自项目场景频率/IFFT/运行时间数据或批准的 Domain 数据集 |
| P0 | `scoring_sg_engine.py:524-754` | C 由通用关键词和可控动作模板推断 | C 应结合人员位置、接管通道、反应时间、制动距离、可感知性和 Domain modifier |
| P1 | `logic_checkers/scenario_checker.py:1088-1290` | Checker 再次根据文本速度推断风险/合理性 | 去除重复评分；Checker 校验输入值、证据引用和规则版本 |

ASIL 不属于上述可配置 Domain 规则：本项目必须且只能读取本次输入模板的
`ASIL_Table`。Domain Profile 不得复制或覆盖 ASIL 矩阵。

## 4. 共享 JSON 参考库问题

当前 `ScenarioRef.json`、`HazardRef.json`、`ScorabilityRef.json`、
`SafetyStateRef.json` 同时承担：

- 通用知识；
- 子系统关键词；
- 项目经验；
- 默认兜底；
- 运行时自动追加内容。

这会造成来源不明、规则互相覆盖和跨项目污染。目标拆分为：

```text
knowledge/
├─ standards/                  # 标准定义，只读
├─ domains/
│  ├─ avp/
│  │  ├─ profile.yaml
│  │  ├─ scenario_rules.yaml
│  │  ├─ severity_policy.yaml
│  │  ├─ exposure_policy.yaml
│  │  ├─ controllability_policy.yaml
│  │  └─ safety_policy.yaml
│  └─ wiper/...
├─ projects/<project-id>/      # 本项目批准事实，只读快照
└─ candidates/                 # Agent 新发现经验，未审批不得用于正式计算
```

每条规则至少包含：`rule_id`、`domain`、`version`、`source`、
`applicability`、`approval_status` 和 `effective_date`。

## 5. 自动化、Agent 与人工判断的边界

### 保留为确定性服务

- 文档与模板读取；
- schema 校验和外键校验；
- 14 Guideword 列表及每项适用性记录完整性；
- ASIL_Table 查表；
- FTTI 公式计算（前提参数必须有来源）；
- ID、去重、Max.ASIL、SG 外键；
- 质量门和 Excel 格式。

### 交给 Agent 节点

- Function/Output/ODD/运行模式的语义抽取；
- Guideword 适用性候选及理由；
- Malfunction、Hazard、Hazardous Event 候选；
- 场景相关性和风险差异解释；
- SG 文本候选与语义归并建议。

### 必须来自输入、批准规则或人工确认

- ODD 边界和速度包络；
- 对象、相对速度、车距、泊入方向、车位类型；
- 场景发生频率/IFFT 与 E；
- 伤害模型、碰撞速度阈值与 S；
- 人员位置、接管时间、可感知性、制动距离与 C；
- FTTI budget、安全状态可实现性。

缺失时不得使用无来源默认值生成 FINAL 结果；应输出
`PENDING + missing_evidence + review_reason`。

## 6. 已确认的重复或隔离代码

- `scen_hazevent_engine.py` 内的 `LocalScenarioChecker` 与正式
  `logic_checkers/scenario_checker.py` 职责重复。
- `scen_hazevent_engine.py` 同一文件内存在两套
  `BEHAVIOR_MAPPING/SPECIFIC_BEHAVIOR_RESULTS/INJURY_MAPPING`。
- `hara_engine.py` 重复实现 17 步 S/E/C/SG，应冻结到 legacy，不再双向修补。
- `report_generator.py` 内含演示场景数据，不应参与运行时逻辑。
- 根目录回归测试依赖 `output/p006...p018` 历史生成物；固定夹具应迁入
  `tests/fixtures`，运行输出不得作为测试真值。

## 7. 清除顺序

1. 先建立 DomainProfile、RunConfig 和强类型 State。
2. 为现有引擎增加 adapter，使新旧流程使用同一输入输出契约。
3. 把 AVP 与跨子系统硬编码移入 domain rules，并做等价回归。
4. 将 Checker 改为纯验证器，删除生成/补写逻辑。
5. 禁止经验库修改共享参考库。
6. 关闭自动旧 Fallback，仅保留显式 legacy 命令。
7. 在调用图、回归和真实 AVP 样例均通过后，再删除重复实现与历史生成物。
