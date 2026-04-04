"""unidep - Unified Conda and Pip requirements management.

Conda environment file generation functions.
"""

from __future__ import annotations

import sys
import warnings
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
    warn,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
    from typing import Dict

    from unidep._dependencies_parsing import DependencyEntry
    from unidep.platform_definitions import CondaPip

    ResolvedRequirements = Dict[str, Dict[Platform | None, Dict[CondaPip, Spec]]]
else:  # pragma: no cover
    ResolvedRequirements = dict

if sys.version_info >= (3, 8):
    from typing import Literal, get_args
else:  # pragma: no cover
    from typing_extensions import Literal, get_args


class CondaEnvironmentSpec(NamedTuple):
    """A conda environment."""

    channels: list[str]
    platforms: list[Platform]
    conda: list[str | dict[str, str]]  # actually a CommentedSeq[str | dict[str, str]]
    pip: list[str]


def _conda_sel(sel: str) -> CondaPlatform:
    """Return the allowed `sel(platform)` string."""
    _platform = sel.split("-", 1)[0]
    assert _platform in get_args(CondaPlatform), f"Invalid platform: {_platform}"
    return cast("CondaPlatform", _platform)


def _extract_conda_pip_dependencies(
    resolved: ResolvedRequirements,
) -> tuple[
    dict[str, dict[Platform | None, Spec]],
    dict[str, dict[Platform | None, Spec]],
]:
    """Extract and separate conda and pip dependencies."""
    conda: dict[str, dict[Platform | None, Spec]] = {}
    pip: dict[str, dict[Platform | None, Spec]] = {}
    for pkg, platform_data in resolved.items():
        for _platform, sources in platform_data.items():
            if "conda" in sources:
                conda.setdefault(pkg, {})[_platform] = sources["conda"]
            else:
                pip.setdefault(pkg, {})[_platform] = sources["pip"]
    return conda, pip


def _extract_conda_pip_dependencies_from_entries(
    entries: Sequence[DependencyEntry],
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


def _resolve_multiple_platform_conflicts(
    platform_to_spec: dict[Platform | None, Spec],
) -> None:
    """Best-effort reduction for platforms that collapse to one conda selector."""
    grouped: dict[
        CondaPlatform,
        dict[Spec, list[Platform | None]],
    ] = defaultdict(lambda: defaultdict(list))
    for _platform, spec in platform_to_spec.items():
        assert _platform is not None
        conda_platform = _conda_sel(_platform)
        grouped[conda_platform][spec].append(_platform)

    for conda_platform, spec_to_platforms in grouped.items():
        for platforms in spec_to_platforms.values():
            for index, _platform in enumerate(platforms):
                if index >= 1:
                    platform_to_spec.pop(_platform)

        if len(spec_to_platforms) > 1:
            ordered_specs = list(spec_to_platforms)
            first, *others = ordered_specs
            first_platform = spec_to_platforms[first][0]
            try:
                spec = _maybe_new_spec_with_combined_pinnings(ordered_specs)
            except VersionConflictError:
                warn(
                    f"Dependency Conflict on '{conda_platform}':\n"
                    f"Multiple versions detected. Retaining '{first.pprint()}' and"
                    f" discarding conflicts: {', '.join(o.pprint() for o in others)}.",
                    stacklevel=2,
                )
            else:
                spec_to_platforms.pop(first)
                spec_to_platforms[spec] = [first_platform]
                if first_platform in platform_to_spec:
                    platform_to_spec[first_platform] = spec

            for other in others:
                platforms = spec_to_platforms[other]
                for _platform in platforms:
                    if _platform in platform_to_spec:
                        platform_to_spec.pop(_platform)


def _add_comment(commment_seq: CommentedSeq, platform: Platform) -> None:
    comment = f"# [{PLATFORM_SELECTOR_MAP[platform][0]}]"
    commment_seq.yaml_add_eol_comment(comment, len(commment_seq) - 1)


def create_conda_env_specification(  # noqa: PLR0912
    entries: Sequence[DependencyEntry] | ResolvedRequirements,
    channels: list[str],
    platforms: list[Platform],
    selector: Literal["sel", "comment"] = "sel",
) -> CondaEnvironmentSpec:
    """Create a conda environment specification from dependency entries."""
    if selector not in ("sel", "comment"):  # pragma: no cover
        msg = f"Invalid selector: {selector}, must be one of ['sel', 'comment']"
        raise ValueError(msg)

    seen_identifiers: set[str] = set()
    if isinstance(entries, dict):
        warnings.warn(
            "`create_conda_env_specification()` accepting the dict returned by "
            "`resolve_conflicts()` is deprecated; pass "
            "`parse_requirements(...).dependency_entries` instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        conda, pip = _extract_conda_pip_dependencies(entries)
        suppress_shadowed_pip = True
    else:
        conda, pip = _extract_conda_pip_dependencies_from_entries(entries, platforms)
        suppress_shadowed_pip = False

    conda_deps: list[str | dict[str, str]] = CommentedSeq()
    pip_deps: list[str] = CommentedSeq()
    for platform_to_spec in conda.values():
        if len(platform_to_spec) > 1 and selector == "sel":
            _resolve_multiple_platform_conflicts(platform_to_spec)
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
            if suppress_shadowed_pip and spec.identifier is not None:
                seen_identifiers.add(spec.identifier)

    for platform_to_spec in pip.values():
        spec_to_platforms: dict[Spec, list[Platform | None]] = {}
        for _platform, spec in platform_to_spec.items():
            spec_to_platforms.setdefault(spec, []).append(_platform)

        for spec, _platforms in spec_to_platforms.items():
            if suppress_shadowed_pip and spec.identifier in seen_identifiers:
                continue
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

    return CondaEnvironmentSpec(channels, platforms, conda_deps, pip_deps)


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
