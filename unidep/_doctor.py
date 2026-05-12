"""Read-only diagnostics for common Python environment problems."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from ruamel.yaml import YAML

from unidep._dependencies_parsing import (
    _load,
    find_requirements_files,
    get_local_dependencies,
)
from unidep.utils import split_path_and_extras

CONDA_DISTRIBUTIONS = {
    "anaconda": ("anaconda3", "anaconda"),
    "miniconda": ("miniconda3", "miniconda"),
    "miniforge": ("miniforge3", "miniforge"),
    "mambaforge": ("mambaforge",),
    "micromamba": ("micromamba",),
    "mamba": ("mamba",),
}

SHELL_PROFILE_FILES = (
    ".bash_profile",
    ".bash_login",
    ".bashrc",
    ".profile",
    ".zprofile",
    ".zshrc",
    ".zlogin",
    ".cshrc",
    ".tcshrc",
)

PYTHON_ENVIRONMENT_VARIABLES = (
    "CONDA_PREFIX",
    "VIRTUAL_ENV",
    "PYENV_VERSION",
    "POETRY_ACTIVE",
    "PIPENV_ACTIVE",
    "PDM_ACTIVE",
    "UV_PROJECT_ENVIRONMENT",
    "HATCH_ENV_ACTIVE",
)

VIRTUALENV_WRAPPER_MARKERS = ("POETRY_ACTIVE", "PIPENV_ACTIVE")
SHADOWED_EXECUTABLES = (
    "pip",
    "pip3",
    "conda",
    "mamba",
    "micromamba",
    "uv",
)
SHADOWED_VERSION_STYLE = "bold cyan"
VERSION_PROBE_TIMEOUT_SECONDS = 2
CONDA_ROOT_PATTERN = re.compile(
    r"(?P<root>(?:~|\$HOME|\$\{HOME\}|[A-Za-z]:[/\\]|[/\\])[^\"':;$()]*?)"
    r"(?P<terminator>[/\\]etc[/\\]profile\.d[/\\]conda\.sh"
    r"|[/\\]bin[/\\](?:conda|mamba|micromamba)\b"
    r"|[/\\](?:bin|condabin)(?=[:;\"' )]|$))",
    re.IGNORECASE,
)
CONDA_ROOT_NAMES = {
    "conda",
    *(marker for markers in CONDA_DISTRIBUTIONS.values() for marker in markers),
}
SUFFIXED_CONDA_ROOT_NAMES = CONDA_ROOT_NAMES - {"conda", "mamba"}
CONDA_ROOT_SUFFIX_SEPARATORS = ("-", "_", ".")


@dataclass(frozen=True)
class DoctorFinding:
    """A single diagnostic finding."""

    code: str
    level: str
    title: str
    details: str
    recommendation: str


@dataclass(frozen=True)
class DoctorReport:
    """The full set of doctor diagnostics."""

    findings: tuple[DoctorFinding, ...]

    def finding_by_code(self, code: str) -> DoctorFinding | None:
        """Return the first finding with ``code``."""
        for finding in self.findings:
            if finding.code == code:
                return finding
        return None

    def summary(self) -> dict[str, int]:
        """Return finding counts by level."""
        return {
            level: sum(1 for finding in self.findings if finding.level == level)
            for level in ("error", "info", "warning")
        }

    def exit_code(self, *, strict: bool = False) -> int:
        """Return the recommended command exit code for this report."""
        if any(finding.level == "error" for finding in self.findings):
            return 1
        if strict and any(finding.level == "warning" for finding in self.findings):
            return 1
        return 0


@dataclass(frozen=True)
class _CondaInitializer:
    distribution: str
    profile: Path
    line_number: int
    root: str | None = None
    normalized_root: str | None = None

    def format_location(self, home: Path, *, include_root: bool = False) -> str:
        try:
            profile = self.profile.relative_to(home)
        except ValueError:  # pragma: no cover
            profile = self.profile
        details = self.distribution
        if include_root and self.root:
            details = f"{details}, {self.root}"
        return f"{profile}:{self.line_number} ({details})"


@dataclass(frozen=True)
class _ShellProfileReadError:
    profile: Path
    error: OSError

    def format_location(self, home: Path) -> str:
        try:
            profile = self.profile.relative_to(home)
        except ValueError:  # pragma: no cover
            profile = self.profile
        return f"{profile}: {self.error}"


@dataclass(frozen=True)
class _ExecutableVersion:
    path: Path
    version: str | None = None
    error: str | None = None

    def format_details(self) -> str:
        if self.version:
            return f"{self.path} ({self.version})"
        return str(self.path)


def run_doctor_command(
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    path_env: str | None = None,
    python_executable: str | None = None,
    project_dir: Path | None = None,
    output_format: str = "text",
    strict: bool = False,
) -> int:
    """Run doctor diagnostics, print a report, and return an exit code."""
    report = run_doctor_checks(
        home=home,
        env=env,
        path_env=path_env,
        python_executable=python_executable,
        project_dir=Path.cwd() if project_dir is None else project_dir,
    )
    if output_format == "json":
        print(format_doctor_report_json(report))
    else:
        print_doctor_report(report)
    return report.exit_code(strict=strict)


def run_doctor_checks(
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    path_env: str | None = None,
    python_executable: str | None = None,
    project_dir: Path | None = None,
) -> DoctorReport:
    """Collect read-only diagnostics for the current Python environment."""
    resolved_home = Path.home() if home is None else home
    resolved_env = os.environ if env is None else env
    resolved_path = resolved_env.get("PATH", "") if path_env is None else path_env
    resolved_python = sys.executable if python_executable is None else python_executable

    findings = [
        *_check_shell_profiles(resolved_home),
        *_check_active_environment(resolved_env),
        *_check_path(
            env=resolved_env,
            path_env=resolved_path,
            python_executable=resolved_python,
        ),
        *(_check_project_local_dependencies(project_dir) if project_dir else []),
    ]
    return DoctorReport(tuple(findings))


def format_doctor_report(report: DoctorReport) -> str:
    """Format a doctor report for terminal output."""
    lines = ["unidep doctor", ""]
    if not report.findings:
        lines.append("No environment issues found.")
        return "\n".join(lines)

    for finding in report.findings:
        lines.extend(
            [
                f"{finding.level.upper()}: {finding.title}",
                f"  Code: {finding.code}",
                f"  Details: {finding.details}",
                f"  Recommendation: {finding.recommendation}",
                "",
            ],
        )
    return "\n".join(lines).rstrip()


def format_doctor_report_json(report: DoctorReport) -> str:
    """Format a doctor report as JSON."""
    payload = {
        "findings": [asdict(finding) for finding in report.findings],
        "summary": report.summary(),
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def print_doctor_report(report: DoctorReport) -> None:
    """Print a doctor report, using rich when it is available."""
    if importlib.util.find_spec("rich") is None:
        print(format_doctor_report(report))
        return
    _print_doctor_report_with_rich(report)


def _print_doctor_report_with_rich(report: DoctorReport) -> None:
    """Print a doctor report with rich styling."""
    console_module = importlib.import_module("rich.console")
    text_module = importlib.import_module("rich.text")

    console = console_module.Console()
    text = text_module.Text()
    text.append("unidep doctor", style="bold")
    text.append("\n\n")

    if not report.findings:
        text.append("No environment issues found.", style="green")
    else:
        for index, finding in enumerate(report.findings):
            if index:
                text.append("\n\n")
            text.append(finding.level.upper(), style=_finding_level_style(finding))
            text.append(f": {finding.title}\n")
            text.append("  Code:", style="bold cyan")
            text.append(f" {finding.code}\n")
            text.append("  Details:", style="bold")
            _append_rich_details(text, finding)
            text.append("  Recommendation:", style="bold green")
            text.append(f" {finding.recommendation}")
    console.print(text)


def _append_rich_details(text: Any, finding: DoctorFinding) -> None:
    text.append(" ")
    if not finding.code.startswith("shadowed-"):
        text.append(f"{finding.details}\n")
        return

    position = 0
    for start, end in _shadowed_version_spans(finding.details):
        if start > position:
            text.append(finding.details[position:start])
        text.append(finding.details[start:end], style=SHADOWED_VERSION_STYLE)
        position = end
    text.append(f"{finding.details[position:]}\n")


def _shadowed_version_spans(details: str) -> list[tuple[int, int]]:
    spans = []
    position = 0
    while True:
        open_index = details.find(" (", position)
        if open_index == -1:
            break
        start = open_index + 1
        end = _matching_closing_parenthesis(details, start)
        if end is None:
            position = start + 1
            continue
        version_text = details[start + 1 : end]
        if _looks_like_tool_version(version_text):
            spans.append((start, end + 1))
        position = end + 1
    return spans


def _matching_closing_parenthesis(text: str, start: int) -> int | None:
    depth = 0
    for index, character in enumerate(text[start:], start=start):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _looks_like_tool_version(text: str) -> bool:
    normalized = text.casefold()
    return any(
        normalized.startswith(f"{executable} ") for executable in SHADOWED_EXECUTABLES
    )


def _finding_level_style(finding: DoctorFinding) -> str:
    if finding.level == "warning":
        return "bold yellow"
    if finding.level == "error":
        return "bold red"
    return "bold cyan"


def _check_shell_profiles(home: Path) -> list[DoctorFinding]:
    initializers, read_errors = _find_conda_initializers(home)
    distributions = {initializer.distribution for initializer in initializers}
    findings = []
    if len(distributions) > 1 and not _all_initializers_share_one_root(initializers):
        details = "; ".join(
            initializer.format_location(home) for initializer in initializers
        )
        findings.append(
            DoctorFinding(
                code="multiple-conda-initializers",
                level="warning",
                title="Multiple Conda-like initializers were found in shell profiles.",
                details=details,
                recommendation=(
                    "Keep one Conda, Mamba, or Micromamba initializer in your shell "
                    "startup files and remove stale initialization blocks."
                ),
            ),
        )
    initializer_roots = {
        initializer.normalized_root
        for initializer in initializers
        if initializer.normalized_root is not None
    }
    if len(initializer_roots) > 1:
        details = "; ".join(
            initializer.format_location(home, include_root=True)
            for initializer in initializers
            if initializer.normalized_root is not None
        )
        findings.append(
            DoctorFinding(
                code="multiple-conda-initializer-roots",
                level="warning",
                title=(
                    "Multiple Conda-like installation roots were found in shell "
                    "profiles."
                ),
                details=details,
                recommendation=(
                    "Keep shell startup initialization for only the Conda, Mamba, "
                    "or Micromamba installation you intend to use and remove stale "
                    "initialization lines."
                ),
            ),
        )
    findings.extend(
        DoctorFinding(
            code="unreadable-shell-profile",
            level="warning",
            title="A shell profile could not be read.",
            details=read_error.format_location(home),
            recommendation=(
                "Check the file permissions, or inspect that profile manually "
                "for Conda, Mamba, or Micromamba initialization blocks."
            ),
        )
        for read_error in read_errors
    )
    return findings


def _find_conda_initializers(
    home: Path,
) -> tuple[list[_CondaInitializer], list[_ShellProfileReadError]]:
    initializers: list[_CondaInitializer] = []
    read_errors: list[_ShellProfileReadError] = []
    for profile_name in SHELL_PROFILE_FILES:
        profile = home / profile_name
        if not profile.is_file():
            continue
        try:
            content = profile.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            read_errors.append(_ShellProfileReadError(profile=profile, error=error))
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            roots = _line_conda_roots(stripped)
            if roots:
                fallback_distributions = _line_conda_distributions(stripped)
                for root in roots:
                    initializer = _conda_root_initializer(
                        root=root,
                        fallback_distributions=fallback_distributions,
                        home=home,
                        profile=profile,
                        line_number=line_number,
                    )
                    if initializer is not None:
                        initializers.append(initializer)
                continue
            distributions = _line_conda_distributions(stripped)
            initializers.extend(
                _CondaInitializer(
                    distribution=distribution,
                    profile=profile,
                    line_number=line_number,
                )
                for distribution in distributions
            )
    return initializers, read_errors


def _line_conda_distributions(line: str) -> list[str]:
    lowered = line.lower()
    if "conda" not in lowered and "mamba" not in lowered:
        return []
    distributions = [
        distribution
        for distribution, markers in CONDA_DISTRIBUTIONS.items()
        if any(marker in lowered for marker in markers)
    ]
    if "mamba" in distributions and len(distributions) > 1:
        distributions = [
            distribution for distribution in distributions if distribution != "mamba"
        ]
    return distributions


def _line_conda_roots(line: str) -> list[str]:
    return [
        match.group("root").rstrip("/")
        for match in CONDA_ROOT_PATTERN.finditer(line)
        if _is_conda_root_match(
            match.group("root"),
            match.group("terminator"),
        )
    ]


def _is_conda_root_match(root: str, terminator: str) -> bool:
    if _terminator_is_explicit_conda(terminator):
        return True
    return _root_looks_conda_like(root)


def _terminator_is_explicit_conda(terminator: str) -> bool:
    normalized = terminator.replace("\\", "/").casefold()
    return (
        normalized == "/condabin"
        or normalized.endswith("/etc/profile.d/conda.sh")
        or bool(
            re.search(r"/bin/(?:conda|mamba|micromamba)\b", normalized),
        )
    )


def _root_looks_conda_like(root: str) -> bool:
    parts = _conda_root_parts(root)
    return bool(parts) and _root_part_looks_conda_like(parts[-1])


def _root_part_looks_conda_like(part: str) -> bool:
    if part in CONDA_ROOT_NAMES:
        return True
    return any(
        part.startswith(f"{root_name}{separator}")
        for root_name in SUFFIXED_CONDA_ROOT_NAMES
        for separator in CONDA_ROOT_SUFFIX_SEPARATORS
    )


def _conda_root_parts(root: str) -> list[str]:
    return [part.casefold() for part in re.split(r"[/\\]+", root.rstrip("/\\")) if part]


def _conda_root_initializer(
    *,
    root: str,
    fallback_distributions: list[str],
    home: Path,
    profile: Path,
    line_number: int,
) -> _CondaInitializer | None:
    distribution = _conda_root_distribution(root)
    if distribution is None:
        distribution = fallback_distributions[0] if fallback_distributions else "conda"
    return _CondaInitializer(
        distribution=distribution,
        profile=profile,
        line_number=line_number,
        root=root,
        normalized_root=_normalize_conda_root(root, home),
    )


def _all_initializers_share_one_root(initializers: list[_CondaInitializer]) -> bool:
    roots = {
        initializer.normalized_root
        for initializer in initializers
        if initializer.normalized_root is not None
    }
    return len(roots) == 1 and all(
        initializer.normalized_root is not None for initializer in initializers
    )


def _conda_root_distribution(root: str) -> str | None:
    distributions = _line_conda_distributions(root)
    if distributions:
        return distributions[0]
    if "conda" in _conda_root_parts(root):
        return "conda"
    return None


def _normalize_conda_root(root: str, home: Path) -> str:
    expanded_root = root
    home_string = os.fspath(home.expanduser().absolute())
    for marker in ("$HOME", "${HOME}", "~"):
        if expanded_root == marker or expanded_root.startswith(f"{marker}/"):
            expanded_root = f"{home_string}{expanded_root[len(marker) :]}"
            break
    return os.path.normcase(os.path.normpath(expanded_root))


def _check_active_environment(env: Mapping[str, str]) -> list[DoctorFinding]:
    findings = []
    conda_shlvl = env.get("CONDA_SHLVL")
    if _conda_shlvl_is_stacked(conda_shlvl):
        prefixes = _active_conda_prefixes(env)
        findings.append(
            DoctorFinding(
                code="stacked-conda-envs",
                level="warning",
                title="Multiple Conda environments appear to be stacked.",
                details=f"CONDA_SHLVL={conda_shlvl}; prefixes: {', '.join(prefixes)}",
                recommendation=(
                    "Run `conda deactivate` or `micromamba deactivate` until only "
                    "the intended environment is active."
                ),
            ),
        )

    active_markers = _active_python_environment_markers(env)
    if len(active_markers) > 1:
        findings.append(
            DoctorFinding(
                code="mixed-active-python-envs",
                level="warning",
                title="Multiple Python environment managers appear active.",
                details="; ".join(active_markers),
                recommendation=(
                    "Activate only the environment manager you intend to use before "
                    "running `unidep install`."
                ),
            ),
        )
    return findings


def _active_python_environment_markers(env: Mapping[str, str]) -> list[str]:
    active_markers = []
    if env.get("CONDA_PREFIX"):
        active_markers.append(f"CONDA_PREFIX={env['CONDA_PREFIX']}")

    if env.get("VIRTUAL_ENV"):
        wrapper_markers = [
            f"{name}={env[name]}"
            for name in VIRTUALENV_WRAPPER_MARKERS
            if env.get(name)
        ]
        marker = f"VIRTUAL_ENV={env['VIRTUAL_ENV']}"
        if wrapper_markers:
            marker = f"{marker} ({', '.join(wrapper_markers)})"
        active_markers.append(marker)

    for name in PYTHON_ENVIRONMENT_VARIABLES:
        if (
            name in ("CONDA_PREFIX", "VIRTUAL_ENV")
            or name in VIRTUALENV_WRAPPER_MARKERS
        ):
            continue
        if env.get(name):
            active_markers.append(f"{name}={env[name]}")

    if not env.get("VIRTUAL_ENV"):
        active_markers.extend(
            f"{name}={env[name]}"
            for name in VIRTUALENV_WRAPPER_MARKERS
            if env.get(name)
        )
    return active_markers


def _conda_shlvl_is_stacked(conda_shlvl: str | None) -> bool:
    if conda_shlvl is None:
        return False
    try:
        return int(conda_shlvl) > 1
    except ValueError:
        return False


def _active_conda_prefixes(env: Mapping[str, str]) -> list[str]:
    prefixes = []
    if env.get("CONDA_PREFIX"):
        prefixes.append(env["CONDA_PREFIX"])
    prefixes.extend(
        env[name]
        for name in sorted(env)
        if name.startswith("CONDA_PREFIX_") and env[name]
    )
    return prefixes


def _check_path(
    *,
    env: Mapping[str, str],
    path_env: str,
    python_executable: str,
) -> list[DoctorFinding]:
    findings = []
    python_path = Path(python_executable)
    findings.extend(_check_active_env_python_mismatch(env, python_path))
    findings.extend(
        _check_path_python_mismatch(
            path_env=path_env,
            path_extensions=env.get("PATHEXT"),
            python_executable=python_path,
        ),
    )

    if env.get("CONDA_PREFIX") and _is_homebrew_python(python_path):
        findings.append(
            DoctorFinding(
                code="homebrew-python-in-conda-env",
                level="warning",
                title="Homebrew Python is active inside a Conda environment.",
                details=f"python executable: {python_executable}",
                recommendation=(
                    "Use the Python executable from the active Conda environment, "
                    "or deactivate Conda before using Homebrew Python."
                ),
            ),
        )

    for executable in SHADOWED_EXECUTABLES:
        matches = _which_all(executable, path_env, path_extensions=env.get("PATHEXT"))
        versions = [_probe_executable_version(match) for match in matches]
        findings.extend(_version_probe_findings(executable, versions))
        if len(matches) > 1 and not _first_match_is_expected_pip(
            executable,
            matches[0],
            python_path,
        ):
            findings.append(
                DoctorFinding(
                    code=f"shadowed-{executable}",
                    level="info",
                    title=f"Multiple `{executable}` executables are on PATH.",
                    details=", ".join(version.format_details() for version in versions),
                    recommendation=(
                        "Check PATH ordering if this command resolves to an "
                        "unexpected environment."
                    ),
                ),
            )
    return findings


def _check_project_local_dependencies(project_dir: Path) -> list[DoctorFinding]:
    findings = []
    project_path = project_dir.resolve()
    gitmodule_paths = _gitmodule_paths(project_path)
    yaml = YAML(typ="safe")
    for requirements_file in find_requirements_files(project_path, depth=1):
        try:
            data = _load(requirements_file, yaml)
            local_dependencies = get_local_dependencies(data)
        except (OSError, KeyError, TypeError, ValueError) as error:
            findings.append(
                DoctorFinding(
                    code="project-local-dependency-scan-failed",
                    level="warning",
                    title="A UniDep dependency file could not be checked.",
                    details=f"{requirements_file}: {error}",
                    recommendation=(
                        "Inspect the dependency file manually or run the relevant "
                        "`unidep` command for the full parser error."
                    ),
                ),
            )
            continue

        for local_dependency in local_dependencies:
            if local_dependency.use != "local":
                continue
            local_path, _extras = split_path_and_extras(local_dependency.local)
            dependency_path = (requirements_file.parent / local_path).resolve()
            if not _local_dependency_is_uninitialized_submodule(
                dependency_path,
                gitmodule_paths,
            ):
                continue
            findings.append(
                DoctorFinding(
                    code="uninitialized-local-git-submodule",
                    level="error",
                    title=(
                        "A local dependency appears to be an uninitialized "
                        "Git submodule."
                    ),
                    details=(
                        f"{requirements_file}: {local_dependency.local} -> "
                        f"{dependency_path}"
                    ),
                    recommendation=(
                        "Fetch the submodule with "
                        "`git submodule update --init --recursive`, then rerun "
                        "`unidep doctor`."
                    ),
                ),
            )
    return findings


def _gitmodule_paths(project_dir: Path) -> set[Path]:
    gitmodules = project_dir / ".gitmodules"
    if not gitmodules.is_file():
        return set()
    paths: set[Path] = set()
    try:
        lines = gitmodules.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return paths
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.strip() == "path":
            paths.add((project_dir / value.strip()).resolve())
    return paths


def _local_dependency_is_uninitialized_submodule(
    dependency_path: Path,
    gitmodule_paths: set[Path],
) -> bool:
    is_registered_submodule = dependency_path in gitmodule_paths
    if not dependency_path.exists():
        return is_registered_submodule
    if not dependency_path.is_dir():
        return False
    if _is_empty_git_submodule_checkout(dependency_path):
        return True
    return is_registered_submodule and not any(dependency_path.iterdir())


def _is_empty_git_submodule_checkout(path: Path) -> bool:
    git_file = path / ".git"
    return git_file.is_file() and len(list(path.iterdir())) == 1


def _first_match_is_expected_pip(
    executable: str,
    match: Path,
    python_executable: Path,
) -> bool:
    return executable in {"pip", "pip3"} and _same_path(
        match.parent,
        python_executable.parent,
    )


def _probe_executable_version(path: Path) -> _ExecutableVersion:
    try:
        completed = subprocess.run(
            [os.fspath(path), "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=VERSION_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return _ExecutableVersion(
            path=path,
            error=f"timed out after {VERSION_PROBE_TIMEOUT_SECONDS} seconds",
        )
    except OSError as error:
        return _ExecutableVersion(path=path, error=str(error))

    output = _first_nonempty_line(completed.stdout) or _first_nonempty_line(
        completed.stderr,
    )
    if completed.returncode:
        details = output or "no version output"
        return _ExecutableVersion(
            path=path,
            error=f"exit code {completed.returncode}: {details}",
        )
    return _ExecutableVersion(path=path, version=output)


def _first_nonempty_line(output: str | None) -> str | None:
    if not output:
        return None
    return next((line.strip() for line in output.splitlines() if line.strip()), None)


def _version_probe_findings(
    executable: str,
    versions: list[_ExecutableVersion],
) -> list[DoctorFinding]:
    return [
        DoctorFinding(
            code=f"{executable}-version-probe-failed",
            level="warning",
            title=f"Could not read `{executable}` version.",
            details=f"{version.path}: {version.error}",
            recommendation=(
                f"Run `{version.path} --version` manually and remove or fix this "
                "executable if it is stale or broken."
            ),
        )
        for version in versions
        if version.error
    ]


def _check_active_env_python_mismatch(
    env: Mapping[str, str],
    python_executable: Path,
) -> list[DoctorFinding]:
    findings = []
    for env_var, code, title in (
        (
            "CONDA_PREFIX",
            "conda-prefix-python-mismatch",
            "Python executable is outside the active Conda environment.",
        ),
        (
            "VIRTUAL_ENV",
            "virtual-env-python-mismatch",
            "Python executable is outside the active virtual environment.",
        ),
    ):
        prefix = env.get(env_var)
        if prefix and not _path_is_inside(python_executable, Path(prefix)):
            findings.append(
                DoctorFinding(
                    code=code,
                    level="warning",
                    title=title,
                    details=(
                        f"{env_var}={prefix}; python executable: {python_executable}"
                    ),
                    recommendation=(
                        "Run UniDep with the Python executable from the active "
                        "environment, or reactivate the intended environment."
                    ),
                ),
            )
    return findings


def _check_path_python_mismatch(
    *,
    path_env: str,
    path_extensions: str | None,
    python_executable: Path,
) -> list[DoctorFinding]:
    findings = []
    for executable in ("python", "python3"):
        matches = _which_all(
            executable,
            path_env,
            path_extensions=path_extensions,
        )
        if matches and not _same_python_environment(matches[0], python_executable):
            findings.append(
                DoctorFinding(
                    code=f"path-{executable}-mismatch",
                    level="warning",
                    title=(
                        f"`{executable}` on PATH differs from the running Python "
                        "executable."
                    ),
                    details=(
                        f"{executable} on PATH: {matches[0]}; "
                        f"running Python: {python_executable}"
                    ),
                    recommendation=(
                        "Check PATH ordering and run UniDep with the Python "
                        "executable from the environment you intend to modify."
                    ),
                ),
            )
    return findings


def _is_homebrew_python(path: Path) -> bool:
    normalized = path.resolve(strict=False).as_posix().lower()
    return "/homebrew/" in normalized or "/cellar/python" in normalized


def _path_is_inside(path: Path, prefix: Path) -> bool:
    try:
        path_string = os.path.normcase(os.fspath(path.expanduser().absolute()))
        prefix_string = os.path.normcase(os.fspath(prefix.expanduser().absolute()))
        return os.path.commonpath([path_string, prefix_string]) == prefix_string
    except ValueError:  # pragma: no cover
        return False


def _same_path(first: Path, second: Path) -> bool:
    first_path = os.path.normcase(os.fspath(first.expanduser().absolute()))
    second_path = os.path.normcase(os.fspath(second.expanduser().absolute()))
    return first_path == second_path


def _same_python_environment(first: Path, second: Path) -> bool:
    if _same_path(first, second):
        return True
    if not (
        _normalized_executable_name(first).startswith("python")
        and _normalized_executable_name(second).startswith("python")
    ):
        return False
    return _same_path(first.parent, second.parent)


def _normalized_executable_name(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".exe"):
        return name[:-4]
    return name


def _which_all(
    executable: str,
    path_env: str,
    *,
    path_extensions: str | None = None,
) -> list[Path]:
    matches = []
    seen = set()
    for entry in path_env.split(os.pathsep):
        if not entry:
            continue
        for candidate in _executable_candidates(
            Path(entry),
            executable,
            path_extensions,
        ):
            if not candidate.is_file() or not os.access(candidate, os.X_OK):
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            matches.append(candidate)
    return matches


def _executable_candidates(
    directory: Path,
    executable: str,
    path_extensions: str | None,
) -> list[Path]:
    candidates = [directory / executable]
    if Path(executable).suffix or not path_extensions:
        return candidates

    extensions = [
        extension.lower()
        for extension in path_extensions.split(";")
        if extension.startswith(".")
    ]
    candidates.extend(
        directory / f"{executable}{extension}" for extension in extensions
    )
    candidates.extend(
        directory / f"{executable}{extension.upper()}" for extension in extensions
    )
    return candidates
