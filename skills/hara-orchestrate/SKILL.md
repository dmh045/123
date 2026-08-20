---
name: hara-orchestrate
description: Orchestrate an end-to-end, auditable HARA run across item extraction, malfunction and hazard analysis, scenario analysis, risk assessment, review gates, and Excel rendering. Use for complete HARA runs, resumptions, reruns, or workflow-status decisions; delegate stage-specific work to the corresponding HARA skill and deterministic project services.
---

# Orchestrate HARA

## Workflow

1. Validate the item document, Excel template, output path, and selected Domain Profile.
2. Invoke `extract-item-definition` and stop for review when required functions, outputs, ODD, modes, or sources are pending.
3. Invoke `analyze-hara` to derive applicable malfunctions, hazards, risk-distinguishing scenarios, S/E/C/FTTI evidence, and safety-goal candidates.
4. Invoke `review-hara` at the Item Definition, HAZOP, risk, and release gates.
5. Determine ASIL only through the input template `ASIL_Table` service.
6. Invoke `render-hara-report` only after the release decision permits the requested output class.
7. Persist stage, inputs, profile version, evidence status, findings, and artifact paths in `HARAState`.

## Routing rules

- Treat `FINALIZED`, `PENDING`, `REJECTED`, and `NOT_APPLICABLE` as explicit states.
- Resume from the last valid checkpoint; do not infer completion from the presence of JSON files.
- Allow draft output when explicitly requested, but retain pending markers and warnings.
- Block formal output if required evidence is pending, the Domain Profile is unapproved, or the template ASIL matrix is invalid.
- Never use record-count targets as deletion rules.
- Never silently switch to the legacy 17-step engine. Record any compatibility-path use in the audit trail.

## Responsibility boundary

- Use Skills for workflow and semantic task instructions.
- Use Domain Profiles for approved domain/project knowledge and applicability policies.
- Use deterministic services for schema validation, IDs, matrix lookup, aggregation, quality gates, and workbook rendering.
- Use the LLM for evidence-linked semantic extraction, candidate generation, applicability explanations, and concise engineering language.

