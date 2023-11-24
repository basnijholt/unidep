# :rocket: UniDep - Unified Conda and Pip Dependency Management :rocket:

[![PyPI](https://img.shields.io/pypi/v/unidep.svg)](https://pypi.python.org/pypi/unidep)
[![Build Status](https://github.com/basnijholt/unidep/actions/workflows/pytest.yml/badge.svg)](https://github.com/basnijholt/unidep/actions/workflows/pytest.yml)
[![CodeCov](https://codecov.io/gh/basnijholt/unidep/branch/main/graph/badge.svg)](https://codecov.io/gh/basnijholt/unidep)

`unidep` simplifies Python project dependency management by enabling a single `requirements.yaml` file to handle both Conda and Pip dependencies.
This streamlined approach allows for creating a unified Conda `environment.yaml`, while also seamlessly integrating with `setup.py` or `pyproject.toml`.
In addition, it can be used as a CLI to combine multiple `requirements.yaml` files into a single `environment.yaml` file.
Simplify your setup and maintain all your dependencies in one place with `unidep`.

## :books: Table of Contents

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->

- [:package: Installation](#package-installation)
- [:page_facing_up: `requirements.yaml` structure](#page_facing_up-requirementsyaml-structure)
  - [Example](#example)
  - [Key Points](#key-points)
  - [Platform Selectors](#platform-selectors)
    - [Supported Selectors](#supported-selectors)
    - [Usage](#usage)
    - [Implementation](#implementation)
  - [Conflict Resolution](#conflict-resolution)
    - [How It Works](#how-it-works)
- [:memo: Usage](#memo-usage)
  - [With `pyproject.toml` or `setup.py`](#with-pyprojecttoml-or-setuppy)
  - [:memo: As a CLI](#memo-as-a-cli)
- [Limitations](#limitations)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## :package: Installation

To install `unidep`, run the following command:

```bash
pip install -U unidep
```

Or just copy the script to your project:
```bash
wget https://raw.githubusercontent.com/basnijholt/unidep/main/unidep.py
```

## :page_facing_up: `requirements.yaml` structure

`unidep` processes `requirements.yaml` files with a specific format:

- **name** (Optional): For documentation, not used in the output.
- **channels**: List of sources for packages, such as `conda-forge`.
- **dependencies**: Mix of Conda and Pip packages.

### Example

Example of a `requirements.yaml` file:

```yaml
name: example_environment
channels:
  - conda-forge
dependencies:
  - numpy  # same name on conda and pip
  - conda: python-graphviz  # When names differ between Conda and Pip
    pip: graphviz
  - pip: slurm-usage  # pip-only
  - conda: mumps  # conda-only
  # Use platform selectors; below only on linux64
  - conda: cuda-toolkit  # [linux64]
```

**⚠️ `unidep` can process this file in `pyproject.toml` or `setup.py` and create a `environment.yaml` file.**

### Key Points

- Standard names (e.g., `- numpy`) are assumed to be the same for Conda and Pip.
- Use `conda: <package>` and `pip: <package>` to specify different names across platforms.
- Use `pip:` to specify packages that are only available through Pip.
- Use `conda:` to specify packages that are only available through Conda.

Using the CLI `unidep` will combine these dependencies into a single conda installable `environment.yaml` file.

### Platform Selectors

This tool supports a range of platform selectors that allow for specific handling of dependencies based on the user's operating system and architecture. This feature is particularly useful for managing conditional dependencies in diverse environments.

#### Supported Selectors

The following selectors are supported:

- `linux`: For all Linux-based systems.
- `linux64`: Specifically for 64-bit Linux systems.
- `aarch64`: For Linux systems on ARM64 architectures.
- `ppc64le`: For Linux on PowerPC 64-bit Little Endian architectures.
- `osx`: For all macOS systems.
- `osx64`: Specifically for 64-bit macOS systems.
- `arm64`: For macOS systems on ARM64 architectures (Apple Silicon).
- `macos`: An alternative to `osx` for macOS systems.
- `unix`: A general selector for all UNIX-like systems (includes Linux and macOS).
- `win`: For all Windows systems.
- `win64`: Specifically for 64-bit Windows systems.

#### Usage

Selectors are used in `requirements.yaml` files to conditionally include dependencies based on the platform:

```yaml
dependencies:
  - some-package  # [unix]
  - another-package  # [win]
  - special-package  # [osx64]
  - pip: cirq  # [macos]
    conda: cirq  # [linux]
```

In this example:
- `some-package` is included only in UNIX-like environments (Linux and macOS).
- `another-package` is specific to Windows.
- `special-package` is included only for 64-bit macOS systems.
- `cirq` is managed by `pip` on macOS and by `conda` on Linux. This demonstrates how you can specify different package managers for the same package based on the platform.

#### Implementation

The tool parses these selectors and filters dependencies according to the platform where it's being run.
This is particularly useful for creating environment files that are portable across different platforms, ensuring that each environment has the appropriate dependencies installed.

### Conflict Resolution

`unidep` features a conflict resolution mechanism to manage version conflicts and platform-specific dependencies in `requirements.yaml` files. This functionality ensures optimal package version selection based on specified requirements.

#### How It Works

- **Version Pinning Priority**: `unidep` gives priority to version-pinned packages when multiple versions of the same package are specified. For instance, if both `foo` and `foo <1` are listed, `foo <1` is selected due to its specific version pin.

- **Minimal Scope Selection**: `unidep` resolves platform-specific dependency conflicts by preferring the version with the most limited platform scope. For instance, given `foo <1 # [linux64]` and `foo >1`, it installs `foo <1` exclusively on Linux-64 and `foo >1` on all other platforms. This approach ensures platform-specific requirements are precisely met.

- **Resolving Intractable Conflicts**: When conflicts are irreconcilable (e.g., `foo >1` vs. `foo <1`), `unidep` issues a warning and defaults to the first encountered specification.

## :memo: Usage

### With `pyproject.toml` or `setup.py`

To use `unidep` in your project, you can configure it in `pyproject.toml`. This setup works alongside a `requirements.yaml` file located in the same directory. The behavior depends on your project's setup:

- **When using only `pyproject.toml`**: The `dependencies` field in `pyproject.toml` will be automatically populated based on the contents of `requirements.yaml`.
- **When using `setup.py`**: The `install_requires` field in `setup.py` will be automatically populated, reflecting the dependencies defined in `requirements.yaml`.

Here's an example `pyproject.toml` configuration:

```toml
[build-system]
build-backend = "setuptools.build_meta"
requires = ["setuptools", "wheel", "unidep"]

[project]
dynamic = ["dependencies"]
```

In this configuration, `unidep` is included as a build requirement, allowing it to process the Python dependencies in the `requirements.yaml` file and update the project's dependencies accordingly.

### :memo: As a CLI

Use `unidep` to scan directories for `requirements.yaml` file(s) and combine them into an `environment.yaml` file.
See [example](example/) for more information or check the output of `unidep -h`:

<!-- CODE:BASH:START -->
<!-- echo '```bash' -->
<!-- unidep -h -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```bash
usage: unidep [-h] [-d DIRECTORY] [-o OUTPUT] [-n NAME] [--depth DEPTH]
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
- **Conda-Focused**: Best suited for Conda environments.

* * *

Try `unidep` today for a streamlined approach to managing your Conda environment dependencies across multiple projects! 🎉👏
