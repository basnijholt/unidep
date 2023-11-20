# :rocket: `conda-join` - Unified Conda and Pip Requirements Management :rocket:

[![PyPI](https://img.shields.io/pypi/v/conda-join.svg)](https://pypi.python.org/pypi/conda-join)
[![Build Status](https://github.com/basnijholt/conda-join/actions/workflows/pytest.yml/badge.svg)](https://github.com/basnijholt/conda-join/actions/workflows/pytest.yml)
[![CodeCov](https://codecov.io/gh/basnijholt/conda-join/branch/main/graph/badge.svg)](https://codecov.io/gh/basnijholt/conda-join)

`conda_join` simplifies Python project dependency management by enabling a single `requirements.yaml` file to handle both Conda and Pip dependencies.
This streamlined approach allows for creating a unified Conda `environment.yaml`, while also seamlessly integrating with `setup.py` or `pyproject.toml`.
In addition, it can be used as a CLI to combine multiple `requirements.yaml` files into a single `environment.yaml` file.
Simplify your setup and maintain all your dependencies in one place with `conda_join`.

## :books: Table of Contents

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->

  - [:package: Installation](#package-installation)
  - [:page_facing_up: `requirements.yaml` structure](#page_facing_up-requirementsyaml-structure)
    - [Basic Structure](#basic-structure)
    - [Example](#example)
    - [Explanation](#explanation)
  - [:memo: Usage](#memo-usage)
- [With `pyproject.toml` or `setup.py`](#with-pyprojecttoml-or-setuppy)
  - [:memo: As a CLI](#memo-as-a-cli)
  - [Limitations](#limitations)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## :package: Installation

To install `conda_join`, run the following command:

```bash
pip install -U conda_join
```

Or just copy the script to your computer:
```bash
wget https://raw.githubusercontent.com/basnijholt/requirements.yaml/main/conda_join.py
```

## :page_facing_up: `requirements.yaml` structure

The `requirements.yaml` files that `conda_join` processes should follow a specific structure for the tool to correctly interpret and combine them. Here's an overview of the expected format:

### Basic Structure
Each `requirements.yaml` file should contain the following key elements:

- **name**: (Optional) A name for the environment. This is not used in the combined output but can be helpful for documentation purposes.
- **channels**: A list of channels from which packages will be sourced. Commonly includes channels like `conda-forge`.
- **dependencies**: A list of package dependencies. This can include both Conda and Pip packages.

### Example
Example of a typical `requirements.yaml` file:

```yaml
name: example_environment
channels:
  - conda-forge
dependencies:
  - numpy  # same name on conda and pip
  - pandas  # same name on conda and pip
  - conda: scipy  # different name on conda and pip
    pip: scipy-package
  - pip: package3  # only available on pip
  - conda: mumps  # only available on conda
```

### Explanation

- Dependencies listed as simple strings (e.g., `- numpy`) are assumed to be similarly named Conda and pip packages.
- If a package is available through both Conda and Pip but with different names, you can specify both using the `conda: <conda_package>` and `pip: <pip_package>` format.
- Packages only available through Pip should be listed with the `pip:` prefix.
- Packages only available through Conda should be listed with the `conda:` prefix.

`conda_join` will combine these dependencies into a single `environment.yaml` file, structured as follows:

```yaml
name: some_name
channels:
  - conda-forge
dependencies:
  - numpy
  - pandas
  - scipy
  pip:
    - scipy-package
    - package3
```

## :memo: Usage

# With `pyproject.toml` or `setup.py`

To use `conda_join` in your project, you can configure it in `pyproject.toml`. This setup works alongside a `requirements.yaml` file located in the same directory. The behavior depends on your project's setup:

- **When using only `pyproject.toml`**: The `dependencies` field in `pyproject.toml` will be automatically populated based on the contents of `requirements.yaml`.
- **When using `setup.py`**: The `install_requires` field in `setup.py` will be automatically populated, reflecting the dependencies defined in `requirements.yaml`.

Here's an example `pyproject.toml` configuration:

```toml
[build-system]
build-backend = "setuptools.build_meta"
requires = ["setuptools", "wheel", "conda_join"]

[project]
dynamic = ["dependencies"]
```

In this configuration, `conda_join` is included as a build requirement, allowing it to process the Python dependencies in the `requirements.yaml` file and update the project's dependencies accordingly.

## :memo: As a CLI

Use `conda_join` to scan directories for `requirements.yaml` file(s) and combine them into an `environment.yaml` file. See[example](example/) for more information or check the output of `conda_join -h`:

<!-- CODE:BASH:START -->
<!-- echo '```bash' -->
<!-- conda-join -h -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```bash
usage: conda-join [-h] [-d DIRECTORY] [-o OUTPUT] [-n NAME] [--depth DEPTH]
                  [--stdout] [-v]

Unified Conda and Pip requirements management.

options:
  -h, --help            show this help message and exit
  -d DIRECTORY, --directory DIRECTORY
                        Base directory to scan for requirements.yaml files, by
                        default `.`
  -o OUTPUT, --output OUTPUT
                        Output file for the conda environment, by default
                        `environment.yaml`
  -n NAME, --name NAME  Name of the conda environment, by default `myenv`
  --depth DEPTH         Depth to scan for requirements.yaml files, by default
                        1
  --stdout              Output to stdout instead of a file
  -v, --verbose         Print verbose output
```

<!-- OUTPUT:END -->

## Limitations
- **No Conflict Resolution**: Doesn't resolve version conflicts between different `requirements.yaml` files.
- **Conda-Focused**: Best suited for Conda environments.

* * *

Try `conda_join` today for a streamlined approach to managing your Conda environment dependencies across multiple projects! 🎉👏
