from __future__ import annotations

from pathlib import Path

from cleo.io.io import IO
from poetry.plugins.plugin import Plugin
from poetry.poetry import Poetry

from unidep._setuptools_integration import get_python_dependencies
from unidep.utils import identify_current_platform

__all__ = ["UniDepPlugin"]

# See example: https://github.com/mtkennerly/poetry-dynamic-versioning


class MyPlugin(Plugin):
    def activate(self, poetry: Poetry, io: IO):
        io.write_line("Setting dependencies with UniDep")
        project_root = Path().resolve()
        requirements_file = project_root / "requirements.yaml"
        if "dependencies" not in metadata.get("dynamic", []):
            return
        if not requirements_file.exists():
            return
        if "dependencies" in metadata:
            error_msg = (
                "You have a requirements.yaml file in your project root,"
                " but you are also using [project.dependencies]."
                " Please choose either requirements.yaml or"
                " [project.dependencies], but not both."
            )
            raise RuntimeError(error_msg)

        poetry.package.dependencies = get_python_dependencies(
            requirements_file,
            platforms=[identify_current_platform()],
            raises_if_missing=False,
        )
