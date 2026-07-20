# Scenario: json-csv

**Target purpose.** Teach an agent to convert between a JSON array of objects and CSV,
faithfully and reversibly.

**Rubric (revision-only, never shown as a test):**
- Emit a header row from the union of object keys, in a stable order.
- Quote fields containing commas, quotes, or newlines (use the `csv`/`json` modules).
- Represent missing keys as empty cells, not the string "None".
- Round-trip: JSON -> CSV -> JSON preserves values (as strings) for flat objects.

**Contingency:** high — depends on heterogeneous keys, embedded commas/quotes, missing fields.
