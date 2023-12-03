"""Conflict resolution for `unidep`."""

from __future__ import annotations

import warnings
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from unidep.base import CondaPip, Meta, Platform


def _prepare_metas_for_conflict_resolution(
    requirements: dict[str, list[Meta]],
) -> dict[str, dict[Platform | None, dict[CondaPip, list[Meta]]]]:
    """Prepare and group metadata for conflict resolution.

    This function groups metadata by platform and source for each package.

    :param requirements: Dictionary mapping package names to a list of Meta objects.
    :return: Dictionary mapping package names to grouped metadata.
    """
    prepared_data = {}
    for package, meta_list in requirements.items():
        grouped_metas: dict[Platform | None, dict[CondaPip, list[Meta]]] = defaultdict(
            lambda: defaultdict(list),
        )
        for meta in meta_list:
            platforms = meta.platforms()
            if platforms is None:
                platforms = [None]  # type: ignore[list-item]
            for _platform in platforms:
                grouped_metas[_platform][meta.which].append(meta)
        # Convert defaultdicts to dicts
        prepared_data[package] = {k: dict(v) for k, v in grouped_metas.items()}
    return prepared_data


def _select_preferred_version_within_platform(
    data: dict[Platform | None, dict[CondaPip, list[Meta]]],
) -> dict[Platform | None, dict[CondaPip, Meta]]:
    reduced_data: dict[Platform | None, dict[CondaPip, Meta]] = {}
    for _platform, packages in data.items():
        reduced_data[_platform] = {}
        for which, metas in packages.items():
            if len(metas) > 1:
                # Sort metas by presence of version pin and then by the pin itself
                metas.sort(key=lambda m: (m.pin is not None, m.pin), reverse=True)
                # Keep the first Meta, which has the highest priority
                selected_meta = metas[0]
                discarded_metas = [m for m in metas[1:] if m != selected_meta]
                if discarded_metas:
                    discarded_metas_str = ", ".join(
                        f"`{m.pprint()}` ({m.which})" for m in discarded_metas
                    )
                    on_platform = _platform or "all platforms"
                    warnings.warn(
                        f"Platform Conflict Detected:\n"
                        f"On '{on_platform}', '{selected_meta.pprint()}' ({which})"
                        " is retained. The following conflicting dependencies are"
                        f" discarded: {discarded_metas_str}.",
                        stacklevel=2,
                    )
                reduced_data[_platform][which] = selected_meta
            else:
                # Flatten the list
                reduced_data[_platform][which] = metas[0]
    return reduced_data


def _resolve_conda_pip_conflicts(sources: dict[CondaPip, Meta]) -> dict[CondaPip, Meta]:
    conda_meta = sources.get("conda")
    pip_meta = sources.get("pip")
    if not conda_meta or not pip_meta:  # If either is missing, there is no conflict
        return sources

    # Compare version pins to resolve conflicts
    if conda_meta.pin and not pip_meta.pin:
        return {"conda": conda_meta}  # Prefer conda if it has a pin
    if pip_meta.pin and not conda_meta.pin:
        return {"pip": pip_meta}  # Prefer pip if it has a pin
    if conda_meta.pin == pip_meta.pin:
        return {"conda": conda_meta, "pip": pip_meta}  # Keep both if pins are identical

    # Handle conflict where both conda and pip have different pins
    warnings.warn(
        "Version Pinning Conflict:\n"
        f"Different version specifications for Conda ('{conda_meta.pin}') and Pip"
        f" ('{pip_meta.pin}'). Both versions are retained.",
        stacklevel=2,
    )
    return {"conda": conda_meta, "pip": pip_meta}


def resolve_conflicts(
    requirements: dict[str, list[Meta]],
) -> dict[str, dict[Platform | None, dict[CondaPip, Meta]]]:
    """Resolve conflicts in a dictionary of requirements.

    Uses the ``ParsedRequirements.requirements`` dict returned by
    `parse_yaml_requirements`.
    """
    prepared = _prepare_metas_for_conflict_resolution(requirements)

    resolved = {
        pkg: _select_preferred_version_within_platform(data)
        for pkg, data in prepared.items()
    }
    for platforms in resolved.values():
        for _platform, sources in platforms.items():
            platforms[_platform] = _resolve_conda_pip_conflicts(sources)
    return resolved
