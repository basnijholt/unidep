"""unidep - Unified Conda and Pip requirements management.

This module provides parsing of `requirements.yaml` and `pyproject.toml` files.
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from unidep.platform_definitions import Platform, Spec, platforms_from_selector
from unidep.utils import (
    PathWithExtras,
    defaultdict_to_dict,
    is_pip_installable,
    parse_folder_or_filename,
    parse_package_str,
    selector_from_comment,
    split_path_and_extras,
    unidep_configured_in_toml,
    warn,
)

if TYPE_CHECKING:
    if sys.version_info >= (3, 8):
        from typing import Literal
    else:  # pragma: no cover
        from typing_extensions import Literal


try:  # pragma: no cover
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib
    HAS_TOML = True
except ImportError:  # pragma: no cover
    HAS_TOML = False


def find_requirements_files(
    base_dir: str | Path = ".",
    depth: int = 1,
    *,
    verbose: bool = False,
) -> list[Path]:
    """Scan a directory for `requirements.yaml` and `pyproject.toml` files."""
    base_path = Path(base_dir)
    found_files = []

    # Define a helper function to recursively scan directories
    def _scan_dir(path: Path, current_depth: int) -> None:
        if verbose:
            print(f"🔍 Scanning in `{path}` at depth {current_depth}")
        if current_depth > depth:
            return
        for child in sorted(path.iterdir()):
            if child.is_dir():
                _scan_dir(child, current_depth + 1)
            elif child.name == "requirements.yaml":
                found_files.append(child)
                if verbose:
                    print(f'🔍 Found `"requirements.yaml"` at `{child}`')
            elif child.name == "pyproject.toml" and unidep_configured_in_toml(child):
                if verbose:
                    print(f'🔍 Found `"pyproject.toml"` with dependencies at `{child}`')
                found_files.append(child)

    _scan_dir(base_path, 0)
    return sorted(found_files)


def _extract_first_comment(
    commented_map: CommentedMap,
    index_or_key: int | str,
) -> str | None:
    """Extract the first comment from a CommentedMap."""
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


def _identifier(identifier: int, selector: str | None) -> str:
    """Return a unique identifier based on the comment."""
    platforms = None if selector is None else tuple(platforms_from_selector(selector))
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
) -> list[Spec]:
    name, pin, selector = parse_package_str(dependency)
    if name in ignore_pins:
        pin = None
    if name in skip_dependencies:
        return []
    if name in overwrite_pins:
        pin = overwrite_pins[name]
    comment = (
        _extract_first_comment(dependencies, index_or_key)
        if isinstance(dependencies, (CommentedMap, CommentedSeq))
        else None
    )
    if comment and selector is None:
        selector = selector_from_comment(comment)
    identifier_hash = _identifier(identifier, selector)
    if which == "both":
        return [
            Spec(name, "conda", pin, identifier_hash, selector),
            Spec(name, "pip", pin, identifier_hash, selector),
        ]
    return [Spec(name, which, pin, identifier_hash, selector)]


class ParsedRequirements(NamedTuple):
    """Requirements with comments."""

    channels: list[str]
    platforms: list[Platform]
    requirements: dict[str, list[Spec]]
    optional_dependencies: dict[str, dict[str, list[Spec]]]


class Requirements(NamedTuple):
    """Requirements as CommentedSeq."""

    # mypy doesn't support CommentedSeq[str], so we use list[str] instead.
    channels: list[str]  # actually a CommentedSeq[str]
    conda: list[str]  # actually a CommentedSeq[str]
    pip: list[str]  # actually a CommentedSeq[str]


def _parse_overwrite_pins(overwrite_pins: list[str]) -> dict[str, str | None]:
    """Parse overwrite pins."""
    result = {}
    for overwrite_pin in overwrite_pins:
        pkg = parse_package_str(overwrite_pin)
        result[pkg.name] = pkg.pin
    return result


def _load(p: Path, yaml: YAML) -> dict[str, Any]:
    if p.suffix == ".toml":
        if not HAS_TOML:  # pragma: no cover
            msg = (
                "❌ No toml support found in your Python installation."
                " If you are using unidep from `pyproject.toml` and this"
                " error occurs during installation, make sure you add"
                '\n\n[build-system]\nrequires = [..., "unidep[toml]"]\n\n'
                " Otherwise, please install it with `pip install tomli`."
            )
            raise ImportError(msg)
        with p.open("rb") as f:
            return tomllib.load(f)["tool"]["unidep"]
    with p.open() as f:
        return yaml.load(f)


def _get_local_dependencies(data: dict[str, Any]) -> list[str]:
    """Get `local_dependencies` from a `requirements.yaml` or `pyproject.toml` file."""
    if "local_dependencies" in data:
        return data["local_dependencies"]
    if "includes" in data:
        warn(
            "⚠️ You are using `includes` in `requirements.yaml` or `pyproject.toml`"
            " `[unidep.tool]` which is deprecated since 0.42.0 and has been renamed to"
            " `local_dependencies`.",
            category=DeprecationWarning,
            stacklevel=2,
        )
        return data["includes"]
    return []


def _to_path_with_extras(
    paths: list[Path],
    extras: list[list[str]] | Literal["*"] | None,
) -> list[PathWithExtras]:
    if isinstance(extras, (list, tuple)) and len(extras) != len(paths):
        msg = (
            f"Length of `extras` ({len(extras)}) does not match length of `paths`"
            f" ({len(paths)})."
        )
        raise ValueError(msg)
    paths_with_extras = [parse_folder_or_filename(p) for p in paths]
    if extras is None:
        return paths_with_extras
    assert extras is not None
    if any(p.extras for p in paths_with_extras):
        msg = (
            "Cannot specify `extras` list when paths are"
            " specified like `path/to/project[extra1,extra2]`, `extras` must be `None`"
            " or specify pure paths without extras like `path/to/project` and specify"
            " extras in `extras`."
        )
        raise ValueError(msg)
    if extras == "*":
        extras = [["*"]] * len(paths)  # type: ignore[list-item]

    return [PathWithExtras(p.path, e) for p, e in zip(paths_with_extras, extras)]


def _update_data_structures(
    *,
    path_with_extras: PathWithExtras,
    datas: list[dict[str, Any]],  # modified in place
    all_extras: list[list[str]],  # modified in place
    seen: set[Path],  # modified in place
    yaml: YAML,
    verbose: bool = False,
) -> None:
    if verbose:
        print(f"📄 Parsing `{path_with_extras.path_with_extras}`")
    data = _load(path_with_extras.path, yaml)
    datas.append(data)
    all_extras.append(path_with_extras.extras)
    _move_local_optional_dependencies_to_dependencies(
        data=data,  # modified in place
        path_with_extras=path_with_extras,
        verbose=verbose,
    )

    seen.add(path_with_extras.path.resolve())

    # Handle "local_dependencies" (or old name "includes", changed in 0.42.0)
    for local_dependency in _get_local_dependencies(data):
        _add_local_dependencies(
            local_dependency=local_dependency,
            path_with_extras=path_with_extras,
            datas=datas,  # modified in place
            all_extras=all_extras,  # modified in place
            seen=seen,  # modified in place
            yaml=yaml,
            verbose=verbose,
        )


def _move_local_optional_dependencies_to_dependencies(
    *,
    data: dict[str, Any],  # modified in place
    path_with_extras: PathWithExtras,
    verbose: bool = False,
) -> None:
    # Move local dependencies from `optional_dependencies` to `local_dependencies`
    extras = path_with_extras.extras
    if "*" in extras:
        extras = list(data.get("optional_dependencies", {}).keys())

    optional_dependencies = data.get("optional_dependencies", {})
    for extra in extras:
        moved = set()
        for dep in optional_dependencies.get(extra, []):
            if isinstance(dep, dict):
                # This is a {"pip": "package"} and/or {"conda": "package"} dependency
                continue
            if _str_is_path_like(dep):
                if verbose:
                    print(
                        f"📄 Moving `{dep}` from the `{extra}` section in"
                        " `optional_dependencies` to `local_dependencies`",
                    )
                data.setdefault("local_dependencies", []).append(dep)
                moved.add(dep)
        for dep in moved:
            extras = optional_dependencies[extra]  # key must exist if moved non-empty
            extras.pop(extras.index(dep))

    # Remove empty optional_dependencies sections
    to_delete = [extra for extra, deps in optional_dependencies.items() if not deps]
    for extra in to_delete:
        if verbose:
            print(f"📄 Removing empty `{extra}` section from `optional_dependencies`")
        optional_dependencies.pop(extra)


def _add_local_dependencies(
    *,
    local_dependency: str,
    path_with_extras: PathWithExtras,
    datas: list[dict[str, Any]],
    all_extras: list[list[str]],
    seen: set[Path],
    yaml: YAML,
    verbose: bool = False,
) -> None:
    try:
        requirements_dep_file = parse_folder_or_filename(
            path_with_extras.path.parent / local_dependency,
        )
        requirements_path = requirements_dep_file.path.resolve()
    except FileNotFoundError:
        # Means that this is a local package that is not managed by unidep.
        # We do not need to do anything here, just in `unidep install`.
        return
    if requirements_path in seen:
        return  # Avoids circular local_dependencies
    if verbose:
        print(f"📄 Parsing `{local_dependency}` from `local_dependencies`")
    datas.append(_load(requirements_path, yaml))
    all_extras.append(requirements_dep_file.extras)
    seen.add(requirements_path)


def parse_requirements(
    *paths: Path,
    ignore_pins: list[str] | None = None,
    overwrite_pins: list[str] | None = None,
    skip_dependencies: list[str] | None = None,
    verbose: bool = False,
    extras: list[list[str]] | Literal["*"] | None = None,
) -> ParsedRequirements:
    """Parse a list of `requirements.yaml` or `pyproject.toml` files.

    Parameters
    ----------
    paths
        Paths to `requirements.yaml` or `pyproject.toml` files.
    ignore_pins
        List of package names to ignore pins for.
    overwrite_pins
        List of package names with pins to overwrite.
    skip_dependencies
        List of package names to skip.
    verbose
        Whether to print verbose output.
    extras
        List of lists of extras to include. The outer list corresponds to the
        `requirements.yaml` or `pyproject.toml` files, the inner list to the
        extras to include for that file. If "*", all extras are included,
        if None, no extras are included.

    """
    paths_with_extras = _to_path_with_extras(paths, extras)  # type: ignore[arg-type]
    ignore_pins = ignore_pins or []
    skip_dependencies = skip_dependencies or []
    overwrite_pins_map = _parse_overwrite_pins(overwrite_pins or [])

    # `data` and `all_extras` are lists of the same length
    datas: list[dict[str, Any]] = []
    all_extras: list[list[str]] = []
    seen: set[Path] = set()
    yaml = YAML(typ="rt")
    for path_with_extras in paths_with_extras:
        _update_data_structures(
            path_with_extras=path_with_extras,
            datas=datas,  # modified in place
            all_extras=all_extras,  # modified in place
            seen=seen,  # modified in place
            yaml=yaml,
            verbose=verbose,
        )

    assert len(datas) == len(all_extras)

    # Parse the requirements from loaded data
    requirements: dict[str, list[Spec]] = defaultdict(list)
    optional_dependencies: dict[str, dict[str, list[Spec]]] = defaultdict(
        lambda: defaultdict(list),
    )
    channels: set[str] = set()
    platforms: set[Platform] = set()

    identifier = -1
    for _extras, data in zip(all_extras, datas):
        channels.update(data.get("channels", []))
        platforms.update(data.get("platforms", []))
        if "dependencies" in data:
            identifier = _add_dependencies(
                data["dependencies"],
                requirements,  # modified in place
                identifier,
                ignore_pins,
                overwrite_pins_map,
                skip_dependencies,
            )
        for opt_name, opt_deps in data.get("optional_dependencies", {}).items():
            if opt_name in _extras or "*" in _extras:
                identifier = _add_dependencies(
                    opt_deps,
                    optional_dependencies[opt_name],  # modified in place
                    identifier,
                    ignore_pins,
                    overwrite_pins_map,
                    skip_dependencies,
                    is_optional=True,
                )

    return ParsedRequirements(
        sorted(channels),
        sorted(platforms),
        dict(requirements),
        defaultdict_to_dict(optional_dependencies),
    )


def _str_is_path_like(s: str) -> bool:
    """Check if a string is path-like."""
    return os.path.sep in s or "/" in s or s.startswith(".")


def _check_allowed_local_dependency(name: str, is_optional: bool) -> None:  # noqa: FBT001
    if _str_is_path_like(name):
        # There should not be path-like dependencies in the optional_dependencies
        # section after _move_local_optional_dependencies_to_dependencies.
        assert not is_optional
        msg = (
            f"Local dependencies (`{name}`) are not allowed in `dependencies`."
            " Use the `local_dependencies` section instead."
        )
        raise ValueError(msg)


def _add_dependencies(
    dependencies: list[str],
    requirements: dict[str, list[Spec]],  # modified in place
    identifier: int,
    ignore_pins: list[str],
    overwrite_pins_map: dict[str, str | None],
    skip_dependencies: list[str],
    *,
    is_optional: bool = False,
) -> int:
    for i, dep in enumerate(dependencies):
        identifier += 1
        if isinstance(dep, str):
            specs = _parse_dependency(
                dep,
                dependencies,
                i,
                "both",
                identifier,
                ignore_pins,
                overwrite_pins_map,
                skip_dependencies,
            )
            for spec in specs:
                _check_allowed_local_dependency(spec.name, is_optional)
                requirements[spec.name].append(spec)
            continue
        assert isinstance(dep, dict)
        for which in ["conda", "pip"]:
            if which in dep:
                specs = _parse_dependency(
                    dep[which],
                    dep,
                    which,
                    which,  # type: ignore[arg-type]
                    identifier,
                    ignore_pins,
                    overwrite_pins_map,
                    skip_dependencies,
                )
                for spec in specs:
                    _check_allowed_local_dependency(spec.name, is_optional)
                    requirements[spec.name].append(spec)
    return identifier


# Alias for backwards compatibility
parse_yaml_requirements = parse_requirements


def _extract_local_dependencies(
    path: Path,
    base_path: Path,
    processed: set[Path],
    dependencies: dict[str, set[str]],
    *,
    check_pip_installable: bool = True,
    verbose: bool = False,
    raise_if_missing: bool = True,
    warn_non_managed: bool = True,
) -> None:
    path, extras = parse_folder_or_filename(path)
    if path in processed:
        return
    processed.add(path)
    yaml = YAML(typ="safe")
    data = _load(path, yaml)
    # Handle "local_dependencies" (or old name "includes", changed in 0.42.0)
    for local_dependency in _get_local_dependencies(data):
        assert not os.path.isabs(local_dependency)  # noqa: PTH117
        local_path, extras = split_path_and_extras(local_dependency)
        abs_local = (path.parent / local_path).resolve()
        if not abs_local.exists():
            if raise_if_missing:
                msg = f"File `{abs_local}` not found."
                raise FileNotFoundError(msg)
            continue

        try:
            requirements_path = parse_folder_or_filename(abs_local).path
        except FileNotFoundError:
            # Means that this is a local package that is not managed by unidep.
            if is_pip_installable(abs_local):
                dependencies[str(base_path)].add(str(abs_local))
                if warn_non_managed:
                    # We do not need to emit this warning when `pip install` is called
                    warn(
                        f"⚠️ Installing a local dependency (`{abs_local.name}`) which"
                        " is not managed by unidep, this will skip all of its"
                        " dependencies, i.e., it will call `pip install` with"
                        "  `--no-dependencies`. To properly manage this dependency,"
                        " add a `requirements.yaml` or `pyproject.toml` file with"
                        " `[tool.unidep]` in its directory.",
                    )
            else:
                msg = (
                    f"`{local_dependency}` in `local_dependencies` is not pip"
                    " installable nor is it managed by unidep. Remove it"
                    " from `local_dependencies`."
                )
                raise RuntimeError(msg) from None
            continue

        project_path = str(requirements_path.parent)
        if project_path == str(base_path):
            continue
        if not check_pip_installable or is_pip_installable(requirements_path.parent):
            dependencies[str(base_path)].add(project_path)
        if verbose:
            print(f"🔗 Adding `{requirements_path}` from `local_dependencies`")
        _extract_local_dependencies(
            requirements_path,
            base_path,
            processed,
            dependencies,
            check_pip_installable=check_pip_installable,
            verbose=verbose,
        )


def parse_local_dependencies(
    *paths: Path,
    check_pip_installable: bool = True,
    verbose: bool = False,
    raise_if_missing: bool = True,
    warn_non_managed: bool = True,
) -> dict[Path, list[Path]]:
    """Extract local project dependencies from a list of `requirements.yaml` or `pyproject.toml` files.

    Works by loading the specified `local_dependencies` list.

    Returns a dictionary with the:
    name of the project folder => list of `Path`s of local dependencies folders.
    """  # noqa: E501
    dependencies: dict[str, set[str]] = defaultdict(set)

    for p in paths:
        if verbose:
            print(f"🔗 Analyzing dependencies in `{p}`")
        base_path = p.resolve().parent
        _extract_local_dependencies(
            path=p,
            base_path=base_path,
            processed=set(),
            dependencies=dependencies,
            check_pip_installable=check_pip_installable,
            verbose=verbose,
            raise_if_missing=raise_if_missing,
            warn_non_managed=warn_non_managed,
        )

    return {
        Path(k): sorted({Path(v) for v in v_set})
        for k, v_set in sorted(dependencies.items())
    }


def yaml_to_toml(yaml_path: Path) -> str:
    """Converts a `requirements.yaml` file TOML format."""
    try:
        import tomli_w
    except ImportError:  # pragma: no cover
        msg = (
            "❌ `tomli_w` is required to convert YAML to TOML."
            " Install it with `pip install tomli_w`."
        )
        raise ImportError(msg) from None
    yaml = YAML(typ="rt")
    data = _load(yaml_path, yaml)
    data.pop("name", None)
    dependencies = data.get("dependencies", [])
    for i, dep in enumerate(dependencies):
        if isinstance(dep, str):
            comment = _extract_first_comment(dependencies, i)
            if comment is not None:
                selector = selector_from_comment(comment)
                if selector is not None:
                    dependencies[i] = f"{dep}:{selector}"
            continue
        assert isinstance(dep, dict)
        for which in ["conda", "pip"]:
            if which in dep:
                comment = _extract_first_comment(dep, which)
                if comment is not None:
                    selector = selector_from_comment(comment)
                    if selector is not None:
                        dep[which] = f"{dep[which]}:{selector}"

    return tomli_w.dumps({"tool": {"unidep": data}})
