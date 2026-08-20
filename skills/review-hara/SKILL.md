---
name: review-hara
description: Audit HARA inputs, intermediate states, and outputs for engineering validity, traceability, completeness, consistency, domain portability, and release readiness. Use for checker execution, human review preparation, regression comparison, problem-list updates, and formal-versus-draft quality-gate decisions.
---

# Review HARA

## Review order

1. Verify source integrity: functions, outputs, ODD, modes, and profile approval.
2. Verify HAZOP applicability coverage without demanding a full Cartesian product.
3. Verify scenario feasibility, ODD compliance, causal relevance, and risk-difference coverage.
4. Verify one-to-one traceability across scenario, hazardous event, S/E/C/ASIL/FTTI, and Safety Goal references.
5. Recompute ASIL from the input template matrix and compare it with stored results.
6. Verify Safety Goal aggregation, Max.ASIL, most stringent FTTI, and safe-state consistency.
7. Verify report mapping, static-cell merging boundaries, workbook integrity, and release status.

## Findings

For each finding, record severity, affected IDs, evidence, failed rule, remediation, and whether it blocks formal output. Distinguish:

- structural defects;
- unsupported engineering judgments;
- domain-specific assumptions;
- presentation defects;
- compatibility-path defects.

Fail closed for missing ASIL tables, invalid S/E/C values, unresolved required evidence, and unapproved Domain Profiles. A draft may carry explicit warnings; a formal report may not.

