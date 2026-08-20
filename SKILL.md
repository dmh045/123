---
name: hara-v13
description: Project-level entry point for running, reviewing, compiling, or rendering HARA analyses in this repository. Route work to the corresponding project Skill while keeping template method rules, grounded project facts, and deterministic execution separate.
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

1. The approved HARA template, compiled into a `MethodContract`, supplies the
   method, S/E/C rules, ASIL matrix, Safety Goal/Safe State method, and report
   contract.
2. Current project source documents, compiled into grounded `ProjectFacts`,
   supply project facts.
3. Deterministic generic services execute rules, validation, IDs, aggregation,
   quality gates, and workbook rendering.
4. The LLM performs bounded, source-linked semantic interpretation only.
5. Human approval resolves explicit ambiguity and engineering review gates.

Mark unsupported facts and judgments `PENDING`. Block formal reports when
required evidence is unresolved or the compiled method is missing, ambiguous,
conflicting, or invalid. A generated Template Role manifest binds role
locations to `template_hash`; it is not a third customer-maintained business
input and contains no copied engineering rules.

## Current implementation status

The Domain runtime and three-phase/17-step scripts remain `MIGRATION_ONLY` for
baseline comparison until Template-Driven cutover. Do not add new business
rules to them and do not treat them as future authority. P0 Template Role
discovery and P0-2 full MethodContract compilation live under
`src/hara_agent/template/`. P0-3a compiles and injects one MethodContract into
the existing Application/WorkflowGraph; it does not create a second workflow.
The remaining Domain candidate/scoring/Safety Goal dependencies are explicit
release blockers until their in-place generic replacements are complete.
