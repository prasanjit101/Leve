# Open-Source Readiness — Design

**Date:** 2026-06-21
**Status:** Approved (pending spec review)

## Goal

Add the standard scaffolding required to publish Leve as a credible open-source
project. The codebase itself is already clean (MIT declared in `pyproject.toml`,
no committed secrets or build artifacts, single-author history). This work is
purely additive: legal, community, contribution, and quality-tooling files plus
README polish. No source behavior changes.

## Decisions

| Topic | Decision |
|-------|----------|
| License | MIT (matches existing `pyproject.toml`) |
| Copyright holder | Prasanjit Dutta |
| Scaffolding scope | Full standard set |
| Linter/formatter | Ruff (lint + format) |
| Quality gate | Pre-commit hooks **and** GitHub Actions CI scoped to `main` |

## Deliverables

### 1. Legal
- `LICENSE` — full MIT text, `Copyright (c) 2026 Prasanjit Dutta`.

### 2. Community & contribution
- `CONTRIBUTING.md` — environment setup (`uv sync`), the `leve` dev/build/eval/
  deploy commands, running tests (`uv run pytest`), installing pre-commit,
  coding standards (point to `CLAUDE.md`), branch/PR conventions, pointer to
  `SPEC.md` for design.
- `CODE_OF_CONDUCT.md` — Contributor Covenant 2.1, contact via the maintainer
  email.
- `SECURITY.md` — private vulnerability-reporting process. Relevant because the
  framework handles auth, credentials, and sandboxed compute.

### 3. GitHub templates (`.github/`)
- `ISSUE_TEMPLATE/bug_report.md`
- `ISSUE_TEMPLATE/feature_request.md`
- `ISSUE_TEMPLATE/config.yml`
- `PULL_REQUEST_TEMPLATE.md`

### 4. Quality tooling
- `.pre-commit-config.yaml` — `ruff` (lint, with `--fix`) + `ruff-format`, plus
  pre-commit-hooks basics: trailing-whitespace, end-of-file-fixer,
  check-yaml, check-toml, check-added-large-files.
- `[tool.ruff]` added to `pyproject.toml` — `target-version = "py312"`,
  line length 88, a sensible default rule set (E, F, I, UP, B) consistent with
  the existing code style.
- `.github/workflows/ci.yml` — triggers on push to `main` and pull requests
  targeting `main`. Steps: checkout, install `uv`, `uv sync --extra dev`,
  `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`.
  Single job on Python 3.12.

### 5. README polish
- Badges: License (MIT), Python version (3.12+), CI status.
- New sections: **Contributing** (link to `CONTRIBUTING.md`), **License**
  (link to `LICENSE`), and a Code-of-Conduct pointer.

## Out of scope (YAGNI)
- Multi-version / multi-OS CI matrix — single Python 3.12 job is enough to start.
- Release automation / PyPI publishing workflow.
- Changelog tooling.
- Any refactoring of `src/leve` source.

## Verification
- `uv run ruff check .` and `uv run ruff format --check .` pass (or any
  formatting changes are applied and committed).
- `uv run pytest` passes.
- `pre-commit run --all-files` passes.
- README renders with working links and badges.
