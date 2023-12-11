"""Conda environment file generation functions."""
from __future__ import annotations

import sys
from collections import defaultdict
from copy import deepcopy
from typing import TYPE_CHECKING, NamedTuple, cast

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from unidep._conflicts import (
    VersionConflictError,
    _maybe_new_meta_with_combined_pinnings,
)
from unidep.platform_definitions import (
    PLATFORM_SELECTOR_MAP,
    CondaPip,
    CondaPlatform,
    Meta,
    Platform,
)
from unidep.utils import (
    add_comment_to_file,
    build_pep508_environment_marker,
    warn,
)

if TYPE_CHECKING:
    from pathlib import Path

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
    return cast(CondaPlatform, _platform)


def _extract_conda_pip_dependencies(
    resolved: dict[str, dict[Platform | None, dict[CondaPip, Meta]]],
) -> tuple[
    dict[str, dict[Platform | None, Meta]],
    dict[str, dict[Platform | None, Meta]],
]:
    """Extract and separate conda and pip dependencies."""
    conda: dict[str, dict[Platform | None, Meta]] = {}
    pip: dict[str, dict[Platform | None, Meta]] = {}
    for pkg, platform_data in resolved.items():
        for _platform, sources in platform_data.items():
            if "conda" in sources:
                conda.setdefault(pkg, {})[_platform] = sources["conda"]
            else:
                pip.setdefault(pkg, {})[_platform] = sources["pip"]
    return conda, pip


def _resolve_multiple_platform_conflicts(
    platform_to_meta: dict[Platform | None, Meta],
) -> None:
    """Fix conflicts for deps with platforms that map to a single Conda platform.

    In a Conda environment with dependencies across various platforms (like
    'linux-aarch64', 'linux64'), this function ensures consistency in metadata
    for each Conda platform (e.g., 'sel(linux): ...'). It maps each platform to
    a Conda platform and resolves conflicts by retaining the first `Meta` object
    per Conda platform, discarding others. This approach guarantees uniform
    metadata across different but equivalent platforms.
    """
    valid: dict[
        CondaPlatform,
        dict[Meta, list[Platform | None]],
    ] = defaultdict(lambda: defaultdict(list))
    for _platform, meta in platform_to_meta.items():
        assert _platform is not None
        conda_platform = _conda_sel(_platform)
        valid[conda_platform][meta].append(_platform)

    for conda_platform, meta_to_platforms in valid.items():
        # We cannot distinguish between e.g., linux-64 and linux-aarch64
        # (which becomes linux). So of the list[Platform] we only need to keep
        # one Platform. We can pop the rest from `platform_to_meta`. This is
        # not a problem because they share the same `Meta` object.
        for platforms in meta_to_platforms.values():
            for j, _platform in enumerate(platforms):
                if j >= 1:
                    platform_to_meta.pop(_platform)

        # Now make sure that valid[conda_platform] has only one key.
        # That means that all `Meta`s for the different Platforms that map to a
        # CondaPlatform are identical. If len > 1, we have a conflict.
        if len(meta_to_platforms) > 1:
            metas, (first_platform, *_) = zip(*meta_to_platforms.items())
            first, *others = metas
            try:
                meta = _maybe_new_meta_with_combined_pinnings(metas)  # type: ignore[arg-type]
            except VersionConflictError:
                # We have a conflict, select the first one.
                msg = (
                    f"Dependency Conflict on '{conda_platform}':\n"
                    f"Multiple versions detected. Retaining '{first.pprint()}' and"
                    f" discarding conflicts: {', '.join(o.pprint() for o in others)}."
                )
                warn(msg, stacklevel=2)
            else:
                # Means that we could combine the pinnings
                meta_to_platforms.pop(first)
                meta_to_platforms[meta] = [first_platform]

            for other in others:
                platforms = meta_to_platforms[other]
                for _platform in platforms:
                    if _platform in platform_to_meta:  # might have been popped already
                        platform_to_meta.pop(_platform)
        # Now we have only one `Meta` left, so we can select it.


def _add_comment(commment_seq: CommentedSeq, platform: Platform) -> None:
    comment = f"# [{PLATFORM_SELECTOR_MAP[platform][0]}]"
    commment_seq.yaml_add_eol_comment(comment, len(commment_seq) - 1)


def create_conda_env_specification(  # noqa: PLR0912
    resolved: dict[str, dict[Platform | None, dict[CondaPip, Meta]]],
    channels: list[str],
    platforms: list[Platform],
    selector: Literal["sel", "comment"] = "sel",
) -> CondaEnvironmentSpec:
    """Create a conda environment specification from resolved requirements."""
    if selector not in ("sel", "comment"):  # pragma: no cover
        msg = f"Invalid selector: {selector}, must be one of ['sel', 'comment']"
        raise ValueError(msg)

    # Split in conda and pip dependencies and prefer conda over pip
    conda, pip = _extract_conda_pip_dependencies(resolved)

    conda_deps: list[str | dict[str, str]] = CommentedSeq()
    pip_deps: list[str] = CommentedSeq()
    seen_identifiers: set[str] = set()
    for platform_to_meta in conda.values():
        if len(platform_to_meta) > 1 and selector == "sel":
            # None has been expanded already if len>1
            _resolve_multiple_platform_conflicts(platform_to_meta)
        for _platform, meta in sorted(platform_to_meta.items()):
            dep_str = meta.name_with_pin()
            if len(platforms) != 1 and _platform is not None:
                if selector == "sel":
                    sel = _conda_sel(_platform)
                    dep_str = {f"sel({sel})": dep_str}  # type: ignore[assignment]
                conda_deps.append(dep_str)
                if selector == "comment":
                    _add_comment(conda_deps, _platform)
            else:
                conda_deps.append(dep_str)
            assert isinstance(meta.identifier, str)
            seen_identifiers.add(meta.identifier)

    for platform_to_meta in pip.values():
        meta_to_platforms: dict[Meta, list[Platform | None]] = {}
        for _platform, meta in platform_to_meta.items():
            meta_to_platforms.setdefault(meta, []).append(_platform)

        for meta, _platforms in meta_to_platforms.items():
            if meta.identifier in seen_identifiers:
                continue

            dep_str = meta.name_with_pin()
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
                        _add_comment(pip_deps, cast(Platform, _platform))
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
