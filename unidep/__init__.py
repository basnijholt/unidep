"""Top-level package for unidep."""

from unidep._conda_env import (
    create_conda_env_specification,
    write_conda_environment_file,
)
from unidep._conflicts import resolve_conflicts
from unidep._version import __version__
from unidep.base import (
    extract_matching_platforms,
    filter_python_dependencies,
    find_requirements_files,
    get_python_dependencies,
    parse_project_dependencies,
    parse_yaml_requirements,
    setuptools_finalizer,
)

__all__ = [
    "create_conda_env_specification",
    "extract_matching_platforms",
    "filter_python_dependencies",
    "find_requirements_files",
    "get_python_dependencies",
    "parse_project_dependencies",
    "parse_yaml_requirements",
    "resolve_conflicts",
    "setuptools_finalizer",
    "write_conda_environment_file",
    "__version__",
]
