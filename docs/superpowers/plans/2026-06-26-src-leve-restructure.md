# `src/leve/` Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group 17 flat top-level modules of `src/leve/` into three cohesive subpackages (`core/`, `serving/`, `security/`) while keeping every existing import path working via permanent re-export shims.

**Architecture:** Each moved module relocates to its subpackage with `git mv` (history preserved). A one-line-ish shim file is left at the original top-level path that wildcard-re-exports the relocated module plus its public names explicitly. Code *inside* the moved subpackages is rewritten to import moved siblings by their new canonical path; non-moved modules keep their imports (resolved by shims). The work proceeds in dependency-leaf order — **security → core → serving** — so the full test suite stays green after every task.

**Tech Stack:** Python 3, hatchling build backend, `uv` for env/runner, `pytest` + `pytest-asyncio`, Typer CLI, LangGraph / LangChain v1 `create_agent`.

## Global Constraints

- **Never delete code.** Every original symbol must remain importable from its original path. Modules are *moved* (`git mv`) and back-filled with shims — nothing is removed (CLAUDE.md hard rule).
- **Shims are permanent.** They are the stable public deep-import API, not a migration aid. No deprecation warnings.
- **`leve/__init__.py` behavior is unchanged.** It still re-exports `define_agent, AgentSpec, CompactionConfig, Credential, Principal, __version__`. Only its internal *import sources* update to canonical paths.
- **Shim pattern (every shim file):** a wildcard re-export `from leve.<pkg>.<mod> import *  # noqa: F401,F403` followed by an explicit named re-export line listing the module's public (non-underscore) top-level symbols. The wildcard guarantees completeness; the explicit line documents the surface and satisfies static analysis.
- **Internal import policy:** code inside a moved subpackage imports moved siblings by new canonical path (e.g. `from leve.core.runtime import LeveContext`); imports of non-moved modules (`loader`, `tools`, `config`, `errors`, `persistence`, `sandbox`, `tracing`, `schedules`, `devlog`, `deploy`, `connections`, `evals`) are left unchanged.
- **Untouched subpackages:** `tools`, `channels`, `connections`, `sandbox`, `loader`, `evals`. **Stays-flat modules:** `config.py`, `errors.py`, `persistence.py`, `tracing.py`, `scaffold.py`, `schedules.py`, `devlog.py`, `deploy.py`, `platform.py`, `testing.py`.
- **Test command:** `uv run pytest -q` (testpaths = `tests`).

### Canonical-path map (17 modules)

| Old path | New canonical path | Subpackage task |
|---|---|---|
| `leve.auth` | `leve.security.auth` | Task 2 |
| `leve.credentials` | `leve.security.credentials` | Task 2 |
| `leve.platform_auth` | `leve.security.platform_auth` | Task 2 |
| `leve.agent` | `leve.core.agent` | Task 3 |
| `leve.graph` | `leve.core.graph` | Task 3 |
| `leve.models` | `leve.core.models` | Task 3 |
| `leve.middleware` | `leve.core.middleware` | Task 3 |
| `leve.instructions` | `leve.core.instructions` | Task 3 |
| `leve.runtime` | `leve.core.runtime` | Task 3 |
| `leve.skills` | `leve.core.skills` | Task 3 |
| `leve.subagents` | `leve.core.subagents` | Task 3 |
| `leve.server` | `leve.serving.server` | Task 4 |
| `leve.session` | `leve.serving.session` | Task 4 |
| `leve.events` | `leve.serving.events` | Task 4 |
| `leve.app` | `leve.serving.app` | Task 4 |
| `leve.cli` | `leve.serving.cli` | Task 4 |
| `leve.tui` | `leve.serving.tui` | Task 4 |

---

## File Structure

After all tasks:

```
src/leve/
  __init__.py                 # unchanged behavior; canonical import sources
  security/
    __init__.py               # new, docstring only
    auth.py  credentials.py  platform_auth.py        # moved (real impl)
  core/
    __init__.py               # new, docstring only
    agent.py  graph.py  models.py  middleware.py
    instructions.py  runtime.py  skills.py  subagents.py   # moved (real impl)
  serving/
    __init__.py               # new, docstring only
    server.py  session.py  events.py  app.py  cli.py  tui.py  # moved (real impl)
  auth.py  credentials.py  platform_auth.py          # shims
  agent.py  graph.py  models.py  middleware.py
  instructions.py  runtime.py  skills.py  subagents.py   # shims
  server.py  session.py  events.py  app.py  cli.py  tui.py  # shims
  config.py errors.py persistence.py tracing.py scaffold.py
  schedules.py devlog.py deploy.py platform.py testing.py   # stay flat, untouched
  tools/ channels/ connections/ sandbox/ loader/ evals/      # untouched
tests/
  test_restructure_shims.py   # new regression harness
```

---

### Task 1: Shim-compatibility regression harness

A single test module that asserts, for all 17 modules, that the **old path and the new canonical path resolve to the *same* object** (`is`), and that the `leve` public API is intact. It is written to its final complete form now; it FAILS today (canonical paths don't exist yet) and turns fully green only after Task 4. Tasks 2–4 each run the subset relevant to them; this task commits the harness and confirms it fails for the right reason.

**Files:**
- Create: `tests/test_restructure_shims.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the test module `tests/test_restructure_shims.py` with test functions `test_security_shims`, `test_core_shims`, `test_serving_shims`, `test_public_api`, used as the green-gate in Tasks 2–4.

- [ ] **Step 1: Write the regression harness**

```python
# tests/test_restructure_shims.py
"""Guards the src/leve restructure: every old top-level import path must
resolve to the SAME object as its new canonical subpackage path, so the
re-export shims stay a faithful stable API. See
docs/superpowers/specs/2026-06-26-src-leve-restructure-design.md.
"""
import importlib

import pytest

# (old module path, new canonical module path, [public symbol names])
SECURITY = [
    ("leve.auth", "leve.security.auth",
     ["Credential", "Principal", "anonymous", "with_broker", "app_principal",
      "InjectedPrincipal", "set_current_principal", "reset_current_principal",
      "current_principal"]),
    ("leve.credentials", "leve.security.credentials",
     ["NeedsConsent", "CredentialBroker", "StaticBroker", "OAuthStoreBroker",
      "TokenExchangeBroker", "create_broker"]),
    ("leve.platform_auth", "leve.security.platform_auth",
     ["store_namespace", "make_auth"]),
]
CORE = [
    ("leve.agent", "leve.core.agent",
     ["CompactionConfig", "AgentSpec", "define_agent", "TriggerClause"]),
    ("leve.graph", "leve.core.graph", ["build_graph", "ExtraToolsResolver"]),
    ("leve.models", "leve.core.models", ["build_model"]),
    ("leve.middleware", "leve.core.middleware",
     ["ApprovalMiddleware", "PrincipalMiddleware"]),
    ("leve.instructions", "leve.core.instructions",
     ["render_instructions", "make_prompt_middleware"]),
    ("leve.runtime", "leve.core.runtime", ["LeveContext"]),
    ("leve.skills", "leve.core.skills",
     ["SkillSpec", "parse_skill", "make_load_skill_tool"]),
    ("leve.subagents", "leve.core.subagents",
     ["DelegateInput", "make_delegation_tool"]),
]
SERVING = [
    ("leve.server", "leve.serving.server",
     ["SessionBroker", "SessionManager", "MessageBody", "ResumeBody",
      "create_app", "API_PREFIX"]),
    ("leve.session", "leve.serving.session", ["AgentRuntime", "extract_reply"]),
    ("leve.events", "leve.serving.events",
     ["turn_start", "turn_end", "approval_requested", "error", "EventNormalizer"]),
    ("leve.app", "leve.serving.app",
     ["build_runtime", "inspect_project", "load_evals", "run_evals"]),
    ("leve.cli", "leve.serving.cli", ["app", "channels_app", "connections_app"]),
    ("leve.tui", "leve.serving.tui", ["LeveTUI", "run_tui"]),
]


def _assert_shim(old_path, new_path, symbols):
    old_mod = importlib.import_module(old_path)
    new_mod = importlib.import_module(new_path)
    for name in symbols:
        assert hasattr(old_mod, name), f"{old_path} missing re-export {name!r}"
        assert hasattr(new_mod, name), f"{new_path} missing {name!r}"
        assert getattr(old_mod, name) is getattr(new_mod, name), (
            f"{old_path}.{name} is not the same object as {new_path}.{name}"
        )


@pytest.mark.parametrize("old,new,syms", SECURITY,
                         ids=[r[0] for r in SECURITY])
def test_security_shims(old, new, syms):
    _assert_shim(old, new, syms)


@pytest.mark.parametrize("old,new,syms", CORE, ids=[r[0] for r in CORE])
def test_core_shims(old, new, syms):
    _assert_shim(old, new, syms)


@pytest.mark.parametrize("old,new,syms", SERVING, ids=[r[0] for r in SERVING])
def test_serving_shims(old, new, syms):
    _assert_shim(old, new, syms)


def test_public_api():
    import leve
    from leve import (  # noqa: F401
        AgentSpec, CompactionConfig, Credential, Principal, define_agent,
    )
    assert leve.__version__ == "0.1.0"
    assert set(leve.__all__) == {
        "AgentSpec", "CompactionConfig", "Credential", "Principal",
        "define_agent", "__version__",
    }
```

- [ ] **Step 2: Run the harness to confirm it fails for the right reason**

Run: `uv run pytest tests/test_restructure_shims.py -q`
Expected: FAIL — `test_security_shims`, `test_core_shims`, `test_serving_shims` error with `ModuleNotFoundError: No module named 'leve.security'` (and `leve.core`, `leve.serving`). `test_public_api` PASSES (public API already works today).

- [ ] **Step 3: Commit**

```bash
git add tests/test_restructure_shims.py
git commit -m "test: add shim-compatibility regression harness for leve restructure

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `leve/security/` package (auth, credentials, platform_auth)

Move the security cluster first — it has no dependency on `core`/`serving`, so moving it cannot create forward references to not-yet-created paths. After this task, old `leve.auth` / `leve.credentials` / `leve.platform_auth` resolve via shims; non-moved modules that import them (`core.middleware`, `core.subagents`, etc., still flat at this point) keep working through those shims.

**Files:**
- Create: `src/leve/security/__init__.py`
- Move: `src/leve/auth.py` → `src/leve/security/auth.py`
- Move: `src/leve/credentials.py` → `src/leve/security/credentials.py`
- Move: `src/leve/platform_auth.py` → `src/leve/security/platform_auth.py`
- Create (shims): `src/leve/auth.py`, `src/leve/credentials.py`, `src/leve/platform_auth.py`
- Test: `tests/test_restructure_shims.py::test_security_shims`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: canonical paths `leve.security.auth`, `leve.security.credentials`, `leve.security.platform_auth` and shims at the three old paths. Later tasks import `from leve.security.auth import ...` and `from leve.security.credentials import ...`.

- [ ] **Step 1: Create the package and move the three modules (history-preserving)**

```bash
mkdir -p src/leve/security
git mv src/leve/auth.py src/leve/security/auth.py
git mv src/leve/credentials.py src/leve/security/credentials.py
git mv src/leve/platform_auth.py src/leve/security/platform_auth.py
```

- [ ] **Step 2: Create `src/leve/security/__init__.py`**

```python
"""Security — principals, credential brokering, and platform auth helpers.

Per-caller permission scoping (``Principal``) and credential resolution
(``CredentialBroker`` and friends) live here. Modules are also re-exported from
their historical top-level paths (``leve.auth``, ``leve.credentials``,
``leve.platform_auth``) for backward compatibility.
"""
```

- [ ] **Step 3: Rewrite moved-sibling imports inside the security modules**

In `src/leve/security/credentials.py`, change line 22:

```python
from leve.security.auth import Credential, Principal
```
(was `from leve.auth import Credential, Principal`; lines 23–24 `from leve.config ...` / `from leve.errors ...` stay unchanged.)

In `src/leve/security/auth.py`, change the `TYPE_CHECKING` import (originally `from leve.credentials import CredentialBroker`):

```python
    from leve.security.credentials import CredentialBroker
```

In `src/leve/security/platform_auth.py`, change line 18:

```python
from leve.security.auth import Principal
```

- [ ] **Step 4: Create the three shims**

`src/leve/auth.py`:
```python
"""Backward-compatible shim — canonical home is :mod:`leve.security.auth`."""
from leve.security.auth import *  # noqa: F401,F403
from leve.security.auth import (  # noqa: F401  explicit public re-exports
    Credential,
    InjectedPrincipal,
    Principal,
    anonymous,
    app_principal,
    current_principal,
    reset_current_principal,
    set_current_principal,
    with_broker,
)
```

`src/leve/credentials.py`:
```python
"""Backward-compatible shim — canonical home is :mod:`leve.security.credentials`."""
from leve.security.credentials import *  # noqa: F401,F403
from leve.security.credentials import (  # noqa: F401  explicit public re-exports
    CredentialBroker,
    NeedsConsent,
    OAuthStoreBroker,
    StaticBroker,
    TokenExchangeBroker,
    create_broker,
)
```

`src/leve/platform_auth.py`:
```python
"""Backward-compatible shim — canonical home is :mod:`leve.security.platform_auth`."""
from leve.security.platform_auth import *  # noqa: F401,F403
from leve.security.platform_auth import (  # noqa: F401  explicit public re-exports
    make_auth,
    store_namespace,
)
```

- [ ] **Step 5: Run the security shim test + full suite**

Run: `uv run pytest tests/test_restructure_shims.py::test_security_shims -q`
Expected: PASS (3 parametrized cases green).

Run: `uv run pytest -q`
Expected: All tests pass except `test_core_shims` and `test_serving_shims` (still erroring — `leve.core` / `leve.serving` don't exist yet). No *other* test regresses.

- [ ] **Step 6: Commit**

```bash
git add -A src/leve
git commit -m "refactor: move auth/credentials/platform_auth into leve.security with shims

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `leve/core/` package (the graph-compilation engine)

Move the 8-module compilation engine. `core` depends on `security` (already moved in Task 2) — so `core.middleware`/`core.subagents`/`core.runtime` are rewritten to `from leve.security.* import ...`, which now exists. `serving` is still flat and imports these via the old shims that this task creates.

**Files:**
- Create: `src/leve/core/__init__.py`
- Move: `agent.py graph.py models.py middleware.py instructions.py runtime.py skills.py subagents.py` from `src/leve/` → `src/leve/core/`
- Create (shims): the 8 same-named files back at `src/leve/`
- Test: `tests/test_restructure_shims.py::test_core_shims`

**Interfaces:**
- Consumes: `leve.security.auth` (`reset_current_principal`, `set_current_principal`, `current_principal`, `Principal`), `leve.security.credentials` (`NeedsConsent`) from Task 2.
- Produces: canonical paths `leve.core.{agent,graph,models,middleware,instructions,runtime,skills,subagents}` and 8 shims. Later tasks import `from leve.core.runtime import LeveContext`, `from leve.core.graph import build_graph`.

- [ ] **Step 1: Create the package and move the eight modules**

```bash
mkdir -p src/leve/core
git mv src/leve/agent.py        src/leve/core/agent.py
git mv src/leve/graph.py        src/leve/core/graph.py
git mv src/leve/models.py       src/leve/core/models.py
git mv src/leve/middleware.py   src/leve/core/middleware.py
git mv src/leve/instructions.py src/leve/core/instructions.py
git mv src/leve/runtime.py      src/leve/core/runtime.py
git mv src/leve/skills.py       src/leve/core/skills.py
git mv src/leve/subagents.py    src/leve/core/subagents.py
```

- [ ] **Step 2: Create `src/leve/core/__init__.py`**

```python
"""Core — the graph-compilation engine.

``graph.build_graph`` wires agent specs, models, instructions, middleware,
skills, and subagents into a runnable LangGraph graph. These modules are also
re-exported from their historical top-level paths (``leve.agent``,
``leve.graph``, etc.) for backward compatibility.
"""
```

- [ ] **Step 3: Rewrite moved-sibling imports inside the core modules**

`src/leve/core/agent.py`: no `leve.*` imports — leave unchanged.

`src/leve/core/graph.py` — rewrite the moved-sibling imports (lines 32–39). `from leve.loader import LoadedAgent` (line 34) stays unchanged:
```python
from leve.core.agent import CompactionConfig
from leve.core.instructions import make_prompt_middleware
from leve.loader import LoadedAgent
from leve.core.middleware import ApprovalMiddleware, PrincipalMiddleware
from leve.core.models import build_model
from leve.core.runtime import LeveContext
from leve.core.skills import make_load_skill_tool
from leve.core.subagents import make_delegation_tool
```

`src/leve/core/models.py` — line 18:
```python
from leve.core.agent import AgentSpec
```

`src/leve/core/middleware.py` — lines 27–28 (`from leve.tools import ToolSpec` on line 29 stays unchanged):
```python
from leve.security.auth import reset_current_principal, set_current_principal
from leve.security.credentials import NeedsConsent
```

`src/leve/core/instructions.py` — line 18:
```python
from leve.core.runtime import LeveContext
```

`src/leve/core/runtime.py` — the `TYPE_CHECKING` import (originally `from leve.auth import Principal`):
```python
    from leve.security.auth import Principal
```

`src/leve/core/skills.py`: only `from leve.errors import LoaderError` (non-moved) — leave unchanged.

`src/leve/core/subagents.py` — lines 24 and 26 (`from leve.errors import LoaderError` on line 25 stays unchanged):
```python
from leve.security.auth import current_principal
from leve.errors import LoaderError
from leve.core.runtime import LeveContext
```

- [ ] **Step 4: Create the eight shims**

`src/leve/agent.py`:
```python
"""Backward-compatible shim — canonical home is :mod:`leve.core.agent`."""
from leve.core.agent import *  # noqa: F401,F403
from leve.core.agent import (  # noqa: F401  explicit public re-exports
    AgentSpec,
    CompactionConfig,
    TriggerClause,
    define_agent,
)
```

`src/leve/graph.py`:
```python
"""Backward-compatible shim — canonical home is :mod:`leve.core.graph`."""
from leve.core.graph import *  # noqa: F401,F403
from leve.core.graph import (  # noqa: F401  explicit public re-exports
    ExtraToolsResolver,
    build_graph,
)
```

`src/leve/models.py`:
```python
"""Backward-compatible shim — canonical home is :mod:`leve.core.models`."""
from leve.core.models import *  # noqa: F401,F403
from leve.core.models import build_model  # noqa: F401  explicit public re-export
```

`src/leve/middleware.py`:
```python
"""Backward-compatible shim — canonical home is :mod:`leve.core.middleware`."""
from leve.core.middleware import *  # noqa: F401,F403
from leve.core.middleware import (  # noqa: F401  explicit public re-exports
    ApprovalMiddleware,
    PrincipalMiddleware,
)
```

`src/leve/instructions.py`:
```python
"""Backward-compatible shim — canonical home is :mod:`leve.core.instructions`."""
from leve.core.instructions import *  # noqa: F401,F403
from leve.core.instructions import (  # noqa: F401  explicit public re-exports
    make_prompt_middleware,
    render_instructions,
)
```

`src/leve/runtime.py`:
```python
"""Backward-compatible shim — canonical home is :mod:`leve.core.runtime`."""
from leve.core.runtime import *  # noqa: F401,F403
from leve.core.runtime import LeveContext  # noqa: F401  explicit public re-export
```

`src/leve/skills.py`:
```python
"""Backward-compatible shim — canonical home is :mod:`leve.core.skills`."""
from leve.core.skills import *  # noqa: F401,F403
from leve.core.skills import (  # noqa: F401  explicit public re-exports
    SkillSpec,
    make_load_skill_tool,
    parse_skill,
)
```

`src/leve/subagents.py`:
```python
"""Backward-compatible shim — canonical home is :mod:`leve.core.subagents`."""
from leve.core.subagents import *  # noqa: F401,F403
from leve.core.subagents import (  # noqa: F401  explicit public re-exports
    DelegateInput,
    make_delegation_tool,
)
```

- [ ] **Step 5: Run the core shim test + full suite**

Run: `uv run pytest tests/test_restructure_shims.py::test_core_shims -q`
Expected: PASS (8 parametrized cases green).

Run: `uv run pytest -q`
Expected: All tests pass except `test_serving_shims` (still erroring — `leve.serving` doesn't exist yet). `test_security_shims` and `test_core_shims` green; no other test regresses.

- [ ] **Step 6: Commit**

```bash
git add -A src/leve
git commit -m "refactor: move graph-compilation engine into leve.core with shims

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `leve/serving/` package (server, session, events, app, cli, tui)

Move the run/serve surface. `serving` depends on `core` and `security` (both moved) plus its own siblings. This is also where the `session.py` → `events` in-package import is normalized, and where the console-script entry point is verified.

**Files:**
- Create: `src/leve/serving/__init__.py`
- Move: `server.py session.py events.py app.py cli.py tui.py` from `src/leve/` → `src/leve/serving/`
- Create (shims): the 6 same-named files back at `src/leve/`
- Test: `tests/test_restructure_shims.py::test_serving_shims`

**Interfaces:**
- Consumes: `leve.core.runtime.LeveContext`, `leve.core.graph.build_graph` (Task 3); `leve.security.auth` (`anonymous`, `with_broker`), `leve.security.credentials.create_broker` (Task 2).
- Produces: canonical paths `leve.serving.{server,session,events,app,cli,tui}` and 6 shims. `leve.serving.cli:app` is the console-script target.

- [ ] **Step 1: Create the package and move the six modules**

```bash
mkdir -p src/leve/serving
git mv src/leve/server.py  src/leve/serving/server.py
git mv src/leve/session.py src/leve/serving/session.py
git mv src/leve/events.py  src/leve/serving/events.py
git mv src/leve/app.py     src/leve/serving/app.py
git mv src/leve/cli.py     src/leve/serving/cli.py
git mv src/leve/tui.py     src/leve/serving/tui.py
```

- [ ] **Step 2: Create `src/leve/serving/__init__.py`**

```python
"""Serving — the run / serve surface.

The HTTP API (``server``), CLI (``cli``), TUI (``tui``), normalized event
stream (``events``), and the session loop (``session``/``app``) that drives a
compiled agent. These modules are also re-exported from their historical
top-level paths (``leve.server``, ``leve.cli``, etc.) for backward
compatibility.
"""
```

- [ ] **Step 3: Rewrite moved-sibling imports inside the serving modules**

`src/leve/serving/server.py` — rewrite moved-sibling imports (lines 26–32 and 290). `from leve.config ...`, `from leve.errors ...`, `from leve.loader ...`, and `from leve.schedules import run_schedule` (line 290) stay unchanged:
```python
from leve.serving.app import build_runtime
from leve.security.auth import anonymous, with_broker
from leve.config import LeveConfig
from leve.errors import ConfigError
from leve.loader import load_project
from leve.core.runtime import LeveContext
from leve.serving.session import AgentRuntime, extract_reply
```

`src/leve/serving/session.py` — rewrite lines 22–25 (`from leve.loader import LoadedAgent` on line 24 stays unchanged). Note the `events` module-object import becomes `from leve.serving import events`:
```python
from leve.serving import events
from leve.serving.events import EventNormalizer
from leve.loader import LoadedAgent
from leve.core.runtime import LeveContext
```

`src/leve/serving/events.py`: no `leve.*` imports — leave unchanged.

`src/leve/serving/app.py` — rewrite the moved-sibling imports among lines 18–27. `config`, `connections`, `evals`, `loader`, `persistence`, `sandbox`, `tracing` stay unchanged:
```python
from leve.config import LeveConfig
from leve.connections import discover_tools
from leve.security.credentials import create_broker
from leve.evals import EvalResult, EvalSpec, run_eval
from leve.core.graph import build_graph
from leve.loader import LoadedAgent, discovery, load_project
from leve.persistence import open_checkpointer, open_store
from leve.sandbox import create_sandbox, make_sandbox_tools
from leve.serving.session import AgentRuntime
from leve.tracing import configure_tracing
```

`src/leve/serving/cli.py` — rewrite the three moved-sibling imports; leave all `scaffold`, `config`, `errors`, `deploy`, `loader`, `devlog` imports unchanged. Specifically:
- line 18 `from leve.app import inspect_project` → `from leve.serving.app import inspect_project`
- line 79 `from leve.app import run_evals` → `from leve.serving.app import run_evals`
- line 231 `from leve.server import create_app` → `from leve.serving.server import create_app`
- line 277 `from leve.tui import run_tui` → `from leve.serving.tui import run_tui`

`src/leve/serving/tui.py` — line 22:
```python
from leve.serving.server import API_PREFIX
```

- [ ] **Step 4: Create the six shims**

`src/leve/server.py`:
```python
"""Backward-compatible shim — canonical home is :mod:`leve.serving.server`."""
from leve.serving.server import *  # noqa: F401,F403
from leve.serving.server import (  # noqa: F401  explicit public re-exports
    API_PREFIX,
    MessageBody,
    ResumeBody,
    SessionBroker,
    SessionManager,
    create_app,
)
```

`src/leve/session.py`:
```python
"""Backward-compatible shim — canonical home is :mod:`leve.serving.session`."""
from leve.serving.session import *  # noqa: F401,F403
from leve.serving.session import (  # noqa: F401  explicit public re-exports
    AgentRuntime,
    extract_reply,
)
```

`src/leve/events.py`:
```python
"""Backward-compatible shim — canonical home is :mod:`leve.serving.events`."""
from leve.serving.events import *  # noqa: F401,F403
from leve.serving.events import (  # noqa: F401  explicit public re-exports
    EventNormalizer,
    approval_requested,
    error,
    turn_end,
    turn_start,
)
```

`src/leve/app.py`:
```python
"""Backward-compatible shim — canonical home is :mod:`leve.serving.app`."""
from leve.serving.app import *  # noqa: F401,F403
from leve.serving.app import (  # noqa: F401  explicit public re-exports
    build_runtime,
    inspect_project,
    load_evals,
    run_evals,
)
```

`src/leve/cli.py`:
```python
"""Backward-compatible shim — canonical home is :mod:`leve.serving.cli`."""
from leve.serving.cli import *  # noqa: F401,F403
from leve.serving.cli import (  # noqa: F401  explicit public re-exports
    app,
    channels_app,
    connections_app,
)
```

`src/leve/tui.py`:
```python
"""Backward-compatible shim — canonical home is :mod:`leve.serving.tui`."""
from leve.serving.tui import *  # noqa: F401,F403
from leve.serving.tui import (  # noqa: F401  explicit public re-exports
    LeveTUI,
    run_tui,
)
```

- [ ] **Step 5: Run the serving shim test + full suite**

Run: `uv run pytest tests/test_restructure_shims.py -q`
Expected: PASS — all of `test_security_shims`, `test_core_shims`, `test_serving_shims`, `test_public_api` green.

Run: `uv run pytest -q`
Expected: Entire suite passes (no test files were edited).

- [ ] **Step 6: Commit**

```bash
git add -A src/leve
git commit -m "refactor: move run/serve surface into leve.serving with shims

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Normalize `leve/__init__.py` sources, console script, and final verification

Point the package root and the console-script entry point at canonical paths (behavior unchanged — both resolve identically through shims, but canonical is the documented source), then run end-to-end import and CLI smoke checks.

**Files:**
- Modify: `src/leve/__init__.py`
- Modify: `pyproject.toml:33` (`[project.scripts]`)

**Interfaces:**
- Consumes: `leve.core.agent` (`AgentSpec`, `CompactionConfig`, `define_agent`), `leve.security.auth` (`Credential`, `Principal`); `leve.serving.cli:app`.
- Produces: nothing downstream — terminal task.

- [ ] **Step 1: Update `leve/__init__.py` import sources to canonical paths**

Replace the two import lines (keep `__all__`, `__version__`, and the docstring exactly as they are):

```python
from leve.core.agent import AgentSpec, CompactionConfig, define_agent
from leve.security.auth import Credential, Principal
```

- [ ] **Step 2: Update the console-script entry point**

In `pyproject.toml`, under `[project.scripts]`, change line 33:

```toml
leve = "leve.serving.cli:app"
```

- [ ] **Step 3: Reinstall so the entry point rebinds, then smoke-test imports**

Run:
```bash
uv sync && uv run python -c "import leve; from leve import define_agent, AgentSpec, CompactionConfig, Credential, Principal; print('public API OK', leve.__version__)"
```
Expected: prints `public API OK 0.1.0`, no traceback.

- [ ] **Step 4: Smoke-test the CLI entry point and a deep canonical import**

Run:
```bash
uv run leve --help >/dev/null && echo "CLI OK"
uv run python -c "from leve.core.graph import build_graph; from leve.serving.server import create_app; from leve.security.credentials import create_broker; print('canonical deep imports OK')"
```
Expected: `CLI OK` then `canonical deep imports OK`, no traceback.

- [ ] **Step 5: Full suite + lint, final green gate**

Run:
```bash
uv run pytest -q
uv run ruff check src/leve
```
Expected: all tests pass; ruff reports no errors for the moved modules and shims (the `# noqa` comments cover the intentional wildcard/unused re-exports).

- [ ] **Step 6: Commit**

```bash
git add src/leve/__init__.py pyproject.toml
git commit -m "refactor: point leve package root and console script at canonical paths

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage**
- `leve/core/` (8 modules) → Task 3. ✓
- `leve/serving/` (6 modules) → Task 4. ✓
- `leve/security/` (3 modules) → Task 2. ✓
- Stays-flat / untouched-subpackage modules → never moved; called out in Global Constraints. ✓
- Re-export shim pattern (wildcard + explicit named) → every shim in Tasks 2–4. ✓
- Shims permanent, no deprecation warnings → docstring-only shims, no `warnings` calls. ✓
- `leve/__init__.py` behavior unchanged, sources updated → Task 5 Step 1. ✓
- Internal import policy (moved siblings → canonical; non-moved → unchanged) → explicit per-file rewrites in Tasks 2–4 Step 3. ✓
- `session.py` → `from leve.serving import events` edge case → Task 4 Step 3. ✓
- `runtime.py` naming (no `leve.runtime` package conflict) → only a module-file shim is created at `leve/runtime.py`; no package dir. ✓
- console-script entry points → Task 5 Step 2 + Step 4 smoke test. ✓
- Star-import correctness (no `__all__` in any module → wildcard exports all public names; explicit lines back it up) → confirmed during data-gathering; harness asserts object identity. ✓
- Verification items 1–4 from the spec → Task 1 harness + Task 5 Steps 3–5. ✓

**2. Placeholder scan:** No `TBD`/`TODO`/"add error handling"/"similar to Task N". Every code step shows complete file content or the exact line(s) to change. ✓

**3. Type consistency:** Symbol names in the harness (Task 1), the rewrite map, and the shim re-export lists were taken verbatim from the source (`grep` of top-level `def`/`class`/assignments). `LeveContext`, `build_graph`, `create_app`, `create_broker`, `EventNormalizer`, `AgentRuntime`, `extract_reply`, `API_PREFIX` are used identically across producing/consuming tasks. ✓
