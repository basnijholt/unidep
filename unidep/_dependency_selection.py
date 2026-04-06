"""Shared conda/pip dependency selection for CLI-facing outputs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Tuple, cast

from packaging.specifiers import InvalidSpecifier, Specifier
from packaging.utils import canonicalize_name
from packaging.version import Version

from unidep._conflicts import (
    VersionConflictError,
    combine_version_pinnings,
    extract_version_operator,
)
from unidep.platform_definitions import (
    PLATFORM_SELECTOR_MAP,
    CondaPip,
    Platform,
    Spec,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from unidep._dependencies_parsing import DependencyEntry, DependencyOrigin

TargetPlatform = Optional[Platform]
FamilyKey = Tuple[Optional[str], Optional[str]]


@dataclass(frozen=True)
class SourceRequirement:
    source: CondaPip
    spec: Spec
    family_key: FamilyKey
    base_name: str
    normalized_name: str
    extras: tuple[str, ...]
    declared_platforms: tuple[Platform, ...] | None
    origin: DependencyOrigin


@dataclass(frozen=True)
class MergedSourceCandidate:
    source: CondaPip
    spec: Spec
    normalized_name: str
    family_keys: tuple[FamilyKey, ...]
    requirements: tuple[SourceRequirement, ...]
    declared_scopes: tuple[tuple[Platform, ...] | None, ...]


@dataclass(frozen=True)
class PlatformCandidates:
    family_key: FamilyKey
    platform: TargetPlatform
    conda: MergedSourceCandidate | None
    pip: MergedSourceCandidate | None


def _operator_order_key(constraint: str) -> tuple[int, str]:
    op = extract_version_operator(constraint)
    order = {
        "===": 0,
        "==": 1,
        "~=": 2,
        ">=": 3,
        "<=": 4,
        "!=": 5,
        ">": 6,
        "<": 7,
        "=": 8,
    }
    return (order.get(op, len(order)), constraint)


def _canonicalize_joined_pinnings(pinnings: list[str]) -> str:
    seen: set[str] = set()
    for pinning in pinnings:
        for stripped in filter(None, (token.strip() for token in pinning.split(","))):
            seen.add(stripped)
    return ",".join(sorted(seen, key=_operator_order_key))


def _parse_pip_name(name: str) -> tuple[str, tuple[str, ...]]:
    if not name.endswith("]") or "[" not in name:
        return name, ()
    base_name, extras = name[:-1].split("[", 1)
    parsed = tuple(sorted(e.strip() for e in extras.split(",") if e.strip()))
    return base_name, parsed


def _build_pip_name(base_name: str, extras: tuple[str, ...]) -> str:
    if not extras:
        return base_name
    return f"{base_name}[{','.join(extras)}]"


def _spec_is_pinned(spec: Spec) -> bool:
    return spec.pin is not None


def _candidate_scope_rank(candidate: MergedSourceCandidate) -> float:
    ranks = [len(scope) for scope in candidate.declared_scopes if scope is not None]
    if not ranks:
        return math.inf
    return min(ranks)


def _candidate_has_universal_origin(candidate: MergedSourceCandidate) -> bool:
    return any(scope is None for scope in candidate.declared_scopes)


def _candidate_has_pip_extras(candidate: MergedSourceCandidate) -> bool:
    return candidate.source == "pip" and bool(_parse_pip_name(candidate.spec.name)[1])


def _candidate_display_key(
    candidate: MergedSourceCandidate,
) -> tuple[int, str, str]:
    return (
        0 if candidate.source == "conda" else 1,
        candidate.normalized_name,
        candidate.spec.name_with_pin(is_pip=candidate.source == "pip"),
    )


def _origin_to_text(origin: DependencyOrigin) -> str:
    parts = [origin.source_file.as_posix(), f"item {origin.dependency_index}"]
    if origin.optional_group is not None:
        parts.append(f"group {origin.optional_group}")
    if origin.local_dependency_chain:
        chain = " -> ".join(path.as_posix() for path in origin.local_dependency_chain)
        parts.append(f"via {chain}")
    return ", ".join(parts)


def _candidate_to_text(candidate: MergedSourceCandidate) -> str:
    rendered = candidate.spec.name_with_pin(is_pip=candidate.source == "pip")
    origins = "; ".join(_origin_to_text(req.origin) for req in candidate.requirements)
    return f"{candidate.source}: {rendered} ({origins})"


def _merge_pin_strings(
    requirements: list[SourceRequirement],
    *,
    allow_unsatisfiable_fallback: bool,
) -> str | None:
    pinned = [req.spec.pin for req in requirements if req.spec.pin is not None]
    if not pinned:
        return None
    unique = list(dict.fromkeys(pinned))
    if len(unique) == 1:
        return unique[0]
    if allow_unsatisfiable_fallback:
        exact_pinnings = [
            pin for pin in unique if _exact_pinning_version_text(pin) is not None
        ]
        distinct_exact_versions = {
            cast("str", _exact_pinning_version_text(pin)) for pin in exact_pinnings
        }
        if len(distinct_exact_versions) > 1:
            pinnings_str = ", ".join(exact_pinnings)
            msg = (
                "Multiple exact version pinnings found: "
                f"{pinnings_str} for `{requirements[0].base_name}`"
            )
            raise VersionConflictError(msg)
    try:
        merged = combine_version_pinnings(unique, name=requirements[0].base_name)
        return _canonicalize_joined_pinnings([merged])
    except VersionConflictError:
        if allow_unsatisfiable_fallback and _joined_pinnings_are_safely_satisfiable(
            unique,
        ):
            return _canonicalize_joined_pinnings(unique)
        raise


def _bump_release_prefix(release: tuple[int, ...], prefix_len: int) -> str:
    assert 0 < prefix_len <= len(release)
    bumped = list(release[:prefix_len])
    bumped[-1] += 1
    return ".".join(str(part) for part in bumped)


def _normalize_pinning_token_for_satisfiability(  # noqa: PLR0911
    pinning: str,
) -> list[str] | None:
    try:
        specifier = Specifier(pinning)
    except InvalidSpecifier:
        return None

    operator = specifier.operator
    version_text = specifier.version

    if operator in {">", ">=", "<", "<="}:
        return [f"{operator}{version_text}"]

    if operator == "!=":
        if "*" in version_text:
            return None
        return [f"!={version_text}"]

    if operator == "==":
        if version_text.endswith(".*"):
            prefix = version_text[:-2]
            parsed = Version(prefix)
            upper = _bump_release_prefix(parsed.release, len(parsed.release))
            return [f">={prefix}", f"<{upper}"]
        Version(version_text)
        return [f"={version_text}"]

    if operator == "~=":
        parsed = Version(version_text)
        upper = _bump_release_prefix(parsed.release, len(parsed.release) - 1)
        return [f">={version_text}", f"<{upper}"]

    return None


def _parse_supported_pinning(pinning: str) -> tuple[str, Version]:
    operator = extract_version_operator(pinning)
    assert operator
    version_text = pinning[len(operator) :].strip()
    return operator, Version(version_text)


def _exact_pinning_version_text(pinning: str) -> str | None:
    operator = extract_version_operator(pinning)
    if operator not in {"==", "===", "="}:
        return None
    return pinning[len(operator) :].strip()


def _stricter_lower_bound(
    current: tuple[Version, bool] | None,
    candidate: tuple[Version, bool],
) -> tuple[Version, bool]:
    if current is None:
        return candidate
    if candidate[0] > current[0]:
        return candidate
    if candidate[0] < current[0]:
        return current
    return (current[0], current[1] and candidate[1])


def _stricter_upper_bound(
    current: tuple[Version, bool] | None,
    candidate: tuple[Version, bool],
) -> tuple[Version, bool]:
    if current is None:
        return candidate
    if candidate[0] < current[0]:
        return candidate
    if candidate[0] > current[0]:
        return current
    return (current[0], current[1] and candidate[1])


def _normalized_pinnings_are_satisfiable(  # noqa: PLR0911, PLR0912
    pinnings: list[str],
) -> bool:
    exact: Version | None = None
    excluded: set[Version] = set()
    lower: tuple[Version, bool] | None = None
    upper: tuple[Version, bool] | None = None

    for pinning in pinnings:
        operator, parsed_version = _parse_supported_pinning(pinning)
        if operator == "=":
            assert exact is None or exact == parsed_version
            exact = parsed_version
        elif operator == "!=":
            excluded.add(parsed_version)
        elif operator == ">":
            lower = _stricter_lower_bound(lower, (parsed_version, False))
        elif operator == ">=":
            lower = _stricter_lower_bound(lower, (parsed_version, True))
        elif operator == "<":
            upper = _stricter_upper_bound(upper, (parsed_version, False))
        elif operator == "<=":
            upper = _stricter_upper_bound(upper, (parsed_version, True))

    if exact is not None:
        if exact in excluded:
            return False
        if lower is not None and (
            exact < lower[0] or (exact == lower[0] and not lower[1])
        ):
            return False
        return not (
            upper is not None
            and (exact > upper[0] or (exact == upper[0] and not upper[1]))
        )

    if lower is not None and upper is not None:
        if lower[0] > upper[0]:
            return False
        if lower[0] == upper[0]:
            if not (lower[1] and upper[1]):
                return False
            if lower[0] in excluded:
                return False

    return True


def _joined_pinnings_are_safely_satisfiable(pinnings: list[str]) -> bool:
    normalized: list[str] = []
    for pinning in pinnings:
        for stripped in filter(None, (token.strip() for token in pinning.split(","))):
            normalized_tokens = _normalize_pinning_token_for_satisfiability(stripped)
            if normalized_tokens is None:
                return False
            normalized.extend(normalized_tokens)
    return _normalized_pinnings_are_satisfiable(normalized)


def _merge_source_requirements(
    source: CondaPip,
    requirements: list[SourceRequirement],
) -> MergedSourceCandidate:
    requirements = list(requirements)
    if source == "pip":
        extras = tuple(
            sorted({extra for req in requirements for extra in req.extras}),
        )
        pin = _merge_pin_strings(
            requirements,
            allow_unsatisfiable_fallback=True,
        )
        name = _build_pip_name(requirements[0].base_name, extras)
        spec = Spec(name=name, which="pip", pin=pin)
        normalized_name = requirements[0].normalized_name
    else:
        pin = _merge_pin_strings(
            requirements,
            allow_unsatisfiable_fallback=False,
        )
        spec = Spec(name=requirements[0].spec.name, which="conda", pin=pin)
        normalized_name = requirements[0].normalized_name
    return MergedSourceCandidate(
        source=source,
        spec=spec,
        normalized_name=normalized_name,
        family_keys=tuple(dict.fromkeys(req.family_key for req in requirements)),
        requirements=tuple(requirements),
        declared_scopes=tuple(req.declared_platforms for req in requirements),
    )


def _entry_family_key(entry: DependencyEntry) -> FamilyKey:
    conda_name = entry.conda.name if entry.conda is not None else None
    pip_name = None
    if entry.pip is not None:
        base_name, _extras = _parse_pip_name(entry.pip.name)
        pip_name = canonicalize_name(base_name)
    return (conda_name, pip_name)


def _source_requirement_from_spec(
    spec: Spec,
    *,
    family_key: FamilyKey,
    origin: DependencyOrigin,
    declared_platforms: tuple[Platform, ...] | None,
) -> SourceRequirement:
    if spec.which == "pip":
        base_name, extras = _parse_pip_name(spec.name)
        normalized_name = canonicalize_name(base_name)
    else:
        base_name = spec.name
        extras = ()
        normalized_name = spec.name
    return SourceRequirement(
        source=spec.which,
        spec=spec,
        family_key=family_key,
        base_name=base_name,
        normalized_name=normalized_name,
        extras=extras,
        declared_platforms=declared_platforms,
        origin=origin,
    )


def _collect_target_platforms(
    _entries: Sequence[DependencyEntry],
    platforms: Sequence[Platform] | None,
) -> list[TargetPlatform]:
    if platforms:
        return cast("list[TargetPlatform]", list(platforms))
    return cast("list[TargetPlatform]", sorted(PLATFORM_SELECTOR_MAP))


def _entry_targets(
    spec: Spec,
    *,
    target_platforms: Sequence[TargetPlatform],
) -> tuple[tuple[Platform, ...] | None, list[TargetPlatform]]:
    declared = spec.platforms()
    if declared is None:
        return None, list(target_platforms)
    targets: list[TargetPlatform] = [
        platform for platform in declared if platform in target_platforms
    ]
    return tuple(declared), targets


def _build_platform_candidates(
    entries: Sequence[DependencyEntry],
    platforms: Sequence[Platform] | None = None,
) -> list[PlatformCandidates]:
    target_platforms = _collect_target_platforms(entries, platforms)
    grouped: dict[
        FamilyKey,
        dict[TargetPlatform, dict[CondaPip, list[SourceRequirement]]],
    ] = {}
    for entry in entries:
        family_key = _entry_family_key(entry)
        for spec in (entry.conda, entry.pip):
            if spec is None:
                continue
            declared_platforms, targets = _entry_targets(
                spec,
                target_platforms=target_platforms,
            )
            source_requirement = _source_requirement_from_spec(
                spec,
                family_key=family_key,
                origin=entry.origin,
                declared_platforms=declared_platforms,
            )
            for platform in targets:
                grouped.setdefault(family_key, {}).setdefault(platform, {}).setdefault(
                    spec.which,
                    [],
                ).append(source_requirement)

    result: list[PlatformCandidates] = []
    for family_key, platform_data in grouped.items():
        for platform, source_lists in sorted(platform_data.items()):
            conda = None
            pip = None
            if source_lists.get("conda"):
                conda = _merge_source_requirements("conda", source_lists["conda"])
            if source_lists.get("pip"):
                pip = _merge_source_requirements("pip", source_lists["pip"])
            result.append(
                PlatformCandidates(
                    family_key=family_key,
                    platform=platform,
                    conda=conda,
                    pip=pip,
                ),
            )
    return result


def _choose_by_precedence(
    conda: MergedSourceCandidate | None,
    pip: MergedSourceCandidate | None,
) -> MergedSourceCandidate | None:
    if conda is None:
        return pip
    if pip is None:
        return conda
    if _candidate_has_pip_extras(pip):
        return pip
    conda_pinned = _spec_is_pinned(conda.spec)
    pip_pinned = _spec_is_pinned(pip.spec)
    if conda_pinned != pip_pinned:
        return conda if conda_pinned else pip
    if conda_pinned and pip_pinned:
        conda_scope = _candidate_scope_rank(conda)
        pip_scope = _candidate_scope_rank(pip)
        if conda_scope != pip_scope:
            return conda if conda_scope < pip_scope else pip
    return conda


def _select_conda_like_candidate(
    platform_candidates: PlatformCandidates,
) -> MergedSourceCandidate | None:
    return _choose_by_precedence(
        platform_candidates.conda,
        platform_candidates.pip,
    )


def _select_pip_candidate(
    platform_candidates: PlatformCandidates,
) -> MergedSourceCandidate | None:
    if platform_candidates.pip is None:
        return None
    return platform_candidates.pip


def _final_identity(candidate: MergedSourceCandidate) -> str:
    if candidate.source == "conda":
        return candidate.spec.name
    return candidate.normalized_name


def _merge_candidate_group(
    candidates: Iterable[MergedSourceCandidate],
) -> MergedSourceCandidate:
    ordered = sorted(candidates, key=_candidate_display_key)
    source = ordered[0].source
    requirements = [
        requirement for candidate in ordered for requirement in candidate.requirements
    ]
    return _merge_source_requirements(source, requirements)


def _can_reconcile_cross_source_collision(
    candidates: Iterable[MergedSourceCandidate],
) -> bool:
    conda_names = {
        conda_name
        for candidate in candidates
        for conda_name, _pip_name in candidate.family_keys
        if conda_name is not None
    }
    pip_names = {
        pip_name
        for candidate in candidates
        for _conda_name, pip_name in candidate.family_keys
        if pip_name is not None
    }
    return len(conda_names) <= 1 and len(pip_names) <= 1


def _raise_final_collision(
    *,
    platform: TargetPlatform,
    identity: str,
    candidates: Iterable[MergedSourceCandidate],
) -> None:
    platform_text = platform or "universal"
    rendered = "\n".join(
        f"  - {_candidate_to_text(candidate)}"
        for candidate in sorted(candidates, key=_candidate_display_key)
    )
    msg = (
        "Final Dependency Collision:\n"
        f"Multiple selected dependency families map to final install identity "
        f"'{identity}' on platform '{platform_text}':\n"
        f"{rendered}\n"
        "Resolve the ambiguity by removing one alternative or making the target "
        "package names distinct."
    )
    raise ValueError(msg)


def _resolve_final_collisions(
    selected: dict[TargetPlatform, list[MergedSourceCandidate]],
) -> dict[TargetPlatform, list[MergedSourceCandidate]]:
    resolved: dict[TargetPlatform, list[MergedSourceCandidate]] = {}
    for platform, candidates in selected.items():
        by_identity: dict[str, list[MergedSourceCandidate]] = {}
        for candidate in candidates:
            by_identity.setdefault(_final_identity(candidate), []).append(candidate)
        resolved_candidates: list[MergedSourceCandidate] = []
        for identity, group in sorted(by_identity.items()):
            if len(group) == 1:
                resolved_candidates.append(group[0])
                continue
            by_source: dict[CondaPip, list[MergedSourceCandidate]] = {}
            for candidate in group:
                by_source.setdefault(candidate.source, []).append(candidate)
            merged_group = [
                _merge_candidate_group(source_candidates)
                for _source, source_candidates in sorted(by_source.items())
            ]
            sources = {candidate.source for candidate in merged_group}
            if len(sources) > 1 and not _can_reconcile_cross_source_collision(
                merged_group,
            ):
                _raise_final_collision(
                    platform=platform,
                    identity=identity,
                    candidates=merged_group,
                )
            if len(sources) > 1:
                conda = next(
                    (
                        candidate
                        for candidate in merged_group
                        if candidate.source == "conda"
                    ),
                    None,
                )
                pip = next(
                    (
                        candidate
                        for candidate in merged_group
                        if candidate.source == "pip"
                    ),
                    None,
                )
                winner = _choose_by_precedence(conda, pip)
                assert winner is not None
                resolved_candidates.append(winner)
                continue
            resolved_candidates.append(merged_group[0])
        resolved[platform] = resolved_candidates
    return resolved


def select_conda_like_requirements(
    entries: Sequence[DependencyEntry],
    platforms: Sequence[Platform] | None = None,
) -> dict[TargetPlatform, list[MergedSourceCandidate]]:
    selected: dict[TargetPlatform, list[MergedSourceCandidate]] = {}
    for platform_candidates in _build_platform_candidates(entries, platforms):
        candidate = _select_conda_like_candidate(platform_candidates)
        assert candidate is not None
        selected.setdefault(platform_candidates.platform, []).append(candidate)
    return _resolve_final_collisions(selected)


def select_pip_requirements(
    entries: Sequence[DependencyEntry],
    platforms: Sequence[Platform] | None = None,
) -> dict[TargetPlatform, list[MergedSourceCandidate]]:
    selected: dict[TargetPlatform, list[MergedSourceCandidate]] = {}
    for platform_candidates in _build_platform_candidates(entries, platforms):
        candidate = _select_pip_candidate(platform_candidates)
        if candidate is None:
            continue
        selected.setdefault(platform_candidates.platform, []).append(candidate)
    return _resolve_final_collisions(selected)


def collapse_selected_universals(
    selected: dict[TargetPlatform, list[MergedSourceCandidate]],
    platforms: Sequence[Platform] | None = None,
) -> dict[TargetPlatform, list[MergedSourceCandidate]]:
    """Compress identical universal-origin candidates back to the universal bucket."""
    result: dict[TargetPlatform, list[MergedSourceCandidate]] = {}

    active_platforms = (
        list(platforms)
        if platforms
        else sorted(platform for platform in selected if platform is not None)
    )
    if not active_platforms:
        return result

    grouped: dict[
        tuple[CondaPip, Spec],
        dict[Platform, MergedSourceCandidate],
    ] = {}
    for platform in active_platforms:
        for candidate in selected.get(platform, []):
            grouped.setdefault(
                (candidate.source, candidate.spec),
                {},
            )[platform] = candidate

    for candidates_by_platform in grouped.values():
        if len(candidates_by_platform) == len(active_platforms) and all(
            _candidate_has_universal_origin(candidate)
            for candidate in candidates_by_platform.values()
        ):
            result.setdefault(None, []).append(
                next(iter(candidates_by_platform.values())),
            )
            continue
        for platform, candidate in candidates_by_platform.items():
            result.setdefault(platform, []).append(candidate)

    return result
