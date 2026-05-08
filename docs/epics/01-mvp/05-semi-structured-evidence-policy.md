# Semi-Structured Evidence Policy

Status: **Planned**
Last updated: 2026-05-08
Epic: 01 MVP
Phase: 05
Related docs: [Roadmap](../../roadmap.md), [Schema Inference](../../schema-inference.md), [Storage Model](../../storage-model.md), [Exports](../../exports.md)

## Intent

Refine how Assquack keeps, promotes, and exports semi-structured source
evidence after the core `VARIANT` staging loop exists.

## Scope

Included:

- Policy for when `_qa_payload VARIANT` is retained in shaped/current tables.
- Promotion helpers for stable fields inside semi-structured payloads.
- Explicit `_qa_raw_json` audit option for lossless source text when required.
- Parquet export behavior for `VARIANT`, including shredding where supported.

Excluded:

- Making raw JSON dumps the default durable storage path.
- Adding storage-policy arguments to the public asset decorator.
- Full incremental history or snapshot mode.

## Architecture Notes

Keep retention and promotion policy behind typed configuration or dedicated
interfaces. The design should preserve raw `VARIANT` evidence for inference
without making every asset carry semi-structured columns forever.

## Implementation Checklist

- [ ] Define retention policy options outside the decorator surface.
- [ ] Add promotion helper interfaces and tests.
- [ ] Add explicit audit-path behavior for `_qa_raw_json`.
- [ ] Test Parquet export behavior for retained `VARIANT` payloads.
- [ ] Document any DuckDB version-specific behavior.
- [ ] Update this status header before handoff.

## Validation

- `pytest`
- type-check command selected during bootstrap
- focused export checks for `VARIANT`
- `git diff --check`

Replace placeholder validation commands with concrete commands once Phase 00
chooses the project tooling.

## Notes

This phase should not reopen the MVP decorator contract. Policy belongs in
configuration or dedicated typed interfaces, not in ad hoc decorator flags.
