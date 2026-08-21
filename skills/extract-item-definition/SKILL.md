---
name: extract-item-definition
description: Extract and validate a typed, traceable HARA Item Definition from DOCX, PDF, and template inputs. Use when identifying vehicle-level functions, outputs, preconditions, triggers, ODD constraints, operating modes, fallback behavior, consequences, performance parameters, and source evidence before malfunction analysis.
---

# Extract Item Definition

## Procedure

1. Read paragraphs and tables without treating merged-cell display fragments as independent functions.
2. Extract typed candidates: Function, Output, Precondition, Trigger, ODD Constraint, Operating Mode, Fallback Behavior, Consequence, and Performance Parameter.
3. Give each value a source location, confidence, status, and extraction version.
4. Keep the concise function name separate from its output and supporting description.
5. Build the ODD envelope from all operating modes and sub-phases, while preserving mode-specific limits.
6. Run structural and semantic validation before releasing functions to HAZOP analysis.
7. Emit risk-fact candidates only through the strong typed, template-independent
   contract with exact source evidence. Compile a separate MethodRiskFactBinding
   for the active template; bounded LLM output remains `PENDING` until approved.

## Rejection rules

- Reject headings, sentence fragments, numbered conditions, consequences, and quality requirements presented as functions.
- Reject a Function and Output that merely copy the same full paragraph.
- Mark contradictory or unsupported project facts `PENDING`; do not fill them from generic defaults.
- Do not infer road type, weather, speed, object, or driver state from hard-coded keyword lists.

## Required output

Return typed values plus source evidence and an extraction audit. The output must distinguish grounded ProjectFacts from template examples, MethodContract rules, LLM inference, and engineering assumptions. Missing facts remain `PENDING`.
