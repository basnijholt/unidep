"""unidep - Unified Conda and Pip requirements management.

This module provides utility functions used throughout the package.
"""

from __future__ import annotations

import ast
import codecs
import configparser
import contextlib
import importlib.util
import io
import platform
import re
import sys
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal, NamedTuple, cast

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

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

    def _string_literal(node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    class SetupVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.package_name: str | None = None

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            if isinstance(node.func, ast.Name) and node.func.id == "setup":
                for keyword in node.keywords:
                    if keyword.arg == "name":
                        self.package_name = _string_literal(keyword.value)
                        if self.package_name is not None:
                            return

    visitor = SetupVisitor()
    visitor.visit(tree)
    if visitor.package_name is None:
        msg = "Could not find the package name in the setup.py file."
        raise KeyError(msg)
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
        with contextlib.suppress(
            KeyError,
            OSError,
            TypeError,
            UnicodeError,
            tomllib.TOMLDecodeError,
        ):
            return package_name_from_pyproject_toml(pyproject_toml)

    setup_cfg = path / "setup.cfg"
    if setup_cfg.exists():
        with contextlib.suppress(
            KeyError,
            OSError,
            UnicodeError,
            configparser.Error,
        ):
            return package_name_from_setup_cfg(setup_cfg)

    setup_py = path / "setup.py"
    if setup_py.exists():
        with contextlib.suppress(
            KeyError,
            OSError,
            SyntaxError,
            UnicodeError,
            ValueError,
        ):
            return package_name_from_setup_py(setup_py)

    return path.name


def _parsed_direct_reference(requirement: str) -> tuple[str, str, str] | None:
    """Return the canonical package name, display name, and URL for a direct ref."""
    try:
        parsed = Requirement(requirement)
    except InvalidRequirement:
        return None
    if parsed.url is None:
        return None
    return canonicalize_name(parsed.name), parsed.name, parsed.url


def detect_conflicting_direct_references(
    requirements: list[str],
    *,
    context: str,
) -> list[str]:
    """Deduplicate direct references and fail on conflicting sources.

    This catches cases like two ``file://`` URLs for the same package name
    before pip/uv reports a lower-level resolver error.
    """
    deduplicated: list[str] = []
    seen_exact: set[str] = set()
    seen_direct_refs: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list),
    )

    for requirement in requirements:
        normalized = requirement.strip()
        if normalized in seen_exact:
            continue
        seen_exact.add(normalized)

        direct_reference = _parsed_direct_reference(normalized)
        if direct_reference is None:
            deduplicated.append(normalized)
            continue

        canonical_name, package_name, source_url = direct_reference
        existing = seen_direct_refs[canonical_name]
        conflicting = [
            existing_requirement
            for existing_url, existing_requirements in existing.items()
            if existing_url != source_url
            for existing_requirement in existing_requirements
        ]
        if conflicting:
            msg = format_duplicate_package_sources_message(
                package_name,
                [*conflicting, normalized],
            )
            msg += f"\n\nWhile {context}."
            raise RuntimeError(msg)

        existing[source_url].append(normalized)
        deduplicated.append(normalized)

    return deduplicated


def detect_conflicting_direct_reference_groups(
    requirement_groups: dict[str, list[str]],
    *,
    context: str,
) -> dict[str, list[str]]:
    """Validate direct references across multiple dependency groups."""
    deduplicated_groups = {
        group_name: detect_conflicting_direct_references(
            requirements,
            context=context,
        )
        for group_name, requirements in requirement_groups.items()
    }
    seen_direct_refs: dict[str, dict[str, list[tuple[str, str]]]] = defaultdict(
        lambda: defaultdict(list),
    )

    for group_name, requirements in deduplicated_groups.items():
        for requirement in requirements:
            direct_reference = _parsed_direct_reference(requirement)
            if direct_reference is None:
                continue

            canonical_name, package_name, source_url = direct_reference
            existing = seen_direct_refs[canonical_name]
            conflicting = [
                f"{existing_group}: {existing_requirement}"
                for existing_url, existing_entries in existing.items()
                if existing_url != source_url
                for existing_group, existing_requirement in existing_entries
            ]
            if conflicting:
                msg = format_duplicate_package_sources_message(
                    package_name,
                    [*conflicting, f"{group_name}: {requirement}"],
                )
                msg += f"\n\nWhile {context}."
                raise RuntimeError(msg)

            existing[source_url].append((group_name, requirement))

    return deduplicated_groups


def detect_duplicate_local_package_paths(paths: list[Path]) -> None:
    """Raise when multiple local paths map to the same distribution name."""
    name_to_paths: dict[str, list[Path]] = defaultdict(list)

    for path in paths:
        resolved = path.resolve()
        canonical_name = canonicalize_name(package_name_from_path(resolved))
        if resolved not in name_to_paths[canonical_name]:
            name_to_paths[canonical_name].append(resolved)

    duplicates = {
        name: local_paths
        for name, local_paths in name_to_paths.items()
        if len(local_paths) > 1
    }
    if not duplicates:
        return

    duplicate_lines = []
    for name, local_paths in sorted(duplicates.items()):
        duplicate_lines.append(f"- {name}")
        duplicate_lines.extend(f"  - {path}" for path in local_paths)

    msg = format_cli_diagnostic(
        "Multiple local packages resolve to the same distribution name.",
        why=[
            "pip and uv may treat these paths as conflicting sources for one"
            " package and fail with duplicate file URL errors",
        ],
        fixes=[
            "keep only one local checkout for each package name",
            "use `use: pypi` or `use: skip` in `local_dependencies` to exclude"
            " vendored copies",
        ],
    )
    detected_paths = "\n".join(duplicate_lines)
    msg += f"\n\nDetected paths:\n{detected_paths}"
    raise RuntimeError(msg)


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


def format_cli_diagnostic(
    summary: str,
    *,
    detected: dict[str, str] | None = None,
    why: list[str] | None = None,
    fixes: list[str] | None = None,
    tips: list[str] | None = None,
    prefix: str = "❌",
) -> str:
    """Format a user-facing CLI diagnostic with consistent sections."""
    sections = _cli_diagnostic_sections(
        detected=detected,
        why=why,
        fixes=fixes,
        tips=tips,
    )
    if _rich_available():
        with contextlib.suppress(ImportError):
            return _format_cli_diagnostic_with_rich(summary, sections, prefix)
    return _format_cli_diagnostic_plain(summary, sections, prefix)


def _cli_diagnostic_sections(
    *,
    detected: dict[str, str] | None,
    why: list[str] | None,
    fixes: list[str] | None,
    tips: list[str] | None,
) -> list[tuple[str, list[str]]]:
    """Build ordered diagnostic sections for CLI messages."""
    sections: list[tuple[str, list[str]]] = []
    if detected:
        sections.append(
            ("Detected:", [f"{key}: {value}" for key, value in detected.items()]),
        )
    if why:
        sections.append(("Why this matters:", why))
    if fixes:
        sections.append(("Do this:", fixes))
    if tips:
        sections.append(("Tip:", tips))
    return sections


def _format_cli_diagnostic_plain(
    summary: str,
    sections: list[tuple[str, list[str]]],
    prefix: str,
) -> str:
    """Render a diagnostic with plain text only."""
    lines = [f"{prefix} {summary}"]
    for heading, items in sections:
        lines.extend(["", heading, *[f"- {item}" for item in items]])
    return "\n".join(lines)


def _rich_available() -> bool:
    """Return whether Rich is importable."""
    return importlib.util.find_spec("rich") is not None


def _format_cli_diagnostic_with_rich(
    summary: str,
    sections: list[tuple[str, list[str]]],
    prefix: str,
) -> str:
    """Render a diagnostic with Rich while preserving string return semantics."""
    from rich import box
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.text import Text

    border_style = _diagnostic_border_style(prefix)
    renderables = []

    summary_line = Text()
    summary_line.append(f"{prefix} ", style=f"bold {border_style}")
    summary_line.append(summary, style="bold")
    renderables.append(summary_line)

    for heading, items in sections:
        renderables.append(Text())
        renderables.append(Text(heading, style="bold cyan"))
        for item in items:
            bullet_line = Text()
            bullet_line.append("• ", style=border_style)
            bullet_line.append(item)
            renderables.append(bullet_line)

    content_lines = [f"{prefix} {summary}"]
    for heading, items in sections:
        content_lines.append(heading)
        content_lines.extend(f"• {item}" for item in items)

    console = Console(
        file=io.StringIO(),
        record=True,
        width=max(60, max(len(line) for line in content_lines) + 4),
        color_system=None,
        highlight=False,
    )
    console.print(
        Panel.fit(
            Group(*renderables),
            border_style=border_style,
            box=box.ROUNDED,
            padding=(0, 1),
        ),
        soft_wrap=True,
    )
    return console.export_text(styles=False).rstrip()


def _diagnostic_border_style(prefix: str) -> str:
    """Map a diagnostic prefix to a Rich color."""
    if prefix == "⚠️":
        return "yellow"
    if prefix == "\N{INFORMATION SOURCE}\N{VARIATION SELECTOR-16}":
        return "cyan"
    return "red"


def format_duplicate_package_sources_message(
    package_name: str,
    sources: list[str],
) -> str:
    """Format a diagnostic for multiple sources resolving to one package."""
    sources_block = "\n".join(f"- {source}" for source in sources)
    msg = format_cli_diagnostic(
        f"UniDep found multiple sources for the same package `{package_name}`.",
        why=[
            "pip and uv cannot reliably resolve one package name from multiple"
            " direct references or conflicting requirement strings",
            "this usually means a vendored copy or duplicate local dependency path"
            " is being pulled in",
        ],
        fixes=[
            f"keep only one source for `{package_name}`",
            "mark one path with `use: pypi` or `use: skip` if you want to override"
            " a nested vendor copy",
            "remove the duplicate entry from `local_dependencies` if both sources are"
            " not needed",
        ],
    )
    return f"{msg}\n\nConflicting sources:\n{sources_block}"


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


def selected_extra_names(
    requested_extras: list[str],
    available_extras: dict[str, Any],
) -> list[str]:
    """Return requested extras that exist, treating `*` as all extras."""
    selected = []
    seen = set()
    if "*" in requested_extras:
        for extra in available_extras:
            selected.append(extra)
            seen.add(extra)
    for extra in requested_extras:
        if extra == "*" or extra not in available_extras or extra in seen:
            continue
        selected.append(extra)
        seen.add(extra)
    return selected


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
