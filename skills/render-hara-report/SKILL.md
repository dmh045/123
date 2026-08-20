---
name: render-hara-report
description: Render a validated HARA state or compatible experience-library data into the supplied Excel template while preserving its structure. Use for report generation, field mapping, hierarchical static-cell merging, Safety Goal sheet population, workbook integrity checks, and overwrite-safe output handling.
---

# Render HARA Report

## Procedure

1. Copy the supplied template; do not create a blank workbook.
2. Validate the template hash and compiled Report Role Contract, then map canonical fields by the discovered headers.
3. Write approved analysis values without recalculating semantic judgments.
4. Populate the discovered HARA output role and Safety Goal output role from validated, aggregated records; sheet names and columns are template data, not runtime constants.
5. Merge only repeated static columns within parent-group boundaries. Never merge dynamic scenario, S/E/C, ASIL, FTTI, or Safety Goal result cells.
6. Keep scenario and rationale text concise, specific, and auditable.
7. Save atomically to the requested fixed output path so a successful run replaces the previous report.
8. Reopen the workbook and validate OOXML integrity, row counts, key IDs, mappings, merges, and cross-sheet references.

## Release behavior

- Refuse formal rendering when the release gate fails.
- Permit draft rendering only when explicitly requested and visibly preserve its draft status.
- Never mutate experience-library values during ingestion; field mapping and presentation belong to the report layer.
- Report a file-lock error without corrupting or partially replacing the last valid workbook.
