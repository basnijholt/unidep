"""Read-only diagnostics for common Python environment problems."""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

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
SHADOWED_EXECUTABLES = ("python", "python3", "pip", "pip3", "conda", "mamba")


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


@dataclass(frozen=True)
class _CondaInitializer:
    distribution: str
    profile: Path
    line_number: int

    def format_location(self, home: Path) -> str:
        try:
            profile = self.profile.relative_to(home)
        except ValueError:  # pragma: no cover
            profile = self.profile
        return f"{profile}:{self.line_number} ({self.distribution})"


def run_doctor_command(
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    path_env: str | None = None,
    python_executable: str | None = None,
) -> int:
    """Run doctor diagnostics, print a report, and return an exit code."""
    report = run_doctor_checks(
        home=home,
        env=env,
        path_env=path_env,
        python_executable=python_executable,
    )
    print_doctor_report(report)
    return 0


def run_doctor_checks(
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    path_env: str | None = None,
    python_executable: str | None = None,
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
            text.append(f" {finding.details}\n")
            text.append("  Recommendation:", style="bold green")
            text.append(f" {finding.recommendation}")
    console.print(text)


def _finding_level_style(finding: DoctorFinding) -> str:
    if finding.level == "warning":
        return "bold yellow"
    if finding.level == "error":
        return "bold red"
    return "bold cyan"


def _check_shell_profiles(home: Path) -> list[DoctorFinding]:
    initializers = _find_conda_initializers(home)
    distributions = {initializer.distribution for initializer in initializers}
    if len(distributions) <= 1:
        return []

    details = "; ".join(
        initializer.format_location(home) for initializer in initializers
    )
    return [
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
    ]


def _find_conda_initializers(home: Path) -> list[_CondaInitializer]:
    initializers: list[_CondaInitializer] = []
    for profile_name in SHELL_PROFILE_FILES:
        profile = home / profile_name
        if not profile.is_file():
            continue
        content = profile.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
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
    return initializers


def _line_conda_distributions(line: str) -> list[str]:
    lowered = line.lower()
    if "conda" not in lowered and "mamba" not in lowered:
        return []
    distributions = [
        distribution
        for distribution, markers in CONDA_DISTRIBUTIONS.items()
        if any(marker in lowered for marker in markers)
    ]
    if "micromamba" in distributions or "mambaforge" in distributions:
        distributions = [
            distribution for distribution in distributions if distribution != "mamba"
        ]
    return distributions


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
    if env.get("CONDA_PREFIX") and _is_homebrew_python(Path(python_executable)):
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
        if len(matches) > 1:
            findings.append(
                DoctorFinding(
                    code=f"shadowed-{executable}",
                    level="info",
                    title=f"Multiple `{executable}` executables are on PATH.",
                    details=", ".join(str(match) for match in matches),
                    recommendation=(
                        "Check PATH ordering if this command resolves to an "
                        "unexpected environment."
                    ),
                ),
            )
    return findings


def _is_homebrew_python(path: Path) -> bool:
    normalized = path.resolve(strict=False).as_posix().lower()
    return "/homebrew/" in normalized or "/cellar/python" in normalized


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
