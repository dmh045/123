---
name: hara-v13
description: Project-level entry point for running, reviewing, or rendering HARA analyses in this repository. Use for complete HARA requests and route each stage to the corresponding project Skill while keeping project facts, domain rules, and deterministic calculations separate.
---

# HARA V13 Agent Entry

Use this file as a compatibility entry point for Codex/OpenCode. Do not treat it as a runtime business-rule source.

## Route work

- Full run, resume, or release: read and follow `skills/hara-orchestrate/SKILL.md`.
- Item Definition extraction: read and follow `skills/extract-item-definition/SKILL.md`.
- Malfunction, hazard, scenario, S/E/C/FTTI, or Safety Goal analysis: read and follow `skills/analyze-hara/SKILL.md`.
- Quality audit or problem-list update: read and follow `skills/review-hara/SKILL.md`.
- Excel generation or formatting: read and follow `skills/render-hara-report/SKILL.md`.

Read only the stage Skill needed for the current request. For an end-to-end run, let `hara-orchestrate` select the remaining Skills.

## Authority order

1. Current project source documents supply project facts.
2. The supplied Excel template supplies the report contract and the only authoritative `ASIL_Table`.
3. The selected, versioned, approved Domain Profile supplies domain rules and defaults.
4. Deterministic services perform validation, IDs, ASIL lookup, aggregation, quality gates, and workbook rendering.
5. Skills define agent workflow and review behavior; they do not override the sources above.

Mark unsupported facts and judgments `PENDING`. Block formal reports when required evidence is unresolved, the Domain Profile is unapproved, or the template ASIL matrix is missing or invalid.

## Current implementation status

The typed Agent core and AVP Domain Profile are under `src/hara_agent/` and `config/domains/avp/`. The current production-compatible execution still uses the three-phase controller while service extraction and graph orchestration are being migrated. Treat `scripts/hara_engine.py` as a legacy compatibility path, not the target architecture.
