# V13旧路径与清理清单（MIGRATION_ONLY Deletion Input）

> 本文列出的 Runtime 只保留到 Application Cutover，用于基线对照。不得新增规则，
> 也不得建立新的兼容层。当前删除计划见
> `TEMPLATE_DRIVEN_GOVERNANCE_MIGRATION.md`。

## 状态定义

- `ACTIVE`：当前三阶段正式主链正在使用。
- `MIGRATE`：功能有效，但需要拆分并迁入Agent服务层。
- `LEGACY`：只为兼容历史路径保留，不再新增规则。
- `ISOLATE`：与HARA主链无依赖，应移出核心目录。
- `DELETE`：已确认无调用、重复生成或调试残留，可清除。

## 路径清单

| 路径 | 状态 | 依据与处理 |
|---|---|---|
| `scripts/main_executor.py` | MIGRATE | 当前总入口，同时混合CLI、经验库、路由、进度和Fallback；拆入Agent应用层 |
| `scripts/hara_controller.py` | MIGRATE | 当前三阶段主编排，但以subprocess和固定JSON通信；由LangGraph替代 |
| `scripts/malfunction_engine.py` | MIGRATE | 主链Phase 1；拆分Function/Malfunction/Hazard服务 |
| `scripts/scen_hazevent_engine.py` | MIGRATE | 主链Phase 2；拆分候选生成、筛选、场景事实和Hazard Event |
| `scripts/scoring_sg_engine.py` | MIGRATE | 主链Phase 3；拆分S/E/C、ASIL、FTTI、SG和Safe State |
| `scripts/logic_checkers/` | MIGRATE | 主链质量门；结构Checker保留，领域规则迁入Profile |
| `scripts/report_generator.py` | MIGRATE | 当前报告主链；拆分映射、渲染、模板契约和质量水印 |
| `scripts/asil_matrix.py` | ACTIVE | 模板`ASIL_Table`权威查表，迁移时保持语义不变 |
| `scripts/ftti.py` | MIGRATE | 候选计算可复用；公式和参数必须迁入批准Profile |
| `scripts/document_parsers/` | MIGRATE | 可复用读取能力；删除项目专用表头/功能名硬映射 |
| `scripts/experience_library.py` | MIGRATE | 可选输入源；取消绝对路径和分析旁路 |
| `scripts/hara_engine.py` | LEGACY | 17步重复实现；冻结后迁入`legacy/v13_17step`，不得新增规则 |
| `scripts/example.py` | LEGACY | 使用`/home/workspace`及旧17步入口；重写后迁入`examples/` |
| `scripts/build_hara_gap_brief.py` | ISOLATE | 一次性汇报文档生成器；迁入`tools/`或外部工作区 |
| `mcp-servers/` | ISOLATE | 当前HARA代码零依赖；若不实现HARA专用MCP，应从产品仓库移除 |
| `AGENTS.md`、`SKILL.md` | ACTIVE | 外部Agent入口说明；不得继续承载运行时业务规则 |
| `scripts/*_output.json` | DELETED | 历史运行生成物已删除，现统一覆盖到`runtime/current/` |
| `scripts/output/` | DELETED | 历史运行文件已删除，空目录不再作为输出入口 |
| `check_ftti.py` | DELETED | 仅打印模板列的临时调试脚本，无生产调用，已删除 |
| `hara_pdf.txt` | DELETED | 临时派生文本，无生产调用，已删除 |

## 已完成的路径收敛

- `hara_controller.py` 的阶段 JSON 与断点文件已改为固定覆盖
  `runtime/current/`，不再把新运行产物写入 `scripts/`。
- `main_executor.py` 的断点文件也已迁入 `runtime/current/`。
- `main_executor.py agent` 已作为显式薄适配入口接入 `src/hara_agent`；原`analyze`默认路由尚未切换，
  因此旧三阶段链路和显式Fallback仍保留。
- 主入口默认正式报告已由 `scripts/output/` 改到根目录 `output/`。
- Legacy Fallback 的上下文导出已改到 `runtime/current/`，正式报告严格使用
  用户传入的 `--output` 路径。
- `scen_hazevent_engine.py` 已取消在脚本目录、仓库根目录和当前工作目录中
  猜测上游 JSON；`--input` 现在必须显式提供。
- 迁移前的 `scripts/*_output.json` 与 `scripts/output/*` 已在真实 AVP 冒烟通过后删除。
- 已建立 `src/hara_agent/` 强类型核心，并由旧控制器通过兼容层调用。
- AVP Safety Goal、功能分类与对应 Safe State 已从
  `scoring_sg_engine.py` 迁入 `config/domains/avp/profile.json`。
- Profile 明确记录版本、来源和 `migration_baseline` 状态；正式质量门会阻断，
  `--allow-draft` 下转为警告。

## 本轮已清理的无调用代码

| 文件 | 删除内容 | 证据 |
|---|---|---|
| `scenario_checker.py` | `find_missing_scenarios`、`_generate_suggestion`、`check_and_prompt_supplement` | 全仓库仅定义和内部互调，生产质量门未调用 |
| `malfunction_engine.py` | `_is_invalid_combo`、`_get_invalid_combinations` | 全仓库无调用，实际适用性由`MalfunctionChecker`执行 |
| `report_generator.py` | `_get_json_value`、`_copy_cells`、`_adjust_column_widths` | 全仓库无调用，已有正式映射和格式化实现 |

## 暂不删除但高度重复的代码

1. `scen_hazevent_engine.LocalScenarioChecker`：导入失败时静默使用另一套规则。目标是删除该副本并对依赖错误Fail-Fast。
2. `hara_engine.py`中的17个Step类：与三阶段引擎重复，迁移期间仅用于显式Legacy回归。
3. `scoring_sg_engine.py`与`hara_engine.py`中的S/E/C和Safe State规则：必须先迁入统一服务和Domain Profile。
4. `ScenarioRef.json`、`HazardRef.json`、`ScorabilityRef.json`、`SafetyStateRef.json`：混合多个子系统，迁移前不能直接删除。
5. `report_generator.py:test_report_generator`及各Parser内嵌测试：应迁入`tests/`，但需先保留测试样本。

## 清理验收规则

任何删除必须同时满足：

1. 全仓库静态引用为零。
2. 不属于AGENTS/SKILL/setup声明的公开入口。
3. 不被动态导入、subprocess或配置文件引用。
4. 删除前后单元测试和AVP回归结果一致。
5. 在Legacy迁移完成前，不以“代码重复”为理由直接删除唯一Fallback实现。

## 2026-08-07 默认路由切换前复核

- 新链路已可通过`main_executor.py agent`显式运行，并有`agent-doctor`只读检查。
- `main_executor.py analyze`仍直接引用`hara_controller.py`；Controller仍通过subprocess引用三个旧Engine，
  旧Engine输出仍由`report_generator.py`消费。
- `hara_engine.py`仍是`fallback`公开命令及自动Fallback的唯一实现。
- `experience_library.py`仍由旧`analyze/info`路径动态导入。
- 因此当前没有任何上述整文件同时满足“静态引用为零、动态引用为零、公开入口为零”三个删除条件；
  本轮不删除旧生产文件。
- 默认路由切换的前置条件尚未满足：AVP Profile为`migration_baseline`且真实LLM配置缺失。
  在`agent-doctor`达到Draft readiness前不得切换；在Profile获批且Release readiness为true前不得移除旧Fallback。
