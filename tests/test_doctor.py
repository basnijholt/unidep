"""Tests for the ``unidep doctor`` diagnostics."""

from __future__ import annotations

import json
import os
import sys
import textwrap
from typing import TYPE_CHECKING

from unidep._doctor import (
    DoctorFinding,
    DoctorReport,
    format_doctor_report,
    print_doctor_report,
    run_doctor_checks,
    run_doctor_command,
)

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    import pytest


def _make_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)


def _disable_rich(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "unidep._doctor.importlib.util.find_spec",
        lambda _name: None,
    )


RICH_MODULES = ("rich", "rich.console", "rich.table", "rich.text")


def _install_fake_rich(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, ModuleType]:
    saved_modules = {
        name: sys.modules[name] for name in RICH_MODULES if name in sys.modules
    }
    rich_dir = tmp_path / "rich"
    rich_dir.mkdir()
    (rich_dir / "__init__.py").write_text("")
    (rich_dir / "console.py").write_text(
        textwrap.dedent(
            """\
            class Console:
                def print(self, value):
                    print(f"RICH:{value}\\nSTYLES:{getattr(value, 'styles', [])}")
            """,
        ),
    )
    (rich_dir / "text.py").write_text(
        textwrap.dedent(
            """\
            class Text:
                def __init__(self):
                    self.parts = []
                    self.styles = []

                def append(self, value, *, style=None):
                    self.parts.append(value)
                    if style is not None:
                        self.styles.append(style)

                def __str__(self):
                    return "".join(self.parts)
            """,
        ),
    )
    (rich_dir / "table.py").write_text(
        textwrap.dedent(
            """\
            class Table:
                def __init__(self, *, show_header, title=None):
                    self.show_header = show_header
                    self.title = title
                    self.columns = []
                    self.rows = []

                def add_column(self, name, *, style=None):
                    self.columns.append((name, style))

                def add_row(self, *values):
                    self.rows.append(values)

                def __str__(self):
                    return f"TABLE:{self.title}|{self.columns}|{self.rows}"
            """,
        ),
    )

    monkeypatch.syspath_prepend(str(tmp_path))
    for name in RICH_MODULES:
        sys.modules.pop(name, None)
    return saved_modules


def _restore_rich_modules(saved_modules: dict[str, ModuleType]) -> None:
    for name in RICH_MODULES:
        sys.modules.pop(name, None)
    sys.modules.update(saved_modules)


def test_shell_profile_scan_reports_multiple_conda_initializers(
    tmp_path: Path,
) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text(
        'source "$HOME/miniconda3/etc/profile.d/conda.sh"\n'
        'eval "$(/opt/micromamba/bin/micromamba shell hook -s zsh)"',
    )

    report = run_doctor_checks(home=tmp_path, env={}, path_env="")

    finding = report.findings[0]
    assert finding.code == "multiple-conda-initializers"
    assert finding.level == "warning"
    assert "miniconda" in finding.details
    assert "micromamba" in finding.details
    assert ".zshrc:1" in finding.details
    assert ".zshrc:2" in finding.details


def test_shell_profile_scan_ignores_commented_conda_initializers(
    tmp_path: Path,
) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text(
        '# source "$HOME/miniconda3/etc/profile.d/conda.sh"\n'
        'eval "$(/opt/micromamba/bin/micromamba shell hook -s zsh)"',
    )

    report = run_doctor_checks(home=tmp_path, env={}, path_env="")

    assert report.finding_by_code("multiple-conda-initializers") is None


def test_shell_profile_scan_treats_mamba_hook_inside_conda_as_one_initializer(
    tmp_path: Path,
) -> None:
    zshrc = tmp_path / ".zshrc"
    for distribution in ("anaconda3", "miniconda3", "miniforge3"):
        zshrc.write_text(f'eval "$(/opt/{distribution}/bin/mamba shell hook -s zsh)"\n')

        report = run_doctor_checks(home=tmp_path, env={}, path_env="")

        assert report.finding_by_code("multiple-conda-initializers") is None


def test_shell_profile_scan_reports_unreadable_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text('source "$HOME/miniconda3/etc/profile.d/conda.sh"\n')
    original_read_text = type(zshrc).read_text

    def read_text(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path == zshrc:
            error_message = "permission denied"
            raise OSError(error_message)
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(type(zshrc), "read_text", read_text)

    report = run_doctor_checks(home=tmp_path, env={}, path_env="")

    finding = report.finding_by_code("unreadable-shell-profile")
    assert finding is not None
    assert finding.level == "warning"
    assert ".zshrc" in finding.details
    assert "permission denied" in finding.details


def test_environment_scan_reports_stacked_conda_envs(tmp_path: Path) -> None:
    report = run_doctor_checks(
        home=tmp_path,
        env={
            "CONDA_PREFIX": "/opt/miniconda3/envs/analysis",
            "CONDA_PREFIX_1": "/opt/miniconda3",
            "CONDA_SHLVL": "2",
        },
        path_env="",
    )

    finding = report.findings[0]
    assert finding.code == "stacked-conda-envs"
    assert finding.level == "warning"
    assert "CONDA_SHLVL=2" in finding.details
    assert "/opt/miniconda3/envs/analysis" in finding.details
    assert "/opt/miniconda3" in finding.details


def test_environment_scan_reports_mixed_active_environment_managers(
    tmp_path: Path,
) -> None:
    prefix = tmp_path / "envs" / "analysis"
    python = prefix / "bin" / "python"
    _make_executable(python)

    report = run_doctor_checks(
        home=tmp_path,
        env={
            "CONDA_PREFIX": str(prefix),
            "VIRTUAL_ENV": str(prefix),
            "PYENV_VERSION": "3.12.3",
            "POETRY_ACTIVE": "1",
        },
        path_env="",
        python_executable=str(python),
    )

    assert [finding.code for finding in report.findings] == [
        "mixed-active-python-envs",
    ]
    assert "CONDA_PREFIX" in report.findings[0].details
    assert "VIRTUAL_ENV" in report.findings[0].details
    assert "PYENV_VERSION" in report.findings[0].details
    assert "POETRY_ACTIVE" in report.findings[0].details


def test_environment_scan_coalesces_virtualenv_wrapper_markers(
    tmp_path: Path,
) -> None:
    report = run_doctor_checks(
        home=tmp_path,
        env={
            "VIRTUAL_ENV": "/work/project/.venv",
            "POETRY_ACTIVE": "1",
            "PIPENV_ACTIVE": "1",
        },
        path_env="",
    )

    assert report.finding_by_code("mixed-active-python-envs") is None


def test_path_scan_reports_homebrew_python_inside_conda_env(tmp_path: Path) -> None:
    homebrew_bin = tmp_path / "opt" / "homebrew" / "bin"
    python = homebrew_bin / "python"
    _make_executable(python)

    report = run_doctor_checks(
        home=tmp_path,
        env={"CONDA_PREFIX": "/opt/miniconda3/envs/analysis"},
        path_env=str(homebrew_bin),
        python_executable=str(python),
    )

    finding = report.finding_by_code("homebrew-python-in-conda-env")
    assert finding is not None
    assert finding.level == "warning"
    assert str(python) in finding.details


def test_path_scan_resolves_homebrew_python_symlinks_inside_conda_env(
    tmp_path: Path,
) -> None:
    usr_local_bin = tmp_path / "usr" / "local" / "bin"
    cellar_bin = tmp_path / "usr" / "local" / "Cellar" / "python@3.12" / "bin"
    python = cellar_bin / "python3"
    _make_executable(python)
    usr_local_bin.mkdir(parents=True)
    python_link = usr_local_bin / "python3"
    python_link.symlink_to(python)

    report = run_doctor_checks(
        home=tmp_path,
        env={"CONDA_PREFIX": "/opt/miniconda3/envs/analysis"},
        path_env=str(usr_local_bin),
        python_executable=str(python_link),
    )

    finding = report.finding_by_code("homebrew-python-in-conda-env")
    assert finding is not None
    assert str(python_link) in finding.details


def test_path_scan_reports_conda_prefix_python_mismatch(tmp_path: Path) -> None:
    conda_prefix = tmp_path / "envs" / "analysis"
    python = tmp_path / "outside" / "bin" / "python"
    _make_executable(python)

    report = run_doctor_checks(
        home=tmp_path,
        env={"CONDA_PREFIX": str(conda_prefix)},
        path_env="",
        python_executable=str(python),
    )

    finding = report.finding_by_code("conda-prefix-python-mismatch")
    assert finding is not None
    assert finding.level == "warning"
    assert str(conda_prefix) in finding.details
    assert str(python) in finding.details


def test_path_scan_reports_virtualenv_python_mismatch(tmp_path: Path) -> None:
    virtual_env = tmp_path / ".venv"
    python = tmp_path / "outside" / "bin" / "python"
    _make_executable(python)

    report = run_doctor_checks(
        home=tmp_path,
        env={"VIRTUAL_ENV": str(virtual_env)},
        path_env="",
        python_executable=str(python),
    )

    finding = report.finding_by_code("virtual-env-python-mismatch")
    assert finding is not None
    assert finding.level == "warning"
    assert str(virtual_env) in finding.details
    assert str(python) in finding.details


def test_path_scan_skips_prefix_mismatch_when_python_is_inside_env(
    tmp_path: Path,
) -> None:
    conda_prefix = tmp_path / "envs" / "analysis"
    python = conda_prefix / "bin" / "python"
    _make_executable(python)

    report = run_doctor_checks(
        home=tmp_path,
        env={"CONDA_PREFIX": str(conda_prefix), "VIRTUAL_ENV": str(conda_prefix)},
        path_env="",
        python_executable=str(python),
    )

    assert report.finding_by_code("conda-prefix-python-mismatch") is None
    assert report.finding_by_code("virtual-env-python-mismatch") is None


def test_path_scan_reports_path_python_mismatch(tmp_path: Path) -> None:
    running_python = tmp_path / "env" / "bin" / "python"
    path_python = tmp_path / "system" / "bin" / "python"
    _make_executable(running_python)
    _make_executable(path_python)

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env=str(path_python.parent),
        python_executable=str(running_python),
    )

    finding = report.finding_by_code("path-python-mismatch")
    assert finding is not None
    assert finding.level == "warning"
    assert str(path_python) in finding.details
    assert str(running_python) in finding.details


def test_path_scan_reports_path_python3_mismatch(tmp_path: Path) -> None:
    running_python = tmp_path / "env" / "bin" / "python"
    path_python3 = tmp_path / "system" / "bin" / "python3"
    _make_executable(running_python)
    _make_executable(path_python3)

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env=str(path_python3.parent),
        python_executable=str(running_python),
    )

    finding = report.finding_by_code("path-python3-mismatch")
    assert finding is not None
    assert finding.level == "warning"
    assert str(path_python3) in finding.details
    assert str(running_python) in finding.details


def test_path_scan_reports_python_shadowing(tmp_path: Path) -> None:
    first_bin = tmp_path / "first" / "bin"
    second_bin = tmp_path / "second" / "bin"
    _make_executable(first_bin / "python")
    _make_executable(second_bin / "python")

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env=f"{first_bin}{os.pathsep}{second_bin}",
        python_executable=str(first_bin / "python"),
    )

    finding = report.finding_by_code("shadowed-python")
    assert finding is not None
    assert finding.level == "info"
    assert str(first_bin / "python") in finding.details
    assert str(second_bin / "python") in finding.details


def test_path_scan_reports_micromamba_shadowing(tmp_path: Path) -> None:
    first_bin = tmp_path / "first" / "bin"
    second_bin = tmp_path / "second" / "bin"
    _make_executable(first_bin / "micromamba")
    _make_executable(second_bin / "micromamba")

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env=f"{first_bin}{os.pathsep}{second_bin}",
        python_executable=sys.executable,
    )

    finding = report.finding_by_code("shadowed-micromamba")
    assert finding is not None
    assert finding.level == "info"
    assert str(first_bin / "micromamba") in finding.details
    assert str(second_bin / "micromamba") in finding.details


def test_path_scan_honors_pathext_for_shadowing(
    tmp_path: Path,
) -> None:
    first_bin = tmp_path / "first" / "bin"
    second_bin = tmp_path / "second" / "bin"
    _make_executable(first_bin / "python.exe")
    _make_executable(second_bin / "python.EXE")

    report = run_doctor_checks(
        home=tmp_path,
        env={"PATHEXT": ".COM;.EXE;.BAT"},
        path_env=f"{first_bin}{os.pathsep}{second_bin}",
        python_executable=str(first_bin / "python.exe"),
    )

    finding = report.finding_by_code("shadowed-python")
    assert finding is not None
    details = finding.details.casefold()
    assert str(first_bin / "python.exe").casefold() in details
    assert str(second_bin / "python.EXE").casefold() in details


def test_run_doctor_command_prints_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    _disable_rich(monkeypatch)

    exit_code = run_doctor_command(home=tmp_path, env={}, path_env="")

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "unidep doctor" in captured.out
    assert "No environment issues found" in captured.out


def test_run_doctor_command_prints_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    _disable_rich(monkeypatch)

    exit_code = run_doctor_command(
        home=tmp_path,
        env={
            "CONDA_PREFIX": "/opt/miniconda3/envs/analysis",
            "VIRTUAL_ENV": "/work/project/.venv",
        },
        path_env="",
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert (
        "WARNING: Multiple Python environment managers appear active." in captured.out
    )
    assert "Code: mixed-active-python-envs" in captured.out
    assert "Recommendation:" in captured.out


def test_run_doctor_command_can_print_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    prefix = tmp_path / "envs" / "analysis"
    python = prefix / "bin" / "python"
    _make_executable(python)

    exit_code = run_doctor_command(
        home=tmp_path,
        env={
            "CONDA_PREFIX": str(prefix),
            "VIRTUAL_ENV": str(prefix),
        },
        path_env="",
        python_executable=str(python),
        output_format="json",
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload == {
        "findings": [
            {
                "code": "mixed-active-python-envs",
                "level": "warning",
                "title": "Multiple Python environment managers appear active.",
                "details": f"CONDA_PREFIX={prefix}; VIRTUAL_ENV={prefix}",
                "recommendation": (
                    "Activate only the environment manager you intend to use before "
                    "running `unidep install`."
                ),
            },
        ],
        "summary": {"error": 0, "info": 0, "warning": 1},
    }


def test_run_doctor_command_strict_returns_nonzero_for_warnings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    _disable_rich(monkeypatch)

    exit_code = run_doctor_command(
        home=tmp_path,
        env={
            "CONDA_PREFIX": "/opt/miniconda3/envs/analysis",
            "VIRTUAL_ENV": "/work/project/.venv",
        },
        path_env="",
        strict=True,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert (
        "WARNING: Multiple Python environment managers appear active." in captured.out
    )


def test_run_doctor_command_uses_rich_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    env = {
        "CONDA_PREFIX": "/opt/miniconda3/envs/analysis",
        "VIRTUAL_ENV": "/work/project/.venv",
    }
    expected_report = format_doctor_report(
        run_doctor_checks(home=tmp_path, env=env, path_env=""),
    )
    saved_modules = _install_fake_rich(tmp_path, monkeypatch)
    try:
        exit_code = run_doctor_command(
            home=tmp_path,
            env=env,
            path_env="",
        )

        captured = capsys.readouterr()
    finally:
        _restore_rich_modules(saved_modules)

    assert exit_code == 0
    assert captured.out.startswith(f"RICH:{expected_report}\nSTYLES:")
    assert (
        "WARNING: Multiple Python environment managers appear active." in captured.out
    )
    assert "Code: mixed-active-python-envs" in captured.out
    assert "mixed-active-python-envs" in captured.out
    assert "TABLE:" not in captured.out
    assert "bold yellow" in captured.out
    assert "bold cyan" in captured.out


def test_run_doctor_command_uses_rich_for_summary_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    expected_report = format_doctor_report(
        run_doctor_checks(home=tmp_path, env={}, path_env=""),
    )
    saved_modules = _install_fake_rich(tmp_path, monkeypatch)
    try:
        exit_code = run_doctor_command(home=tmp_path, env={}, path_env="")

        captured = capsys.readouterr()
    finally:
        _restore_rich_modules(saved_modules)

    assert exit_code == 0
    assert captured.out.startswith(f"RICH:{expected_report}\nSTYLES:")
    assert "No environment issues found" in captured.out


def test_rich_report_styles_multiple_finding_levels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    report = DoctorReport(
        (
            DoctorFinding(
                code="broken",
                level="error",
                title="An error finding.",
                details="first detail",
                recommendation="fix the error",
            ),
            DoctorFinding(
                code="shadowed-python",
                level="info",
                title="An info finding.",
                details="second detail",
                recommendation="check PATH",
            ),
        ),
    )
    expected_report = format_doctor_report(report)
    saved_modules = _install_fake_rich(tmp_path, monkeypatch)
    try:
        print_doctor_report(report)

        captured = capsys.readouterr()
    finally:
        _restore_rich_modules(saved_modules)

    assert captured.out.startswith(f"RICH:{expected_report}\nSTYLES:")
    assert "\n\nINFO: An info finding." in captured.out
    assert "bold red" in captured.out
    assert "bold cyan" in captured.out


def test_report_helpers_handle_missing_codes_and_findings(tmp_path: Path) -> None:
    report = run_doctor_checks(home=tmp_path, env={}, path_env="")

    assert report.finding_by_code("missing") is None


def test_report_exit_code_returns_nonzero_for_errors() -> None:
    report = DoctorReport(
        (
            DoctorFinding(
                code="broken",
                level="error",
                title="An error finding.",
                details="first detail",
                recommendation="fix the error",
            ),
        ),
    )

    assert report.exit_code() == 1


def test_ignores_unrelated_profile_lines_and_invalid_conda_shlvl(
    tmp_path: Path,
) -> None:
    (tmp_path / ".bashrc").write_text("export PATH=/usr/bin:$PATH\n")

    report = run_doctor_checks(
        home=tmp_path,
        env={"CONDA_SHLVL": "not-an-integer"},
        path_env="",
    )

    assert report.findings == ()


def test_path_scan_ignores_duplicate_resolved_executables(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    _make_executable(bin_dir / "python")

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env=f"{bin_dir}{os.pathsep}{bin_dir}",
        python_executable=str(bin_dir / "python"),
    )

    assert report.finding_by_code("shadowed-python") is None
