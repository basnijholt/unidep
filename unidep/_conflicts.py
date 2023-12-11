"""Conflict resolution for `unidep`."""

from __future__ import annotations

import sys
from collections import defaultdict
from typing import TYPE_CHECKING

from packaging import version

from unidep.platform_definitions import Meta, Platform
from unidep.utils import warn

if sys.version_info >= (3, 8):
    from typing import get_args
else:  # pragma: no cover
    from typing_extensions import get_args


if TYPE_CHECKING:
    from unidep.platform_definitions import CondaPip

VALID_OPERATORS = ["<=", ">=", "<", ">", "="]


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
            _platforms = meta.platforms()
            if _platforms is None:
                _platforms = [None]  # type: ignore[list-item]
            for _platform in _platforms:
                grouped_metas[_platform][meta.which].append(meta)

        # Convert defaultdicts to dicts
        prepared_data[package] = {k: dict(v) for k, v in grouped_metas.items()}
    return prepared_data


def _pop_unused_platforms_and_maybe_expand_none(
    platform_data: dict[Platform | None, dict[CondaPip, list[Meta]]],
    platforms: list[Platform] | None,
) -> None:
    """Expand `None` to all platforms if there is a platform besides None."""
    allowed_platforms = get_args(Platform)
    if platforms:
        allowed_platforms = platforms  # type: ignore[assignment]

    # If there is a platform besides None, expand None to all platforms
    if len(platform_data) > 1 and None in platform_data:
        sources = platform_data.pop(None)
        for _platform in allowed_platforms:
            for which, metas in sources.items():
                platform_data.setdefault(_platform, {}).setdefault(which, []).extend(
                    metas,
                )

    # Remove platforms that are not allowed
    to_pop = platform_data.keys() - allowed_platforms
    to_pop.discard(None)
    for _platform in to_pop:
        platform_data.pop(_platform)


def _maybe_new_meta_with_combined_pinnings(
    metas: list[Meta],
) -> Meta:
    pinned_metas = [m for m in metas if m.pin is not None]
    if len(pinned_metas) > 1:
        first = pinned_metas[0]
        pins = [m.pin for m in pinned_metas]
        pin = combine_version_pinnings(pins, name=first.name)  # type: ignore[arg-type]
        return Meta(
            name=first.name,
            which=first.which,
            comment=None,
            pin=pin,
            identifier=first.identifier,  # should I create a new one?
        )

    # Flatten the list
    return metas[0]


def _combine_pinning_within_platform(
    data: dict[Platform | None, dict[CondaPip, list[Meta]]],
) -> dict[Platform | None, dict[CondaPip, Meta]]:
    reduced_data: dict[Platform | None, dict[CondaPip, Meta]] = {}
    for _platform, packages in data.items():
        reduced_data[_platform] = {}
        for which, metas in packages.items():
            meta = _maybe_new_meta_with_combined_pinnings(metas)
            reduced_data[_platform][which] = meta
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
    warn(
        "Version Pinning Conflict:\n"
        f"Different version specifications for Conda ('{conda_meta.pin}') and Pip"
        f" ('{pip_meta.pin}'). Both versions are retained.",
        stacklevel=2,
    )
    return {"conda": conda_meta, "pip": pip_meta}


class VersionConflictError(ValueError):
    """Raised when a version conflict is detected."""


def resolve_conflicts(
    requirements: dict[str, list[Meta]],
    platforms: list[Platform] | None = None,
) -> dict[str, dict[Platform | None, dict[CondaPip, Meta]]]:
    """Resolve conflicts in a dictionary of requirements.

    Uses the ``ParsedRequirements.requirements`` dict returned by
    `parse_yaml_requirements`.
    """
    if platforms and not set(platforms).issubset(get_args(Platform)):
        msg = f"Invalid platform: {platforms}, must contain only {get_args(Platform)}"
        raise VersionConflictError(msg)

    prepared = _prepare_metas_for_conflict_resolution(requirements)
    for data in prepared.values():
        _pop_unused_platforms_and_maybe_expand_none(data, platforms)
    resolved = {
        pkg: _combine_pinning_within_platform(data) for pkg, data in prepared.items()
    }

    for _platforms in resolved.values():
        for _platform, sources in _platforms.items():
            _platforms[_platform] = _resolve_conda_pip_conflicts(sources)
    return resolved


def _parse_pinning(pinning: str) -> tuple[str, version.Version]:
    """Separates the operator and the version number."""
    pinning = pinning.strip()
    for operator in VALID_OPERATORS:
        if pinning.startswith(operator):
            version_part = pinning[len(operator) :].strip()
            if version_part:
                try:
                    return operator, version.parse(version_part)
                except version.InvalidVersion:
                    break
            else:
                break  # Empty version string

    msg = f"Invalid version pinning: '{pinning}', must start with one of {VALID_OPERATORS}"  # noqa: E501
    raise VersionConflictError(msg)


def _is_redundant(pinning: str, other_pinnings: list[str]) -> bool:
    """Determines if a version pinning is redundant given a list of other pinnings."""
    op, version = _parse_pinning(pinning)

    for other in other_pinnings:
        other_op, other_version = _parse_pinning(other)
        if other == pinning:
            continue

        if op == "<" and (
            other_op == "<"
            and version >= other_version
            or other_op == "<="
            and version > other_version
        ):
            return True
        if op == "<=" and other_op in ["<", "<="] and version >= other_version:
            return True
        if op == ">" and (
            other_op == ">"
            and version <= other_version
            or other_op == ">="
            and version < other_version
        ):
            return True
        if op == ">=" and other_op in [">", ">="] and version <= other_version:
            return True

    return False


def _is_valid_pinning(pinning: str) -> bool:
    """Checks if a version pinning string is valid."""
    if any(op in pinning for op in VALID_OPERATORS):
        try:
            # Attempt to parse the version part of the pinning
            _parse_pinning(pinning)
            return True  # noqa: TRY300
        except VersionConflictError:
            # If parsing fails, the pinning is not valid
            return False
    # If the pinning doesn't contain any recognized operator, it's not valid
    return False


def _deduplicate(pinnings: list[str]) -> list[str]:
    """Removes duplicate strings."""
    return list(dict.fromkeys(pinnings))  # preserve order


def _split_pinnings(metas: list[str]) -> list[str]:
    """Extracts version pinnings from a list of Meta objects."""
    return [_pin.strip().replace(" ", "") for pin in metas for _pin in pin.split(",")]


def combine_version_pinnings(pinnings: list[str], *, name: str | None = None) -> str:
    """Combines a list of version pinnings into a single string."""
    pinnings = _split_pinnings(pinnings)
    pinnings = _deduplicate(pinnings)
    valid_pinnings = [p for p in pinnings if _is_valid_pinning(p)]
    if not valid_pinnings:
        return ""

    exact_pinnings = [p for p in valid_pinnings if p.startswith("=")]
    if len(exact_pinnings) > 1:
        pinnings_str = ", ".join(exact_pinnings)
        msg = f"Multiple exact version pinnings found: {pinnings_str} for `{name}`"
        raise VersionConflictError(msg)

    err_msg = f"Contradictory version pinnings found for `{name}`"

    if exact_pinnings:
        exact_pin = exact_pinnings[0]
        exact_version = version.parse(exact_pin[1:])
        for other_pin in valid_pinnings:
            if other_pin != exact_pin:
                op, ver = _parse_pinning(other_pin)
                if not (
                    (op == "<" and exact_version < ver)
                    or (op == "<=" and exact_version <= ver)
                    or (op == ">" and exact_version > ver)
                    or (op == ">=" and exact_version >= ver)
                ):
                    msg = f"{err_msg}: {exact_pin} and {other_pin}"
                    raise VersionConflictError(msg)
        return exact_pin

    non_redundant_pinnings = [
        pin for pin in valid_pinnings if not _is_redundant(pin, valid_pinnings)
    ]

    for i, pin in enumerate(non_redundant_pinnings):
        for other_pin in non_redundant_pinnings[i + 1 :]:
            op1, ver1 = _parse_pinning(pin)
            op2, ver2 = _parse_pinning(other_pin)
            msg = f"{err_msg}: {pin} and {other_pin}"
            # Check for direct contradictions like >2 and <1
            if (op1 == ">" and op2 == "<" and ver1 >= ver2) or (
                op1 == "<" and op2 == ">" and ver1 <= ver2
            ):
                raise VersionConflictError(msg)

            # Check for contradictions involving inclusive bounds like >=2 and <1
            if (
                (op1 == ">=" and op2 == "<" and ver1 >= ver2)
                or (op1 == ">" and op2 == "<=" and ver1 >= ver2)
                or (op1 == "<=" and op2 == ">" and ver1 <= ver2)
                or (op1 == ">" and op2 == "<=" and ver1 >= ver2)
            ):
                raise VersionConflictError(msg)

    return ",".join(non_redundant_pinnings)
