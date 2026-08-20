# MethodContract golden fixture

The JSON file in this directory is a generated fingerprint manifest for the
current workbook hash. It is not a production truth source and is not a third
customer-maintained input.

Production compilation always starts from the workbook and its hash-bound
`TemplateRoleContract`. The Application compiles once per run and does not
load this test snapshot.
