from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from unidep._dependencies_parsing import ParsedRequirements
    from unidep.platform_definitions import CondaPip, Platform, Spec

try:  # pragma: no cover
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib
    HAS_TOML = True
except ImportError:  # pragma: no cover
    HAS_TOML = False


def generate_pixi_toml(
    resolved_dependencies: dict[str, dict[Platform | None, dict[CondaPip, Spec]]],
    requirements: ParsedRequirements,
    output_file: str = "pixi.toml",
    *,
    verbose: bool = False,
) -> None:
    pixi_data = {}

    pixi_data["project"] = {
        "platforms": requirements.platforms,
        "channels": requirements.channels,
    }

    # Include extra configurations from pyproject.toml
    pixi_data.update(_parse_pixi_sections_from_pyproject())

    # Map unidep dependencies to pixi.toml sections
    pixi_data.setdefault("dependencies", {})
    pixi_data.setdefault("pypi-dependencies", {})

    # Add conda dependencies
    for dep in resolved_dependencies["conda"]:
        pixi_data["dependencies"][dep.name] = dep.pin or "*"

    # Add pip dependencies
    for dep in resolved_dependencies["pip"]:
        pixi_data["pypi-dependencies"][dep.name] = dep.pin or "*"

    # Write pixi.toml file
    with open(output_file, "w") as f:  # noqa: PTH123
        tomllib.dump(pixi_data, f)
    if verbose:
        print(f"✅ Generated pixi.toml at {output_file}")


def _parse_pixi_sections_from_pyproject() -> dict[str, Any]:
    pyproject_path = Path("pyproject.toml")
    if not pyproject_path.exists():
        return {}
    with pyproject_path.open("rb") as f:
        pyproject_data = tomllib.load(f)
    return pyproject_data.get("tool", {}).get("unidep", {}).get("pixi", {})
