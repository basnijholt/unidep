"""Conflict resolution for `unidep`."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from packaging import version

from unidep.utils import warn

if TYPE_CHECKING:
    import sys

    from unidep.platform_definitions import CondaPip, Meta, Platform

    if sys.version_info >= (3, 8):
        from typing import Literal
    else:  # pragma: no cover
        from typing_extensions import Literal

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
    strategy: Literal["discard", "combine"] = "discard",  # noqa: ARG001
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
                    warn(
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
    warn(
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
    raise ValueError(msg)


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
        except ValueError:
            # If parsing fails, the pinning is not valid
            return False
    # If the pinning doesn't contain any recognized operator, it's not valid
    return False


def combine_version_pinnings(pinnings: list[str]) -> str:
    """Combines a list of version pinnings into a single string."""
    pinnings = [p.replace(" ", "") for p in pinnings if p]
    valid_pinnings = [p for p in pinnings if _is_valid_pinning(p)]
    if not valid_pinnings:
        return ""

    exact_pinnings = [p for p in valid_pinnings if p.startswith("=")]
    if len(exact_pinnings) > 1:
        msg = f"Multiple exact version pinnings found: {', '.join(exact_pinnings)}"
        raise ValueError(msg)

    err_msg = "Contradictory version pinnings found"
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
                    raise ValueError(msg)
        return exact_pin

    non_redundant_pinnings = [
        pin for pin in valid_pinnings if not _is_redundant(pin, valid_pinnings)
    ]

    for i, pin in enumerate(non_redundant_pinnings):
        for other_pin in non_redundant_pinnings[i + 1 :]:
            op1, ver1 = _parse_pinning(pin)
            op2, ver2 = _parse_pinning(other_pin)
            if (op1 in ["<", "<="] and op2 in [">", ">="] and ver1 < ver2) or (
                op1 in [">", ">="] and op2 in ["<", "<="] and ver1 > ver2
            ):
                msg = f"{err_msg}: {pin} and {other_pin}"
                raise ValueError(msg)

    return ",".join(non_redundant_pinnings)
