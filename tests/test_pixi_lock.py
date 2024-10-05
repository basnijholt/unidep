"""unidep pixi-lock tests."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

from ruamel.yaml import YAML

from unidep._pixi_lock import pixi_lock_command


def test_pixi_lock_command(tmp_path: Path) -> None:
    folder = tmp_path / "simple_monorepo"
    shutil.copytree(Path(__file__).parent / "simple_monorepo", folder)
    with patch("unidep._pixi_lock._run_pixi_lock", return_value=None):
        pixi_lock_command(
            depth=1,
            directory=folder,
            files=None,
            platforms=["osx-64", "osx-arm64"],
            verbose=True,
            only_global=False,
            ignore_pins=[],
            overwrite_pins=[],
            skip_dependencies=[],
            extra_flags=["--", "--micromamba"],
        )
    with YAML(typ="safe") as yaml:
        with (folder / "project1" / "pixi.lock").open() as f:
            lock1 = yaml.load(f)
        with (folder / "project2" / "pixi.lock").open() as f:
            lock2 = yaml.load(f)
    assert lock1["environments"]["default"]["packages"] == {
        "osx-64": [
            {
                "conda": "https://conda.anaconda.org/conda-forge/osx-64/bzip2-1.0.8-hfdf4475_7.conda",
            },
            {
                "conda": "https://conda.anaconda.org/conda-forge/osx-64/python_abi-3.13-5_cp313t.conda",
            },
        ],
        "osx-arm64": [
            {
                "conda": "https://conda.anaconda.org/conda-forge/osx-arm64/bzip2-1.0.8-h99b78c6_7.conda",
            },
            {
                "conda": "https://conda.anaconda.org/conda-forge/noarch/tzdata-2024b-hc8b5060_0.conda",
            },
            {
                "conda": "https://conda.anaconda.org/conda-forge/osx-arm64/python_abi-3.13-5_cp313t.conda",
            },
        ],
    }
    assert lock2["environments"]["default"]["packages"] == {
        "osx-64": [
            {
                "conda": "https://conda.anaconda.org/conda-forge/osx-64/python_abi-3.13-5_cp313t.conda",
            },
        ],
        "osx-arm64": [
            {
                "conda": "https://conda.anaconda.org/conda-forge/noarch/tzdata-2024b-hc8b5060_0.conda",
            },
            {
                "conda": "https://conda.anaconda.org/conda-forge/osx-arm64/python_abi-3.13-5_cp313t.conda",
            },
        ],
    }
