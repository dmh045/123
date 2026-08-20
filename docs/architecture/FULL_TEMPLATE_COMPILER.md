# Full Template Compiler (P0-2)

## Boundary

P0-2 compiles method data but does not cut over production runtime, call a
Provider, copy a Domain Profile, generate project Safety Goal catalogs, or
delete legacy code.

```text
Template workbook
  -> structural scanner
  -> hash-bound TemplateRoleContract
  -> role-specific deterministic parsers
  -> typed Rule IR
  -> MethodContract
```

The compiler consumes a role plus its discovered region. Sheet names and the
current workbook hash do not select parser behavior.

## Rule classification

Template text is classified before it can be executable:

- `NORMATIVE_RULE` / `NORMATIVE`
- `CRITERION` / `CRITERION`
- `EXAMPLE` / `EXAMPLE`
- `ASSUMPTION` / `ASSUMPTION` or `REFERENCE`, according to source wording
- `WORKFLOW_INSTRUCTION` / `INSTRUCTION`
- `REFERENCE` / `REFERENCE`
- output headers compile to `ReportFieldMapping`

Exposure and controllability examples are non-executable by construction.
They cannot become substring rules or override duration, frequency, or
avoidability criteria.

## Provenance and ambiguity

Every compiled value carries a `SourceRef` containing workbook name,
`template_hash`, sheet, exact range, raw text, and a source hash. Severity
results such as `S0(S1)` remain alternatives; no first/max policy is applied.
The current severity variable `v` is represented as `SPEED_UNSPECIFIED` because
the workbook does not identify ego, relative, impact speed, or delta-V.

`READY_WITH_WARNINGS` means compilation represented the source faithfully and
future execution can fail closed on unresolved cases. `NOT_READY` is used for
blocking conditions such as conflicting normative rules, missing provenance,
or an incomplete ASIL matrix.

## Lifecycle and golden fixture

The Application compiles one MethodContract per run and shares that in-memory
contract with the existing workflow. A persistent deserialization cache is
deliberately deferred until runtime measurements show that it is needed; this
avoids creating a second contract-loading path before production cutover.

The compact generated manifest under `tests/fixtures/method_contract/` is
regression evidence only. It retains section fingerprints, cardinalities,
diagnostic summaries, and a provenance-set fingerprint without duplicating the
compiled workbook content.
