"""unidep - Unified Conda and Pip requirements management."""

from unidep._conda_env import (
    create_conda_env_specification,
    write_conda_environment_file,
)
from unidep._conflicts import resolve_conflicts
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

__all__ = [
    "create_conda_env_specification",
    "filter_python_dependencies",
    "find_requirements_files",
    "get_python_dependencies",
    "parse_local_dependencies",
    "parse_requirements",
    "resolve_conflicts",
    "write_conda_environment_file",
    "__version__",
]
