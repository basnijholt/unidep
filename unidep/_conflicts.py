"""unidep - Unified Conda and Pip requirements management.

Verion conflict detections and resolution.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from typing import TYPE_CHECKING

from packaging import version

from unidep.platform_definitions import Platform, Spec
from unidep.utils import defaultdict_to_dict, warn

if sys.version_info >= (3, 8):
    from typing import get_args
else:  # pragma: no cover
    from typing_extensions import get_args


if TYPE_CHECKING:
    from unidep.platform_definitions import CondaPip

VALID_OPERATORS = ["<=", ">=", "<", ">", "=", "!="]
_REPO_URL = "https://github.com/basnijholt/unidep"


def _prepare_specs_for_conflict_resolution(
    requirements: dict[str, list[Spec]],
) -> dict[str, dict[Platform | None, dict[CondaPip, list[Spec]]]]:
    """Prepare and group metadata for conflict resolution.

    This function groups metadata by platform and source for each package.

    :param requirements: Dictionary mapping package names to a list of Spec objects.
    :return: Dictionary mapping package names to grouped metadata.
    """
    prepared_data = {}
    for package, spec_list in requirements.items():
        grouped_specs: dict[Platform | None, dict[CondaPip, list[Spec]]] = defaultdict(
            lambda: defaultdict(list),
        )
        for spec in spec_list:
            _platforms = spec.platforms()
            if _platforms is None:
                _platforms = [None]  # type: ignore[list-item]
            for _platform in _platforms:
                grouped_specs[_platform][spec.which].append(spec)

        prepared_data[package] = grouped_specs
    return defaultdict_to_dict(prepared_data)


def _pop_unused_platforms_and_maybe_expand_none(
    platform_data: dict[Platform | None, dict[CondaPip, list[Spec]]],
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
            for which, specs in sources.items():
                platform_data.setdefault(_platform, {}).setdefault(which, []).extend(
                    specs,
                )

    # Remove platforms that are not allowed
    to_pop = platform_data.keys() - allowed_platforms
    to_pop.discard(None)
    for _platform in to_pop:
        platform_data.pop(_platform)


def _maybe_new_spec_with_combined_pinnings(
    specs: list[Spec],
) -> Spec:
    pinned_specs = [m for m in specs if m.pin is not None]
    if len(pinned_specs) == 1:
        return pinned_specs[0]
    if len(pinned_specs) > 1:
        first = pinned_specs[0]
        pins = [m.pin for m in pinned_specs]
        pin = combine_version_pinnings(pins, name=first.name)  # type: ignore[arg-type]
        return Spec(
            name=first.name,
            which=first.which,
            pin=pin,
            identifier=first.identifier,  # should I create a new one?
        )

    # Flatten the list
    return specs[0]


def _combine_pinning_within_platform(
    data: dict[Platform | None, dict[CondaPip, list[Spec]]],
) -> dict[Platform | None, dict[CondaPip, Spec]]:
    reduced_data: dict[Platform | None, dict[CondaPip, Spec]] = {}
    for _platform, packages in data.items():
        reduced_data[_platform] = {}
        for which, specs in packages.items():
            spec = _maybe_new_spec_with_combined_pinnings(specs)
            reduced_data[_platform][which] = spec
    return reduced_data


def _resolve_conda_pip_conflicts(sources: dict[CondaPip, Spec]) -> dict[CondaPip, Spec]:
    conda_spec = sources.get("conda")
    pip_spec = sources.get("pip")
    if not conda_spec or not pip_spec:  # If either is missing, there is no conflict
        return sources

    # Compare version pins to resolve conflicts
    if conda_spec.pin and not pip_spec.pin:
        return {"conda": conda_spec}  # Prefer conda if it has a pin
    if pip_spec.pin and not conda_spec.pin:
        return {"pip": pip_spec}  # Prefer pip if it has a pin
    if conda_spec.pin == pip_spec.pin:
        return {"conda": conda_spec, "pip": pip_spec}  # Keep both if pins are identical

    # Handle conflict where both conda and pip have different pins
    warn(
        "Version Pinning Conflict:\n"
        f"Different version specifications for Conda ('{conda_spec.pin}') and Pip"
        f" ('{pip_spec.pin}'). Both versions are retained.",
        stacklevel=2,
    )
    return {"conda": conda_spec, "pip": pip_spec}


class VersionConflictError(ValueError):
    """Raised when a version conflict is detected."""


def _add_optional_dependencies(
    requirements: dict[str, list[Spec]],
    optional_dependencies: dict[str, dict[str, list[Spec]]] | None,
) -> None:
    """Add optional dependencies to the requirements dictionary."""
    if optional_dependencies is None:
        return
    for dependencies in optional_dependencies.values():
        for pkg, specs in dependencies.items():
            requirements.setdefault(pkg, []).extend(specs)


def resolve_conflicts(
    requirements: dict[str, list[Spec]],
    platforms: list[Platform] | None = None,
    optional_dependencies: dict[str, dict[str, list[Spec]]] | None = None,
) -> dict[str, dict[Platform | None, dict[CondaPip, Spec]]]:
    """Resolve conflicts in a dictionary of requirements.

    Parameters
    ----------
    requirements
        Dictionary mapping package names to a list of Spec objects.
        Typically ``ParsedRequirements.requirements`` is passed here, which is
        returned by `parse_requirements`.
    platforms
        List of platforms to resolve conflicts for.
        Typically ``ParsedRequirements.platforms`` is passed here, which is
        returned by `parse_requirements`.
    optional_dependencies
        Dictionary mapping package names to a dictionary of optional dependencies.
        Typically ``ParsedRequirements.optional_dependencies`` is passed here, which is
        returned by `parse_requirements`. If passing this argument, all optional
        dependencies will be added to the requirements dictionary. Pass `None` to
        ignore optional dependencies.

    Returns
    -------
    Dictionary mapping package names to a dictionary of resolved metadata.
    The resolved metadata is a dictionary mapping platforms to a dictionary
    mapping sources to a single `Spec` object.

    """
    if platforms and not set(platforms).issubset(get_args(Platform)):
        msg = f"Invalid platform: {platforms}, must contain only {get_args(Platform)}"
        raise VersionConflictError(msg)

    _add_optional_dependencies(requirements, optional_dependencies)

    prepared = _prepare_specs_for_conflict_resolution(requirements)
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


def _split_pinnings(pinnings: list[str]) -> list[str]:
    """Extracts version pinnings from a list of Spec objects."""
    return [_pin.lstrip().rstrip() for pin in pinnings for _pin in pin.split(",")]


def combine_version_pinnings(pinnings: list[str], *, name: str | None = None) -> str:
    """Combines a list of version pinnings into a single string."""
    pinnings = [p for p in pinnings if p != ""]
    pinnings = _split_pinnings(pinnings)
    pinnings = _deduplicate(pinnings)
    if len(pinnings) == 1:
        return pinnings[0]
    for pin in pinnings:
        if not _is_valid_pinning(pin):
            ops = ", ".join(VALID_OPERATORS)
            url = f"{_REPO_URL}/blob/main/README.md#supported-version-pinnings"
            msg = (
                f"Invalid version pinning '{pin}' for '{name}'. "
                "UniDep supports only the following operators for combining pinnings: "
                f"{ops}. For complex pinnings (like VCS URLs, local paths, or build"
                " strings), ensure all pinnings are identical. Divergent complex"
                f" pinnings cannot be combined. See {url} for more information."
            )

            raise VersionConflictError(msg)

    valid_pinnings = [p.replace(" ", "") for p in pinnings]
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
