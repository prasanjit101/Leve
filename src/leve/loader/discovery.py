"""Filesystem discovery primitives for the loader.

Two jobs, kept separate from *what* gets loaded:

1. **Import an agent file as a module** — under a per-project namespace package
   so that (a) relative imports inside the agent tree work (``from ..lib import
   …``), and (b) two different projects loaded in one process (the test suite,
   a multi-project host) never collide in ``sys.modules``.
2. **Collect objects by type** — the convention from SPEC §4.3: the loader finds
   ``ToolSpec`` / ``AgentSpec`` instances *by type*, so the symbol name a user
   chose is irrelevant.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import TypeVar

from leve.errors import LoaderError

T = TypeVar("T")

# Resolved project dir -> mounted namespace-package root. Stable across reloads
# of the same project so module identities don't churn needlessly.
_MOUNTED: dict[str, str] = {}
_mount_counter = 0


def mount_project(project_dir: Path) -> str:
    """Mount ``project_dir`` as a fresh namespace package, returning its root name.

    Any modules previously imported under this project's root are purged so a
    reload picks up edits (and so repeated test loads start clean).
    """

    global _mount_counter
    key = str(project_dir.resolve())

    if key in _MOUNTED:
        root = _MOUNTED[key]
        for name in [m for m in sys.modules if m == root or m.startswith(root + ".")]:
            del sys.modules[name]
    else:
        _mount_counter += 1
        root = f"_leve_project_{_mount_counter}"
        _MOUNTED[key] = root

    spec = importlib.machinery.ModuleSpec(name=root, loader=None, is_package=True)
    spec.submodule_search_locations = [key]
    module = importlib.util.module_from_spec(spec)
    sys.modules[root] = module
    return root


def import_path(root: str, project_dir: Path, path: Path) -> ModuleType:
    """Import a single ``.py`` file living under a mounted project root."""

    rel = path.resolve().relative_to(project_dir.resolve()).with_suffix("")
    dotted = ".".join((root, *rel.parts))
    try:
        return importlib.import_module(dotted)
    except Exception as exc:  # surface import-time errors with the offending file
        raise LoaderError(f"Failed to import {path}: {exc}") from exc


def collect_instances(module: ModuleType, type_: type[T]) -> list[T]:
    """Return module-level instances of ``type_`` in definition order, de-duped."""

    found: list[T] = []
    seen: set[int] = set()
    for value in vars(module).values():
        if isinstance(value, type_) and id(value) not in seen:
            seen.add(id(value))
            found.append(value)
    return found


def find_single(module: ModuleType, type_: type[T], *, what: str, path: Path) -> T:
    """Collect exactly one instance of ``type_``; raise a clear error otherwise."""

    found = collect_instances(module, type_)
    if not found:
        raise LoaderError(f"{path} defines no {what}.")
    if len(found) > 1:
        raise LoaderError(f"{path} defines {len(found)} {what}s; expected exactly one.")
    return found[0]


def python_files(directory: Path) -> list[Path]:
    """Sorted, non-dunder ``.py`` files directly inside ``directory`` (if it exists)."""

    if not directory.is_dir():
        return []
    return sorted(
        p
        for p in directory.iterdir()
        if p.is_file() and p.suffix == ".py" and not p.name.startswith("_")
    )
