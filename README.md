# 🚀 UniDep - Unified Conda and Pip Dependency Management 🚀

![](https://media.githubusercontent.com/media/basnijholt/nijho.lt/main/content/project/unidep/featured.png)

[![PyPI](https://img.shields.io/pypi/v/unidep.svg)](https://pypi.python.org/pypi/unidep)
[![Build Status](https://github.com/basnijholt/unidep/actions/workflows/pytest.yml/badge.svg)](https://github.com/basnijholt/unidep/actions/workflows/pytest.yml)
[![CodeCov](https://codecov.io/gh/basnijholt/unidep/branch/main/graph/badge.svg)](https://codecov.io/gh/basnijholt/unidep)

`unidep` streamlines Python project dependency management by allowing a single `requirements.yaml` file to handle both Conda and Pip dependencies.
This approach enables the creation of a unified Conda `environment.yaml`, while also integrating with `setup.py` or `pyproject.toml`.
As a command-line interface (CLI) tool, `unidep` merges multiple `requirements.yaml` files into a consolidated `environment.yaml`, and supports generating consistent [`conda-lock` files](https://conda.github.io/conda-lock/output/), which is particularly useful for monorepos.
Additionally, it facilitates the installation of Conda, Pip, and local dependencies with a single `unidep install` command.
With `unidep`, manage all your dependencies efficiently in one place.

## :rocket: Features

- **🔗 Unified Management**: Single-file handling of Conda and Pip dependencies.
- **⚙️ Project Tool Integration**: Easily works with `pyproject.toml` and `setup.py`, so `requirements.yaml` is used during `pip install`.
- **🏢 Monorepo Support**: Merge multiple `requirements.yaml` into one Conda environment `environment.yaml` using the CLI tool and maintain a global and per-package `conda-lock` files.
- **🌍 Platform-Specific Support**: Specify dependencies for different operating systems or architectures.
- **🛠️ Conflict Resolution**: Simplifies complex dependency management by resolving version conflicts.
- **🔄 `unidep install` CLI**: Automates installation of Conda, Pip, and local package dependencies.

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
    - [`unidep merge`](#unidep-merge)
    - [`unidep install`](#unidep-install)
    - [`unidep install-all`](#unidep-install-all)
    - [`unidep conda-lock`](#unidep-conda-lock)
    - [`unidep pip`](#unidep-pip)
    - [`unidep conda`](#unidep-conda)
- [Limitations](#limitations)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## :package: Installation

To install `unidep`, run the following command:

```bash
pip install -U unidep
```

or

```bash
conda install -c conda-forge unidep
```

## :page_facing_up: `requirements.yaml` structure

`unidep` processes `requirements.yaml` files with a specific format (similar but _**not**_ the same as a Conda `environment.yaml` file):

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
platforms:  # (Optional) specify platforms that are supported (like conda-lock)
  - linux-64
  - osx-arm64
includes:
  - ../other-project-using-unidep  # include other projects that use unidep
  - ../common-requirements.yaml  # include other requirements.yaml files
```

**⚠️ `unidep` can process this file in `pyproject.toml` or `setup.py` and create a `environment.yaml` file.**

For a more in-depth example, see the [`example`](example/) directory.

### Key Points

- Standard names (e.g., `- numpy`) are assumed to be the same for Conda and Pip.
- Use `conda: <package>` and `pip: <package>` to specify different names across platforms.
- Use `pip:` to specify packages that are only available through Pip.
- Use `conda:` to specify packages that are only available through Conda.
- Use `# [selector]` to specify platform-specific dependencies.
- Use `platforms:` to specify the platforms that are supported.
- Use `includes:` to include other `requirements.yaml` files and merge them into one.

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
requires = ["setuptools", "unidep"]

[project]
dynamic = ["dependencies"]
```

In this configuration, `unidep` is included as a build requirement, allowing it to process the Python dependencies in the `requirements.yaml` file and update the project's dependencies accordingly.

### :memo: As a CLI

See [example](example/) for more information or check the output of `unidep -h` for the available sub commands:

<!-- CODE:BASH:START -->
<!-- echo '```bash' -->
<!-- unidep -h -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```bash
usage: unidep [-h]
              {merge,install,install-all,conda-lock,pip,conda,version} ...

Unified Conda and Pip requirements management.

positional arguments:
  {merge,install,install-all,conda-lock,pip,conda,version}
                        Subcommands
    merge               Combine multiple (or a single) `requirements.yaml`
                        files into a single Conda installable
                        `environment.yaml` file.
    install             Automatically install all dependencies from one or
                        more `requirements.yaml` files. This command first
                        installs dependencies with Conda, then with Pip.
                        Finally, it installs local packages (those containing
                        the `requirements.yaml` files) using `pip install [-e]
                        ./project`.
    install-all         Install dependencies from all `requirements.yaml`
                        files found in the current directory or specified
                        directory. This command first installs dependencies
                        using Conda, then Pip, and finally the local packages.
    conda-lock          Generate a global `conda-lock.yml` file for a
                        collection of `requirements.yaml` files. Additionally,
                        create individual `conda-lock.yml` files for each
                        `requirements.yaml` file consistent with the global
                        lock file.
    pip                 Get the pip requirements for the current platform
                        only.
    conda               Get the conda requirements for the current platform
                        only.
    version             Print version information of unidep.

options:
  -h, --help            show this help message and exit
```

<!-- OUTPUT:END -->

#### `unidep merge`

Use `unidep merge` to scan directories for `requirements.yaml` file(s) and combine them into an `environment.yaml` file.
See `unidep merge -h` for more information:

<!-- CODE:BASH:START -->
<!-- echo '```bash' -->
<!-- unidep merge -h -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```bash
usage: unidep merge [-h] [-o OUTPUT] [-n NAME] [--stdout]
                    [--selector {sel,comment}] [-d DIRECTORY] [-v]
                    [--platform {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}]
                    [--depth DEPTH]

Combine multiple (or a single) `requirements.yaml` files into a single Conda
installable `environment.yaml` file. Example usage: `unidep merge --directory
. --depth 1 --output environment.yaml` to search for `requirements.yaml` files
in the current directory and its subdirectories and create `environment.yaml`.
These are the defaults, so you can also just run `unidep merge`.

options:
  -h, --help            show this help message and exit
  -o OUTPUT, --output OUTPUT
                        Output file for the conda environment, by default
                        `environment.yaml`
  -n NAME, --name NAME  Name of the conda environment, by default `myenv`
  --stdout              Output to stdout instead of a file
  --selector {sel,comment}
                        The selector to use for the environment markers, if
                        `sel` then `- numpy # [linux]` becomes `sel(linux):
                        numpy`, if `comment` then it remains `- numpy #
                        [linux]`, by default `sel`
  -d DIRECTORY, --directory DIRECTORY
                        Base directory to scan for `requirements.yaml`
                        file(s), by default `.`
  -v, --verbose         Print verbose output
  --platform {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}, -p {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}
                        The platform(s) to get the requirements for. Multiple
                        platforms can be specified. By default, the current
                        platform (`linux-64`) is used.
  --depth DEPTH         Maximum depth to scan for `requirements.yaml` files,
                        by default 1
```

<!-- OUTPUT:END -->

#### `unidep install`

Use `unidep install` on one or more `requirements.yaml` files and install the dependencies on the current platform using conda, then install the remaining dependencies with pip, and finally install the current package with `pip install [-e] .`.
See `unidep install -h` for more information:

<!-- CODE:BASH:START -->
<!-- echo '```bash' -->
<!-- unidep install -h -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```bash
usage: unidep install [-h] [-v] [-e] [--skip-local] [--skip-pip]
                      [--skip-conda] [--no-dependencies]
                      [--conda-executable {conda,mamba,micromamba}]
                      [--dry-run]
                      files [files ...]

Automatically install all dependencies from one or more `requirements.yaml`
files. This command first installs dependencies with Conda, then with Pip.
Finally, it installs local packages (those containing the `requirements.yaml`
files) using `pip install [-e] ./project`. Example usage: `unidep install
requirements.yaml` for a single file. For multiple files or folders: `unidep
install ./project1 ./project2`. The command accepts both file paths and
directories containing a `requirements.yaml` file. Use `--editable` or `-e` to
install the local packages in editable mode. See `unidep install-all` to
install all `requirements.yaml` in the current folder.

positional arguments:
  files                 The `requirements.yaml` file(s) to parse or folder(s)
                        that contain those file(s), by default `.`

options:
  -h, --help            show this help message and exit
  -v, --verbose         Print verbose output
  -e, --editable        Install the project in editable mode
  --skip-local          Skip installing local dependencies
  --skip-pip            Skip installing pip dependencies from
                        `requirements.yaml`
  --skip-conda          Skip installing conda dependencies from
                        `requirements.yaml`
  --no-dependencies     Skip installing dependencies from `requirements.yaml`
                        file(s) and only install local package(s). This passes
                        the `--no-dependencies` flag to `pip install`. Useful
                        after installing a `conda-lock.yml` file because then
                        all dependencies have already been installed.
  --conda-executable {conda,mamba,micromamba}
                        The conda executable to use
  --dry-run, --dry      Only print the commands that would be run
```

<!-- OUTPUT:END -->

#### `unidep install-all`

Use `unidep install-all` on a folder with packages that contain `requirements.yaml` files and install the dependencies on the current platform using conda, then install the remaining dependencies with pip, and finally install the current package with `pip install [-e] ./package1 ./package2`.
See `unidep install -h` for more information:

<!-- CODE:BASH:START -->
<!-- echo '```bash' -->
<!-- unidep install -h -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```bash
usage: unidep install [-h] [-v] [-e] [--skip-local] [--skip-pip]
                      [--skip-conda] [--no-dependencies]
                      [--conda-executable {conda,mamba,micromamba}]
                      [--dry-run]
                      files [files ...]

Automatically install all dependencies from one or more `requirements.yaml`
files. This command first installs dependencies with Conda, then with Pip.
Finally, it installs local packages (those containing the `requirements.yaml`
files) using `pip install [-e] ./project`. Example usage: `unidep install
requirements.yaml` for a single file. For multiple files or folders: `unidep
install ./project1 ./project2`. The command accepts both file paths and
directories containing a `requirements.yaml` file. Use `--editable` or `-e` to
install the local packages in editable mode. See `unidep install-all` to
install all `requirements.yaml` in the current folder.

positional arguments:
  files                 The `requirements.yaml` file(s) to parse or folder(s)
                        that contain those file(s), by default `.`

options:
  -h, --help            show this help message and exit
  -v, --verbose         Print verbose output
  -e, --editable        Install the project in editable mode
  --skip-local          Skip installing local dependencies
  --skip-pip            Skip installing pip dependencies from
                        `requirements.yaml`
  --skip-conda          Skip installing conda dependencies from
                        `requirements.yaml`
  --no-dependencies     Skip installing dependencies from `requirements.yaml`
                        file(s) and only install local package(s). This passes
                        the `--no-dependencies` flag to `pip install`. Useful
                        after installing a `conda-lock.yml` file because then
                        all dependencies have already been installed.
  --conda-executable {conda,mamba,micromamba}
                        The conda executable to use
  --dry-run, --dry      Only print the commands that would be run
```

<!-- OUTPUT:END -->

#### `unidep conda-lock`

Use `unidep conda-lock` on one or multiple `requirements.yaml` files and output the conda-lock file.
Optionally, when using a monorepo with multiple subpackages (with their own `requirements.yaml` files), generate a lock file for each subpackage.
See `unidep conda -h` for more information:

<!-- CODE:BASH:START -->
<!-- echo '```bash' -->
<!-- unidep conda-lock -h -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```bash
usage: unidep conda-lock [-h] [--only-global] [--check-input-hash]
                         [-d DIRECTORY] [-v]
                         [--platform {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}]
                         [--depth DEPTH]

Generate a global `conda-lock.yml` file for a collection of
`requirements.yaml` files. Additionally, create individual `conda-lock.yml`
files for each `requirements.yaml` file consistent with the global lock file.
Example usage: `unidep conda-lock --directory ./projects` to generate conda-
lock files for all `requirements.yaml` files in the `./projects` directory.
Use `--only-global` to generate only the global lock file. The `--check-input-
hash` option can be used to avoid regenerating lock files if the input hasn't
changed.

options:
  -h, --help            show this help message and exit
  --only-global         Only generate the global lock file
  --check-input-hash    Check existing input hashes in lockfiles before
                        regenerating lock files. This flag is directly passed
                        to `conda-lock`.
  -d DIRECTORY, --directory DIRECTORY
                        Base directory to scan for `requirements.yaml`
                        file(s), by default `.`
  -v, --verbose         Print verbose output
  --platform {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}, -p {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}
                        The platform(s) to get the requirements for. Multiple
                        platforms can be specified. By default, the current
                        platform (`linux-64`) is used.
  --depth DEPTH         Maximum depth to scan for `requirements.yaml` files,
                        by default 1
```

<!-- OUTPUT:END -->

#### `unidep pip`

Use `unidep pip` on a `requirements.yaml` file and output the pip installable dependencies on the current platform (default).
See `unidep pip -h` for more information:

<!-- CODE:BASH:START -->
<!-- echo '```bash' -->
<!-- unidep pip -h -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```bash
usage: unidep pip [-h] [-f FILE] [-v]
                  [--platform {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}]
                  [--separator SEPARATOR]

Get the pip requirements for the current platform only. Example usage: `unidep
pip --file folder1 --file folder2/requirements.yaml --seperator ' ' --platform
linux-64` to extract all the pip dependencies specific to the linux-64
platform. Note that the `--file` argument can be used multiple times to
specify multiple `requirements.yaml` files and that --file can also be a
folder that contains a `requirements.yaml` file.

options:
  -h, --help            show this help message and exit
  -f FILE, --file FILE  The `requirements.yaml` file to parse or folder that
                        contains that file, by default `requirements.yaml`
  -v, --verbose         Print verbose output
  --platform {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}, -p {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}
                        The platform(s) to get the requirements for. Multiple
                        platforms can be specified. By default, the current
                        platform (`linux-64`) is used.
  --separator SEPARATOR
                        The separator between the dependencies, by default ` `
```

<!-- OUTPUT:END -->

#### `unidep conda`

Use `unidep conda` on a `requirements.yaml` file and output the conda installable dependencies on the current platform (default).
See `unidep conda -h` for more information:

<!-- CODE:BASH:START -->
<!-- echo '```bash' -->
<!-- unidep conda -h -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```bash
usage: unidep conda [-h] [-f FILE] [-v]
                    [--platform {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}]
                    [--separator SEPARATOR]

Get the conda requirements for the current platform only. Example usage:
`unidep conda --file folder1 --file folder2/requirements.yaml --seperator ' '
--platform linux-64` to extract all the conda dependencies specific to the
linux-64 platform. Note that the `--file` argument can be used multiple times
to specify multiple `requirements.yaml` files and that --file can also be a
folder that contains a `requirements.yaml` file.

options:
  -h, --help            show this help message and exit
  -f FILE, --file FILE  The `requirements.yaml` file to parse or folder that
                        contains that file, by default `requirements.yaml`
  -v, --verbose         Print verbose output
  --platform {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}, -p {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}
                        The platform(s) to get the requirements for. Multiple
                        platforms can be specified. By default, the current
                        platform (`linux-64`) is used.
  --separator SEPARATOR
                        The separator between the dependencies, by default ` `
```

<!-- OUTPUT:END -->

## Limitations
- **Conda-Focused**: Best suited for Conda environments.

* * *

Try `unidep` today for a streamlined approach to managing your Conda environment dependencies across multiple projects! 🎉👏
