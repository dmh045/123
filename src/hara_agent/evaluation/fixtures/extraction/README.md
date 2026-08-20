# Extraction integrity fixtures

Fixtures are evaluation inputs, never production truth sources.

- `synthetic/`: small deterministic contract fixtures used by unit tests.
- `legacy_avp/`: regression evidence grounded in the repository's
  `input/ItemDef.docx`. Every expected fact has a real `DocumentReader`
  block locator and excerpt.
- `customer_gold/`: reserved for separately approved customer gold sets.

The harness reads expected facts from these files but never writes them into
`ItemDefinitionFacts`, prompts, Domain Profiles, or scenario candidates.
