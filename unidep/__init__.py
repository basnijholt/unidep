"""Top-level package for unidep."""

from unidep._conda_env import (
    create_conda_env_specification,
    write_conda_environment_file,
)
from unidep._conflicts import resolve_conflicts
from unidep._setuptools_integration import (
    filter_python_dependencies,
    get_python_dependencies,
)
from unidep._version import __version__
from unidep._yaml_parsing import (
    find_requirements_files,
    parse_project_dependencies,
    parse_yaml_requirements,
)

__all__ = [
    "create_conda_env_specification",
    "filter_python_dependencies",
    "find_requirements_files",
    "get_python_dependencies",
    "parse_project_dependencies",
    "parse_yaml_requirements",
    "resolve_conflicts",
    "write_conda_environment_file",
    "__version__",
]
