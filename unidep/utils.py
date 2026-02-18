"""unidep - Unified Conda and Pip requirements management.

This module provides utility functions used throughout the package.
"""

from __future__ import annotations

import ast
import codecs
import configparser
import contextlib
import platform
import re
import sys
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal, NamedTuple, cast

from unidep._version import __version__
from unidep.platform_definitions import (
    PEP508_MARKERS,
    Platform,
    Selector,
    Spec,
    platforms_from_selector,
    validate_selector,
)

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


def add_comment_to_file(
    filename: str | Path,
    extra_lines: list[str] | None = None,
) -> None:
    """Add a comment to the top of a file."""
    if extra_lines is None:
        extra_lines = []
    with open(filename, "r+") as f:  # noqa: PTH123
        content = f.read()
        f.seek(0, 0)
        command_line_args = " ".join(sys.argv[1:])
        txt = [
            f"# This file is created and managed by `unidep` {__version__}.",
            "# For details see https://github.com/basnijholt/unidep",
            f"# File generated with: `unidep {command_line_args}`",
            *extra_lines,
        ]
        content = "\n".join(txt) + "\n\n" + content
        f.write(content)


def remove_top_comments(filename: str | Path) -> None:
    """Removes the top comments (lines starting with '#') from a file."""
    with open(filename) as file:  # noqa: PTH123
        lines = file.readlines()

    first_non_comment = next(
        (i for i, line in enumerate(lines) if not line.strip().startswith("#")),
        len(lines),
    )
    content_without_comments = lines[first_non_comment:]
    with open(filename, "w") as file:  # noqa: PTH123
        file.writelines(content_without_comments)


def escape_unicode(string: str) -> str:
    """Escape unicode characters."""
    return codecs.decode(string, "unicode_escape")


def is_pip_installable(folder: str | Path) -> bool:  # pragma: no cover
    """Determine if the project is pip installable.

    Checks for existence of setup.py or [build-system] in pyproject.toml.
    If the `toml` library is available, it is used to parse the `pyproject.toml` file.
    If the `toml` library is not available, the function checks for the existence of
    a line starting with "[build-system]". This does not handle the case where
    [build-system] is inside of a multi-line literal string.
    """
    path = Path(folder)
    if (path / "setup.py").exists():
        return True

    pyproject_path = path / "pyproject.toml"
    if pyproject_path.exists():
        with pyproject_path.open("rb") as file:
            pyproject_data = tomllib.load(file)
            return "build-system" in pyproject_data
    return False


class UnsupportedPlatformError(Exception):
    """Raised when the current platform is not supported."""


def identify_current_platform() -> Platform:
    """Detect the current platform."""
    system = platform.system().lower()
    architecture = platform.machine().lower()

    if system == "linux":
        if architecture == "x86_64":
            return "linux-64"
        if architecture == "aarch64":
            return "linux-aarch64"
        if architecture == "ppc64le":
            return "linux-ppc64le"
        msg = f"Unsupported Linux architecture `{architecture}`"
        raise UnsupportedPlatformError(msg)
    if system == "darwin":
        if architecture == "x86_64":
            return "osx-64"
        if architecture == "arm64":
            return "osx-arm64"
        msg = f"Unsupported macOS architecture `{architecture}`"
        raise UnsupportedPlatformError(msg)
    if system == "windows":
        if "64" in architecture:
            return "win-64"
        msg = f"Unsupported Windows architecture `{architecture}`"
        raise UnsupportedPlatformError(msg)
    msg = f"Unsupported operating system `{system}` with architecture `{architecture}`"
    raise UnsupportedPlatformError(msg)


def collect_selector_platforms(
    requirements: dict[str, list[Spec]],
    optional_dependencies: dict[str, dict[str, list[Spec]]] | None = None,
) -> list[Platform]:
    """Collect all platforms referenced by dependency selectors."""
    selector_platforms: set[Platform] = set()

    def _collect(specs_by_name: dict[str, list[Spec]]) -> None:
        for specs in specs_by_name.values():
            for spec in specs:
                if spec.selector is None:
                    continue
                selector_platforms.update(platforms_from_selector(spec.selector))

    _collect(requirements)
    if optional_dependencies is not None:
        for optional_specs in optional_dependencies.values():
            _collect(optional_specs)
    return sorted(selector_platforms)


def resolve_platforms(
    *,
    requested_platforms: list[Platform] | None,
    declared_platforms: list[Platform] | set[Platform] | None = None,
    selector_platforms: list[Platform] | set[Platform] | None = None,
    default_current: bool = True,
) -> list[Platform]:
    """Resolve effective platforms with a shared precedence policy.

    Precedence is:
    1) explicitly requested platforms
    2) declared platforms from requirements files
    3) selector-derived platforms from dependency specs
    4) current platform fallback (optional)
    """
    for candidate in (requested_platforms, declared_platforms, selector_platforms):
        if candidate:
            return sorted(set(candidate))
    if default_current:
        return [identify_current_platform()]
    return []


def build_pep508_environment_marker(
    platforms: list[Platform | tuple[Platform, ...]],
) -> str:
    """Generate a PEP 508 selector for a list of platforms."""
    sorted_platforms = tuple(sorted(platforms))
    if sorted_platforms in PEP508_MARKERS:
        return PEP508_MARKERS[sorted_platforms]  # type: ignore[index]
    environment_markers = [
        PEP508_MARKERS[platform]
        for platform in sorted(sorted_platforms)
        if platform in PEP508_MARKERS
    ]
    return " or ".join(environment_markers)


class ParsedPackageStr(NamedTuple):
    """A package name and version pinning."""

    name: str
    pin: str | None = None
    # can be of type `Selector` but also space separated string of `Selector`s
    selector: str | None = None


def parse_package_str(package_str: str) -> ParsedPackageStr:
    """Splits a string into package name, version pinning, and platform selector."""
    # Regex to match package name, version pinning, and optionally platform selector
    # Note: the name_pattern currently allows for paths and extras, however,
    # paths cannot contain spaces or contain brackets.
    name_pattern = r"[a-zA-Z0-9_.\-/]+(\[[a-zA-Z0-9_.,\-]+\])?"
    version_pin_pattern = r".*?"
    selector_pattern = r"[a-z0-9\s]+"
    pattern = rf"({name_pattern})\s*({version_pin_pattern})?(:({selector_pattern}))?$"
    match = re.match(pattern, package_str)

    if match:
        package_name = match.group(1).strip()
        version_pin = match.group(3).strip() if match.group(3) else None
        selector = match.group(5).strip() if match.group(5) else None

        if selector is not None:
            for s in selector.split():
                validate_selector(cast("Selector", s))

        return ParsedPackageStr(
            package_name,
            version_pin,
            selector,
        )

    msg = f"Invalid package string: '{package_str}'"
    raise ValueError(msg)


def package_name_from_setup_cfg(file_path: Path) -> str:
    """Read the package name from ``setup.cfg`` metadata."""
    config = configparser.ConfigParser()
    config.read(file_path)
    name = config.get("metadata", "name", fallback=None)
    if name is None:
        msg = "Could not find the package name in the setup.cfg file."
        raise KeyError(msg)
    return name


def package_name_from_setup_py(file_path: Path) -> str:
    """Read the package name from a simple ``setup.py`` AST."""
    with file_path.open() as f:
        file_content = f.read()

    tree = ast.parse(file_content)

    class SetupVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.package_name = None

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            if isinstance(node.func, ast.Name) and node.func.id == "setup":
                for keyword in node.keywords:
                    if keyword.arg == "name":
                        self.package_name = keyword.value.value  # type: ignore[attr-defined]

    visitor = SetupVisitor()
    visitor.visit(tree)
    if visitor.package_name is None:
        msg = "Could not find the package name in the setup.py file."
        raise KeyError(msg)
    assert isinstance(visitor.package_name, str)
    return visitor.package_name


def package_name_from_pyproject_toml(file_path: Path) -> str:
    """Read project name from ``pyproject.toml`` (PEP 621 or Poetry)."""
    with file_path.open("rb") as f:
        data = tomllib.load(f)
    with contextlib.suppress(KeyError):
        return data["project"]["name"]
    with contextlib.suppress(KeyError):
        return data["tool"]["poetry"]["name"]
    msg = f"Could not find the package name in the pyproject.toml file: {data}."
    raise KeyError(msg)


def package_name_from_path(path: Path) -> str:
    """Get the package name from ``pyproject.toml``, ``setup.cfg``, or ``setup.py``."""
    pyproject_toml = path / "pyproject.toml"
    if pyproject_toml.exists():
        with contextlib.suppress(Exception):
            return package_name_from_pyproject_toml(pyproject_toml)

    setup_cfg = path / "setup.cfg"
    if setup_cfg.exists():
        with contextlib.suppress(Exception):
            return package_name_from_setup_cfg(setup_cfg)

    setup_py = path / "setup.py"
    if setup_py.exists():
        with contextlib.suppress(Exception):
            return package_name_from_setup_py(setup_py)

    return path.name


def _simple_warning_format(
    message: Warning | str,
    category: type[Warning],  # noqa: ARG001
    filename: str,
    lineno: int,
    line: str | None = None,  # noqa: ARG001
) -> str:  # pragma: no cover
    """Format warnings without code context."""
    return (
        f"---------------------\n"
        f"⚠️  *** WARNING *** ⚠️\n"
        f"{message}\n"
        f"Location: {filename}:{lineno}\n"
        f"---------------------\n"
    )


def warn(
    message: str | Warning,
    category: type[Warning] = UserWarning,
    stacklevel: int = 1,
) -> None:
    """Emit a warning with a custom format specific to this package."""
    original_format = warnings.formatwarning
    warnings.formatwarning = _simple_warning_format
    try:
        warnings.warn(message, category, stacklevel=stacklevel + 1)
    finally:
        warnings.formatwarning = original_format


def selector_from_comment(comment: str) -> str | None:
    """Extract a valid selector from a comment."""
    multiple_brackets_pat = re.compile(r"#.*\].*\[")  # Detects multiple brackets
    if multiple_brackets_pat.search(comment):
        msg = f"Multiple bracketed selectors found in comment: '{comment}'"
        raise ValueError(msg)

    sel_pat = re.compile(r"#\s*\[([^\[\]]+)\]")
    m = sel_pat.search(comment)
    if not m:
        return None
    selectors = m.group(1).strip().split()
    for s in selectors:
        validate_selector(cast("Selector", s))
    return " ".join(selectors)


def extract_matching_platforms(comment: str) -> list[Platform]:
    """Get all platforms matching a comment."""
    selector = selector_from_comment(comment)
    if selector is None:
        return []
    return platforms_from_selector(selector)


def unidep_configured_in_toml(path: Path) -> bool:
    """Check if dependencies are specified in pyproject.toml."""
    with path.open("rb") as f:
        data = tomllib.load(f)
    return bool(data.get("tool", {}).get("unidep", {}))


def split_path_and_extras(input_str: str | Path) -> tuple[Path, list[str]]:
    """Parse a string of the form `path/to/file[extra1,extra2]` into parts.

    Returns a tuple of the `pathlib.Path` and a list of extras
    """
    if isinstance(input_str, Path):
        input_str = str(input_str)

    if not input_str:  # Check for empty string
        return Path(), []

    pattern = r"^(.+?)(?:\[([^\[\]]+)\])?$"
    match = re.search(pattern, input_str)

    if match is None:  # pragma: no cover
        # I don't think this is possible, but just in case
        return Path(), []

    path = Path(match.group(1))
    extras = match.group(2)
    if not extras:
        return path, []
    extras = [extra.strip() for extra in extras.split(",")]
    return path, extras


class PathWithExtras(NamedTuple):
    """A dependency file and extras."""

    path: Path
    extras: list[str]

    @property
    def path_with_extras(self) -> Path:
        """Path including extras, e.g., `path/to/file[test,docs]`."""
        if not self.extras:
            return self.path
        return Path(f"{self.path}[{','.join(self.extras)}]")

    def resolved(self) -> PathWithExtras:
        """Resolve the path and extras."""
        return PathWithExtras(self.path.resolve(), self.extras)

    def canonicalized(self) -> PathWithExtras:
        """Resolve path and normalize extras for deterministic graph keys."""
        return PathWithExtras(self.path.resolve(), sorted(set(self.extras)))

    def __hash__(self) -> int:
        """Hash the path and extras."""
        return hash((self.path, tuple(sorted(self.extras))))

    def __eq__(self, other: object) -> bool:
        """Check if two `PathWithExtras` are equal."""
        if not isinstance(other, PathWithExtras):
            return NotImplemented
        return self.path == other.path and set(self.extras) == set(other.extras)


LocalDependencyUse = Literal["local", "pypi", "skip"]


class LocalDependency(NamedTuple):
    """A local dependency with optional PyPI alternative and `use` mode."""

    local: str
    pypi: str | None = None
    use: LocalDependencyUse = "local"


def parse_folder_or_filename(folder_or_file: str | Path) -> PathWithExtras:
    """Get the path to `requirements.yaml` or `pyproject.toml` file."""
    folder_or_file, extras = split_path_and_extras(folder_or_file)
    path = Path(folder_or_file)
    if path.is_dir():
        fname_yaml = path / "requirements.yaml"
        if fname_yaml.exists():
            return PathWithExtras(fname_yaml, extras)
        fname_toml = path / "pyproject.toml"
        if fname_toml.exists() and unidep_configured_in_toml(fname_toml):
            return PathWithExtras(fname_toml, extras)
        msg = (
            f"File `{fname_yaml}` or `{fname_toml}` (with unidep configuration)"
            f" not found in `{folder_or_file}`."
        )
        raise FileNotFoundError(msg)
    if not path.exists():
        msg = f"File `{path}` not found."
        raise FileNotFoundError(msg)
    return PathWithExtras(path, extras)


def defaultdict_to_dict(d: defaultdict | Any) -> dict:
    """Convert (nested) defaultdict to (nested) dict."""
    if isinstance(d, defaultdict):
        d = {key: defaultdict_to_dict(value) for key, value in d.items()}
    return d


def get_package_version(package_name: str) -> str | None:
    """Returns the version of the given package.

    Parameters
    ----------
    package_name
        The name of the package to find the version of.

    Returns
    -------
    The version of the package, or None if the package is not found.

    """
    if sys.version_info >= (3, 8):
        import importlib.metadata

        try:
            return importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            return None
    else:  # pragma: no cover
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import pkg_resources

        try:
            return pkg_resources.get_distribution(package_name).version
        except pkg_resources.DistributionNotFound:
            return None
