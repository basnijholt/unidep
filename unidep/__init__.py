"""unidep - Unified Conda and Pip requirements management."""

from __future__ import annotations

import warnings

from unidep._conda_env import (
    create_conda_env_specification,
    write_conda_environment_file,
)
from unidep._conflicts import resolve_conflicts as _resolve_conflicts
from unidep._dependencies_parsing import (
    find_requirements_files,
    parse_local_dependencies,
    parse_requirements,
)
from unidep._setuptools_integration import (
    filter_python_dependencies,
    get_python_dependencies,
)
from unidep._version import __version__


def resolve_conflicts(*args: object, **kwargs: object) -> object:
    """Backward-compatible wrapper for ``unidep._conflicts.resolve_conflicts``."""
    warnings.warn(
        "`unidep.resolve_conflicts` is deprecated and will be removed in a future "
        "major release; import it from `unidep._conflicts` instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return _resolve_conflicts(*args, **kwargs)


__all__ = [
    "__version__",
    "create_conda_env_specification",
    "filter_python_dependencies",
    "find_requirements_files",
    "get_python_dependencies",
    "parse_local_dependencies",
    "parse_requirements",
    "resolve_conflicts",
    "write_conda_environment_file",
]
