# Assquack Epics

Epics track implementation architecture separately from developer-facing docs.
Regular docs under `docs/*.md` should help developers use and reason about the
library. Epic phase files should help implementors build it: they record phase
architecture, scope, sequencing, validation, status, and remaining decisions.

## File Layout

Use one folder per epic:

```text
docs/epics/[number]-[short-desc]/
```

Use one Markdown file per phase:

```text
docs/epics/[number]-[short-desc]/[phase-no]-[short-desc].md
```

Examples:

```text
docs/epics/01-mvp/00-repo-bootstrap.md
docs/epics/01-mvp/01-local-duckdb-core.md
```

Numbers should be zero-padded when practical so editors sort the work in
execution order.

## Phase Status Header

Every phase file must start with a status header immediately after the H1:

```markdown
# Phase Title

Status: **Planned**
Last updated: YYYY-MM-DD
Epic: 01 MVP
Phase: 00
Related docs: [Roadmap](../../roadmap.md), [Developer API](../../developer-api.md)
```

Allowed statuses:

- `Planned`: scoped but not started.
- `In Progress`: implementation has begun and the phase is not yet complete.
- `Blocked`: cannot proceed without a decision, dependency, or external action.
- `Complete`: implementation, docs, tests, and checks are done.

When implementation starts for a phase, set that phase to `In Progress`. Leave
it `In Progress` while any implementation, documentation, tests, or required
checks remain incomplete. Mark it `Complete` only after the phase behavior is
implemented, developer docs are updated where needed, tests cover the behavior,
and validation checks have passed. Use `Blocked` when a dependency, missing
tooling choice, or unresolved product/architecture decision prevents progress.

Before handing work back after a relevant coding task, update the status header
for every phase touched by the task. If the task changes scope but not status,
update `Last updated` and record the scope change in the phase body.

## Phase Body

Keep phase files implementor-facing and reviewable:

- `## Intent`: what the phase exists to achieve.
- `## Scope`: what is included and excluded.
- `## Architecture Notes`: design boundaries, patterns, and important
  implementation decisions.
- `## Implementation Checklist`: concrete checkboxes agents can update.
- `## Validation`: commands or checks that prove the phase is ready.
- `## Notes`: assumptions, design decisions, blockers, and follow-ups.

Validation entries may use placeholder command names only until Phase 00 chooses
the project tooling. Once tooling is selected, replace placeholders with the
concrete commands agents should run.

Do not write end-user how-to material in epic docs. Put developer-facing usage
guidance, examples, and API explanations in the regular docs, then link to them
from the epic phase when implementors need context.

## Related Docs

- [Roadmap](../roadmap.md): implementation sequencing and future work.
- [Developer API](../developer-api.md): public contracts that epics implement.
- [Storage Model](../storage-model.md): database and metadata contracts.
- [Configuration](../configuration.md): runtime and Pydantic config contracts.
