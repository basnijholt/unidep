#!/usr/bin/env python3
"""unidep - Unified Conda and Pip requirements management.

This module provides setuptools integration for unidep.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from unidep._build_system_integration import _deps
from unidep.utils import parse_folder_or_filename

if TYPE_CHECKING:
    from setuptools import Distribution


def _setuptools_finalizer(dist: Distribution) -> None:  # pragma: no cover
    """Entry point called by setuptools to get the dependencies for a project."""
    # PEP 517 says that "All hooks are run with working directory set to the
    # root of the source tree".
    project_root = Path.cwd()
    try:
        requirements_file = parse_folder_or_filename(project_root).path
    except FileNotFoundError:
        return
    if requirements_file.exists() and dist.install_requires:  # type: ignore[attr-defined]
        msg = (
            "You have a `requirements.yaml` file in your project root or"
            " configured unidep in `pyproject.toml` with `[tool.unidep]`,"
            " but you are also using setuptools' `install_requires`."
            " Remove the `install_requires` line from `setup.py`."
        )
        raise RuntimeError(msg)

    deps = _deps(requirements_file)
    dist.install_requires = deps.dependencies  # type: ignore[attr-defined]

    if deps.extras:
        dist.extras_require = deps.extras  # type: ignore[attr-defined]
