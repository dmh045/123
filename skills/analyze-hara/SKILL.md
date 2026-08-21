---
name: analyze-hara
description: Perform evidence-linked HARA semantic analysis after Item Definition approval. Use to judge guideword applicability, derive malfunctions and vehicle-level hazards, generate and screen ODD-valid scenario candidates, evaluate risk-distinguishing S/E/C/FTTI evidence, and propose semantically aggregated safety goals.
---

# Analyze HARA

## Procedure

1. Record every template guideword for coverage, but generate a malfunction only when it is semantically applicable.
2. Derive the malfunction and vehicle-level hazard with a traceable causal chain.
3. Load scenario dimensions from the active MethodContract and constrain them with grounded ProjectFacts; never use a Domain Profile or Python keyword arrays as project facts.
4. Exclude deterministic ODD violations with explicit reasons.
5. Assess remaining candidates for physical feasibility, functional relevance, causal relevance, and meaningful risk variation.
6. Evaluate all feasible, risk-distinguishing candidates before aggregation. Do not enforce a fixed number of scenarios per malfunction.
7. Aggregate only scenarios with an equivalent risk signature; retain covered scenario IDs and the merge rationale.
8. Execute compiled template S/E/C rules against scenario facts and produce concise, source-linked rationales. Mark missing rules or facts `PENDING`.
   Accept canonical facts only when their MethodContract hash matches; resolve
   neither invalid units/values nor equal-specificity context conflicts by precedence guessing.
9. Calculate ASIL only through `MethodContract.asil`, compiled from the active template; never use arithmetic scoring or a copied matrix.
10. Propose Safety Goals by safety intent, highest ASIL, most stringent FTTI, and safe-state semantics rather than one goal per HARA row.

## Guardrails

- Preserve different scenarios when collision object, relative speed, distance, operating state, or controllability changes the risk outcome.
- Treat quantity ranges as diagnostics only.
- Keep LLM judgments separate from deterministic calculation results.
- Keep non-applicable combinations in the applicability audit, not as fabricated HARA rows.
- There is no alternate engineering runtime. Missing MethodContract rules or ProjectFacts must fail closed.
