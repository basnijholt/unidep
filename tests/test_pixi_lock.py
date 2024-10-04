"""unidep pixi-lock tests."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

from ruamel.yaml import YAML

from unidep._pixi_lock import pixi_lock_command


def test_conda_lock_command(tmp_path: Path) -> None:
    folder = tmp_path / "simple_monorepo"
    shutil.copytree(Path(__file__).parent / "simple_monorepo", folder)
    with patch("unidep._conda_lock._run_pixi_lock", return_value=None):
        pixi_lock_command(
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
