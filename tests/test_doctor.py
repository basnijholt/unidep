"""Tests for the ``unidep doctor`` diagnostics."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from typing import TYPE_CHECKING, Any

from unidep._doctor import (
    DoctorFinding,
    DoctorReport,
    _line_conda_roots,
    _shadowed_version_spans,
    format_doctor_report,
    print_doctor_report,
    run_doctor_checks,
    run_doctor_command,
)

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    import pytest


def _make_executable(path: Path, content: str = "#!/bin/sh\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
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
                        self.styles.append((value, style))

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


def test_shell_profile_scan_reports_multiple_conda_initializer_roots(
    tmp_path: Path,
) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text(
        'source "$HOME/miniconda3/etc/profile.d/conda.sh"\n'
        'source "/opt/miniconda3/etc/profile.d/conda.sh"',
    )

    report = run_doctor_checks(home=tmp_path, env={}, path_env="")

    finding = report.finding_by_code("multiple-conda-initializer-roots")
    assert finding is not None
    assert finding.level == "warning"
    assert "$HOME/miniconda3" in finding.details
    assert "/opt/miniconda3" in finding.details
    assert ".zshrc:1" in finding.details
    assert ".zshrc:2" in finding.details


def test_shell_profile_scan_reports_multiple_condabin_initializer_roots(
    tmp_path: Path,
) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text(
        'export PATH="$HOME/miniconda3/condabin:$PATH"\n'
        'export PATH="/opt/miniconda3/condabin:$PATH"',
    )

    report = run_doctor_checks(home=tmp_path, env={}, path_env="")

    finding = report.finding_by_code("multiple-conda-initializer-roots")
    assert finding is not None
    assert finding.level == "warning"
    assert "$HOME/miniconda3" in finding.details
    assert "/opt/miniconda3" in finding.details
    assert ".zshrc:1" in finding.details
    assert ".zshrc:2" in finding.details


def test_shell_profile_scan_reports_custom_condabin_initializer_root(
    tmp_path: Path,
) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text(
        'export PATH="/opt/toolchain/condabin:$HOME/miniconda3/condabin:$PATH"',
    )

    report = run_doctor_checks(home=tmp_path, env={}, path_env="")

    finding = report.finding_by_code("multiple-conda-initializer-roots")
    assert finding is not None
    assert finding.level == "warning"
    assert "/opt/toolchain" in finding.details
    assert "$HOME/miniconda3" in finding.details
    assert ".zshrc:1" in finding.details


def test_shell_profile_scan_reports_multiple_conda_roots_on_one_path_line(
    tmp_path: Path,
) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text('export PATH="$HOME/miniconda3/bin:/opt/miniconda3/bin:$PATH"')

    report = run_doctor_checks(home=tmp_path, env={}, path_env="")

    finding = report.finding_by_code("multiple-conda-initializer-roots")
    assert finding is not None
    assert finding.level == "warning"
    assert "$HOME/miniconda3" in finding.details
    assert "/opt/miniconda3" in finding.details
    assert ".zshrc:1" in finding.details


def test_shell_profile_scan_reports_suffixed_stale_conda_root(
    tmp_path: Path,
) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text(
        'export PATH="$HOME/miniconda3/bin:$HOME/miniconda3-old/bin:$PATH"',
    )

    report = run_doctor_checks(home=tmp_path, env={}, path_env="")

    finding = report.finding_by_code("multiple-conda-initializer-roots")
    assert finding is not None
    assert finding.level == "warning"
    assert "$HOME/miniconda3" in finding.details
    assert "$HOME/miniconda3-old" in finding.details
    assert ".zshrc:1" in finding.details


def test_shell_profile_scan_ignores_non_conda_path_entries_on_conda_line(
    tmp_path: Path,
) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text('export PATH="/usr/local/bin:$HOME/miniconda3/bin:$PATH"')

    report = run_doctor_checks(home=tmp_path, env={}, path_env="")

    assert _line_conda_roots(zshrc.read_text()) == ["$HOME/miniconda3"]
    assert report.finding_by_code("multiple-conda-initializer-roots") is None


def test_shell_profile_scan_reports_generic_conda_sh_initializer_root(
    tmp_path: Path,
) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text(
        "source /opt/conda/etc/profile.d/conda.sh\n"
        'source "$HOME/miniconda3/etc/profile.d/conda.sh"',
    )

    report = run_doctor_checks(home=tmp_path, env={}, path_env="")

    finding = report.finding_by_code("multiple-conda-initializer-roots")
    assert finding is not None
    assert finding.level == "warning"
    assert "/opt/conda" in finding.details
    assert "$HOME/miniconda3" in finding.details


def test_shell_profile_scan_allows_mamba_hook_inside_generic_conda_root(
    tmp_path: Path,
) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text(
        "source /opt/conda/etc/profile.d/conda.sh\n"
        'eval "$(/opt/conda/bin/mamba shell hook -s zsh)"',
    )

    report = run_doctor_checks(home=tmp_path, env={}, path_env="")

    assert report.finding_by_code("multiple-conda-initializers") is None
    assert report.finding_by_code("multiple-conda-initializer-roots") is None


def test_shell_profile_scan_allows_mamba_hook_inside_custom_conda_root(
    tmp_path: Path,
) -> None:
    zshrc = tmp_path / ".zshrc"
    for root in ("/opt/toolchain", "/srv/conda-24"):
        zshrc.write_text(
            f"source {root}/etc/profile.d/conda.sh\n"
            f'eval "$({root}/bin/mamba shell hook -s zsh)"',
        )

        report = run_doctor_checks(home=tmp_path, env={}, path_env="")

        assert report.finding_by_code("multiple-conda-initializers") is None
        assert report.finding_by_code("multiple-conda-initializer-roots") is None


def test_shell_profile_scan_ignores_env_bin_inside_generic_conda_root(
    tmp_path: Path,
) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text(
        "source /opt/conda/etc/profile.d/conda.sh\n"
        'export PATH="/opt/conda/envs/foo/bin:$PATH"',
    )

    report = run_doctor_checks(home=tmp_path, env={}, path_env="")

    assert report.finding_by_code("multiple-conda-initializer-roots") is None


def test_shell_profile_scan_parses_windows_style_conda_root() -> None:
    roots = _line_conda_roots(
        r'eval "$(C:\Users\runneradmin\AppData\Local\Temp\case/miniconda3/bin/mamba shell hook -s zsh)"',
    )

    assert roots == [
        r"C:\Users\runneradmin\AppData\Local\Temp\case/miniconda3",
    ]


def test_shell_profile_scan_allows_repeated_conda_initializer_root(
    tmp_path: Path,
) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text(
        'source "$HOME/miniconda3/etc/profile.d/conda.sh"\n'
        f'eval "$({tmp_path}/miniconda3/bin/mamba shell hook -s zsh)"',
    )

    report = run_doctor_checks(home=tmp_path, env={}, path_env="")

    assert report.finding_by_code("multiple-conda-initializer-roots") is None


def test_shell_profile_scan_allows_initializer_without_parseable_root(
    tmp_path: Path,
) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text("mamba shell hook --shell zsh\n")

    report = run_doctor_checks(home=tmp_path, env={}, path_env="")

    assert report.findings == ()


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


def test_path_scan_reports_symlinked_virtualenv_python_mismatch(
    tmp_path: Path,
) -> None:
    base_python = tmp_path / "cpython" / "bin" / "python"
    running_python = tmp_path / "env-a" / "bin" / "python"
    path_python = tmp_path / "env-b" / "bin" / "python"
    _make_executable(base_python)
    running_python.parent.mkdir(parents=True)
    path_python.parent.mkdir(parents=True)
    running_python.symlink_to(base_python)
    path_python.symlink_to(base_python)

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


def test_path_scan_allows_python3_from_same_environment(tmp_path: Path) -> None:
    running_python = tmp_path / "env" / "bin" / "python"
    path_python3 = tmp_path / "env" / "bin" / "python3"
    _make_executable(running_python)
    _make_executable(path_python3)

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env=str(path_python3.parent),
        python_executable=str(running_python),
    )

    assert report.finding_by_code("path-python3-mismatch") is None


def test_path_scan_allows_windows_python3_from_same_environment(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "env" / "Scripts"
    running_python = scripts / "python.exe"
    path_python3 = scripts / "python3.EXE"
    _make_executable(running_python)
    _make_executable(path_python3)

    report = run_doctor_checks(
        home=tmp_path,
        env={"PATHEXT": ".EXE"},
        path_env=str(scripts),
        python_executable=str(running_python),
    )

    assert report.finding_by_code("path-python3-mismatch") is None


def test_path_scan_reports_non_python_interpreter_name_mismatch(
    tmp_path: Path,
) -> None:
    running_python = tmp_path / "env" / "bin" / "pypy3"
    path_python = tmp_path / "env" / "bin" / "python"
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
    assert str(path_python) in finding.details
    assert str(running_python) in finding.details


def test_path_scan_ignores_python_shadowing(tmp_path: Path) -> None:
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

    assert report.finding_by_code("shadowed-python") is None


def test_path_scan_ignores_expected_python_shadowing_from_running_env(
    tmp_path: Path,
) -> None:
    env_bin = tmp_path / "env" / "bin"
    system_bin = tmp_path / "system" / "bin"
    _make_executable(env_bin / "python")
    _make_executable(env_bin / "python3")
    _make_executable(system_bin / "python")
    _make_executable(system_bin / "python3")

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env=f"{env_bin}{os.pathsep}{system_bin}",
        python_executable=str(env_bin / "python"),
    )

    assert report.finding_by_code("path-python-mismatch") is None
    assert report.finding_by_code("path-python3-mismatch") is None
    assert report.finding_by_code("shadowed-python") is None
    assert report.finding_by_code("shadowed-python3") is None


def test_path_scan_ignores_expected_pip_shadowing_from_running_env(
    tmp_path: Path,
) -> None:
    env_bin = tmp_path / "env" / "bin"
    system_bin = tmp_path / "system" / "bin"
    _make_executable(env_bin / "python")
    _make_executable(env_bin / "pip")
    _make_executable(env_bin / "pip3")
    _make_executable(system_bin / "pip")
    _make_executable(system_bin / "pip3")

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env=f"{env_bin}{os.pathsep}{system_bin}",
        python_executable=str(env_bin / "python"),
    )

    assert report.finding_by_code("shadowed-pip") is None
    assert report.finding_by_code("shadowed-pip3") is None


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


def test_path_scan_reports_uv_shadowing_with_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_bin = tmp_path / "first" / "bin"
    second_bin = tmp_path / "second" / "bin"
    _make_executable(first_bin / "uv")
    _make_executable(second_bin / "uv")

    def run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        root_name = command[0].replace("\\", "/").split("/")[-3]
        version = {
            "first": "uv 0.8.1",
            "second": "uv 0.4.0",
        }[root_name]
        return subprocess.CompletedProcess(command, 0, stdout=f"{version}\n")

    monkeypatch.setattr("unidep._doctor.subprocess.run", run)

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env=f"{first_bin}{os.pathsep}{second_bin}",
        python_executable=sys.executable,
    )

    finding = report.finding_by_code("shadowed-uv")
    assert finding is not None
    assert finding.level == "info"
    assert f"{first_bin / 'uv'} (uv 0.8.1)" in finding.details
    assert f"{second_bin / 'uv'} (uv 0.4.0)" in finding.details


def test_path_scan_warns_when_tool_version_probe_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bin_dir = tmp_path / "bin"
    _make_executable(bin_dir / "uv")

    def run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 2, stderr="broken uv\n")

    monkeypatch.setattr("unidep._doctor.subprocess.run", run)

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env=str(bin_dir),
        python_executable=sys.executable,
    )

    finding = report.finding_by_code("uv-version-probe-failed")
    assert finding is not None
    assert finding.level == "warning"
    assert str(bin_dir / "uv") in finding.details
    assert "exit code 2" in finding.details
    assert "broken uv" in finding.details


def test_path_scan_warns_when_tool_version_probe_times_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bin_dir = tmp_path / "bin"
    _make_executable(bin_dir / "uv")

    def run(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="uv --version", timeout=2)

    monkeypatch.setattr("unidep._doctor.subprocess.run", run)

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env=str(bin_dir),
        python_executable=sys.executable,
    )

    finding = report.finding_by_code("uv-version-probe-failed")
    assert finding is not None
    assert "timed out after 2 seconds" in finding.details


def test_path_scan_warns_when_tool_version_probe_cannot_execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bin_dir = tmp_path / "bin"
    _make_executable(bin_dir / "uv")

    def run(*_args: object, **_kwargs: object) -> None:
        error_message = "exec format error"
        raise OSError(error_message)

    monkeypatch.setattr("unidep._doctor.subprocess.run", run)

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env=str(bin_dir),
        python_executable=sys.executable,
    )

    finding = report.finding_by_code("uv-version-probe-failed")
    assert finding is not None
    assert str(bin_dir / "uv") in finding.details
    assert "exec format error" in finding.details


def test_path_scan_honors_pathext_for_shadowing(
    tmp_path: Path,
) -> None:
    first_bin = tmp_path / "first" / "bin"
    second_bin = tmp_path / "second" / "bin"
    _make_executable(first_bin / "pip.exe")
    _make_executable(second_bin / "pip.EXE")

    report = run_doctor_checks(
        home=tmp_path,
        env={"PATHEXT": ".COM;.EXE;.BAT"},
        path_env=f"{first_bin}{os.pathsep}{second_bin}",
        python_executable=sys.executable,
    )

    finding = report.finding_by_code("shadowed-pip")
    assert finding is not None
    details = finding.details.casefold()
    assert str(first_bin / "pip.exe").casefold() in details
    assert str(second_bin / "pip.EXE").casefold() in details


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


def test_rich_report_styles_shadowed_tool_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    report = DoctorReport(
        (
            DoctorFinding(
                code="shadowed-uv",
                level="info",
                title="Multiple `uv` executables are on PATH.",
                details="/first/uv (uv 0.8.1), /second/uv (uv 0.4.0)",
                recommendation="Check PATH ordering.",
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
    assert "('(uv 0.8.1)', 'bold cyan')" in captured.out
    assert "('(uv 0.4.0)', 'bold cyan')" in captured.out


def test_rich_report_styles_recommendation_inline_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    report = DoctorReport(
        (
            DoctorFinding(
                code="uninitialized-local-git-submodule",
                level="error",
                title="A local dependency appears to be an uninitialized Git submodule.",
                details="requirements.yaml: ./vendor -> /project/vendor",
                recommendation=(
                    "Fetch the submodule with "
                    "`git submodule update --init --recursive`, then rerun "
                    "`unidep doctor`."
                ),
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
    assert "('git submodule update --init --recursive', 'bold cyan')" in captured.out
    assert "('unidep doctor', 'bold cyan')" in captured.out


def test_shadowed_version_spans_ignores_unclosed_version() -> None:
    assert _shadowed_version_spans("/first/uv (uv 0.8.1") == []


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
                code="shadowed-pip",
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


def test_doctor_reports_local_dependency_git_submodule(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    vendor = project / "vendor"
    vendor.mkdir()
    (project / ".gitmodules").write_text(
        textwrap.dedent(
            """\
            [submodule "vendor"]
                path = vendor
                url = https://example.invalid/vendor.git
            """,
        ),
    )
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            local_dependencies:
              - ./vendor
            """,
        ),
    )

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env="",
        project_dir=project,
    )

    finding = report.finding_by_code("uninitialized-local-git-submodule")
    assert finding is not None
    assert finding.level == "error"
    assert "requirements.yaml" in finding.details
    assert "./vendor" in finding.details
    assert "git submodule update --init --recursive" in finding.recommendation


def test_doctor_reports_missing_registered_local_dependency_submodule(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".gitmodules").write_text(
        textwrap.dedent(
            """\
            [submodule "vendor"]
                path = vendor
                url = https://example.invalid/vendor.git
            """,
        ),
    )
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            local_dependencies:
              - ./vendor
            """,
        ),
    )

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env="",
        project_dir=project,
    )

    assert report.finding_by_code("uninitialized-local-git-submodule") is not None


def test_doctor_reports_git_file_only_local_dependency_submodule(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    vendor = project / "vendor"
    vendor.mkdir()
    (vendor / ".git").write_text("gitdir: ../.git/modules/vendor")
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            local_dependencies:
              - ./vendor
            """,
        ),
    )

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env="",
        project_dir=project,
    )

    assert report.finding_by_code("uninitialized-local-git-submodule") is not None


def test_doctor_ignores_healthy_and_nonlocal_local_dependencies(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    vendor = project / "vendor"
    vendor.mkdir()
    (vendor / "pyproject.toml").write_text("[build-system]\nrequires = []\n")
    local_file = project / "local.txt"
    local_file.write_text("not a directory")
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            local_dependencies:
              - ./vendor
              - ./local.txt
              - local: ./vendor
                pypi: vendor-package
                use: pypi
            """,
        ),
    )

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env="",
        project_dir=project,
    )

    assert report.finding_by_code("uninitialized-local-git-submodule") is None


def test_doctor_warns_when_local_dependency_scan_fails(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            local_dependencies:
              - 42
            """,
        ),
    )

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env="",
        project_dir=project,
    )

    finding = report.finding_by_code("project-local-dependency-scan-failed")
    assert finding is not None
    assert finding.level == "warning"
    assert "Invalid local dependency format" in finding.details


def test_doctor_ignores_unreadable_gitmodules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    vendor = project / "vendor"
    vendor.mkdir()
    gitmodules = project / ".gitmodules"
    gitmodules.write_text("path = vendor\n")
    (project / "requirements.yaml").write_text(
        textwrap.dedent(
            """\
            local_dependencies:
              - ./vendor
            """,
        ),
    )
    original_read_text = type(project).read_text

    def read_text(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == gitmodules:
            raise OSError
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(project), "read_text", read_text)

    report = run_doctor_checks(
        home=tmp_path,
        env={},
        path_env="",
        project_dir=project,
    )

    assert report.finding_by_code("uninitialized-local-git-submodule") is None
