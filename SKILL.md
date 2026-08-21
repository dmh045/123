---
name: hara-v13
description: Project entry point for compiling templates, extracting project facts, running HARA, reviewing evidence, and rendering reports.
---

# HARA V13

Route the task to the narrowest applicable project skill:

- End-to-end run or resume: `skills/hara-orchestrate/SKILL.md`
- Item extraction: `skills/extract-item-definition/SKILL.md`
- HARA analysis: `skills/analyze-hara/SKILL.md`
- Quality review: `skills/review-hara/SKILL.md`
- Excel rendering: `skills/render-hara-report/SKILL.md`

## Authority

1. The active template compiles to one hash-bound `MethodContract` containing workflow, scenario ontology, S/E/C rules, ASIL matrix, SG/Safe-State method, and `ReportContract`.
2. The Item document compiles to source-grounded, template-independent `ProjectFacts` and `RiskFacts`.
3. `MethodRiskFactBinding` maps neutral facts to the active method. Exact ontology names bind automatically; ambiguous mappings require human confirmation and are cached by template hash.
4. Deterministic services execute compiled rules. The LLM may only perform bounded, evidence-linked interpretation.
5. Missing evidence, ambiguous rules, or pending approval blocks a formal report.

Unsupported or unapproved facts remain `PENDING`; deterministic execution must fail closed.

The repository has one production runtime: `python -m hara_agent`. Do not add parallel engines, compatibility wrappers, fixed sheet coordinates, copied template rules, or hidden defaults.
