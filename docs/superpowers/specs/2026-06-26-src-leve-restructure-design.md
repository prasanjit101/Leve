# `src/leve/` Restructure — Design

**Date:** 2026-06-26
**Status:** Approved (design)

## Problem

`src/leve/` has **27 Python modules sitting flat** at the top level alongside 6
subpackages (`tools`, `channels`, `connections`, `sandbox`, `loader`, `evals`).
The flat modules fall into obvious domains, but nothing in the directory layout
expresses those domains — a newcomer scanning the package sees 27 peer files
with no signal about what belongs with what.

## Goal

Group the largest, most cohesive clusters into subpackages so the directory
structure communicates the architecture, **without breaking any existing import
path**. Existing imports (including the test suite, which uses deep paths like
`leve.agent`, `leve.graph`, `leve.runtime`, `leve.session`, `leve.app`,
`leve.server`) must continue to work unchanged.

Non-goal: reorganizing every leaf module. Per the agreed scope, only the biggest
clusters move; small leaf/infra modules stay at the top level.

## Target layout

Three new subpackages absorb 17 of the flat modules:

### `leve/core/` — the graph-compilation engine
The framework's heart. `graph.py` already wires all of these together to build
the runnable LangGraph graph.

```
core/agent.py         core/middleware.py     core/skills.py
core/graph.py         core/instructions.py   core/subagents.py
core/models.py        core/runtime.py
```

### `leve/serving/` — the run / serve surface
HTTP API, CLI, TUI, and the session loop that drives a compiled agent.

```
serving/server.py     serving/app.py     serving/cli.py
serving/session.py    serving/events.py  serving/tui.py
```

### `leve/security/` — principals & credentials
Per-caller permission scoping and credential brokering.

```
security/auth.py      security/credentials.py    security/platform_auth.py
```

### Stays flat at the top level
Leaf and infra modules that don't form a large cluster:

```
config.py   errors.py   persistence.py   tracing.py
scaffold.py schedules.py devlog.py        deploy.py    platform.py   testing.py
```

Existing subpackages (`tools`, `channels`, `connections`, `sandbox`, `loader`,
`evals`) are **untouched**.

## Backward compatibility — re-export shims

Every moved module leaves a **one-line shim** at its original top-level path.
The real implementation lives in the subpackage; the shim re-exports it.

```python
# leve/agent.py  (shim — kept indefinitely for a stable public API)
from leve.core.agent import *  # noqa: F401,F403
from leve.core.agent import AgentSpec, CompactionConfig, define_agent  # re-export named symbols
```

Rules for shims:
- Wildcard re-export **plus** explicit re-export of every name the original
  module's `__all__` / public surface exposed, so `from leve.agent import X`
  resolves whether or not `X` is in `__all__`.
- Shims are **permanent** (decided): they are the stable public deep-import API,
  not a temporary migration aid. No deprecation warnings.
- `leve/__init__.py` is unchanged in behavior — it keeps re-exporting the public
  top-level API (`define_agent`, `AgentSpec`, `Credential`, `Principal`,
  `__version__`). Its internal import sources update to the new canonical paths.

## Internal import policy

- Code **inside** the moved subpackages imports siblings by their **new
  canonical path** (`from leve.core.runtime import LeveContext`), not via shims.
- Code **outside** the moved set may keep using old paths (resolved by shims) or
  be updated opportunistically — but this change does not require a sweep of
  every call site. Only the moved modules' own imports are normalized.
- `__pycache__` directories are regenerated automatically; not tracked.

## Canonical-path map

| Old path                | New canonical path           | Shim left at old path |
|-------------------------|------------------------------|-----------------------|
| `leve.agent`            | `leve.core.agent`            | yes                   |
| `leve.graph`            | `leve.core.graph`            | yes                   |
| `leve.models`           | `leve.core.models`           | yes                   |
| `leve.middleware`       | `leve.core.middleware`       | yes                   |
| `leve.instructions`     | `leve.core.instructions`     | yes                   |
| `leve.runtime`          | `leve.core.runtime`          | yes                   |
| `leve.skills`           | `leve.core.skills`           | yes                   |
| `leve.subagents`        | `leve.core.subagents`        | yes                   |
| `leve.server`           | `leve.serving.server`        | yes                   |
| `leve.session`          | `leve.serving.session`       | yes                   |
| `leve.events`           | `leve.serving.events`        | yes                   |
| `leve.app`              | `leve.serving.app`           | yes                   |
| `leve.cli`              | `leve.serving.cli`           | yes                   |
| `leve.tui`              | `leve.serving.tui`           | yes                   |
| `leve.auth`             | `leve.security.auth`         | yes                   |
| `leve.credentials`      | `leve.security.credentials`  | yes                   |
| `leve.platform_auth`    | `leve.security.platform_auth`| yes                   |

## Risks & edge cases

- **`session.py` imports the `events` module object** (`from leve import events`
  / `from leve.events import EventNormalizer`). After the move both live in
  `leve.serving`; the in-package import becomes
  `from leve.serving import events`. The `leve.events` shim still resolves the
  external form.
- **`runtime.py` naming**: there is no existing `leve/runtime/` package, so
  `leve/core/runtime.py` introduces no conflict. The shim `leve/runtime.py`
  coexists with the package only as a module file (no `leve.runtime` package).
- **`cli.py` / `server.py` entry points**: if `pyproject.toml` declares console
  scripts pointing at `leve.cli:...` or `leve.server:...`, those keep working
  via the shims. Entry-point strings may also be updated to canonical paths
  (verified during implementation).
- **Star-import correctness**: modules without an explicit `__all__` export all
  non-underscore names via `*`; the explicit named re-exports in each shim cover
  any symbol a consumer imports by name regardless.

## Verification

1. `from leve import define_agent, AgentSpec, Credential, Principal` works.
2. Every old deep path used by the test suite imports successfully.
3. Full test suite passes unchanged (no test import edits required).
4. `python -c "import leve"` and the CLI entry point run clean.

## Outcome

Top level drops from 27 loose modules to ~10 leaf modules + 6 existing
subpackages + 3 new subpackages (+ 17 thin shim files that are clearly stubs).
The architecture is now legible from the directory tree, and no consumer breaks.
