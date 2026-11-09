# MVP simplification decisions

**Final decisions and implementation record.** Updated 9 November 2026.

Baseline: replace-only MVP, incorporating Maitland's NDJSON fragment direction
and MAD.Prefect compatibility. Rowan approved the reductions below; this record
does not claim Maitland requested them or authorise further implementation.

| Area | Verdict and current behaviour | Implementation |
| --- | --- | --- |
| Multiple export destinations | **Removed.** One configured destination or `None`; no destination lists or cross-destination collision checks. Keep fingerprints, authentication, multiple formats, manifests and cached export reattempts. | `c96c0ac`; net 19 production lines removed. |
| Unused `chunk_size` | **Removed.** No runtime behaviour lost; MB-based NDJSON fragment sizing remains. | `6f3c1e3`; six production lines removed. |
| Single-entry strategy registries | **Keep both.** Schema policy and materialization/publication selection remain separate extension points for future work. | No removal. |
| Deep argument/configuration snapshots | **Simplified.** Use ordinary Python references and resolve identity at execution before the producer runs. Preserve independent derived options and original templates. | `1b3e6d5`; net 95 production lines removed. |
| Credential-provider abstraction | **Removed.** Read credentials directly from the process environment. The single-destination MVP has no demonstrated non-environment credential-source requirement; custom sources are deferred and can be revisited in Git history. Keep authentication methods, input validation, fingerprints and per-operation credential capture. | Implemented in this change; net 48 production lines removed. |
| Persistent stage tracking | **Simplified.** Keep publication `status`, `export_status`, overall `outcome`, `failed_stage` and error details. Remove the four other stage-status columns and progress writes; keep the readable five-stage pipeline. Hard interruptions before export no longer identify the exact stage. | Implemented in this change; validation recorded in the request/run phase. |

## Argument and configuration decisions

The original MAD.Prefect safeguard prevents sibling assets from overwriting each
other's formatted options. That remains. The additional per-call snapshot
promises were broader than that safeguard and have been removed.

| Scenario | Agreed behaviour |
| --- | --- |
| Caller changes a nested argument before awaiting | The asset uses the changed value when execution starts. Treating this as a bug was debatable; ordinary reference semantics were chosen. |
| Caller changes configuration before awaiting | The asset uses configuration as it stands when execution starts. Rowan did not consider this a bug. Do not mutate shared configuration during active execution. |
| Producer modifies a supplied list or dictionary | The caller sees that mutation. Asset identity and resolved names/paths were calculated before producer execution and do not change with it. |

The request remains a simple bound-call record. No replacement copying framework,
compatibility shim or historical-argument snapshot was introduced. Export operations
still capture their resolved paths and authentication inputs for the operation.

## PR handoff

The previous reductions are implemented and reviewed. The additional stage-column
simplification preserves publication success, export outcomes and caught failure
details. Keep the default connection, `current_publications` and native
DuckDB-inferred types. No fragment-restoration cache or further removal is agreed.

Validation evidence is recorded in the relevant phases:
[exports](../epics/01-mvp/04-export-compatibility.md),
[fragment configuration](../epics/01-mvp/02-replace-materialization.md), and
[request/run pipeline](../epics/01-mvp/08-request-and-run-pipeline.md).
Current contracts: [Exports](../exports.md), [Configuration](../configuration.md),
[Argument behaviour](../developer-api.md#call-arguments-and-execution).
