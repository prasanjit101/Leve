# Contributing to Leve

Thanks for your interest in improving Leve! This guide covers how to set up a
development environment, the conventions we follow, and how to get a change
merged.

By participating in this project you agree to abide by our
[Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

Leve uses [uv](https://docs.astral.sh/uv/) for dependency management.

To install the environment:

```bash
git clone https://github.com/prasanjit101/Leve.git
cd Leve
uv sync                    # install runtime dependencies
uv sync --extra dev        # install runtime + dev dependencies
```

Useful commands while developing:

```bash
uv run leve init myagent   # scaffold a sample project
uv run leve dev            # dev server + TUI client
uv run leve build          # compile & validate without serving
uv run leve eval           # run eval suites
uv run leve deploy         # emit deploy artifacts
```

## Running the checks

Please make sure these pass before opening a pull request:

```bash
uv run ruff check .        # lint
uv run ruff format --check .   # formatting
uv run pytest              # tests
```

The same checks run in CI on every pull request targeting `main`.

## Pre-commit hooks

We use [pre-commit](https://pre-commit.com/) to run linting and formatting
automatically before each commit:

```bash
uv run pre-commit install      # one-time setup
uv run pre-commit run --all-files   # run against the whole repo
```

## Coding standards

The project's coding standards live in [`CLAUDE.md`](CLAUDE.md). In short: write
clean, modular, well-named code; follow DRY/YAGNI/SOLID; and keep modules
focused on a single responsibility. New behavior should come with tests.

The framework's full design lives in [`SPEC.md`](SPEC.md) — read it before
making changes to core behavior.

## Pull request process

1. Fork the repository and create a feature branch from `main`
   (`git checkout -b my-feature`).
2. Make your change with accompanying tests.
3. Run the checks above and make sure they pass.
4. Open a pull request against `main` using the PR template. Describe **what**
   changed and **why**, and link any related issues.
5. A maintainer will review. Address feedback by pushing additional commits to
   your branch.

## Reporting bugs and requesting features

Use the [issue templates](https://github.com/prasanjit101/Leve/issues/new/choose).
For security issues, please follow [SECURITY.md](SECURITY.md) instead of opening
a public issue.
