"""Tests for package-index install helpers."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from unidep._artifact_metadata import (
    PlatformDependencySet,
    SelectedMetadataDependencies,
    UnidepMetadata,
    UnidepMetadataError,
)
from unidep._index_install import (
    InstallRuntime,
    _build_pip_install_command,
    _download_package_artifact,
    _load_unidep_metadata_for_spec,
    _parse_package_requirements,
    _parse_requirement_or_none,
    _pip_install_packages,
    _warn_ignored_package_install_flags,
    install_package_specs_command,
)


def _make_runtime(
    *,
    calls: list[list[str]] | None = None,
    maybe_conda_executable: Any = lambda: None,
    identify_current_platform: Any = lambda: "linux-64",
) -> InstallRuntime:
    def _run_capture(cmd: list[str] | tuple[str, ...], **_: object) -> None:
        if calls is None:
            return
        calls.append([str(c) for c in cmd])

    return InstallRuntime(
        maybe_conda_executable=maybe_conda_executable,
        maybe_conda_run=lambda *_args, **_kwargs: [],
        python_executable=lambda *_args, **_kwargs: "python",
        maybe_create_conda_env_args=lambda *_args, **_kwargs: [],
        maybe_exe=lambda conda_executable: conda_executable,
        format_inline_conda_package=lambda pkg: pkg,
        use_uv=lambda _no_uv: False,
        identify_current_platform=identify_current_platform,
        run_subprocess=_run_capture,
    )


def _metadata(
    *,
    conda: list[str] | None = None,
    pip: list[str] | None = None,
) -> UnidepMetadata:
    return UnidepMetadata(
        schema_version=1,
        project="demo-package",
        version="1.2.3",
        channels=["conda-forge"],
        platforms={
            "linux-64": PlatformDependencySet(
                conda=list(conda or []),
                pip=list(pip or []),
            ),
        },
        extras={},
    )


def test_build_pip_install_command_uses_uv() -> None:
    cmd = _build_pip_install_command(
        python_executable="python",
        conda_run=["micromamba", "run"],
        no_uv=False,
        use_uv=lambda _no_uv: True,
    )
    assert cmd == [
        "micromamba",
        "run",
        "uv",
        "pip",
        "install",
        "--python",
        "python",
    ]


def test_pip_install_packages_no_packages() -> None:
    called = False

    def _run(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called = True

    _pip_install_packages(
        dry_run=False,
        python_executable="python",
        conda_run=[],
        no_uv=True,
        use_uv=lambda _no_uv: False,
        run_subprocess=_run,
    )
    assert not called


def test_download_package_artifact_returns_wheel(tmp_path: Path) -> None:
    destination = tmp_path / "download"
    destination.mkdir()

    def _run(cmd: list[str], **_: object) -> None:
        idx = cmd.index("--dest")
        out = Path(cmd[idx + 1])
        (out / "demo_package-1.2.3-py3-none-any.whl").write_text("wheel")

    artifact = _download_package_artifact(
        "demo-package==1.2.3",
        destination=destination,
        python_executable="python",
        conda_run=[],
        dry_run=False,
        run_subprocess=_run,
    )
    assert artifact is not None
    assert artifact.suffix == ".whl"


def test_download_package_artifact_dry_run_returns_none(tmp_path: Path) -> None:
    destination = tmp_path / "download"
    destination.mkdir()

    artifact = _download_package_artifact(
        "demo-package==1.2.3",
        destination=destination,
        python_executable="python",
        conda_run=[],
        dry_run=True,
        run_subprocess=lambda *_args, **_kwargs: None,
    )
    assert artifact is None


def test_download_package_artifact_returns_sdist(tmp_path: Path) -> None:
    destination = tmp_path / "download"
    destination.mkdir()

    def _run(cmd: list[str], **_: object) -> None:
        idx = cmd.index("--dest")
        out = Path(cmd[idx + 1])
        (out / "demo_package-1.2.3.tar.gz").write_text("sdist")

    artifact = _download_package_artifact(
        "demo-package==1.2.3",
        destination=destination,
        python_executable="python",
        conda_run=[],
        dry_run=False,
        run_subprocess=_run,
    )
    assert artifact is not None
    assert artifact.name.endswith(".tar.gz")


def test_download_package_artifact_returns_none_when_nothing_downloaded(
    tmp_path: Path,
) -> None:
    """Pip may download nothing for marker-gated requirements; return None."""
    destination = tmp_path / "download"
    destination.mkdir()

    artifact = _download_package_artifact(
        "demo-package==1.2.3",
        destination=destination,
        python_executable="python",
        conda_run=[],
        dry_run=False,
        run_subprocess=lambda *_args, **_kwargs: None,
    )
    assert artifact is None


def test_parse_requirement_or_none_invalid() -> None:
    assert _parse_requirement_or_none("this is not valid") is None


def test_parse_package_requirements_invalid_spec() -> None:
    with pytest.raises(ValueError, match="Invalid package requirement specifier"):
        _parse_package_requirements(("demo==1.0", "invalid requirement"))


def test_warn_ignored_package_install_flags(capsys: pytest.CaptureFixture[str]) -> None:
    _warn_ignored_package_install_flags(
        editable=True,
        skip_local=True,
        ignore_pins=["numpy"],
        overwrite_pins=["numpy=1.0"],
        skip_dependencies=["pandas"],
    )
    out = capsys.readouterr().out
    assert "`--editable` is ignored" in out
    assert "`--skip-local` is ignored" in out
    assert "`--ignore-pin` is ignored" in out
    assert "`--overwrite-pin` is ignored" in out
    assert "`--skip-dependency` is ignored" in out


def test_load_unidep_metadata_for_spec_none_artifact(tmp_path: Path) -> None:
    with patch("unidep._index_install._download_package_artifact", return_value=None):
        metadata = _load_unidep_metadata_for_spec(
            "demo-package==1.2.3",
            destination=tmp_path,
            python_executable="python",
            conda_run=[],
            dry_run=False,
            run_subprocess=lambda *_args, **_kwargs: None,
        )
    assert metadata is None


def test_load_unidep_metadata_for_spec_sdist_fallback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact = tmp_path / "demo-package-1.2.3.tar.gz"
    artifact.write_text("sdist")

    with patch(
        "unidep._index_install._download_package_artifact",
        return_value=artifact,
    ):
        metadata = _load_unidep_metadata_for_spec(
            "demo-package==1.2.3",
            destination=tmp_path,
            python_executable="python",
            conda_run=[],
            dry_run=False,
            run_subprocess=lambda *_args, **_kwargs: None,
        )
    assert metadata is None
    assert "Downloaded source distribution" in capsys.readouterr().out


def test_load_unidep_metadata_for_spec_invalid_metadata(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact = tmp_path / "demo-package-1.2.3-py3-none-any.whl"
    artifact.write_text("wheel")

    with patch(
        "unidep._index_install._download_package_artifact",
        return_value=artifact,
    ), patch(
        "unidep._index_install.extract_unidep_metadata_from_wheel",
        side_effect=UnidepMetadataError("broken"),
    ):
        metadata = _load_unidep_metadata_for_spec(
            "demo-package==1.2.3",
            destination=tmp_path,
            python_executable="python",
            conda_run=[],
            dry_run=False,
            run_subprocess=lambda *_args, **_kwargs: None,
        )
    assert metadata is None
    assert "Invalid UniDep metadata" in capsys.readouterr().out


def test_install_package_specs_rejects_conda_lock_file(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match="1"):
        install_package_specs_command(
            "demo-package==1.2.3",
            conda_executable=None,
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=Path("conda-lock.yml"),
            dry_run=True,
            editable=False,
            runtime=_make_runtime(),
        )
    assert "`--conda-lock-file` is only supported" in capsys.readouterr().out


def test_install_package_specs_no_dependencies_fallback_uses_no_deps_flag() -> None:
    calls: list[list[str]] = []
    with patch(
        "unidep._index_install._load_unidep_metadata_for_spec",
        return_value=None,
    ):
        install_package_specs_command(
            "demo-package==1.2.3",
            conda_executable=None,
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=None,
            dry_run=False,
            editable=False,
            no_dependencies=True,
            runtime=_make_runtime(calls=calls),
        )
    assert calls == [
        ["python", "-m", "pip", "install", "--no-deps", "demo-package==1.2.3"],
    ]


def test_install_package_specs_unusable_metadata_falls_back_to_pip(
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[list[str]] = []
    with patch(
        "unidep._index_install._load_unidep_metadata_for_spec",
        return_value=_metadata(),
    ), patch(
        "unidep._index_install.select_unidep_dependencies",
        side_effect=UnidepMetadataError("bad metadata"),
    ):
        install_package_specs_command(
            "demo-package==1.2.3",
            conda_executable=None,
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=None,
            dry_run=False,
            editable=False,
            runtime=_make_runtime(calls=calls),
        )
    assert (
        "UniDep metadata for `demo-package==1.2.3` is unusable"
        in capsys.readouterr().out
    )
    assert calls == [["python", "-m", "pip", "install", "demo-package==1.2.3"]]


def test_install_package_specs_truly_missing_extras_falls_back_to_plain_pip(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When an extra is not defined at all in metadata, fall back to pip."""
    calls: list[list[str]] = []
    selected = SelectedMetadataDependencies(
        channels=["conda-forge"],
        conda=[],
        pip=[],
        missing_extras=["nonexistent"],
    )
    with patch(
        "unidep._index_install._load_unidep_metadata_for_spec",
        return_value=_metadata(),
    ), patch(
        "unidep._index_install.select_unidep_dependencies",
        return_value=selected,
    ):
        install_package_specs_command(
            "demo-package[nonexistent]==1.2.3",
            conda_executable="conda",
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=None,
            dry_run=False,
            editable=False,
            runtime=_make_runtime(calls=calls),
        )
    out = capsys.readouterr().out
    assert "does not define extra(s): nonexistent" in out
    assert "Falling back to pip-only install" in out
    assert calls == [
        ["python", "-m", "pip", "install", "demo-package[nonexistent]==1.2.3"],
    ]


def test_install_package_specs_extra_no_platform_delta_keeps_base_deps() -> None:
    """An extra with no delta on the current platform must NOT discard base deps."""
    calls: list[list[str]] = []
    # select_unidep_dependencies returns base deps and no missing extras
    # (the extra exists but simply has no contribution on this platform)
    selected = SelectedMetadataDependencies(
        channels=["conda-forge"],
        conda=["qsimcirq * cuda*"],
        pip=["requests>=2"],
        missing_extras=[],
    )
    with patch(
        "unidep._index_install._load_unidep_metadata_for_spec",
        return_value=_metadata(conda=["qsimcirq * cuda*"], pip=["requests>=2"]),
    ), patch(
        "unidep._index_install.select_unidep_dependencies",
        return_value=selected,
    ):
        install_package_specs_command(
            "demo-package[dev]==1.2.3",
            conda_executable="conda",
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=None,
            dry_run=False,
            editable=False,
            runtime=_make_runtime(calls=calls),
        )
    # Base conda deps must be installed, NOT discarded
    assert any("conda" in cmd[0] and "qsimcirq * cuda*" in cmd for cmd in calls), (
        f"Expected conda install with base deps, got: {calls}"
    )
    # Pip deps from metadata must be installed
    assert any("requests>=2" in cmd for cmd in calls), (
        f"Expected pip install with base deps, got: {calls}"
    )
    # The package itself must be installed with --no-deps
    assert any("--no-deps" in cmd and "demo-package==1.2.3" in cmd for cmd in calls), (
        f"Expected --no-deps install, got: {calls}"
    )


def test_load_unidep_metadata_for_spec_bad_zip(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A corrupt .whl (BadZipFile) should fall back gracefully."""
    artifact = tmp_path / "demo-package-1.2.3-py3-none-any.whl"
    artifact.write_text("this is not a zip file")

    with patch(
        "unidep._index_install._download_package_artifact",
        return_value=artifact,
    ):
        metadata = _load_unidep_metadata_for_spec(
            "demo-package==1.2.3",
            destination=tmp_path,
            python_executable="python",
            conda_run=[],
            dry_run=False,
            run_subprocess=lambda *_args, **_kwargs: None,
        )
    assert metadata is None
    assert "Invalid UniDep metadata" in capsys.readouterr().out


def test_load_unidep_metadata_for_spec_invalid_utf8(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact = tmp_path / "demo-package-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(artifact, "w") as zf:
        zf.writestr("demo-package-1.2.3.dist-info/unidep.json", b"\xff\xfe")

    with patch(
        "unidep._index_install._download_package_artifact",
        return_value=artifact,
    ):
        metadata = _load_unidep_metadata_for_spec(
            "demo-package==1.2.3",
            destination=tmp_path,
            python_executable="python",
            conda_run=[],
            dry_run=False,
            run_subprocess=lambda *_args, **_kwargs: None,
        )
    assert metadata is None
    assert "Invalid UniDep metadata" in capsys.readouterr().out


def test_marker_gated_requirement_falls_back_to_pip() -> None:
    """A requirement whose marker is false produces no download; should not crash."""
    calls: list[list[str]] = []

    # Simulate pip downloading nothing (marker false → exit 0, empty dir)
    with patch(
        "unidep._index_install._download_package_artifact",
        return_value=None,
    ):
        install_package_specs_command(
            'demo-package==1.2.3; python_version < "2"',
            conda_executable=None,
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=None,
            dry_run=False,
            editable=False,
            runtime=_make_runtime(calls=calls),
        )
    # Should fall back to pip install without crashing
    assert calls == [
        [
            "python",
            "-m",
            "pip",
            "install",
            'demo-package==1.2.3; python_version < "2"',
        ],
    ]


def test_install_package_specs_pins_to_metadata_version() -> None:
    """The --no-deps install should use the exact version from metadata."""
    calls: list[list[str]] = []
    # User passes a range spec, but metadata resolves to 1.2.3
    with patch(
        "unidep._index_install._load_unidep_metadata_for_spec",
        return_value=_metadata(pip=["requests>=2"]),
    ):
        install_package_specs_command(
            "demo-package>=1.0",
            conda_executable=None,
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=None,
            dry_run=False,
            editable=False,
            runtime=_make_runtime(calls=calls),
        )
    # Must pin to metadata version, not the original range spec
    assert ["python", "-m", "pip", "install", "--no-deps", "demo-package==1.2.3"] in (
        calls
    )
    # The original range spec should NOT appear in any --no-deps call
    assert not any("demo-package>=1.0" in cmd for cmd in calls if "--no-deps" in cmd)


def test_install_package_specs_preserves_direct_reference_spec() -> None:
    """Direct URL requirements should not be rewritten to ``name==version``."""
    calls: list[list[str]] = []
    direct_spec = (
        "demo-package @ https://example.com/demo-package-1.2.3-py3-none-any.whl"
    )
    with patch(
        "unidep._index_install._load_unidep_metadata_for_spec",
        return_value=_metadata(pip=["requests>=2"]),
    ):
        install_package_specs_command(
            direct_spec,
            conda_executable=None,
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=None,
            dry_run=False,
            editable=False,
            runtime=_make_runtime(calls=calls),
        )
    assert ["python", "-m", "pip", "install", "--no-deps", direct_spec] in calls
    assert not any("demo-package==1.2.3" in cmd for cmd in calls if "--no-deps" in cmd)


def test_install_package_specs_skip_conda_moves_to_fallback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When --skip-conda is set, packages with conda deps use pip fallback."""
    calls: list[list[str]] = []
    with patch(
        "unidep._index_install._load_unidep_metadata_for_spec",
        return_value=_metadata(conda=["qsimcirq * cuda*"], pip=["requests>=2"]),
    ):
        install_package_specs_command(
            "demo-package==1.2.3",
            conda_executable=None,
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=None,
            dry_run=False,
            editable=False,
            skip_conda=True,
            runtime=_make_runtime(calls=calls),
        )
    out = capsys.readouterr().out
    assert "Skipping UniDep Conda dependencies" in out
    # Package should be installed via pip fallback (without --no-deps)
    # so pip can resolve the conda deps that were skipped
    assert ["python", "-m", "pip", "install", "demo-package==1.2.3"] in calls
    # Should NOT have a --no-deps install for this package
    assert not any("--no-deps" in cmd and "demo-package==1.2.3" in cmd for cmd in calls)


def test_install_package_specs_skip_conda_preserves_extras_and_markers() -> None:
    """Fallback must keep the original requirement string (extras/markers intact)."""
    calls: list[list[str]] = []
    user_spec = 'demo-package[dev]>=1.0; python_version >= "3.8"'
    with patch(
        "unidep._index_install._load_unidep_metadata_for_spec",
        return_value=_metadata(conda=["qsimcirq * cuda*"], pip=[]),
    ):
        install_package_specs_command(
            user_spec,
            conda_executable=None,
            conda_env_name=None,
            conda_env_prefix=None,
            conda_lock_file=None,
            dry_run=False,
            editable=False,
            skip_conda=True,
            runtime=_make_runtime(calls=calls),
        )

    assert ["python", "-m", "pip", "install", user_spec] in calls
    assert not any(
        cmd[:4] == ["python", "-m", "pip", "install"] and "demo-package==1.2.3" in cmd
        for cmd in calls
    )
