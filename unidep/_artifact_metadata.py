"""Helpers for UniDep artifact metadata embedded in built distributions."""

from __future__ import annotations

import copy
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from unidep._conda_env import create_conda_env_specification
from unidep._conflicts import resolve_conflicts
from unidep._dependencies_parsing import parse_requirements
from unidep.platform_definitions import Platform, Spec
from unidep.utils import collect_selector_platforms, dedupe, resolve_platforms

if TYPE_CHECKING:
    from unidep._dependencies_parsing import ParsedRequirements

if sys.version_info >= (3, 8):
    from typing import Literal, get_args
else:  # pragma: no cover
    from typing_extensions import Literal, get_args

CondaExecutable = Literal["conda", "mamba", "micromamba"]

UNIDEP_METADATA_FILENAME = "unidep.json"
UNIDEP_SCHEMA_VERSION = 1


class UnidepMetadataError(ValueError):
    """Raised when UniDep artifact metadata is invalid."""


@dataclass(frozen=True)
class PlatformDependencySet:
    """Resolved dependencies for a single platform."""

    conda: list[str]
    pip: list[str]


@dataclass(frozen=True)
class UnidepMetadata:
    """Validated UniDep metadata loaded from a distribution artifact."""

    schema_version: int
    project: str
    version: str
    channels: list[str]
    platforms: dict[Platform, PlatformDependencySet]
    extras: dict[str, dict[Platform, PlatformDependencySet]]


@dataclass(frozen=True)
class SelectedMetadataDependencies:
    """Dependencies selected from metadata for one platform and extra set."""

    channels: list[str]
    conda: list[str]
    pip: list[str]
    missing_extras: list[str]


def _normalise_dep_name(dep: str) -> str:
    """Extract and normalise the package name from a dependency string.

    Handles both conda specs (``name pin``) and pip specs (``name>=ver``).
    Returns a lowercase name with runs of ``[-_.]`` collapsed to ``-``.
    """
    # Split on the first whitespace (conda) or version operator (pip).
    # Include ``~`` so compatible-release pins (e.g. ``pkg~=1.2``) are parsed.
    name = re.split(r"[\s~>=<!;@\[]", dep, maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def _as_str_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        msg = f"`{field}` must be a list of strings."
        raise UnidepMetadataError(msg)
    return value


def _parse_platform_deps(
    value: Any,
    *,
    field: str,
) -> dict[Platform, PlatformDependencySet]:
    if not isinstance(value, dict):
        msg = f"`{field}` must be a mapping of platforms."
        raise UnidepMetadataError(msg)

    valid_platforms = set(get_args(Platform))
    parsed: dict[Platform, PlatformDependencySet] = {}
    for raw_platform, raw_deps in value.items():
        if raw_platform not in valid_platforms:
            msg = f"Unsupported platform `{raw_platform}` in `{field}`."
            raise UnidepMetadataError(msg)
        if not isinstance(raw_deps, dict):
            msg = f"Platform entry `{raw_platform}` in `{field}` must be an object."
            raise UnidepMetadataError(msg)
        conda_deps = _as_str_list(
            raw_deps.get("conda", []),
            field=f"{field}.{raw_platform}.conda",
        )
        pip_deps = _as_str_list(
            raw_deps.get("pip", []),
            field=f"{field}.{raw_platform}.pip",
        )
        parsed[cast("Platform", raw_platform)] = PlatformDependencySet(
            conda=dedupe(conda_deps),
            pip=dedupe(pip_deps),
        )
    return parsed


def parse_unidep_metadata(data: Any) -> UnidepMetadata:
    """Validate and parse raw UniDep metadata."""
    if not isinstance(data, dict):
        msg = "UniDep metadata must be a JSON object."
        raise UnidepMetadataError(msg)

    schema_version = data.get("schema_version")
    if schema_version != UNIDEP_SCHEMA_VERSION:
        msg = (
            f"Unsupported UniDep metadata schema `{schema_version}`."
            f" Expected `{UNIDEP_SCHEMA_VERSION}`."
        )
        raise UnidepMetadataError(msg)

    project = data.get("project")
    version = data.get("version")
    if not isinstance(project, str) or not project:
        msg = "`project` must be a non-empty string."
        raise UnidepMetadataError(msg)
    if not isinstance(version, str) or not version:
        msg = "`version` must be a non-empty string."
        raise UnidepMetadataError(msg)

    channels = _as_str_list(data.get("channels", []), field="channels")
    platforms = _parse_platform_deps(data.get("platforms"), field="platforms")

    extras_raw = data.get("extras", {})
    if not isinstance(extras_raw, dict):
        msg = "`extras` must be an object mapping extra names to platforms."
        raise UnidepMetadataError(msg)
    extras: dict[str, dict[Platform, PlatformDependencySet]] = {}
    seen_normalised_extras: dict[str, str] = {}  # normalised → original
    for extra_name, extra_platform_data in extras_raw.items():
        if not isinstance(extra_name, str) or not extra_name:
            msg = "`extras` keys must be non-empty strings."
            raise UnidepMetadataError(msg)
        norm = _normalise_extra_name(extra_name)
        if norm in seen_normalised_extras:
            msg = (
                f"Extras `{seen_normalised_extras[norm]}` and `{extra_name}`"
                f" normalise to the same name `{norm}` (PEP 685)."
            )
            raise UnidepMetadataError(msg)
        seen_normalised_extras[norm] = extra_name
        extras[extra_name] = _parse_platform_deps(
            extra_platform_data,
            field=f"extras.{extra_name}",
        )

    return UnidepMetadata(
        schema_version=schema_version,
        project=project,
        version=version,
        channels=channels,
        platforms=platforms,
        extras=extras,
    )


def extract_unidep_metadata_from_wheel(wheel: str | Path) -> UnidepMetadata | None:
    """Read UniDep metadata from a wheel if present."""
    wheel_path = Path(wheel)
    with zipfile.ZipFile(wheel_path) as zf:
        names = zf.namelist()
        primary = [
            n for n in names if n.endswith(f".dist-info/{UNIDEP_METADATA_FILENAME}")
        ]
        fallback = [
            n
            for n in names
            if n.endswith(f".dist-info/extra_metadata/{UNIDEP_METADATA_FILENAME}")
        ]
        candidates = sorted(primary) or sorted(fallback)
        if not candidates:
            return None
        raw = json.loads(zf.read(candidates[0]).decode("utf-8"))
    return parse_unidep_metadata(raw)


def _normalise_extra_name(name: str) -> str:
    """Normalise an extra name per PEP 685 (lowercase, collapse ``[-_.]``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def select_unidep_dependencies(
    metadata: UnidepMetadata,
    *,
    platform: Platform,
    extras: list[str] | None = None,
) -> SelectedMetadataDependencies:
    """Select dependencies for a specific platform and optional extras."""
    try:
        base = metadata.platforms[platform]
    except KeyError as exc:
        msg = f"Platform `{platform}` is not present in UniDep metadata."
        raise UnidepMetadataError(msg) from exc

    # Build a normalised lookup for extras so that ``Dev``, ``dev``, and
    # ``dev_extra`` all match regardless of casing/separator style (PEP 685).
    normalised_extras: dict[str, dict[Platform, PlatformDependencySet]] = {
        _normalise_extra_name(k): v for k, v in metadata.extras.items()
    }

    conda = list(base.conda)
    pip = list(base.pip)
    missing_extras: list[str] = []
    # Track normalised names that extras explicitly contributed to each
    # channel so that we can detect when an extra intentionally moves a
    # dependency from one channel to the other (e.g. conda → pip).
    extra_added_conda_names: set[str] = set()
    extra_added_pip_names: set[str] = set()
    for extra in sorted(set(extras or [])):
        extra_platforms = normalised_extras.get(_normalise_extra_name(extra))
        if extra_platforms is None:
            # Extra is not defined at all in the metadata — truly missing.
            missing_extras.append(extra)
            continue
        extra_deps = extra_platforms.get(platform)
        if extra_deps is None:
            # Extra exists but has no dependencies on this platform (the
            # build step only emits a platform entry when there is a non-empty
            # delta).  This is *not* a missing extra — it simply contributes
            # nothing on this platform.
            continue
        conda.extend(extra_deps.conda)
        pip.extend(extra_deps.pip)
        extra_added_conda_names.update(_normalise_dep_name(d) for d in extra_deps.conda)
        extra_added_pip_names.update(_normalise_dep_name(d) for d in extra_deps.pip)

    # An extra may move a dependency between channels (pip↔conda).  When
    # a normalised package name appears in both lists we must pick one:
    #
    #  • If an extra *added* the pip entry but did NOT add a conda entry
    #    for the same name, the extra intentionally moved the dependency
    #    from conda → pip.  Prefer pip (remove the base conda entry).
    #  • Otherwise (extra moved pip→conda, both from base, or both from
    #    extras) prefer conda (remove the pip entry).
    all_conda_names = {_normalise_dep_name(d) for d in conda}
    all_pip_names = {_normalise_dep_name(d) for d in pip}
    overlap = all_conda_names & all_pip_names

    moved_to_pip = {
        n
        for n in overlap
        if n in extra_added_pip_names and n not in extra_added_conda_names
    }
    moved_to_conda = overlap - moved_to_pip

    conda = [d for d in conda if _normalise_dep_name(d) not in moved_to_pip]
    pip = [d for d in pip if _normalise_dep_name(d) not in moved_to_conda]

    return SelectedMetadataDependencies(
        channels=list(metadata.channels),
        conda=dedupe(conda),
        pip=dedupe(pip),
        missing_extras=missing_extras,
    )


def _metadata_payload(
    *,
    project: str,
    version: str,
    channels: list[str],
    platforms: dict[Platform, PlatformDependencySet],
    extras: dict[str, dict[Platform, PlatformDependencySet]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": UNIDEP_SCHEMA_VERSION,
        "project": project,
        "version": version,
        "channels": channels,
        "platforms": {
            platform: {
                "conda": deps.conda,
                "pip": deps.pip,
            }
            for platform, deps in platforms.items()
        },
    }
    if extras:
        payload["extras"] = {
            extra: {
                platform: {
                    "conda": deps.conda,
                    "pip": deps.pip,
                }
                for platform, deps in platform_map.items()
            }
            for extra, platform_map in extras.items()
        }
    return payload


def _resolve_platform_specs(
    requirements: ParsedRequirements,
    *,
    platforms: list[Platform],
    optional_dependencies: dict[str, dict[str, list[Spec]]] | None = None,
) -> dict[Platform, PlatformDependencySet]:
    by_platform: dict[Platform, PlatformDependencySet] = {}
    for platform in platforms:
        # Resolve once per platform so the emitted metadata for that platform
        # never includes dependencies selected only for other platforms.
        resolved = resolve_conflicts(
            copy.deepcopy(requirements.requirements),
            [platform],
            optional_dependencies=copy.deepcopy(optional_dependencies),
        )
        env_spec = create_conda_env_specification(
            resolved,
            requirements.channels,
            platforms=[platform],
        )
        conda = [dep for dep in env_spec.conda if isinstance(dep, str)]
        by_platform[platform] = PlatformDependencySet(
            conda=dedupe(conda),
            pip=dedupe(list(env_spec.pip)),
        )
    return by_platform


def build_unidep_metadata(
    requirements_file: str | Path,
    *,
    project: str,
    version: str,
    verbose: bool = False,
) -> dict[str, Any]:
    """Build UniDep artifact metadata from local requirements configuration.

    .. note::

       ``local_dependencies`` entries with ``pypi:`` alternatives are handled
       differently here than in ``get_python_dependencies`` (which populates
       ``install_requires``).  This function always resolves local deps
       recursively (``include_local_dependencies=True``) and merges their
       conda/pip specs into the metadata, but it does **not** emit the PyPI
       alternative package reference itself.  When the wheel is later
       installed with ``--no-deps``, the PyPI alternative will therefore be
       absent.  For monorepo projects that rely on ``UNIDEP_SKIP_LOCAL_DEPS``
       and publish individual packages to PyPI, the ``install_requires``
       metadata (populated by ``_deps``) remains the authoritative source for
       inter-project PyPI references.
    """
    requirements_path = Path(requirements_file)
    requirements = parse_requirements(
        requirements_path,
        verbose=verbose,
        extras="*",
    )
    platforms = resolve_platforms(
        requested_platforms=None,
        declared_platforms=requirements.platforms,
        selector_platforms=collect_selector_platforms(
            requirements.requirements,
            requirements.optional_dependencies,
        ),
        default_current=False,
    )
    if not platforms:
        platforms = sorted(get_args(Platform))

    base_by_platform = _resolve_platform_specs(requirements, platforms=platforms)
    extras_payload: dict[str, dict[Platform, PlatformDependencySet]] = {}
    for extra, extra_specs in requirements.optional_dependencies.items():
        with_extra = _resolve_platform_specs(
            requirements,
            platforms=platforms,
            optional_dependencies={extra: extra_specs},
        )
        extra_platform_payload: dict[Platform, PlatformDependencySet] = {}
        for platform in platforms:
            base = base_by_platform[platform]
            enriched = with_extra[platform]
            extra_conda = [dep for dep in enriched.conda if dep not in set(base.conda)]
            extra_pip = [dep for dep in enriched.pip if dep not in set(base.pip)]
            if extra_conda or extra_pip:
                extra_platform_payload[platform] = PlatformDependencySet(
                    conda=extra_conda,
                    pip=extra_pip,
                )
        # Always record the extra — even when the delta is empty on every
        # platform — so that install-time selection can distinguish "extra
        # exists but contributes nothing" from "extra is truly undefined".
        extras_payload[extra] = extra_platform_payload

    return _metadata_payload(
        project=project,
        version=version,
        channels=dedupe(list(requirements.channels)),
        platforms=base_by_platform,
        extras=extras_payload,
    )
