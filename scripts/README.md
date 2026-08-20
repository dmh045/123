# scripts目录状态

该目录是V13迁移期实现，不是目标Agent架构。

当前正式主链：

```text
main_executor.py
  → hara_controller.py
  → malfunction_engine.py
  → scen_hazevent_engine.py
  → scoring_sg_engine.py
  → report_generator.py
```

旧路径：`hara_engine.py`仅作为17步Legacy Fallback。新业务规则不得继续添加到该文件。

`*_output.json`和`scripts/output/`是迁移前运行生成物，不属于源码。当前主链已经统一覆盖写入
`runtime/current/`，正式Excel写入用户指定的`output/`路径。

Agent重构目标与迁移映射见：

- `docs/architecture/HARA_AGENT_REFACTOR_PLAN.md`
- `docs/architecture/LEGACY_CODE_INVENTORY.md`
- `docs/architecture/HARDCODED_RULE_AUDIT.md`
