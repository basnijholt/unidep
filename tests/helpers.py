"""unidep tests."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from unidep._dependencies_parsing import yaml_to_toml

if TYPE_CHECKING:
    import sys

    if sys.version_info >= (3, 8):
        from typing import Literal
    else:  # pragma: no cover
        from typing_extensions import Literal


REPO_ROOT = Path(__file__).parent.parent


def maybe_as_toml(toml_or_yaml: Literal["toml", "yaml"], p: Path) -> Path:
    if toml_or_yaml == "toml":
        toml = yaml_to_toml(p)
        p.unlink()
        p = p.with_name("pyproject.toml")
        p.write_text(toml)
    return p
