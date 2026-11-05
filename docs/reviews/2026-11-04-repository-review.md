# Repository review — 4 November 2026

Review status: COMPLETE

Assquack's main path is understandable: capture, infer, publish, then export.
Keep that shape and the native DuckDB result contract. The most valuable changes
are to explain the query-name traversal, distinguish a current publication from
historical run metadata, and remove leftover file locking and unused data shapes.
Three reproduced correctness defects need narrow fixes; none justifies snapshots,
migrations, a new execution framework, or speculative recovery machinery.

This is a review, not an implementation. Recommendations below are ordered by
readability and maintenance value. Correctness severity is recorded separately:
P1 means incorrect returned data in ordinary use; P2 means a concrete integrity
or development-contract failure; P3 means misleading documentation. “None” means
no runtime defect established. Readability priority takes precedence over severity
in this document's ordering, not permission to lose documented behavior.

## Basis and stage-one adjudication

Reviewed HEAD: `7d135159da5f2e4f5b8876fd51c65cfbb701f20f`.
The pre-existing working tree contained modified `docs/README.md` and untracked
`docs/reviews/design-review-remit.md`. Those edits are preserved.

Stage two began only after correctness chat
`01a1a4dc-8f6a-7d70-ab6a-f36baca41c26` completed and its report contained
`Review status: COMPLETE`. Its report, probes and logs remain under
`.tmp/visible-repo-review-2026-11-04/correctness/`. The conclusions below were
checked against source and documented contracts, rather than accepted from test
counts alone. Stage two did not rerun its runtime probes.

| Stage-one item | Decision | Reason and final disposition |
| --- | --- | --- |
| C01: cache can return another argument's rows | **Accept, simplify implementation scope** | `_cache.py:20` combines asset run history with mere table existence. The probe reports argument 1's run while returning argument 2's rows. Use one current-publication association, shared with C02; DF-002. No history snapshots or per-row cache scans. |
| C02: old result exports false provenance | **Accept defect; reject stale-result failure alternative** | `_result.py:66-80` writes the current table but records old result fields. Results deliberately refer to current tables. Resolve the actual publication and export it; rejecting an old result would unnecessarily narrow that behavior. DF-002. |
| C03: reserved metadata target can be replaced | **Accept** | The synthetic target `assquack_meta.exports` replaces the manifest. These schemas are explicitly reserved. One check after template resolution and sanitization is justified; no permissions subsystem. DF-007. |
| C04: declared development environment fails tests | **Accept; separate required and optional dependencies** | The UDF test fails because NumPy is missing. Declare its test dependency. A complete native-input validation environment should also include pandas/Arrow, without making them runtime dependencies. Optional runtime support is not itself broken by a skip. DF-009. |
| C05: strict typing fails | **Accept debt; defer exact generic-call annotation** | Five known source diagnostics are real, but not newly introduced runtime bugs. Two further diagnostics concern absent pandas typing. Fix local type mismatches; demonstrate an honest callable annotation before choosing it. Do not replace the runtime API to satisfy Pyright. DF-008. |
| C06: bound-argument example fails | **Accept** | `orders.with_arguments(customer="acme")` cannot bind to either preceding zero-argument `orders`. Supply a self-contained customer source. DF-006. |
| C07: overview contradicts schema/storage behavior | **Accept, broaden documentation reconciliation slightly** | Current-run inference and retained local fragments are implemented. The overview also retains obsolete writer language; other active pages contain similar stale promises. Correct the prose, not the implementation. DF-006. |

All seven findings have a disposition. None of the seven underlying observations
was unsupported. Rejected remedies and suspicions are listed explicitly below;
disagreement is based on consequences, not on manufacturing a rejection quota.

## Prioritized findings

### DF-001 — Make query qualification readable in its own module

Readability priority: **1**. Correctness severity: **None established**.

`src/assquack/_query/general.py:66-267` combines SQL parsing, parameter conversion,
catalog discovery, caller-frame inspection, CTE scope handling and SQL rebuilding.
The three nested helpers `table_name`, `table_argument`, and `qualify` obscure
which ones return a value and which mutate the syntax tree. `visible`, `tables`,
and `replacements` omit what their sets contain. The nested conditional at line
78 makes even parameter preparation harder to read than the behavior requires.

**Smallest remedy:** retain the single traversal and its nearby nested helpers,
rename them as in the naming table, and use ordinary `if/elif/else` for parameter
normalization. Separate the discovery, traversal and final binding paragraphs
with the comments below. If the discovery paragraphs still distract, extract
only caller replacement-name collection into one helper with its existing
`finally: del frame`. Do not create a visitor class, general SQL abstraction,
custom parser or typed model for DuckDB's entire serialized syntax tree.

The apparent special cases have concrete contracts: a CTE anchor cannot see its
own recursive name; physical tables win collisions with Python/file scans;
`query_table` parameters can also occur elsewhere in the statement. Removing
those cases would violate the final resolution rule in
[phase 07](../epics/01-mvp/07-default-connection.md#final-resolution-rule-correction).
The existing CTE comments are useful and should remain.

**Acceptance:** run `test_scoped_query_resolution.py`, `test_general_query.py`,
`test_query_chaining.py`, and `test_default_connection.py`; retain native relations,
caller parameter values, quoted names, recursive anchors and collision behavior.
This is an internal readability change, with no public API change.

### DF-002 — Give the current publication one explicit identity

Readability priority: **1**. Correctness severity: **P1 cache; P2 export provenance**.

The code uses “latest successful asset run” and “the rows currently occupying this
physical table” as though they were interchangeable. They are not:

```python
@asset("main.shared", config=config)
def shared(value):
    return [{"value": value}]

first = await shared(1)
second = await shared(2)
again = await shared.cache_first()(1)
```

Stage one's synthetic probe observed only two source calls. `again.cached` was
true and its reported run was `first.run_id`, but its rows contained value 2 and
`second.run_id`. `_api.py:217-229` makes arguments part of asset identity;
`_cache.py:20-25` checks that identity's history and table existence separately;
`commit.py:43-59` replaces the shared table.

The second probe retained a one-row result, refreshed the table to two rows, then
called `old_result.export_parquet(...)`. The file contained the two new rows,
while `_result.py:66-80` recorded the old run and row count of 1. Lazy reads of new
rows are documented; the false manifest is the defect.

**Smallest coherent remedy:** maintain one current-publication record per
catalog/schema/table, identifying its asset and successful run. Update it inside
the existing replacement transaction. Obtain row count and publication time from
that run, rather than duplicating a second metadata history. Cache lookup must
match the requested asset to the current publication, then apply TTL. Explicit
export must resolve that same current publication under `database_operation` and
record its asset/run/count, even when invoked through an older result. Keep the
result object's original run fields as execution metadata and explain that its
table reference remains live. Do not silently mutate a frozen result.

This is a proposed storage-model amendment, not a claim that the new record already
exists. Choose its exact DDL during implementation. A per-table association is
necessary because an asset ID can also survive a table-option change when its
name is explicit; the mutable `assets` row alone is not publication history.
One `_qa_run_id` row cannot represent an empty publication. Do not scan all rows,
add snapshots, invalidate every other asset's history, or assume extraction order
is publication order. Arbitrary external SQL replacement remains outside managed
publication; no database-tamper detection is proposed.

**Acceptance:** alternating arguments sharing one table; two functions sharing a
table; a table-option change with fixed explicit name; empty replacement; failed
replacement retaining the prior association; old-result export recording the
actual current asset/run/count. Preserve original TTL on cache/export retry.
Update the storage, cache and result-lifetime docs together.

### DF-003 — Remove the remaining file lock, not the connection lock

Readability priority: **2**. Correctness severity: **None established**.

`_result.py:63-66` takes `writer_lock` only for explicit Parquet exports. Automatic
exports and table publication instead use `database_operation`. The standalone
`_storage/locking.py` implements synchronous polling, asynchronous nesting,
ContextVar ownership and operating-system file locking for an architecture that
now explicitly uses one process-lifetime connection. Its subprocess test proves
the standalone lock works; it does not prove multi-process Assquack publication.

**Remedy:** remove this call and the now-unused lock module. Keep the connection
lock around explicit export and its manifest. Remove only the standalone file-lock
tests (`test_writer_lock.py:40,81` and `test_fragment_inputs.py:200`); preserve the
overlapping-capture test at `test_writer_lock.py:11`, relocating/renaming that file
to describe connection coordination. Change “writer lock” in
`retention.py:22` and `metadata.py:88` to “shared connection lock”.

This follows [the current single-process contract](../storage-model.md#concurrency).
It does not remove protection from publication transactions or imply that direct
external DuckDB calls are coordinated. Retain the cancellation and nested-asset
tests, which exercise real behavior.

### DF-004 — Remove data shapes that no longer carry useful information

Readability priority: **2**. Correctness severity: **None established**.

`ProjectionSpec.source_field` (`models.py:37`) is constructed as a JSON path in
`schema.py:40-44`, but staging reads only `column_name` and `duckdb_type`.
The only source-field consumer outside its construction is an assertion in
`tests/test_schema_inference.py:32`. The apparent JSON-path mapping capability
does not exist in publication. `StagingTables` (`models.py:43`) wraps just one
`TableReference`, requiring repeated `staging.shaped` access without expressing
another invariant.

**Remedy:** remove `source_field` and its JSON-path construction/imports; retain
tests proving unusual column names survive inference and publication. Pass the
candidate `TableReference` directly to commit and cleanup, named `candidate_table`.
Keep the typed projection dataclass and strategy interfaces. This removes unused
representation, rather than replacing useful typed boundaries with dictionaries.

Separately, `_config.py:130` accepts and validates `chunk_size`, but no production
consumer reads it. The docs already say it does not control capture. Recommend
removing this ineffective setting and its environment alias in the pre-release
API, with the related config/tests/docs updated together. This public deletion is
a **decision to approve during implementation**, not a behavior fix authorized by
this review. Do not repurpose it to change streaming or fragment boundaries.

**Acceptance:** existing inference, failed-cast, empty-input, unusual-name and
publication tests; existing byte-boundary tests remain unchanged in meaning.

### DF-005 — Share the small export-and-manifest operation

Readability priority: **2**. Correctness severity: **None beyond DF-002**.

`pipeline.py:237-271` and `_result.py:66-83` separately spell out destination
writing and the same manifest fields. The duplication helped the two callers
choose different provenance and locking rules. The shared DuckDB writer itself
is already correctly centralized.

**Remedy:** once DF-002 supplies truthful publication metadata, use one small
internal `write_export_and_record` operation for a single destination/target and
publication. Keep cache retry selection in the pipeline and explicit export
destination fallback in the result method. The helper should write first and
record success second. Do not add an export manager, retry scheduler, new public
export API or another writer interface. If sharing requires a large context
object or merely moves two long argument lists, retain the short loops and share
only the manifest construction.

**Acceptance:** partial output success remains recorded, failed/unattempted
outputs receive no manifest, cached retries skip completed targets, explicit
exports still work when automatic exports are disabled. No remote transaction
or stronger atomicity guarantee is implied.

### DF-006 — Give readers one current story

Readability priority: **2**. Correctness severity: **P3**.

The overview is the first reading path, but `overview.md:32-35` discourages the
local fragments the implementation requires, line 78 promises cross-run schema
observation, and lines 68-70 retain the old writer model. Its schema link also
suggests raw VARIANT evidence is mandatory. The focused schema/lifecycle pages
describe the current behavior more accurately.

Reconcile the active prose in a small docs change:

- Describe bounded local NDJSON evidence, current-run inference, selective durable
  VARIANT columns, and the shared connection lock in the overview. Keep historical
  decisions in the journal/epics, clearly labeled as superseded.
- Fix `developer-examples.md:89` with a customer-accepting asset that returns an
  `amount` field. Use a table such as `sales.orders_{customer}` so the binding
  example also explains physical table placement. Mark `rows`/`api_pages()`
  snippets as sketches rather than implying they are standalone programs.
- Replace “may be sync or async” at `developer-api.md:163-165` with the actual
  contract: invoking an asset is awaited; its producer may be sync or async.
  Remove the misleading SELECT-validation sentence at line 214. Do not restore
  SELECT-only prevalidation to make the old text true.
- `configuration.md:109` says multiple database files are future work, although
  the same page documents implemented named attachments. State one write
  destination per config and multiple configs per shared engine.
- `storage-model.md:240` suggests a replacement filesystem fallback. Remove that
  implication from current scope; native DuckDB writing is the agreed boundary.
- Rename `test_retained_snapshot_allows_same_table_replacement_and_nested_publication`
  at `tests/test_general_query.py:91` to describe a retained **lazy relation**.
  Its assertion reads the replacement value 9 (projected as 10); “snapshot” teaches
  the opposite lifetime guarantee.

Historical phase validation counts are dated evidence, not claims that today's
environment passes. Do not rewrite them to today's count or reopen completed
phases merely because a new review found separate defects. During implementation,
track the new changes in the relevant phase as required by repository workflow.

**Acceptance:** execute the corrected binding example offline with synthetic
rows, compare the current API/lifecycle paragraphs, run documentation link and
terminology checks. No architecture diagram is needed for this review itself;
the existing lifecycle and connection diagrams already express the flow.

### DF-007 — Enforce the reserved schema boundary once

Readability priority: **3**. Correctness severity: **P2**.

The successful stage-one call to `@asset("assquack_meta.exports")` replaced the
manifest columns with `_qa_run_id`, `_qa_loaded_at`, and `oops`. This is direct
evidence, not a hypothetical permissions problem. `_resolve_table` at
`_api.py:279-285` sanitizes the target without enforcing the documented reservation.

Reject resolved, sanitized schemas `assquack_meta` and `assquack_stage` at that
boundary, before source execution. The check must run after templates resolve so
templated and differently spelled inputs cannot evade it. Keep the two names
explicit; do not add a configurable reserved-name registry, metadata repair, or
a ban on general trusted SQL. Test both schemas, sanitized/template forms, and
preservation of the existing manifest. No new public capability is involved.

### DF-008 — Repair type contracts locally and honestly

Readability priority: **3**. Correctness severity: **P2 development contract**.

The configured strict check has five known source errors: `_api.py:65,108,239`
and `_templates.py:118,123`. Use a typed dictionary factory at line 65 and a shared
structured-export type at the request boundary rather than pretending a TypedDict
is `Mapping[str, str]`. Place the shared declaration where it does not cause a
runtime import cycle; do not add a second near-identical export model.

For nested assets, make the argument-inspection collaboration explicit in the
internal API and use a narrow generic annotation where necessary. Do not invent a
public method solely to avoid a protected-member diagnostic. The existing cycle
guard remains useful for nested argument graphs.

The `ParamSpec` plus inserted `assquack_config` keyword cannot be fixed by an
arbitrary cast that claims to preserve every callable signature. Keep runtime
binding and the approved override. Compare the smallest honest callable annotation
against typed consumer examples for positional arguments, keyword-only arguments,
sync/async producers, bound copies and config override. The exact annotation is
deferred until that check; a localized documented typing limitation is preferable
to an elaborate runtime decorator framework or globally disabling strict checks.

The two additional `_query/asset.py:54` diagnostics must be retested with the
optional DataFrame dependency available; do not patch runtime query behavior to
mask an incomplete development environment.

### DF-009 — Make the documented test environment reproducible

Readability priority: **3**. Correctness severity: **P2 development contract**.

`pyproject.toml:25-28` declares pytest, Pyright and Ruff, but the unconditional
capture UDF at `tests/test_fragment_inputs.py:403` requires NumPy in the reviewed
DuckDB environment. Stage one finished with **427 passed, 8 skipped, 1 failed**.
The skipped tests are pandas/Arrow accepted-input routes.

Declare NumPy as a development/test dependency and document a complete input-test
environment containing pandas and Arrow. Keep optional integrations optional for
library users. Do not skip the UDF test merely to obtain green output: it protects
the concrete no-view/no-pre-execution contract. A future simpler synthetic oracle
is reasonable only if it proves the same two properties. Update the lock through
normal dependency tooling during implementation; no dependencies changed here.

**Acceptance:** install the documented development groups, run the full suite with
no unexplained optional-input skips, then run typing with that interpreter. The
historical formatting exception in `docs/mvp_requirement_observations/questions/query_connections.py`
is a minor tidy-up, not a functional failure or a reason for repository-wide churn.

## Concrete naming and paragraph comments

Line references refer to the reviewed working tree before implementation. These
are targeted recommendations, not a request to rename every short local variable.

| Target | Suggested name | Why |
| --- | --- | --- |
| `_query/general.py:117` `table_name` | `qualify_table_name` | Returns a quoted, qualified name rather than merely extracting a name. |
| `_query/general.py:132` `table_argument` | `qualify_query_table_argument` | Names the special SQL function and transformation. |
| `_query/general.py:182` `qualify` | `qualify_table_references` | Makes the in-place traversal's subject explicit. |
| `_query/general.py:117,132,182` `visible` | `visible_cte_names` | Scope contains CTE names, not every visible database object. |
| `_query/general.py:97,110` `replacements`, `tables` | `python_relation_names`, `physical_relation_names` | The latter includes views; both are names, not row objects. |
| `_query/general.py:261` `consumed` | `inlined_parameter_names` | These parameters were replaced in the tree and no longer need binding. |
| `_api.py:214` `table_override` | `resolved_table_name` | This is the rendered required table, not an optional override. |
| `_materialization/pipeline.py:128` `committed` | `publication_committed` | Explains why later export failure must not mark the run failed. |
| `_materialization/models.py:43` / pipeline `staging` | `candidate_table` after removing wrapper | One typed candidate table exists. |
| `_materialization/json_reader.py:84,87,90` `pending`, `start`, `end` | `record_bytes_so_far`, `segment_start`, `newline_index` | Explains the cross-buffer record-length calculation. |
| `_templates.py:134` `_SafeFormatDict` | `_PartialFormatArguments` | “Safe” is vague; missing placeholders are preserved, not sanitized. |

Suggested paragraph comments, to add only where that reasoning is still needed
after simplification:

| Before target | Suggested comment |
| --- | --- |
| `_query/general.py:85` catalog discovery | `# One user catalog permits short physical names. With several, require an explicit catalog rather than guessing from the selected config.` |
| `_query/general.py:133` parameter handling | `# Table-name parameters must be qualified before DuckDB binds the query. Inline only this occurrence; another occurrence may still need the original value.` |
| `_query/general.py:260` binding cleanup | `# Drop bindings only when every occurrence was inlined. Reused parameters must keep their original caller value.` |
| `_materialization/pipeline.py:177` publication completion | `# The table is now durable. Export or cleanup failures must not turn this successful publication into a failed extraction.` |
| `_materialization/json_reader.py:87` buffer scan | `# A record may span buffers. Carry its byte count forward and exclude CR only when it is immediately followed by the newline delimiter.` |
| `_templates.py:98` nested arguments | `# Direct arguments take precedence. Nested asset arguments fill missing names, with the first encountered nested value winning.` |
| `_storage/metadata.py:103` retention query | `# Keep one successful extraction and three failed attempts independently; publication time orders successes, while start time orders failures.` |
| `_exports/destinations/filesystem.py:109` target identity check | `# A target is valid only for the resolution that captured its authentication. Equal-looking targets from an older operation must not reuse these credentials.` |

Keep the existing explanations for direct COPY replacement scans, generator
cleanup, JSON size recovery, Hive inference, transaction ownership and retention
path checks. They already explain non-obvious requirements well. Avoid comments
that just announce “validate”, “loop” or “return”.

## Rejected recommendations and retained complexity

| Proposal considered | Decision and evidence |
| --- | --- |
| Delete schema/commit registries because only one mode exists | **Reject.** EXT-05/EXT-06 explicitly record approved selection boundaries and run-local strategy resources. Preserve them; remove unused payload around them (DF-004). Do not impose public injection or implement append/merge now. |
| Replace export composition with direct local/Azure branching | **Reject.** The 27 October decision explicitly retains preparation/authentication registries and internal provider injection. The worker-thread bridge serves the documented async-load/sync-export contract. It is not proof a public provider API is needed. |
| Freeze old results or forbid all old-result exports | **Reject.** Lazy current-table references are intentional. Correct metadata via DF-002, without snapshot storage or a new stale-result rule. |
| Add cache snapshots, automatic repair, migrations or retry orchestration | **Reject.** The demonstrated fault needs a current publication association. History and compatibility machinery would expand scope without solving a separate requirement. |
| Remove all runtime validation because types already constrain inputs | **Reject as a blanket rule.** Asset/config boundaries accept Python runtime values; reserved schemas can destroy metadata; URI and option checks protect explicit export contracts. Avoid adding duplicate internal guards, but these concrete checks have purpose. |
| Remove retention ownership, path/link checks or exclusive directory creation | **Reject.** This code deletes files. Metadata ownership, refusal of redirected paths and unknown content, and preservation of orphaned evidence have specific tests. A plain recursive delete would weaken documented behavior. No general filesystem security framework is warranted. |
| Remove exception-frame cleanup and target identity checks as defensive clutter | **Reject.** `test_destination_resolution.py:253-319` exercises retained exceptions and stale resolved targets. Credential lifetime is a real contract. Keep the small cleanup implementation and its rationale. |
| Probe lazy relation emptiness before COPY | **Reject.** It can execute a volatile source twice. Current tests and the empty-probe benchmark gate establish that consequence. Cheap checks on already materialized pandas/Arrow objects are different and can stay. |
| Scan all fragments up front to simplify reader recovery | **Reject.** The approved reader attempts normally, scans bounded buffers only for the specific size error, and retries once. Retain that bounded path and improve its variable names. |
| Remove AST qualification or replace it with regex SQL rewriting | **Reject.** Single/multiple-catalog rules, CTEs, quoted names and parameter reuse require structural handling. DF-001 improves the existing traversal without changing semantics. |
| Turn every small forwarding method into a defect | **Reject.** Asset query invokes materialization; result query does not. Thin methods preserve that useful boundary. `CONNECTION_LOCK` already has an accurate name. |
| Add manual CSV/JSON methods immediately | **Defer.** Manual export is Parquet-only while declarations support three formats. This is a real API asymmetry, but the current behavior is documented. Compare one general export method in a future API discussion; do not add a method per format or alter invocation semantics in this cleanup. |
| Rewrite historical benchmark harnesses into a shared framework | **Reject for this review.** Their duplicated fixtures and short statistical names preserve independent experiments. They are not production abstraction requirements. Do not edit retained results or infer new performance claims from source review. |
| Treat deferred modes, live Azure atomicity or advanced template formatting as new bugs | **Reject.** They are recorded scope limits/open decisions. No live or remote guarantee was established by this review. |

## Public operation inventory

| Operation | Result and side effect | Owner |
| --- | --- | --- |
| `asset(table, ..., config=...)` | Typed definition; validates declaration, no database I/O | `_api.py` |
| `asset.with_arguments(...)`, `with_options(...)` | New definition; retains templates, binds arguments/options | `_api.py`, `_templates.py` |
| `asset.cache_first(expiration)` | New definition selecting cache policy | `_api.py` |
| `await asset(...)` | `AssquackResult`; may capture, publish and export | `_api.py`, `_materialization/pipeline.py` |
| `await asset.query(...)` | Native relation after ordinary asset invocation | `_api.py`, `_query/asset.py` |
| `asset.exists(...)`, `result.exists()` | Boolean; checks managed publication, may attach/bootstrap | `_cache.py`, storage; DF-002 applies |
| `await result.query(...)`, `result.to_df()` | Native relation / collected DataFrame; no producer call | `_result.py`, `_query/asset.py` |
| `result.export_parquet(...)` | One URI or ordered URI list; writes current table and manifests | `_result.py`, `_exports`; DF-002/003/005 apply |
| `config.attach()` | Synchronous named attachment; no asset extraction | `_config.py`, `_storage/database.py` |
| `use_config(config)`, `await assquack.query(...)` | Task-local scope / native relation; scope does not waive qualification rules | `_query/context.py`, `_query/general.py` |

The mix of awaited queries and synchronous explicit exports is established API,
not evidence of nonblocking DuckDB work. No async runtime redesign is proposed.

## Coverage, validation and limitations

The inventory below covers Git-tracked paths plus existing untracked, non-ignored
files. All production Python modules were inspected, including public binding,
config, metadata, capture, inference, retention, query qualification and export
authentication/preparation/writing. Tests were surveyed across all 30 files, with
detailed reads of fixtures and the behavior implicated by findings. Active API,
configuration, schema, storage, export and lifecycle docs were compared with code;
epic decisions and historical observations were checked for supersession.

Benchmark coverage is structural: harness entry points, fixture/validation and
summary conventions, with closer inspection of capture and empty-probe mechanics.
Older dictionary/relation/typed-input harness bodies and generated timing/plan
files were sampled, not independently audited line by line. Generated benchmark
JSON/CSV/log values and the dependency lock's package records were not individually
revalidated. The complete file table is an inventory, not a certification of every
measurement or every test assertion. A blank cell means no design flaw was raised.

The `.submodules/MAD.Prefect` gitlink is listed separately as historical third-party
reference and was not recursively inspected. No new compatibility equivalence is
claimed from it. Ignored scratch, dependency environments, Git internals and local
runtime files are excluded. No environment files, credentials, customer data,
live APIs or live databases were read or executed.

The repository has no `code-style-settings.json`. The code-style-assistant scales
informed qualitative review, but this task's explicit readability-first remit and
docs-only scope override its default settings-file creation and per-file numeric
score format. No settings file was created. The Assquack documentation skill and
repository preferences supplied the documentation checks and contract boundaries.

| Validation | Result and attribution |
| --- | --- |
| Stage-one full offline pytest | 427 passed, 8 skipped, 1 failed; missing NumPy at the capture UDF. Log inspected; not rerun by stage two. |
| Stage-one synthetic probes | C01-C03 reproduced; probe source and output inspected. No new runtime execution in stage two. |
| Stage-one capture oracle | 10 passed. No performance matrix rerun. |
| Stage-one Ruff | Lint passed; format check flags one historical question example, 94 files already formatted. |
| Stage-one Pyright | Seven errors: five known source errors, two missing-pandas-related errors. No claim of a clean type gate. |
| Stage-one dependency metadata | `poetry check --lock` passed; this does not prove complete test dependencies. |
| Stage-two static checks | Call-site searches confirm residual lock usage, unused projection field, single-table wrapper and unused runtime `chunk_size`. |
| Stage-two artifact checks | 254 inventory paths reconciled without duplicates or omissions; all seven stage-one decisions and nine finding IDs checked. All 96 first-party Python files parsed successfully without execution; this is a syntax check, not additional behavioral coverage. |
| Stage-two documentation checks | Repository link/anchor checker passed. Terminology matches are confined to the explicitly historical locking note and epic references to it. `git diff --check` passed; the untracked review was also checked directly for trailing whitespace. |

Stage one's first sandbox run hit Windows temporary-directory ACL failures; its
normally approved offline rerun produced the results above. A cached optional
NumPy binary targeted Python 3.11, incompatible with the installed Python 3.13
environment. No package installation or sandbox bypass was used for this review.
Live Azure authentication, remote atomicity, other platforms and optional input
execution beyond the recorded environment remain unverified.

No implementation, tests, dependencies, branch, commit or staging entries were
changed. Only this review and its index entry are delivered; stage-two scratch
belongs under `.tmp/visible-repo-review-2026-11-04/readability/`.

## Related Docs

- [Documentation index](../README.md): current architecture and implementation map.
- [Design review remit](design-review-remit.md): broader design questions and preserved decisions.
- [Developer API](../developer-api.md): invocation, binding, caching and native results.
- [Storage model](../storage-model.md): publication metadata and connection lifetime.
- [Exports](../exports.md): destination resolution and truthful manifests.
- [Default connection phase](../epics/01-mvp/07-default-connection.md): final query resolution rules.
- [Extensibility decisions](../epics/01-mvp/06-extensibility-defects.md): approved boundaries retained here.

## Repository file table

Inventory reconciliation and per-file findings follow. Paths identify files in
this checkout; source-line targets are in the findings above.

Inventory: **254 paths**, including one third-party submodule gitlink.
Reconciled with Git: no missing paths and no duplicate rows.

| File | Design bugs |
| --- | --- |
| `.agents/references/code-style.md` |  |
| `.agents/references/preferences.md` |  |
| `.agents/skills/assquack-coding/SKILL.md` |  |
| `.agents/skills/assquack-coding/agents/openai.yaml` |  |
| `.agents/skills/assquack-documentation/SKILL.md` |  |
| `.agents/skills/assquack-documentation/agents/openai.yaml` |  |
| `.agents/skills/assquack-documentation/scripts/check_doc_links.py` |  |
| `.gitignore` |  |
| `.gitmodules` |  |
| `.submodules/MAD.Prefect` |  |
| `.vscode/settings.json` |  |
| `AGENTS.md` |  |
| `README.md` | DF-009 — Reproducible test dependencies |
| `benchmarks/capture-20261102-methodology.md` |  |
| `benchmarks/capture-20261102-report.md` |  |
| `benchmarks/capture-results-20261102/results.md` |  |
| `benchmarks/capture_20261102.py` |  |
| `benchmarks/dict-fragments-20261028-methodology.md` |  |
| `benchmarks/dict-fragments-20261028-report.md` |  |
| `benchmarks/dict-fragments-no-empty-20261028-report.md` |  |
| `benchmarks/dict_fragments_20261028.py` |  |
| `benchmarks/dict_fragments_no_empty_20261028.py` |  |
| `benchmarks/empty-check-20261103/README.md` |  |
| `benchmarks/empty-check-20261103/environment.json` |  |
| `benchmarks/empty-check-20261103/gates.json` |  |
| `benchmarks/empty-check-20261103/plans/aggregate-10000-empty.txt` |  |
| `benchmarks/empty-check-20261103/plans/aggregate-10000-nonempty.txt` |  |
| `benchmarks/empty-check-20261103/plans/aggregate-100000-empty.txt` |  |
| `benchmarks/empty-check-20261103/plans/aggregate-100000-nonempty.txt` |  |
| `benchmarks/empty-check-20261103/plans/aggregate-1000000-empty.txt` |  |
| `benchmarks/empty-check-20261103/plans/aggregate-1000000-nonempty.txt` |  |
| `benchmarks/empty-check-20261103/plans/filter-10000-empty.txt` |  |
| `benchmarks/empty-check-20261103/plans/filter-10000-nonempty.txt` |  |
| `benchmarks/empty-check-20261103/plans/filter-100000-empty.txt` |  |
| `benchmarks/empty-check-20261103/plans/filter-100000-nonempty.txt` |  |
| `benchmarks/empty-check-20261103/plans/filter-1000000-empty.txt` |  |
| `benchmarks/empty-check-20261103/plans/filter-1000000-nonempty.txt` |  |
| `benchmarks/empty-check-20261103/plans/join-10000-empty.txt` |  |
| `benchmarks/empty-check-20261103/plans/join-10000-nonempty.txt` |  |
| `benchmarks/empty-check-20261103/plans/join-100000-empty.txt` |  |
| `benchmarks/empty-check-20261103/plans/join-100000-nonempty.txt` |  |
| `benchmarks/empty-check-20261103/plans/join-1000000-empty.txt` |  |
| `benchmarks/empty-check-20261103/plans/join-1000000-nonempty.txt` |  |
| `benchmarks/empty-check-20261103/plans/scan-10000-empty.txt` |  |
| `benchmarks/empty-check-20261103/plans/scan-10000-nonempty.txt` |  |
| `benchmarks/empty-check-20261103/plans/scan-100000-empty.txt` |  |
| `benchmarks/empty-check-20261103/plans/scan-100000-nonempty.txt` |  |
| `benchmarks/empty-check-20261103/plans/scan-1000000-empty.txt` |  |
| `benchmarks/empty-check-20261103/plans/scan-1000000-nonempty.txt` |  |
| `benchmarks/empty-check-20261103/plans/window-10000-empty.txt` |  |
| `benchmarks/empty-check-20261103/plans/window-10000-nonempty.txt` |  |
| `benchmarks/empty-check-20261103/plans/window-100000-empty.txt` |  |
| `benchmarks/empty-check-20261103/plans/window-100000-nonempty.txt` |  |
| `benchmarks/empty-check-20261103/plans/window-1000000-empty.txt` |  |
| `benchmarks/empty-check-20261103/plans/window-1000000-nonempty.txt` |  |
| `benchmarks/empty-check-20261103/pyrightconfig.json` |  |
| `benchmarks/empty-check-20261103/raw.jsonl` |  |
| `benchmarks/empty-check-20261103/rerun-selection.json` |  |
| `benchmarks/empty-check-20261103/rerun.py` |  |
| `benchmarks/empty-check-20261103/reruns.jsonl` |  |
| `benchmarks/empty-check-20261103/results.md` |  |
| `benchmarks/empty-check-20261103/run.py` |  |
| `benchmarks/empty-check-20261103/summarize.py` |  |
| `benchmarks/empty-check-20261103/summary.json` |  |
| `benchmarks/ndjson_diagnostics.py` |  |
| `benchmarks/relation-fragments-20261028-methodology.md` |  |
| `benchmarks/relation-fragments-20261028-report.md` |  |
| `benchmarks/relation_fragments_20261028.py` |  |
| `benchmarks/results/dict-fragments-20261028-611887bed6.jsonl` |  |
| `benchmarks/results/dict-fragments-20261028-611887bed6.metadata.json` |  |
| `benchmarks/results/dict-fragments-20261028-611887bed6.summary.csv` |  |
| `benchmarks/results/dict-fragments-20261028-611887bed6.summary.json` |  |
| `benchmarks/results/dict-fragments-20261028-611887bed6.validation.json` |  |
| `benchmarks/results/dict-fragments-20261028-6996de212a.jsonl` |  |
| `benchmarks/results/dict-fragments-20261028-6996de212a.metadata.json` |  |
| `benchmarks/results/dict-fragments-20261028-6996de212a.status.json` |  |
| `benchmarks/results/dict-fragments-no-empty-20261028-d24f8a4ee6.jsonl` |  |
| `benchmarks/results/dict-fragments-no-empty-20261028-d24f8a4ee6.metadata.json` |  |
| `benchmarks/results/dict-fragments-no-empty-20261028-d24f8a4ee6.summary.csv` |  |
| `benchmarks/results/dict-fragments-no-empty-20261028-d24f8a4ee6.summary.json` |  |
| `benchmarks/results/dict-fragments-no-empty-20261028-d24f8a4ee6.validation.json` |  |
| `benchmarks/results/ndjson-2026-10-28-compatibility.json` |  |
| `benchmarks/results/ndjson-2026-10-28-environment.json` |  |
| `benchmarks/results/ndjson-2026-10-28.jsonl` |  |
| `benchmarks/results/relation-fragments-20261028-2b7821c911.jsonl` |  |
| `benchmarks/results/relation-fragments-20261028-2b7821c911.metadata.json` |  |
| `benchmarks/results/relation-fragments-20261028-2b7821c911.summary.json` |  |
| `benchmarks/results/relation-fragments-20261028-67174f6bd8.jsonl` |  |
| `benchmarks/results/relation-fragments-20261028-67174f6bd8.metadata.json` |  |
| `benchmarks/results/relation-fragments-20261028-67174f6bd8.summary.json` |  |
| `benchmarks/results/relation-fragments-20261028-67174f6bd8.tables.md` |  |
| `benchmarks/results/relation-fragments-20261028-67174f6bd8.validation.json` |  |
| `benchmarks/results/typed-input-writes-20261028-effa6693a3.jsonl` |  |
| `benchmarks/results/typed-input-writes-20261028-effa6693a3.metadata.json` |  |
| `benchmarks/results/typed-input-writes-20261028-effa6693a3.summary.csv` |  |
| `benchmarks/results/typed-input-writes-20261028-effa6693a3.summary.json` |  |
| `benchmarks/results/typed-input-writes-20261028-effa6693a3.table.md` |  |
| `benchmarks/results/typed-input-writes-20261028-effa6693a3.validation.json` |  |
| `benchmarks/results/typed-input-writes-20261028-fa8f10596b.jsonl` |  |
| `benchmarks/results/typed-input-writes-20261028-fa8f10596b.metadata.json` |  |
| `benchmarks/results/typed-input-writes-20261028-fa8f10596b.summary.json` |  |
| `benchmarks/results/typed-input-writes-20261028-fb1347db68.jsonl` |  |
| `benchmarks/results/typed-input-writes-20261028-fb1347db68.metadata.json` |  |
| `benchmarks/results/typed-input-writes-20261028-fb1347db68.summary.csv` |  |
| `benchmarks/results/typed-input-writes-20261028-fb1347db68.summary.json` |  |
| `benchmarks/results/typed-input-writes-20261028-fb1347db68.table.md` |  |
| `benchmarks/results/typed-input-writes-20261028-fb1347db68.validation.json` |  |
| `benchmarks/results/typed-input-writes-20261029-152-2133ad9c40.fixtures.json` |  |
| `benchmarks/results/typed-input-writes-20261029-152-2133ad9c40.jsonl` |  |
| `benchmarks/results/typed-input-writes-20261029-152-2133ad9c40.metadata.json` |  |
| `benchmarks/results/typed-input-writes-20261029-152-2133ad9c40.summary.json` |  |
| `benchmarks/results/typed-input-writes-20261029-152-2133ad9c40.tables.md` |  |
| `benchmarks/results/typed-input-writes-20261029-152-2133ad9c40.validation.json` |  |
| `benchmarks/results/typed-input-writes-20261029-152-c08bad27aa.fixtures.json` |  |
| `benchmarks/results/typed-input-writes-20261029-152-c08bad27aa.jsonl` |  |
| `benchmarks/results/typed-input-writes-20261029-152-c08bad27aa.metadata.json` |  |
| `benchmarks/results/typed-input-writes-20261029-152-c08bad27aa.summary.json` |  |
| `benchmarks/results/typed-input-writes-20261029-152-c08bad27aa.tables.md` |  |
| `benchmarks/results/typed-input-writes-20261029-152-c08bad27aa.validation.json` |  |
| `benchmarks/results/typed-writes-152-run.log` |  |
| `benchmarks/results/typed-writes-152-smoke.log` |  |
| `benchmarks/summarize_capture_20261102.py` |  |
| `benchmarks/summarize_dict_fragments_20261028.py` |  |
| `benchmarks/summarize_dict_fragments_no_empty_20261028.py` |  |
| `benchmarks/summarize_ndjson.py` |  |
| `benchmarks/summarize_relation_fragments_20261028.py` |  |
| `benchmarks/summarize_typed_input_writes_20261028.py` |  |
| `benchmarks/summarize_typed_input_writes_20261029_152.py` |  |
| `benchmarks/test_capture_20261102.py` |  |
| `benchmarks/typed-input-writes-20261028-methodology.md` |  |
| `benchmarks/typed-input-writes-20261028-pyright.json` |  |
| `benchmarks/typed-input-writes-20261028-report.md` |  |
| `benchmarks/typed-input-writes-20261029-152-methodology.md` |  |
| `benchmarks/typed-input-writes-20261029-152-pyright.json` |  |
| `benchmarks/typed-input-writes-20261029-152-report.md` |  |
| `benchmarks/typed_input_writes_20261028.py` |  |
| `benchmarks/typed_input_writes_20261029_152.py` |  |
| `docs/README.md` |  |
| `docs/assquack-mvp-plan.md` |  |
| `docs/configuration.md` | DF-004 — Unused data shapes and setting; DF-006 — Current documentation and names |
| `docs/developer-api.md` | DF-002 — Current publication identity; DF-006 — Current documentation and names |
| `docs/developer-examples.md` | DF-006 — Current documentation and names |
| `docs/epics/01-mvp/00-repo-bootstrap.md` |  |
| `docs/epics/01-mvp/01-local-duckdb-core.md` |  |
| `docs/epics/01-mvp/02-replace-materialization.md` |  |
| `docs/epics/01-mvp/03-query-and-cache.md` |  |
| `docs/epics/01-mvp/04-export-compatibility.md` |  |
| `docs/epics/01-mvp/05-semi-structured-evidence-policy.md` |  |
| `docs/epics/01-mvp/06-extensibility-defects.md` |  |
| `docs/epics/01-mvp/07-default-connection.md` |  |
| `docs/epics/README.md` |  |
| `docs/exports.md` | DF-002 — Current publication identity |
| `docs/journal/2026-09-08-mvp-scope.md` |  |
| `docs/mad-prefect-reference.md` |  |
| `docs/materialization-lifecycle.md` | DF-004 — Unused data shapes and setting |
| `docs/mvp_requirement_observations/cache-and-writer-lock-behaviour.md` |  |
| `docs/mvp_requirement_observations/current-timestamp-upsert.md` |  |
| `docs/mvp_requirement_observations/disabled-export-feedback.md` |  |
| `docs/mvp_requirement_observations/duckdb-v1.5-storage-version.md` |  |
| `docs/mvp_requirement_observations/environment-backed-configuration.md` |  |
| `docs/mvp_requirement_observations/partial-lazy-template-formatting.md` |  |
| `docs/mvp_requirement_observations/questions/asset-schema-fallback.md` |  |
| `docs/mvp_requirement_observations/questions/query_connections.py` |  |
| `docs/overview.md` | DF-006 — Current documentation and names |
| `docs/research/2026-10-23-export-destination-architecture.md` |  |
| `docs/research/2026-10-23-fsspec-export-extensibility.md` |  |
| `docs/research/2026-10-26-dagster-credential-sourcing.md` |  |
| `docs/research/2026-10-26-duckdb-filesystem-preparation.md` |  |
| `docs/research/2026-10-28-ndjson-diagnostics.md` |  |
| `docs/research/2026-10-28-ndjson-timing-tables.md` |  |
| `docs/reviews/2026-11-04-repository-review.md` |  |
| `docs/reviews/design-review-remit.md` |  |
| `docs/roadmap.md` |  |
| `docs/schema-inference.md` |  |
| `docs/storage-model.md` | DF-002 — Current publication identity; DF-006 — Current documentation and names |
| `poetry.lock` | DF-009 — Reproducible test dependencies |
| `pyproject.toml` | DF-009 — Reproducible test dependencies |
| `src/assquack/__init__.py` |  |
| `src/assquack/_api.py` | DF-002 — Current publication identity; DF-007 — Reserved schema boundary; DF-008 — Honest type contracts |
| `src/assquack/_cache.py` | DF-002 — Current publication identity |
| `src/assquack/_config.py` | DF-004 — Unused data shapes and setting |
| `src/assquack/_errors.py` |  |
| `src/assquack/_exports/__init__.py` | DF-005 — Shared export recording |
| `src/assquack/_exports/authentication/__init__.py` |  |
| `src/assquack/_exports/authentication/azure.py` |  |
| `src/assquack/_exports/authentication/base.py` |  |
| `src/assquack/_exports/authentication/credentials.py` |  |
| `src/assquack/_exports/authentication/local.py` |  |
| `src/assquack/_exports/authentication/registry.py` |  |
| `src/assquack/_exports/destinations/__init__.py` |  |
| `src/assquack/_exports/destinations/base.py` |  |
| `src/assquack/_exports/destinations/filesystem.py` |  |
| `src/assquack/_exports/destinations/fingerprint.py` |  |
| `src/assquack/_exports/models.py` |  |
| `src/assquack/_exports/preparation/__init__.py` |  |
| `src/assquack/_exports/preparation/azure.py` |  |
| `src/assquack/_exports/preparation/local.py` |  |
| `src/assquack/_exports/preparation/registry.py` |  |
| `src/assquack/_exports/targets.py` |  |
| `src/assquack/_exports/writer/__init__.py` |  |
| `src/assquack/_exports/writer/file.py` |  |
| `src/assquack/_materialization/__init__.py` |  |
| `src/assquack/_materialization/commit.py` | DF-002 — Current publication identity; DF-004 — Unused data shapes and setting |
| `src/assquack/_materialization/fragments.py` |  |
| `src/assquack/_materialization/json_reader.py` |  |
| `src/assquack/_materialization/models.py` | DF-004 — Unused data shapes and setting; DF-008 — Honest type contracts |
| `src/assquack/_materialization/pipeline.py` | DF-002 — Current publication identity; DF-004 — Unused data shapes and setting; DF-005 — Shared export recording |
| `src/assquack/_materialization/retention.py` | DF-003 — Obsolete file locking |
| `src/assquack/_materialization/schema.py` | DF-004 — Unused data shapes and setting |
| `src/assquack/_materialization/staging.py` |  |
| `src/assquack/_query/__init__.py` |  |
| `src/assquack/_query/asset.py` | DF-008 — Honest type contracts |
| `src/assquack/_query/context.py` |  |
| `src/assquack/_query/general.py` | DF-001 — Readable query qualification |
| `src/assquack/_result.py` | DF-002 — Current publication identity; DF-003 — Obsolete file locking; DF-005 — Shared export recording |
| `src/assquack/_storage/__init__.py` |  |
| `src/assquack/_storage/database.py` |  |
| `src/assquack/_storage/locking.py` | DF-003 — Obsolete file locking |
| `src/assquack/_storage/metadata.py` | DF-002 — Current publication identity; DF-003 — Obsolete file locking |
| `src/assquack/_storage/models.py` | DF-002 — Current publication identity |
| `src/assquack/_storage/sql.py` |  |
| `src/assquack/_storage/tables.py` | DF-002 — Current publication identity |
| `src/assquack/_templates.py` | DF-008 — Honest type contracts |
| `src/assquack/py.typed` |  |
| `tests/conftest.py` |  |
| `tests/database_helpers.py` |  |
| `tests/test_asset_contract.py` |  |
| `tests/test_asset_options.py` |  |
| `tests/test_asset_query_execution.py` |  |
| `tests/test_asset_timestamps.py` |  |
| `tests/test_azure_exports.py` |  |
| `tests/test_azure_sas.py` |  |
| `tests/test_cache.py` | DF-002 — Current publication identity |
| `tests/test_config.py` | DF-004 — Unused data shapes and setting |
| `tests/test_credential_chain.py` |  |
| `tests/test_default_connection.py` |  |
| `tests/test_destination_resolution.py` |  |
| `tests/test_export_destinations.py` |  |
| `tests/test_exports.py` | DF-002 — Current publication identity; DF-005 — Shared export recording |
| `tests/test_fragment_inputs.py` | DF-003 — Obsolete file locking; DF-009 — Reproducible test dependencies |
| `tests/test_fragment_retention.py` | DF-004 — Unused data shapes and setting |
| `tests/test_fragments.py` | DF-004 — Unused data shapes and setting |
| `tests/test_general_query.py` | DF-006 — Current documentation and names |
| `tests/test_imports.py` |  |
| `tests/test_json_reader.py` |  |
| `tests/test_materialization_mode.py` |  |
| `tests/test_pipe_exports.py` | DF-005 — Shared export recording |
| `tests/test_query_chaining.py` | DF-004 — Unused data shapes and setting |
| `tests/test_run_identity.py` |  |
| `tests/test_schema_inference.py` | DF-004 — Unused data shapes and setting |
| `tests/test_schema_replacement.py` | DF-004 — Unused data shapes and setting |
| `tests/test_scoped_query_resolution.py` |  |
| `tests/test_table_resolution.py` | DF-007 — Reserved schema boundary |
| `tests/test_writer_lock.py` | DF-003 — Obsolete file locking |
