"""YAML parsing for `unidep`."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from ruamel.yaml import YAML

from unidep.platform_definitions import Meta, Platform

if TYPE_CHECKING:
    import sys

    from ruamel.yaml.comments import CommentedMap

    if sys.version_info >= (3, 8):
        from typing import Literal
    else:  # pragma: no cover
        from typing_extensions import Literal

from unidep.utils import (
    extract_matching_platforms,
    extract_name_and_pin,
    is_pip_installable,
)


def find_requirements_files(
    base_dir: str | Path = ".",
    depth: int = 1,
    filename: str = "requirements.yaml",
    *,
    verbose: bool = False,
) -> list[Path]:
    """Scan a directory for requirements.yaml files."""
    base_path = Path(base_dir)
    found_files = []

    # Define a helper function to recursively scan directories
    def _scan_dir(path: Path, current_depth: int) -> None:
        if verbose:
            print(f"🔍 Scanning in `{path}` at depth {current_depth}")
        if current_depth > depth:
            return
        for child in path.iterdir():
            if child.is_dir():
                _scan_dir(child, current_depth + 1)
            elif child.name == filename:
                found_files.append(child)
                if verbose:
                    print(f"🔍 Found `{filename}` at `{child}`")

    _scan_dir(base_path, 0)
    return sorted(found_files)


def _extract_first_comment(
    commented_map: CommentedMap,
    index_or_key: int | str,
) -> str | None:
    comments = commented_map.ca.items.get(index_or_key, None)
    if comments is None:
        return None
    comment_strings = next(
        c.value.split("\n")[0].rstrip().lstrip() for c in comments if c is not None
    )
    if not comment_strings:
        # empty string
        return None
    return "".join(comment_strings)


def _identifier(identifier: int, comment: str | None) -> str:
    """Return a unique identifier based on the comment."""
    platforms = None if comment is None else tuple(extract_matching_platforms(comment))
    data_str = f"{identifier}-{platforms}"
    # Hash using SHA256 and take the first 8 characters for a shorter hash
    return hashlib.sha256(data_str.encode()).hexdigest()[:8]


def _parse_dependency(
    dependency: str,
    dependencies: CommentedMap,
    index_or_key: int | str,
    which: Literal["conda", "pip", "both"],
    identifier: int,
    ignore_pins: list[str],
    overwrite_pins: dict[str, str | None],
    skip_dependencies: list[str],
) -> list[Meta]:
    comment = _extract_first_comment(dependencies, index_or_key)
    name, pin = extract_name_and_pin(dependency)
    if name in ignore_pins:
        pin = None
    if name in skip_dependencies:
        return []
    if name in overwrite_pins:
        pin = overwrite_pins[name]
    identifier_hash = _identifier(identifier, comment)
    if which == "both":
        return [
            Meta(name, "conda", comment, pin, identifier_hash),
            Meta(name, "pip", comment, pin, identifier_hash),
        ]
    return [Meta(name, which, comment, pin, identifier_hash)]


class ParsedRequirements(NamedTuple):
    """Requirements with comments."""

    channels: list[str]
    platforms: list[Platform]
    requirements: dict[str, list[Meta]]


class Requirements(NamedTuple):
    """Requirements as CommentedSeq."""

    # mypy doesn't support CommentedSeq[str], so we use list[str] instead.
    channels: list[str]  # actually a CommentedSeq[str]
    conda: list[str]  # actually a CommentedSeq[str]
    pip: list[str]  # actually a CommentedSeq[str]


def _include_path(include: str) -> Path:
    """Return the path to an included file."""
    path = Path(include)
    if path.is_dir():
        path /= "requirements.yaml"
    return path.resolve()


def _parse_overwrite_pins(overwrite_pins: list[str]) -> dict[str, str | None]:
    """Parse overwrite pins."""
    result = {}
    for overwrite_pin in overwrite_pins:
        name, pin = extract_name_and_pin(overwrite_pin)
        result[name] = pin
    return result


def parse_yaml_requirements(  # noqa: PLR0912
    *paths: Path,
    ignore_pins: list[str] | None = None,
    overwrite_pins: list[str] | None = None,
    skip_dependencies: list[str] | None = None,
    verbose: bool = False,
) -> ParsedRequirements:
    """Parse a list of `requirements.yaml` files including comments."""
    ignore_pins = ignore_pins or []
    skip_dependencies = skip_dependencies or []
    overwrite_pins_map = _parse_overwrite_pins(overwrite_pins or [])
    requirements: dict[str, list[Meta]] = defaultdict(list)
    channels: set[str] = set()
    platforms: set[Platform] = set()
    datas = []
    seen: set[Path] = set()
    yaml = YAML(typ="rt")
    for p in paths:
        if verbose:
            print(f"📄 Parsing `{p}`")
        with p.open() as f:
            data = yaml.load(f)
        datas.append(data)
        seen.add(p.resolve())

        # Deal with includes
        for include in data.get("includes", []):
            include_path = _include_path(p.parent / include)
            if include_path in seen:
                continue  # Avoids circular includes
            if verbose:
                print(f"📄 Parsing include `{include}`")
            with include_path.open() as f:
                datas.append(yaml.load(f))
            seen.add(include_path)
    identifier = -1
    for data in datas:
        for channel in data.get("channels", []):
            channels.add(channel)
        for _platform in data.get("platforms", []):
            platforms.add(_platform)
        if "dependencies" not in data:
            continue
        dependencies = data["dependencies"]
        for i, dep in enumerate(data["dependencies"]):
            identifier += 1
            if isinstance(dep, str):
                metas = _parse_dependency(
                    dep,
                    dependencies,
                    i,
                    "both",
                    identifier,
                    ignore_pins,
                    overwrite_pins_map,
                    skip_dependencies,
                )
                for meta in metas:
                    requirements[meta.name].append(meta)
                continue
            assert isinstance(dep, dict)
            for which in ["conda", "pip"]:
                if which in dep:
                    metas = _parse_dependency(
                        dep[which],
                        dep,
                        which,
                        which,  # type: ignore[arg-type]
                        identifier,
                        ignore_pins,
                        overwrite_pins_map,
                        skip_dependencies,
                    )
                    for meta in metas:
                        requirements[meta.name].append(meta)

    return ParsedRequirements(sorted(channels), sorted(platforms), dict(requirements))


def _extract_project_dependencies(
    path: Path,
    base_path: Path,
    processed: set,
    dependencies: dict[str, set[str]],
    *,
    check_pip_installable: bool = True,
    verbose: bool = False,
) -> None:
    if path in processed:
        return
    processed.add(path)
    yaml = YAML(typ="safe")
    with path.open() as f:
        data = yaml.load(f)
    for include in data.get("includes", []):
        include_path = _include_path(path.parent / include)
        if not include_path.exists():
            msg = f"Include file `{include_path}` does not exist."
            raise FileNotFoundError(msg)
        include_base_path = str(include_path.parent)
        if include_base_path == str(base_path):
            continue
        if not check_pip_installable or (
            is_pip_installable(base_path) and is_pip_installable(include_path.parent)
        ):
            dependencies[str(base_path)].add(include_base_path)
        if verbose:
            print(f"🔗 Adding include `{include_path}`")
        _extract_project_dependencies(
            include_path,
            base_path,
            processed,
            dependencies,
            check_pip_installable=check_pip_installable,
        )


def parse_project_dependencies(
    *paths: Path,
    check_pip_installable: bool = True,
    verbose: bool = False,
) -> dict[Path, list[Path]]:
    """Extract local project dependencies from a list of `requirements.yaml` files.

    Works by scanning for `includes` in the `requirements.yaml` files.
    """
    dependencies: dict[str, set[str]] = defaultdict(set)

    for p in paths:
        if verbose:
            print(f"🔗 Analyzing dependencies in `{p}`")
        base_path = p.resolve().parent
        _extract_project_dependencies(
            path=p,
            base_path=base_path,
            processed=set(),
            dependencies=dependencies,
            check_pip_installable=check_pip_installable,
            verbose=verbose,
        )

    return {
        Path(k): sorted({Path(v) for v in v_set})
        for k, v_set in sorted(dependencies.items())
    }
