# Extraction integrity fixtures

Fixtures are evaluation inputs, never production truth sources.

- `synthetic/`: small deterministic contract fixtures used by unit tests.
- `customer_gold/`: reserved for separately approved customer gold sets.

The harness reads expected facts from these files but never writes them into
`ItemDefinitionFacts`, prompts, Domain Profiles, or scenario candidates.
