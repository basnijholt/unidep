"""unidep conda-lock tests."""

from __future__ import annotations

import shutil
import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from ruamel.yaml import YAML

from unidep._conda_lock import (
    LockSpec,
    _check_consistent_lock_files,
    _conda_lock_subpackage,
    _conda_lock_subpackages,
    _download_and_get_package_names,
    _handle_missing_keys,
    _parse_conda_lock_packages,
    conda_lock_command,
)
from unidep.utils import remove_top_comments

if TYPE_CHECKING:
    from unidep.platform_definitions import CondaPip, Platform


def test_conda_lock_command(tmp_path: Path) -> None:
    folder = tmp_path / "simple_monorepo"
    shutil.copytree(Path(__file__).parent / "simple_monorepo", folder)
    with patch("unidep._conda_lock._run_conda_lock", return_value=None):
        conda_lock_command(
            depth=1,
            directory=folder,
            files=None,
            platforms=["linux-64", "osx-arm64"],
            verbose=True,
            only_global=False,
            check_input_hash=True,
            ignore_pins=[],
            overwrite_pins=[],
            skip_dependencies=[],
            extra_flags=["--", "--micromamba"],
        )
    with YAML(typ="safe") as yaml:
        with (folder / "project1" / "conda-lock.yml").open() as f:
            lock1 = yaml.load(f)
        with (folder / "project2" / "conda-lock.yml").open() as f:
            lock2 = yaml.load(f)

    assert [p["name"] for p in lock1["package"] if p["platform"] == "osx-arm64"] == [
        "bzip2",
        "python_abi",
        "tzdata",
    ]
    assert [p["name"] for p in lock2["package"] if p["platform"] == "osx-arm64"] == [
        "python_abi",
        "tzdata",
    ]


def test_conda_lock_command_pip_package_with_conda_dependency(tmp_path: Path) -> None:
    folder = tmp_path / "test-pip-package-with-conda-dependency"
    shutil.copytree(
        Path(__file__).parent / "test-pip-package-with-conda-dependency",
        folder,
    )
    with patch("unidep._conda_lock._run_conda_lock", return_value=None):
        conda_lock_command(
            depth=1,
            directory=folder,
            files=None,
            platforms=["linux-64"],
            verbose=True,
            only_global=False,
            check_input_hash=True,
            ignore_pins=[],
            overwrite_pins=[],
            skip_dependencies=[],
            extra_flags=[],
        )
    with YAML(typ="safe") as yaml:
        with (folder / "project1" / "conda-lock.yml").open() as f:
            lock1 = yaml.load(f)
        with (folder / "project2" / "conda-lock.yml").open() as f:
            lock2 = yaml.load(f)
    assert [p["name"] for p in lock1["package"]] == [
        "_libgcc_mutex",
        "_openmp_mutex",
        "bzip2",
        "ca-certificates",
        "ld_impl_linux-64",
        "libexpat",
        "libffi",
        "libgcc-ng",
        "libgomp",
        "libnsl",
        "libsqlite",
        "libstdcxx-ng",
        "libuuid",
        "libzlib",
        "ncurses",
        "openssl",
        "pybind11",
        "pybind11-global",
        "python",
        "python_abi",
        "readline",
        "tk",
        "tzdata",
        "xz",
    ]
    assert [p["name"] for p in lock2["package"]] == [
        "_libgcc_mutex",
        "_openmp_mutex",
        "bzip2",
        "ca-certificates",
        "ld_impl_linux-64",
        "libexpat",
        "libffi",
        "libgcc-ng",
        "libgomp",
        "libnsl",
        "libsqlite",
        "libstdcxx-ng",
        "libuuid",
        "libzlib",
        "ncurses",
        "openssl",
        "pybind11",
        "pybind11-global",
        "python",
        "python_abi",
        "readline",
        "tk",
        "tzdata",
        "xz",
        "cutde",
        "mako",
        "markupsafe",
        "rsync-time-machine",
    ]


def test_conda_lock_global_infers_selector_platforms(tmp_path: Path) -> None:
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        """\
channels:
  - conda-forge
dependencies:
  - cuda-toolkit  # [linux64]
""",
    )
    with patch("unidep._conda_lock._run_conda_lock", return_value=None), patch(
        "unidep.utils.identify_current_platform",
        return_value="osx-arm64",
    ):
        conda_lock_command(
            depth=1,
            directory=tmp_path,
            files=[req_file],
            platforms=[],
            verbose=False,
            only_global=True,
            check_input_hash=False,
            ignore_pins=[],
            overwrite_pins=[],
            skip_dependencies=[],
            extra_flags=[],
        )

    tmp_env = tmp_path / "tmp.environment.yaml"
    with YAML(typ="safe") as yaml, tmp_env.open() as f:
        data = yaml.load(f)
    assert data["platforms"] == ["linux-64"]


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_conda_lock_command_pip_and_conda_different_name(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    folder = tmp_path / "test-pip-and-conda-different-name"
    shutil.copytree(Path(__file__).parent / "test-pip-and-conda-different-name", folder)
    files = [
        folder / "project1" / "requirements.yaml",
        folder / "project2" / "requirements.yaml",
    ]
    with patch("unidep._conda_lock._run_conda_lock", return_value=None):
        conda_lock_command(
            depth=1,
            directory=folder,  # ignored when using files
            files=files,
            platforms=["linux-64"],
            verbose=True,
            only_global=False,
            check_input_hash=True,
            ignore_pins=[],
            overwrite_pins=[],
            skip_dependencies=[],
            extra_flags=[],
        )
    assert "Missing keys" not in capsys.readouterr().out


def test_remove_top_comments(tmp_path: Path) -> None:
    test_file = tmp_path / "test_file.txt"
    test_file.write_text(
        "# Comment line 1\n# Comment line 2\nActual content line 1\nActual content line 2",
    )

    remove_top_comments(test_file)

    with test_file.open("r") as file:
        content = file.read()

    assert content == "Actual content line 1\nActual content line 2"


def test_handle_missing_keys(capsys: pytest.CaptureFixture) -> None:
    lock_spec = LockSpec(
        packages={
            ("conda", "linux-64", "python-nonexistent"): {
                "name": "python-nonexistent",
                "manager": "conda",
                "platform": "linux-64",
                "dependencies": [],
                "url": "https://example.com/nonexistent",
            },
        },
        dependencies={("conda", "linux-64", "nonexistent"): set()},
    )
    # Here the package name on pip contains the conda package name, so we will download
    # the conda package to verify that this is our package.

    locked: list[dict[str, Any]] = []
    locked_keys: set[tuple[CondaPip, Platform, str]] = {}  # type: ignore[assignment]
    missing_keys: set[tuple[CondaPip, Platform, str]] = {
        ("pip", "linux-64", "nonexistent"),
    }
    with patch(
        "unidep._conda_lock._download_and_get_package_names",
        return_value=None,
    ) as mock:
        _handle_missing_keys(
            lock_spec=lock_spec,
            locked_keys=locked_keys,
            missing_keys=missing_keys,
            locked=locked,
        )
        mock.assert_called_once()

    assert f"❌ Missing keys {missing_keys}" in capsys.readouterr().out
    assert ("pip", "linux-64", "nonexistent") in missing_keys


def test_handle_missing_keys_adds_matching_conda_package() -> None:
    pkg = {
        "name": "msgpack-python",
        "manager": "conda",
        "platform": "linux-64",
        "dependencies": {},
        "url": "https://example.com/msgpack-python.conda",
    }
    lock_spec = LockSpec(
        packages={("conda", "linux-64", "msgpack-python"): pkg},
        dependencies={("conda", "linux-64", "msgpack-python"): set()},
    )
    locked: list[dict[str, Any]] = []
    locked_keys: set[tuple[CondaPip, Platform, str]] = set()
    missing_keys: set[tuple[CondaPip, Platform, str]] = {
        ("pip", "linux-64", "msgpack"),
    }

    with patch(
        "unidep._conda_lock._download_and_get_package_names",
        return_value=["msgpack"],
    ):
        _handle_missing_keys(
            lock_spec=lock_spec,
            locked_keys=locked_keys,
            missing_keys=missing_keys,
            locked=locked,
        )

    assert missing_keys == set()
    assert locked == [pkg]
    assert ("conda", "linux-64", "msgpack-python") in locked_keys


def test_download_and_get_package_names_reads_site_packages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlretrieve(_url: str, filename: str) -> None:
        Path(filename).write_text("archive")

    def fake_extract(
        _src: str,
        *,
        dest_dir: str,
        components: str | None = None,
    ) -> None:
        del components
        site_packages = Path(dest_dir) / "site-packages"
        (site_packages / "pkg").mkdir(parents=True)
        (site_packages / "pkg.dist-info").mkdir()
        (site_packages / "pkg.egg-info").mkdir()

    api_module = types.ModuleType("conda_package_handling.api")
    api_module.extract = fake_extract  # type: ignore[attr-defined]
    package_module = types.ModuleType("conda_package_handling")
    package_module.api = api_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "conda_package_handling", package_module)
    monkeypatch.setitem(sys.modules, "conda_package_handling.api", api_module)
    monkeypatch.setattr("urllib.request.urlretrieve", fake_urlretrieve)

    names = _download_and_get_package_names(
        {
            "name": "pkg",
            "manager": "conda",
            "platform": "linux-64",
            "url": "https://example.com/pkg.conda",
        },
    )
    assert names == ["pkg"]


def test_download_and_get_package_names_returns_none_without_python_dirs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlretrieve(_url: str, filename: str) -> None:
        Path(filename).write_text("archive")

    def fake_extract(
        _src: str,
        *,
        dest_dir: str,
        components: str | None = None,
    ) -> None:
        del components
        (Path(dest_dir) / "lib" / "not-python").mkdir(parents=True)

    api_module = types.ModuleType("conda_package_handling.api")
    api_module.extract = fake_extract  # type: ignore[attr-defined]
    package_module = types.ModuleType("conda_package_handling")
    package_module.api = api_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "conda_package_handling", package_module)
    monkeypatch.setitem(sys.modules, "conda_package_handling.api", api_module)
    monkeypatch.setattr("urllib.request.urlretrieve", fake_urlretrieve)

    names = _download_and_get_package_names(
        {
            "name": "pkg",
            "manager": "conda",
            "platform": "linux-64",
            "url": "https://example.com/pkg.conda",
        },
    )
    assert names is None


def test_download_and_get_package_names_returns_none_without_lib_or_site_packages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlretrieve(_url: str, filename: str) -> None:
        Path(filename).write_text("archive")

    def fake_extract(
        _src: str,
        *,
        dest_dir: str,
        components: str | None = None,
    ) -> None:
        del components
        (Path(dest_dir) / "share").mkdir(parents=True)

    api_module = types.ModuleType("conda_package_handling.api")
    api_module.extract = fake_extract  # type: ignore[attr-defined]
    package_module = types.ModuleType("conda_package_handling")
    package_module.api = api_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "conda_package_handling", package_module)
    monkeypatch.setitem(sys.modules, "conda_package_handling.api", api_module)
    monkeypatch.setattr("urllib.request.urlretrieve", fake_urlretrieve)

    names = _download_and_get_package_names(
        {
            "name": "pkg",
            "manager": "conda",
            "platform": "linux-64",
            "url": "https://example.com/pkg.conda",
        },
    )
    assert names is None


def test_download_and_get_package_names_returns_none_without_site_packages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlretrieve(_url: str, filename: str) -> None:
        Path(filename).write_text("archive")

    def fake_extract(
        _src: str,
        *,
        dest_dir: str,
        components: str | None = None,
    ) -> None:
        del components
        (Path(dest_dir) / "lib" / "python3.12").mkdir(parents=True)

    api_module = types.ModuleType("conda_package_handling.api")
    api_module.extract = fake_extract  # type: ignore[attr-defined]
    package_module = types.ModuleType("conda_package_handling")
    package_module.api = api_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "conda_package_handling", package_module)
    monkeypatch.setitem(sys.modules, "conda_package_handling.api", api_module)
    monkeypatch.setattr("urllib.request.urlretrieve", fake_urlretrieve)

    names = _download_and_get_package_names(
        {
            "name": "pkg",
            "manager": "conda",
            "platform": "linux-64",
            "url": "https://example.com/pkg.conda",
        },
    )
    assert names is None


def test_conda_lock_subpackages_skips_root_requirements(
    tmp_path: Path,
) -> None:
    root_req = tmp_path / "requirements.yaml"
    root_req.write_text("dependencies:\n  - numpy\n")
    subdir = tmp_path / "project"
    subdir.mkdir()
    sub_req = subdir / "requirements.yaml"
    sub_req.write_text("dependencies:\n  - pandas\n")

    conda_lock_file = tmp_path / "conda-lock.yml"
    yaml = YAML(typ="rt")
    with conda_lock_file.open("w") as fp:
        yaml.dump(
            {
                "metadata": {
                    "channels": [{"url": "conda-forge"}],
                    "platforms": ["linux-64"],
                },
                "package": [],
            },
            fp,
        )

    with patch(
        "unidep._conda_lock.find_requirements_files",
        return_value=[root_req, sub_req],
    ), patch(
        "unidep._conda_lock._conda_lock_subpackage",
        return_value=subdir / "conda-lock.yml",
    ) as mock:
        lock_files = _conda_lock_subpackages(tmp_path, 1, conda_lock_file)

    mock.assert_called_once()
    assert mock.call_args.kwargs["file"] == sub_req
    assert lock_files == [subdir / "conda-lock.yml"]


def test_check_consistent_lock_files_reports_mismatches(tmp_path: Path) -> None:
    global_lock = tmp_path / "global.yml"
    sub_lock = tmp_path / "sub.yml"
    lock_data = {
        "metadata": {"channels": [], "platforms": ["linux-64"]},
        "package": [
            {
                "name": "numpy",
                "platform": "linux-64",
                "manager": "conda",
                "version": "1.0",
            },
        ],
    }
    sub_data = {
        "metadata": {"channels": [], "platforms": ["linux-64"]},
        "package": [
            {
                "name": "numpy",
                "platform": "linux-64",
                "manager": "conda",
                "version": "2.0",
            },
        ],
    }
    yaml = YAML(typ="safe")
    with global_lock.open("w") as fp:
        yaml.dump(lock_data, fp)
    with sub_lock.open("w") as fp:
        yaml.dump(sub_data, fp)

    mismatches = _check_consistent_lock_files(global_lock, [sub_lock])
    assert len(mismatches) == 1
    assert mismatches[0].name == "numpy"
    assert mismatches[0].version == "2.0"
    assert mismatches[0].version_global == "1.0"


def test_conda_lock_subpackage_uses_selected_same_name_pip_winner(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        """\
        dependencies:
          - conda: foo
          - pip: foo >1
        """,
    )
    lock_spec = LockSpec(
        packages={
            ("pip", "linux-64", "foo"): {
                "name": "foo",
                "manager": "pip",
                "platform": "linux-64",
                "version": "2.0",
                "dependencies": {},
            },
        },
        dependencies={("pip", "linux-64", "foo"): set()},
    )

    output = _conda_lock_subpackage(
        file=req_file,
        lock_spec=lock_spec,
        channels=["conda-forge"],
        platforms=["linux-64"],
        yaml=YAML(typ="rt"),
    )

    assert "Missing keys" not in capsys.readouterr().out
    yaml = YAML(typ="safe")
    with output.open() as fp:
        data = yaml.load(fp)
    assert [(pkg["manager"], pkg["name"]) for pkg in data["package"]] == [
        ("pip", "foo"),
    ]


def test_conda_lock_subpackage_uses_selected_paired_different_name_pip_winner(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    req_file = tmp_path / "requirements.yaml"
    req_file.write_text(
        """\
        dependencies:
          - conda: python-graphviz
            pip: graphviz >1
        """,
    )
    lock_spec = LockSpec(
        packages={
            ("pip", "linux-64", "graphviz"): {
                "name": "graphviz",
                "manager": "pip",
                "platform": "linux-64",
                "version": "2.0",
                "dependencies": {},
            },
        },
        dependencies={("pip", "linux-64", "graphviz"): set()},
    )

    output = _conda_lock_subpackage(
        file=req_file,
        lock_spec=lock_spec,
        channels=["conda-forge"],
        platforms=["linux-64"],
        yaml=YAML(typ="rt"),
    )

    assert "Missing keys" not in capsys.readouterr().out
    yaml = YAML(typ="safe")
    with output.open() as fp:
        data = yaml.load(fp)
    assert [(pkg["manager"], pkg["name"]) for pkg in data["package"]] == [
        ("pip", "graphviz"),
    ]


def test_circular_dependency() -> None:
    """Test that circular dependencies are handled correctly.

    This test is based on the following requirements.yml file:

    ```yaml
    channels:
        - conda-forge
    dependencies:
        - sphinx
    platforms:
        - linux-64
    ```

    The sphinx package has a circular dependency to itself, e.g., `sphinx` depends
    on `sphinxcontrib-applehelp` which depends on `sphinx`.

    Then we called `unidep conda-lock` on the above requirements.yml file. The
    bit to reproduce the error is in the `package` list below.
    """
    package = [
        {
            "name": "sphinx",
            "manager": "conda",
            "platform": "linux-64",
            "dependencies": {"sphinxcontrib-applehelp": ""},
        },
        {
            "name": "sphinxcontrib-applehelp",
            "version": "1.0.8",
            "manager": "conda",
            "platform": "linux-64",
            "dependencies": {"sphinx": ">=5"},
        },
    ]
    lock_spec = _parse_conda_lock_packages(package)
    assert lock_spec.packages == {
        ("conda", "linux-64", "sphinx"): {
            "name": "sphinx",
            "manager": "conda",
            "platform": "linux-64",
            "dependencies": {"sphinxcontrib-applehelp": ""},
        },
        ("conda", "linux-64", "sphinxcontrib-applehelp"): {
            "name": "sphinxcontrib-applehelp",
            "version": "1.0.8",
            "manager": "conda",
            "platform": "linux-64",
            "dependencies": {"sphinx": ">=5"},
        },
    }
