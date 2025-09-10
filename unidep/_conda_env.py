"""unidep - Unified Conda and Pip requirements management.

Conda environment file generation functions.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from copy import deepcopy
from typing import TYPE_CHECKING, NamedTuple, cast

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from unidep._conflicts import (
    VersionConflictError,
    _maybe_new_spec_with_combined_pinnings,
)
from unidep._dependency_selection import (
    collapse_selected_universals,
    select_conda_like_requirements,
)
from unidep.platform_definitions import (
    PLATFORM_SELECTOR_MAP,
    CondaPlatform,
    Platform,
    Spec,
)
from unidep.utils import (
    add_comment_to_file,
    build_pep508_environment_marker,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from unidep._dependencies_parsing import DependencyEntry

if sys.version_info >= (3, 8):
    from typing import Literal, get_args
else:  # pragma: no cover
    from typing_extensions import Literal, get_args


class CondaEnvironmentSpec(NamedTuple):
    """A conda environment."""

    channels: list[str]
    pip_indices: list[str]
    platforms: list[Platform]
    conda: list[str | dict[str, str]]  # actually a CommentedSeq[str | dict[str, str]]
    pip: list[str]


def _conda_sel(sel: str) -> CondaPlatform:
    """Return the allowed `sel(platform)` string."""
    _platform = sel.split("-", 1)[0]
    assert _platform in get_args(CondaPlatform), f"Invalid platform: {_platform}"
    return cast("CondaPlatform", _platform)


def _as_dependency_entries(
    entries: Sequence[DependencyEntry],
) -> list[DependencyEntry]:
    if isinstance(entries, dict):
        msg = (
            "`create_conda_env_specification()` now requires dependency entries from "
            "`parse_requirements(...).dependency_entries`, not the output of "
            "`resolve_conflicts()`."
        )
        raise TypeError(msg)
    return list(entries)


def _extract_conda_pip_dependencies(
    entries: list[DependencyEntry],
    platforms: list[Platform],
) -> tuple[
    dict[str, dict[Platform | None, Spec]],
    dict[str, dict[Platform | None, Spec]],
]:
    """Extract dependencies using the shared conda-like selector."""
    conda: dict[str, dict[Platform | None, Spec]] = {}
    pip: dict[str, dict[Platform | None, Spec]] = {}
    selected = collapse_selected_universals(
        select_conda_like_requirements(entries, platforms),
        platforms,
    )
    for _platform, candidates in selected.items():
        for candidate in candidates:
            if candidate.source == "conda":
                conda.setdefault(candidate.spec.name, {})[_platform] = candidate.spec
            else:
                pip.setdefault(candidate.spec.name, {})[_platform] = candidate.spec
    return conda, pip


def _ensure_sel_representable(
    platform_to_spec: dict[Platform | None, Spec],
) -> None:
    """Ensure selected specs can be represented with `sel(...)` selectors."""
    grouped: dict[CondaPlatform, list[tuple[Platform, Spec]]] = defaultdict(list)
    for _platform, spec in sorted(platform_to_spec.items()):
        assert _platform is not None
        grouped[_conda_sel(_platform)].append((_platform, spec))

    for conda_platform, platform_specs in grouped.items():
        keep_platform = platform_specs[0][0]
        unique_specs = list(dict.fromkeys(spec for _, spec in platform_specs))
        if len(unique_specs) > 1:
            try:
                merged_spec = _maybe_new_spec_with_combined_pinnings(unique_specs)
            except VersionConflictError:
                msg = (
                    "Selected dependencies cannot be represented with `sel(...)` "
                    f"for '{conda_platform}'. Use selector='comment' instead."
                )
                raise ValueError(msg) from None
        else:
            merged_spec = unique_specs[0]

        for _platform, _spec in platform_specs:
            if _platform != keep_platform:
                platform_to_spec.pop(_platform, None)
        platform_to_spec[keep_platform] = merged_spec


def _add_comment(commment_seq: CommentedSeq, platform: Platform) -> None:
    comment = f"# [{PLATFORM_SELECTOR_MAP[platform][0]}]"
    commment_seq.yaml_add_eol_comment(comment, len(commment_seq) - 1)


def create_conda_env_specification(  # noqa: PLR0912
    entries: Sequence[DependencyEntry],
    channels: list[str],
    pip_indices: list[str],
    platforms: list[Platform],
    selector: Literal["sel", "comment"] = "sel",
) -> CondaEnvironmentSpec:
    """Create a conda environment specification from dependency entries."""
    if selector not in ("sel", "comment"):  # pragma: no cover
        msg = f"Invalid selector: {selector}, must be one of ['sel', 'comment']"
        raise ValueError(msg)

    entries = _as_dependency_entries(entries)
    conda, pip = _extract_conda_pip_dependencies(entries, platforms)

    conda_deps: list[str | dict[str, str]] = CommentedSeq()
    pip_deps: list[str] = CommentedSeq()
    for platform_to_spec in conda.values():
        if len(platform_to_spec) > 1 and selector == "sel":
            _ensure_sel_representable(platform_to_spec)
        for _platform, spec in sorted(platform_to_spec.items()):
            dep_str = spec.name_with_pin()
            if len(platforms) != 1 and _platform is not None:
                if selector == "sel":
                    sel = _conda_sel(_platform)
                    dep_str = {f"sel({sel})": dep_str}  # type: ignore[assignment]
                conda_deps.append(dep_str)
                if selector == "comment":
                    _add_comment(conda_deps, _platform)
            else:
                conda_deps.append(dep_str)

    for platform_to_spec in pip.values():
        spec_to_platforms: dict[Spec, list[Platform | None]] = {}
        for _platform, spec in platform_to_spec.items():
            spec_to_platforms.setdefault(spec, []).append(_platform)

        for spec, _platforms in spec_to_platforms.items():
            dep_str = spec.name_with_pin(is_pip=True)
            if _platforms != [None] and len(platforms) != 1:
                if selector == "sel":
                    marker = build_pep508_environment_marker(_platforms)  # type: ignore[arg-type]
                    dep_str = f"{dep_str}; {marker}"
                    pip_deps.append(dep_str)
                else:
                    assert selector == "comment"
                    # We can only add comments with a single platform because
                    # `conda-lock` doesn't implement logic, e.g., [linux or win]
                    # should be spread into two lines, one with [linux] and the
                    # other with [win].
                    for _platform in _platforms:
                        pip_deps.append(dep_str)
                        _add_comment(pip_deps, cast("Platform", _platform))
            else:
                pip_deps.append(dep_str)

    return CondaEnvironmentSpec(channels, pip_indices, platforms, conda_deps, pip_deps)


def write_conda_environment_file(
    env_spec: CondaEnvironmentSpec,
    output_file: str | Path | None = "environment.yaml",
    name: str = "myenv",
    *,
    verbose: bool = False,
) -> None:
    """Generate a conda environment.yaml file or print to stdout."""
    resolved_dependencies = deepcopy(env_spec.conda)
    if env_spec.pip:
        resolved_dependencies.append({"pip": env_spec.pip})  # type: ignore[arg-type, dict-item]
    env_data = CommentedMap({"name": name})
    if env_spec.channels:
        env_data["channels"] = env_spec.channels
    # Add pip_repositories for conda-lock compatibility
    if env_spec.pip_indices:
        env_data["pip_repositories"] = env_spec.pip_indices
    if resolved_dependencies:
        env_data["dependencies"] = resolved_dependencies
    if env_spec.platforms:
        env_data["platforms"] = env_spec.platforms
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=2, offset=2)
    if output_file:
        if verbose:
            print(f"📝 Generating environment file at `{output_file}`")
        with open(output_file, "w") as f:  # noqa: PTH123
            yaml.dump(env_data, f)
        if verbose:
            print("📝 Environment file generated successfully.")
        add_comment_to_file(output_file)
    else:
        yaml.dump(env_data, sys.stdout)
