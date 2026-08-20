# Scenario Evidence v2 Deterministic Contract

## Status

P0-2b is **READY** as a deterministic contract and evaluation substrate. P0-2c1 has added an
explicit Provider migration path, `scenario-feasibility-v10` + `scenario-evidence-v2`, for evaluation.
Production still defaults to `scenario-feasibility-v9` + `scenario-evidence-v1`.

## Causal evidence layers

```
FactRegistry
  -> what information is available

Evidence Support
  -> which exact EvidenceRecord values an edge cites

Causal Mechanism Definition
  -> an approved, versioned state-transition rule and its typed premises

Mechanism Application
  -> exact premise_id -> evidence_ref bindings for this edge

Causal Verdict
  -> deterministic contract validation plus the separate semantic assessment
```

Evidence existence is not evidence entailment. In v2's first deterministic boundary, a required positive
edge is accepted only when every support resolves exactly with matching kind and permitted authority, and a
FINALIZED `DOMAIN_POLICY` mechanism has complete, kind-correct premise bindings. This validates application
structure; it does not prove arbitrary engineering causal truth.

## Contracts

- `CausalSupport`: `evidence_ref` plus canonical `EvidenceKind`. Provenance remains a separate axis.
- `CausalEdgeV2`: stable M→B→I→H→Harm edge identity, claim, per-support evidence, and mechanism application.
- `CausalMechanismDefinition`: id/version, premises, result state, sources, approval and provenance.
- `CausalMechanismApplication`: mechanism id/version and exact `premise_id -> evidence_ref` bindings.
- `RiskDimensionChangeV2`: reuses `CausalSupport`; it does not introduce a second evidence type system.

`STRICT_RELEASE` rejects assumptions, `LEGACY_MIGRATION`, `LLM_INFERENCE`, pending domain rules, malformed
derived evidence and unapproved mechanisms. `MIGRATION_EVALUATION` is explicit and only relaxes migration
authority inspection; it does not approve pending rules or malformed derivations.

## Catalog boundary

`CausalMechanismCatalog` exposes deterministic listing, `get(id, version)` and
`validate_application(...)`. P0-2c1 serializes the listed definitions into the controlled
`AVAILABLE_CAUSAL_MECHANISMS` prompt section without gold bindings. Its SHA-256 fingerprint uses canonical
id/version/premise/approval/provenance material. P0-2c1 tests use only `TEST_ONLY` mechanisms; the project
contains no new AVP braking, collision or pedestrian causal knowledge.

## Compatibility and differential evaluation

The v1 implementation is unchanged. A mixed `DIRECT_FACT + DERIVED_PHYSICS` fixture intentionally produces:

- v1: `DERIVED_PHYSICS_KIND_MISMATCH`
- v2: valid, when both supports and an approved fixture mechanism bind correctly

Negative assessments retain breakpoint and cross-field behavior: only the validated edge prefix before the
first unsupported transition requires mechanisms; later edges are not fabricated. `causal=false` requires no
risk dimensions or hazard outputs, while `causal=true` requires all four edges, dimensions and hazard outputs.

## P0-2c1 Provider migration boundary

`ScenarioFeasibilityAgent` accepts an explicit `assessment_contract` mode. The default `v1` selects the
unchanged v9 prompt, v1 parser and `ScenarioFeasibilityAssessmentList`. Explicit `v2` selects v10,
`ScenarioFeasibilityAssessmentV2List`, the authority-aware Evidence Registry snapshot and the controlled
mechanism catalog.

The v10 change is limited to the machine contract: v1 hop-level `basis_type/evidence_refs` becomes ordered
`edges[]`, per-record `supports[]` and `mechanism_application`. Atomic Scenario, structured-fact authority,
No New World State, M→B→I→H→Harm, counterfactual, breakpoint and risk-dimension principles are preserved.

The v2 parser fails closed on extra/missing shapes, v1/v2 hybrids, missing/duplicate/unknown assessments,
unknown mechanisms, version mismatch and invalid bindings. It performs no semantic repair. Positive results
require all four ordered edges plus dimensions/hazard/harm. Negative results allow exactly the valid edge
prefix before the breakpoint and require empty dimensions/hazard/harm.

Prompt version, assessment contract version and mechanism-catalog fingerprint are combined into a stable
contract fingerprint and recorded in Provider request metadata, evaluation reports and workflow audit events
(there is currently no independent Scenario semantic cache). This prevents a future cache implementation
from treating v9/v1 and v10/v2 artifacts as interchangeable.

P0-2c1 was validated only with deterministic/stub Provider fixtures. No `.env` secret was read and no real
Provider was invoked. The complete regression result is `372 passed, 21 skipped, 0 failed`.

## P0-2c2 real-provider evaluation result

P0-2c2 kept the prompt, Provider generation parameters, v2 contract and production v9/v1 default frozen.
The controlled negative returned the raw expected false verdict and breakpoint in three attempts, but all
three violated the v2 assessment shape. The controlled positive produced three adaptive-batch failures and
no parseable assessment. Both batch orders completed three logical repeats, but neither order produced a
contract-valid cross-order sample; agreement and stability metrics therefore remain null.

The fixed AVP request exposed an evaluation-runner batching defect before any AVP Provider request was sent:
the 16,362-character registry-backed request exceeded a locally inherited 12,000-character limit. The
evaluation-only limit is now 30,000. A clean v2/v1 rerun was then refused because the external Provider usage
allowance was exhausted, so the real AVP observation and v1/v2 differential are explicitly not completed.
The rejected local attempts are not presented as Provider behavior.

The consolidated report is `runtime/evaluation/p0-2c2-summary-20260819-final.json`. Its status is Provider
Schema Stability **NOT_READY**, Controlled Semantic Stability **NOT_READY**, and Real AVP Scenario Evidence
**OBSERVATIONAL_ONLY / NOT_COMPLETED**. The primary blocker is `PROVIDER_SCHEMA_INSTABILITY`. The empty formal
AVP mechanism catalog is still reported as `KNOWLEDGE_SUBSTRATE_BLOCKED`, while
`CONTRACT_EXPRESSIVENESS_GAP` cannot be concluded without valid real AVP observations. Full regression:
`380 passed, 21 skipped, 0 failed`.

## Deferred after P0-2c2

- P0-2c3 prompt/Provider contract optimization using a new run identity
- completion of the fixed-pair v9/v1 versus v10/v2 observational differential
- engineering approval and population of any production mechanism catalog
