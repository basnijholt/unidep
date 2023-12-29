# 🚀 UniDep - Unified Conda and Pip Dependency Management 🚀

![UniDep logo](https://media.githubusercontent.com/media/basnijholt/nijho.lt/main/content/project/unidep/featured.png)

[![PyPI](https://img.shields.io/pypi/v/unidep.svg)](https://pypi.python.org/pypi/unidep)
[![Build Status](https://github.com/basnijholt/unidep/actions/workflows/pytest.yml/badge.svg)](https://github.com/basnijholt/unidep/actions/workflows/pytest.yml)
[![CodeCov](https://codecov.io/gh/basnijholt/unidep/branch/main/graph/badge.svg)](https://codecov.io/gh/basnijholt/unidep)
[![GitHub Repo stars](https://img.shields.io/github/stars/basnijholt/unidep)](https://github.com/basnijholt/unidep)
[![Documentation](https://readthedocs.org/projects/unidep/badge/?version=latest)](https://unidep.readthedocs.io/)

> UniDep streamlines Python project dependency management by unifying Conda and Pip packages in a single system.
> [Learn when to use UniDep](#q-when-to-use-unidep) in our [FAQ](#-faq).

Handling dependencies in Python projects can be challenging, especially when juggling Python and non-Python packages.
This often leads to confusion and inefficiency, as developers juggle between multiple dependency files.

- **📝 Unified Dependency File**: Use either `requirements.yaml` or `pyproject.toml` to manage both Conda and Pip dependencies in one place.
- **⚙️ Build System Integration**: Integrates with Setuptools and Hatchling for automatic dependency handling during `pip install ./your-package`.
- **💻 One-Command Installation**: `unidep install` handles Conda, Pip, and local dependencies effortlessly.
- **🏢 Monorepo-Friendly**: Render (multiple) `requirements.yaml` or `pyproject.toml` files into one Conda `environment.yaml` file and maintain fully consistent global *and* per sub package `conda-lock` files.
- **🌍 Platform-Specific Support**: Specify dependencies for different operating systems or architectures.
- **🔧 `pip-compile` Integration**: Generate fully pinned `requirements.txt` files from `requirements.yaml` or `pyproject.toml` files using `pip-compile`.
- **🔒 Integration with `conda-lock`**: Generate fully pinned `conda-lock.yml` files from (multiple) `requirements.yaml` or `pyproject.toml` file(s), leveraging `conda-lock`.

`unidep` is designed to make dependency management in Python projects as simple and efficient as possible.
Try it now and streamline your development process!

> [!TIP]
> Check out the [example `requirements.yaml` and `pyproject.toml` below](#example).

<!-- toc-start -->

## :books: Table of Contents

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->

- [:package: Installation](#package-installation)
- [:memo: `requirements.yaml` and `pyproject.toml` structure](#memo-requirementsyaml-and-pyprojecttoml-structure)
  - [Example](#example)
    - [Example `requirements.yaml`](#example-requirementsyaml)
    - [Example `pyproject.toml`](#example-pyprojecttoml)
  - [Key Points](#key-points)
  - [Supported Version Pinnings](#supported-version-pinnings)
  - [Conflict Resolution](#conflict-resolution)
    - [How It Works](#how-it-works)
  - [Platform Selectors](#platform-selectors)
    - [Supported Selectors](#supported-selectors)
    - [Usage](#usage)
    - [Implementation](#implementation)
- [:jigsaw: Build System Integration](#jigsaw-build-system-integration)
  - [Example packages](#example-packages)
  - [Setuptools Integration](#setuptools-integration)
  - [Hatchling Integration](#hatchling-integration)
- [:desktop_computer: As a CLI](#desktop_computer-as-a-cli)
  - [`unidep merge`](#unidep-merge)
  - [`unidep install`](#unidep-install)
  - [`unidep install-all`](#unidep-install-all)
  - [`unidep conda-lock`](#unidep-conda-lock)
  - [`unidep pip-compile`](#unidep-pip-compile)
  - [`unidep pip`](#unidep-pip)
  - [`unidep conda`](#unidep-conda)
- [❓ FAQ](#-faq)
  - [**Q: When to use UniDep?**](#q-when-to-use-unidep)
  - [**Q: Just show me a full example!**](#q-just-show-me-a-full-example)
  - [**Q: How is this different from conda/mamba/pip?**](#q-how-is-this-different-from-condamambapip)
  - [**Q: I found a project using unidep, now what?**](#q-i-found-a-project-using-unidep-now-what)
  - [**Q: How to handle local dependencies that do not use UniDep?**](#q-how-to-handle-local-dependencies-that-do-not-use-unidep)
  - [**Q: Can't Conda already do this?**](#q-cant-conda-already-do-this)
  - [**Q: What is the difference between `conda-lock` and `unidep conda-lock`?**](#q-what-is-the-difference-between-conda-lock-and-unidep-conda-lock)
- [:hammer_and_wrench: Troubleshooting](#hammer_and_wrench-troubleshooting)
  - [`pip install` fails with `FileNotFoundError`](#pip-install-fails-with-filenotfounderror)
- [:warning: Limitations](#warning-limitations)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

<!-- toc-end -->

## :package: Installation

To install `unidep`, run the following command:

```bash
pip install "unidep[all]"
```

or

```bash
conda install -c conda-forge unidep
```

## :memo: `requirements.yaml` and `pyproject.toml` structure

`unidep` allows either using a
1. `requirements.yaml` file with a specific format (similar but _**not**_ the same as a Conda `environment.yaml` file) or
2. `pyproject.toml` file with a `[tool.unidep]` section.

Both files contain the following keys:

- **name** (Optional): For documentation, not used in the output.
- **channels**: List of conda channels for packages, such as `conda-forge`.
- **dependencies**: Mix of Conda and Pip packages.
- **local_dependencies** (Optional): List of paths to other `requirements.yaml` or `pyproject.toml` files to include.
- **platforms** (Optional): List of platforms that are supported (used in `conda-lock`).

Whether you use a `requirements.yaml` or `pyproject.toml` file, the same information can be specified in either.
Choose the format that works best for your project.

### Example
#### Example `requirements.yaml`

Example of a `requirements.yaml` file:

```yaml
name: example_environment
channels:
  - conda-forge
dependencies:
  - numpy                   # same name on conda and pip
  - conda: python-graphviz  # When names differ between Conda and Pip
    pip: graphviz
  - pip: slurm-usage >=1.1.0,<2  # pip-only
  - conda: mumps                 # conda-only
  # Use platform selectors
  - conda: cuda-toolkit =11.8    # [linux64]
local_dependencies:
  - ../other-project-using-unidep     # include other projects that use unidep
  - ../common-requirements.yaml       # include other requirements.yaml files
  - ../project-not-managed-by-unidep  # 🚨 Skips its dependencies!
platforms:  # (Optional) specify platforms that are supported (used in conda-lock)
  - linux-64
  - osx-arm64
```

> [!IMPORTANT]
> `unidep` can process this during `pip install` and create a Conda installable `environment.yaml` or `conda-lock.yml` file, and more!

> [!NOTE]
> For a more in-depth example containing multiple installable projects, see the [`example`](example/) directory.

#### Example `pyproject.toml`

***Alternatively***, one can fully configure the dependencies in the `pyproject.toml` file in the `[tool.unidep]` section:

```toml
[tool.unidep]
channels = ["conda-forge"]
dependencies = [
    "numpy",                                         # same name on conda and pip
    { conda = "python-graphviz", pip = "graphviz" }, # When names differ between Conda and Pip
    { pip = "slurm-usage >=1.1.0,<2" },              # pip-only
    { conda = "mumps" },                             # conda-only
    { conda = "cuda-toolkit =11.8:linux64" }         # Use platform selectors by appending `:linux64`
]
local_dependencies = [
    "../other-project-using-unidep",   # include other projects that use unidep
    "../common-requirements.yaml"      # include other requirements.yaml files
    "../project-not-managed-by-unidep" # 🚨 Skips its dependencies!
]
platforms = [ # (Optional) specify platforms that are supported (used in conda-lock)
    "linux-64",
    "osx-arm64"
]
```

This data structure is *identical* to the `requirements.yaml` format, with the exception of the `name` field and the [platform selectors](#platform-selectors).
In the `requirements.yaml` file, one can use e.g., `# [linux64]`, which in the `pyproject.toml` file is `:linux64` at the end of the package name.

See [Build System Integration](#jigsaw-build-system-integration) for more information on how to set up `unidep` with different build systems (Setuptools or Hatchling).

> [!IMPORTANT]
> In these docs, we often mention the `requirements.yaml` format for simplicity, but the same information can be specified in `pyproject.toml` as well.
> Everything that is possible in `requirements.yaml` is also possible in `pyproject.toml`!

### Key Points

- Standard names (e.g., `- numpy`) are assumed to be the same for Conda and Pip.
- Use a dictionary with `conda: <package>` *and* `pip: <package>` to specify different names across platforms.
- Use `pip:` to specify packages that are only available through Pip.
- Use `conda:` to specify packages that are only available through Conda.
- Use `# [selector]` (YAML only) or `package:selector` to specify platform-specific dependencies.
- Use `platforms:` to specify the platforms that are supported.
- Use `local_dependencies:` to include other `requirements.yaml` or `pyproject.toml` files and merge them into one. Also allows projects that are not managed by `unidep` to be included, but be aware that this skips their dependencies!

> *We use the YAML notation here, but the same information can be specified in `pyproject.toml` as well.*

### Supported Version Pinnings

UniDep supports a range of version pinning operators (the same as Conda):

- **Standard Version Constraints**: Specify exact versions or ranges with standard operators like `=`, `>`, `<`, `>=`, `<=`.
  - Example: `=1.0.0`, `>1.0.0, <2.0.0`.

- **Version Exclusions**: Exclude specific versions using `!=`.
  - Example: `!=1.5.0`.

- **Redundant Pinning Resolution**: Automatically resolves redundant version specifications.
  - Example: `>1.0.0, >0.5.0` simplifies to `>1.0.0`.

- **Contradictory Version Detection**: Errors are raised for contradictory pinnings to maintain dependency integrity. See the [Conflict Resolution](#conflict-resolution) section for more information.
  - Example: Specifying `>2.0.0, <1.5.0` triggers a `VersionConflictError`.

- **Invalid Pinning Detection**: Detects and raises errors for unrecognized or improperly formatted version specifications.

- **Conda Build Pinning**: UniDep also supports Conda's build pinning, allowing you to specify builds in your pinning patterns.
  - Example: Conda supports pinning builds like `qsimcirq * cuda*` or `vtk * *egl*`.
  - **Limitation**: While UniDep allows such build pinning, it requires that there be a single pin per package. UniDep cannot resolve conflicts where multiple build pinnings are specified for the same package.
    - Example: UniDep can handle `qsimcirq * cuda*`, but it cannot resolve a scenario with both `qsimcirq * cuda*` and `qsimcirq * cpu*`.

- **Other Special Cases**: In addition to Conda build pins, UniDep supports all special pinning formats, such as VCS (Version Control System) URLs or local file paths. This includes formats like `package @ git+https://git/repo/here` or `package @ file:///path/to/package`. However, UniDep has a limitation: it can handle only one special pin per package. These special pins can be combined with an unpinned version specification, but not with multiple special pin formats for the same package.
  - Example: UniDep can manage dependencies specified as `package @ git+https://git/repo/here` and `package` in the same `requirements.yaml`. However, it cannot resolve scenarios where both `package @ git+https://git/repo/here` and `package @ file:///path/to/package` are specified for the same package.

> [!WARNING]
> **Pinning Validation and Combination**: UniDep actively validates and/or combines pinnings only when **multiple different pinnings** are specified for the same package.
> This means if your `requirements.yaml` files include multiple pinnings for a single package, UniDep will attempt to resolve them into a single, coherent specification.
> However, if the pinnings are contradictory or incompatible, UniDep will raise an error to alert you of the conflict.

### Conflict Resolution

`unidep` features a conflict resolution mechanism to manage version conflicts and platform-specific dependencies in `requirements.yaml` or `pyproject.toml` files.

#### How It Works

- **Version Pinning Priority**: `unidep` gives priority to version-pinned packages when the same package is specified multiple times. For instance, if both `foo` and `foo <1` are listed, `foo <1` is selected due to its specific version pin.

- **Platform-Specific Version Pinning**: `unidep` resolves platform-specific dependency conflicts by preferring the version with the narrowest platform scope. For instance, given `foo <3 # [linux64]` and `foo >1`, it installs `foo >1,<3` exclusively on Linux-64 and `foo >1` on all other platforms.

- **Intractable Conflicts**: When conflicts are irreconcilable (e.g., `foo >1` vs. `foo <1`), `unidep` raises an exception.

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
  - some-package >=1  # [unix]
  - another-package   # [win]
  - special-package   # [osx64]
  - pip: cirq         # [macos win]
    conda: cirq       # [linux]
```

Or when using `pyproject.toml` instead of `requirements.yaml`:

```toml
[tool.unidep]
dependencies = [
    "some-package >=1:unix",
    "another-package:win",
    "special-package:osx64",
    { pip = "cirq:macos win", conda = "cirq:linux" },
]
```

In this example:

- `some-package` is included only in UNIX-like environments (Linux and macOS).
- `another-package` is specific to Windows.
- `special-package` is included only for 64-bit macOS systems.
- `cirq` is managed by `pip` on macOS and Windows, and by `conda` on Linux. This demonstrates how you can specify different package managers for the same package based on the platform.

Note that the `package-name:unix` syntax can also be used in the `requirements.yaml` file, but the `package-name # [unix]` syntax is not supported in `pyproject.toml`.

#### Implementation

`unidep` parses these selectors and filters dependencies according to the platform where it's being installed.
It is also used for creating environment and lock files that are portable across different platforms, ensuring that each environment has the appropriate dependencies installed.

## :jigsaw: Build System Integration

> [!TIP]
> See [`example/`](example/) for working examples of using `unidep` with different build systems.

`unidep` seamlessly integrates with popular Python build systems to simplify dependency management in your projects.

### Example packages

Explore these installable [example](example/) packages to understand how `unidep` integrates with different build tools and configurations:

| Project                                                    | Build Tool   | `pyproject.toml` | `requirements.yaml` | `setup.py` |
| ---------------------------------------------------------- | ------------ | ---------------- | ------------------- | ---------- |
| [`setup_py_project`](example/setup_py_project)             | `setuptools` | ✅                | ✅                   | ✅          |
| [`setuptools_project`](example/setuptools_project)         | `setuptools` | ✅                | ✅                   | ❌          |
| [`pyproject_toml_project`](example/pyproject_toml_project) | `setuptools` | ✅                | ❌                   | ❌          |
| [`hatch_project`](example/hatch_project)                   | `hatch`      | ✅                | ✅                   | ❌          |
| [`hatch2_project`](example/hatch2_project)                 | `hatch`      | ✅                | ❌                   | ❌          |

### Setuptools Integration

For projects using `setuptools`, configure `unidep` in `pyproject.toml` and either specify dependencies in a `requirements.yaml` file or include them in `pyproject.toml` too.

- **Using `pyproject.toml` only**: The `[project.dependencies]` field in `pyproject.toml` gets automatically populated from `requirements.yaml` or from the `[tool.unidep]` section in `pyproject.toml`.
- **Using `setup.py`**: The `install_requires` field in `setup.py` automatically reflects dependencies specified in `requirements.yaml` or `pyproject.toml`.

**Example `pyproject.toml` Configuration**:

```toml
[build-system]
build-backend = "setuptools.build_meta"
requires = ["setuptools", "unidep"]

[project]
dynamic = ["dependencies"]
```

### Hatchling Integration

For projects managed with [Hatch](https://hatch.pypa.io/), `unidep` can be configured in `pyproject.toml` to automatically process the dependencies from `requirements.yaml` or from the `[tool.unidep]` section in `pyproject.toml`.

**Example Configuration for Hatch**:

```toml
[build-system]
requires = ["hatchling", "unidep"]
build-backend = "hatchling.build"

[project]
dynamic = ["dependencies"]
# Additional project configurations

[tool.hatch]
# Additional Hatch configurations

[tool.hatch.metadata.hooks.unidep]
```

## :desktop_computer: As a CLI

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
              {merge,install,install-all,conda-lock,pip-compile,pip,conda,version}
              ...

Unified Conda and Pip requirements management.

positional arguments:
  {merge,install,install-all,conda-lock,pip-compile,pip,conda,version}
                        Subcommands
    merge               Combine multiple (or a single) `requirements.yaml` or
                        `pyproject.toml` files into a single Conda installable
                        `environment.yaml` file.
    install             Automatically install all dependencies from one or
                        more `requirements.yaml` or `pyproject.toml` files.
                        This command first installs dependencies with Conda,
                        then with Pip. Finally, it installs local packages
                        (those containing the `requirements.yaml` or
                        `pyproject.toml` files) using `pip install [-e]
                        ./project`.
    install-all         Install dependencies from all `requirements.yaml` or
                        `pyproject.toml` files found in the current directory
                        or specified directory. This command first installs
                        dependencies using Conda, then Pip, and finally the
                        local packages.
    conda-lock          Generate a global `conda-lock.yml` file for a
                        collection of `requirements.yaml` or `pyproject.toml`
                        files. Additionally, create individual `conda-
                        lock.yml` files for each `requirements.yaml` or
                        `pyproject.toml` file consistent with the global lock
                        file.
    pip-compile         Generate a fully pinned `requirements.txt` file from
                        one or more `requirements.yaml` or `pyproject.toml`
                        files using `pip-compile` from `pip-tools`. This
                        command consolidates all pip dependencies defined in
                        the `requirements.yaml` or `pyproject.toml` files and
                        compiles them into a single `requirements.txt` file,
                        taking into account the specific versions and
                        dependencies of each package.
    pip                 Get the pip requirements for the current platform
                        only.
    conda               Get the conda requirements for the current platform
                        only.
    version             Print version information of unidep.

options:
  -h, --help            show this help message and exit
```

<!-- OUTPUT:END -->

### `unidep merge`

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
                    [--depth DEPTH] [--skip-dependency SKIP_DEPENDENCY]
                    [--ignore-pin IGNORE_PIN] [--overwrite-pin OVERWRITE_PIN]

Combine multiple (or a single) `requirements.yaml` or `pyproject.toml` files
into a single Conda installable `environment.yaml` file. Example usage:
`unidep merge --directory . --depth 1 --output environment.yaml` to search for
`requirements.yaml` or `pyproject.toml` files in the current directory and its
subdirectories and create `environment.yaml`. These are the defaults, so you
can also just run `unidep merge`.

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
                        Base directory to scan for `requirements.yaml` or
                        `pyproject.toml` file(s), by default `.`
  -v, --verbose         Print verbose output
  --platform {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}, -p {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}
                        The platform(s) to get the requirements for. Multiple
                        platforms can be specified. By default, the current
                        platform (`linux-64`) is used.
  --depth DEPTH         Maximum depth to scan for `requirements.yaml` or
                        `pyproject.toml` files, by default 1
  --skip-dependency SKIP_DEPENDENCY
                        Skip installing a specific dependency that is in one
                        of the `requirements.yaml` or `pyproject.toml` files.
                        This option can be used multiple times, each time
                        specifying a different package to skip. For example,
                        use `--skip-dependency pandas` to skip installing
                        pandas.
  --ignore-pin IGNORE_PIN
                        Ignore the version pin for a specific package, e.g.,
                        `--ignore-pin numpy`. This option can be repeated to
                        ignore multiple packages.
  --overwrite-pin OVERWRITE_PIN
                        Overwrite the version pin for a specific package,
                        e.g., `--overwrite-pin 'numpy==1.19.2'`. This option
                        can be repeated to overwrite the pins of multiple
                        packages.
```

<!-- OUTPUT:END -->

### `unidep install`

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
                      [--skip-conda] [--skip-dependency SKIP_DEPENDENCY]
                      [--no-dependencies]
                      [--conda-executable {conda,mamba,micromamba}]
                      [--dry-run] [--ignore-pin IGNORE_PIN]
                      [--overwrite-pin OVERWRITE_PIN]
                      files [files ...]

Automatically install all dependencies from one or more `requirements.yaml` or
`pyproject.toml` files. This command first installs dependencies with Conda,
then with Pip. Finally, it installs local packages (those containing the
`requirements.yaml` or `pyproject.toml` files) using `pip install [-e]
./project`. Example usage: `unidep install .` for a single project. For
multiple projects: `unidep install ./project1 ./project2`. The command accepts
both file paths and directories containing a `requirements.yaml` or
`pyproject.toml` file. Use `--editable` or `-e` to install the local packages
in editable mode. See `unidep install-all` to install all `requirements.yaml`
or `pyproject.toml` files in and below the current folder.

positional arguments:
  files                 The `requirements.yaml` or `pyproject.toml` file(s) to
                        parse or folder(s) that contain those file(s), by
                        default `.`

options:
  -h, --help            show this help message and exit
  -v, --verbose         Print verbose output
  -e, --editable        Install the project in editable mode
  --skip-local          Skip installing local dependencies
  --skip-pip            Skip installing pip dependencies from
                        `requirements.yaml` or `pyproject.toml`
  --skip-conda          Skip installing conda dependencies from
                        `requirements.yaml` or `pyproject.toml`
  --skip-dependency SKIP_DEPENDENCY
                        Skip installing a specific dependency that is in one
                        of the `requirements.yaml` or `pyproject.toml` files.
                        This option can be used multiple times, each time
                        specifying a different package to skip. For example,
                        use `--skip-dependency pandas` to skip installing
                        pandas.
  --no-dependencies     Skip installing dependencies from `requirements.yaml`
                        or `pyproject.toml` file(s) and only install local
                        package(s). Useful after installing a `conda-lock.yml`
                        file because then all dependencies have already been
                        installed.
  --conda-executable {conda,mamba,micromamba}
                        The conda executable to use
  --dry-run, --dry      Only print the commands that would be run
  --ignore-pin IGNORE_PIN
                        Ignore the version pin for a specific package, e.g.,
                        `--ignore-pin numpy`. This option can be repeated to
                        ignore multiple packages.
  --overwrite-pin OVERWRITE_PIN
                        Overwrite the version pin for a specific package,
                        e.g., `--overwrite-pin 'numpy==1.19.2'`. This option
                        can be repeated to overwrite the pins of multiple
                        packages.
```

<!-- OUTPUT:END -->

### `unidep install-all`

Use `unidep install-all` on a folder with packages that contain `requirements.yaml` files and install the dependencies on the current platform using conda, then install the remaining dependencies with pip, and finally install the current package with `pip install [-e] ./package1 ./package2`.
See `unidep install-all -h` for more information:

<!-- CODE:BASH:START -->
<!-- echo '```bash' -->
<!-- unidep install -h -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```bash
usage: unidep install [-h] [-v] [-e] [--skip-local] [--skip-pip]
                      [--skip-conda] [--skip-dependency SKIP_DEPENDENCY]
                      [--no-dependencies]
                      [--conda-executable {conda,mamba,micromamba}]
                      [--dry-run] [--ignore-pin IGNORE_PIN]
                      [--overwrite-pin OVERWRITE_PIN]
                      files [files ...]

Automatically install all dependencies from one or more `requirements.yaml` or
`pyproject.toml` files. This command first installs dependencies with Conda,
then with Pip. Finally, it installs local packages (those containing the
`requirements.yaml` or `pyproject.toml` files) using `pip install [-e]
./project`. Example usage: `unidep install .` for a single project. For
multiple projects: `unidep install ./project1 ./project2`. The command accepts
both file paths and directories containing a `requirements.yaml` or
`pyproject.toml` file. Use `--editable` or `-e` to install the local packages
in editable mode. See `unidep install-all` to install all `requirements.yaml`
or `pyproject.toml` files in and below the current folder.

positional arguments:
  files                 The `requirements.yaml` or `pyproject.toml` file(s) to
                        parse or folder(s) that contain those file(s), by
                        default `.`

options:
  -h, --help            show this help message and exit
  -v, --verbose         Print verbose output
  -e, --editable        Install the project in editable mode
  --skip-local          Skip installing local dependencies
  --skip-pip            Skip installing pip dependencies from
                        `requirements.yaml` or `pyproject.toml`
  --skip-conda          Skip installing conda dependencies from
                        `requirements.yaml` or `pyproject.toml`
  --skip-dependency SKIP_DEPENDENCY
                        Skip installing a specific dependency that is in one
                        of the `requirements.yaml` or `pyproject.toml` files.
                        This option can be used multiple times, each time
                        specifying a different package to skip. For example,
                        use `--skip-dependency pandas` to skip installing
                        pandas.
  --no-dependencies     Skip installing dependencies from `requirements.yaml`
                        or `pyproject.toml` file(s) and only install local
                        package(s). Useful after installing a `conda-lock.yml`
                        file because then all dependencies have already been
                        installed.
  --conda-executable {conda,mamba,micromamba}
                        The conda executable to use
  --dry-run, --dry      Only print the commands that would be run
  --ignore-pin IGNORE_PIN
                        Ignore the version pin for a specific package, e.g.,
                        `--ignore-pin numpy`. This option can be repeated to
                        ignore multiple packages.
  --overwrite-pin OVERWRITE_PIN
                        Overwrite the version pin for a specific package,
                        e.g., `--overwrite-pin 'numpy==1.19.2'`. This option
                        can be repeated to overwrite the pins of multiple
                        packages.
```

<!-- OUTPUT:END -->

### `unidep conda-lock`

Use `unidep conda-lock` on one or multiple `requirements.yaml` files and output the conda-lock file.
Optionally, when using a monorepo with multiple subpackages (with their own `requirements.yaml` files), generate a lock file for each subpackage.
See `unidep conda-lock -h` for more information:

<!-- CODE:BASH:START -->
<!-- echo '```bash' -->
<!-- unidep conda-lock -h -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```bash
usage: unidep conda-lock [-h] [--only-global] [--lockfile LOCKFILE]
                         [--check-input-hash] [-d DIRECTORY] [-v]
                         [--platform {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}]
                         [--depth DEPTH] [--skip-dependency SKIP_DEPENDENCY]
                         [--ignore-pin IGNORE_PIN]
                         [--overwrite-pin OVERWRITE_PIN]

Generate a global `conda-lock.yml` file for a collection of
`requirements.yaml` or `pyproject.toml` files. Additionally, create individual
`conda-lock.yml` files for each `requirements.yaml` or `pyproject.toml` file
consistent with the global lock file. Example usage: `unidep conda-lock
--directory ./projects` to generate conda-lock files for all
`requirements.yaml` or `pyproject.toml` files in the `./projects` directory.
Use `--only-global` to generate only the global lock file. The `--check-input-
hash` option can be used to avoid regenerating lock files if the input hasn't
changed.

options:
  -h, --help            show this help message and exit
  --only-global         Only generate the global lock file
  --lockfile LOCKFILE   Specify a path for the global lockfile (default:
                        `conda-lock.yml` in current directory). Path should be
                        relative, e.g., `--lockfile ./locks/example.conda-
                        lock.yml`.
  --check-input-hash    Check existing input hashes in lockfiles before
                        regenerating lock files. This flag is directly passed
                        to `conda-lock`.
  -d DIRECTORY, --directory DIRECTORY
                        Base directory to scan for `requirements.yaml` or
                        `pyproject.toml` file(s), by default `.`
  -v, --verbose         Print verbose output
  --platform {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}, -p {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}
                        The platform(s) to get the requirements for. Multiple
                        platforms can be specified. By default, the current
                        platform (`linux-64`) is used.
  --depth DEPTH         Maximum depth to scan for `requirements.yaml` or
                        `pyproject.toml` files, by default 1
  --skip-dependency SKIP_DEPENDENCY
                        Skip installing a specific dependency that is in one
                        of the `requirements.yaml` or `pyproject.toml` files.
                        This option can be used multiple times, each time
                        specifying a different package to skip. For example,
                        use `--skip-dependency pandas` to skip installing
                        pandas.
  --ignore-pin IGNORE_PIN
                        Ignore the version pin for a specific package, e.g.,
                        `--ignore-pin numpy`. This option can be repeated to
                        ignore multiple packages.
  --overwrite-pin OVERWRITE_PIN
                        Overwrite the version pin for a specific package,
                        e.g., `--overwrite-pin 'numpy==1.19.2'`. This option
                        can be repeated to overwrite the pins of multiple
                        packages.
```

<!-- OUTPUT:END -->

### `unidep pip-compile`

Use `unidep pip-compile` on one or multiple `requirements.yaml` files and output a fully locked `requirements.txt` file using `pip-compile` from [`pip-tools`](https://pip-tools.readthedocs.io/en/latest/).
See `unidep pip-compile -h` for more information:

<!-- CODE:BASH:START -->
<!-- echo '```bash' -->
<!-- unidep pip-compile -h -->
<!-- echo '```' -->
<!-- CODE:END -->
<!-- OUTPUT:START -->
<!-- ⚠️ This content is auto-generated by `markdown-code-runner`. -->
```bash
usage: unidep pip-compile [-h] [-o OUTPUT_FILE] [-d DIRECTORY] [-v]
                          [--platform {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}]
                          [--depth DEPTH] [--skip-dependency SKIP_DEPENDENCY]
                          [--ignore-pin IGNORE_PIN]
                          [--overwrite-pin OVERWRITE_PIN]
                          ...

Generate a fully pinned `requirements.txt` file from one or more
`requirements.yaml` or `pyproject.toml` files using `pip-compile` from `pip-
tools`. This command consolidates all pip dependencies defined in the
`requirements.yaml` or `pyproject.toml` files and compiles them into a single
`requirements.txt` file, taking into account the specific versions and
dependencies of each package. Example usage: `unidep pip-compile --directory
./projects` to generate a `requirements.txt` file for all `requirements.yaml`
or `pyproject.toml` files in the `./projects` directory. Use `--output-file
requirements.txt` to specify a different output file.

positional arguments:
  extra_flags           Extra flags to pass to `pip-compile`. These flags are
                        passed directly and should be provided in the format
                        expected by `pip-compile`. For example, `unidep pip-
                        compile -- --generate-hashes --allow-unsafe`. Note
                        that the `--` is required to separate the flags for
                        `unidep` from the flags for `pip-compile`.

options:
  -h, --help            show this help message and exit
  -o OUTPUT_FILE, --output-file OUTPUT_FILE
                        Output file for the pip requirements, by default
                        `requirements.txt`
  -d DIRECTORY, --directory DIRECTORY
                        Base directory to scan for `requirements.yaml` or
                        `pyproject.toml` file(s), by default `.`
  -v, --verbose         Print verbose output
  --platform {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}, -p {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}
                        The platform(s) to get the requirements for. Multiple
                        platforms can be specified. By default, the current
                        platform (`linux-64`) is used.
  --depth DEPTH         Maximum depth to scan for `requirements.yaml` or
                        `pyproject.toml` files, by default 1
  --skip-dependency SKIP_DEPENDENCY
                        Skip installing a specific dependency that is in one
                        of the `requirements.yaml` or `pyproject.toml` files.
                        This option can be used multiple times, each time
                        specifying a different package to skip. For example,
                        use `--skip-dependency pandas` to skip installing
                        pandas.
  --ignore-pin IGNORE_PIN
                        Ignore the version pin for a specific package, e.g.,
                        `--ignore-pin numpy`. This option can be repeated to
                        ignore multiple packages.
  --overwrite-pin OVERWRITE_PIN
                        Overwrite the version pin for a specific package,
                        e.g., `--overwrite-pin 'numpy==1.19.2'`. This option
                        can be repeated to overwrite the pins of multiple
                        packages.
```

<!-- OUTPUT:END -->

### `unidep pip`

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
                  [--skip-dependency SKIP_DEPENDENCY]
                  [--ignore-pin IGNORE_PIN] [--overwrite-pin OVERWRITE_PIN]
                  [--separator SEPARATOR]

Get the pip requirements for the current platform only. Example usage: `unidep
pip --file folder1 --file folder2/requirements.yaml --seperator ' ' --platform
linux-64` to extract all the pip dependencies specific to the linux-64
platform. Note that the `--file` argument can be used multiple times to
specify multiple `requirements.yaml` or `pyproject.toml` files and that --file
can also be a folder that contains a `requirements.yaml` or `pyproject.toml`
file.

options:
  -h, --help            show this help message and exit
  -f FILE, --file FILE  The `requirements.yaml` or `pyproject.toml` file to
                        parse, or folder that contains that file, by default
                        `.`
  -v, --verbose         Print verbose output
  --platform {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}, -p {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}
                        The platform(s) to get the requirements for. Multiple
                        platforms can be specified. By default, the current
                        platform (`linux-64`) is used.
  --skip-dependency SKIP_DEPENDENCY
                        Skip installing a specific dependency that is in one
                        of the `requirements.yaml` or `pyproject.toml` files.
                        This option can be used multiple times, each time
                        specifying a different package to skip. For example,
                        use `--skip-dependency pandas` to skip installing
                        pandas.
  --ignore-pin IGNORE_PIN
                        Ignore the version pin for a specific package, e.g.,
                        `--ignore-pin numpy`. This option can be repeated to
                        ignore multiple packages.
  --overwrite-pin OVERWRITE_PIN
                        Overwrite the version pin for a specific package,
                        e.g., `--overwrite-pin 'numpy==1.19.2'`. This option
                        can be repeated to overwrite the pins of multiple
                        packages.
  --separator SEPARATOR
                        The separator between the dependencies, by default ` `
```

<!-- OUTPUT:END -->

### `unidep conda`

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
                    [--skip-dependency SKIP_DEPENDENCY]
                    [--ignore-pin IGNORE_PIN] [--overwrite-pin OVERWRITE_PIN]
                    [--separator SEPARATOR]

Get the conda requirements for the current platform only. Example usage:
`unidep conda --file folder1 --file folder2/requirements.yaml --seperator ' '
--platform linux-64` to extract all the conda dependencies specific to the
linux-64 platform. Note that the `--file` argument can be used multiple times
to specify multiple `requirements.yaml` or `pyproject.toml` files and that
--file can also be a folder that contains a `requirements.yaml` or
`pyproject.toml` file.

options:
  -h, --help            show this help message and exit
  -f FILE, --file FILE  The `requirements.yaml` or `pyproject.toml` file to
                        parse, or folder that contains that file, by default
                        `.`
  -v, --verbose         Print verbose output
  --platform {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}, -p {linux-64,linux-aarch64,linux-ppc64le,osx-64,osx-arm64,win-64}
                        The platform(s) to get the requirements for. Multiple
                        platforms can be specified. By default, the current
                        platform (`linux-64`) is used.
  --skip-dependency SKIP_DEPENDENCY
                        Skip installing a specific dependency that is in one
                        of the `requirements.yaml` or `pyproject.toml` files.
                        This option can be used multiple times, each time
                        specifying a different package to skip. For example,
                        use `--skip-dependency pandas` to skip installing
                        pandas.
  --ignore-pin IGNORE_PIN
                        Ignore the version pin for a specific package, e.g.,
                        `--ignore-pin numpy`. This option can be repeated to
                        ignore multiple packages.
  --overwrite-pin OVERWRITE_PIN
                        Overwrite the version pin for a specific package,
                        e.g., `--overwrite-pin 'numpy==1.19.2'`. This option
                        can be repeated to overwrite the pins of multiple
                        packages.
  --separator SEPARATOR
                        The separator between the dependencies, by default ` `
```

<!-- OUTPUT:END -->

## ❓ FAQ

Here is a list of questions we have either been asked by users or potential pitfalls we hope to help users avoid:

### **Q: When to use UniDep?**

**A:** UniDep is particularly useful for setting up full development environments that require both Python *and* non-Python dependencies (e.g., CUDA, compilers, etc.) with a single command.

In fields like research, data science, robotics, AI, and ML projects, it is common to work from a locally cloned Git repository.

Setting up a full development environment can be a pain, especially if you need to install non Python dependencies like compilers, low-level numerical libraries, or CUDA (luckily Conda has all of them).
Typically, instructions are different for each OS and their corresponding package managers (`apt`, `brew`, `yum`, `winget`, etc.).

With UniDep, you can specify all your Pip and Conda dependencies in a single file.
To get set up on a new machine, you just need to install Conda (we recommend [micromamba](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html)) and run `pip install unidep; unidep install-all -e` in your project directory, to install all dependencies and local packages in editable mode in the current Conda environment.

For fully reproducible environments, you can run `unidep conda-lock` to generate a `conda-lock.yml` file.
Then, run `conda env create -f conda-lock.yml -n myenv` to create a new Conda environment with all the third-party dependencies.
Finally, run `unidep install-all -e --no-dependencies` to install all your local packages in editable mode.

For those who prefer not to use Conda, you can simply run `pip install -e .` on a project using UniDep.
You'll need to install the non-Python dependencies yourself, but you'll have a list of them in the `requirements.yaml` file.

In summary, use UniDep if you:

- Prefer installing packages with conda but still want your package to be pip installable.
- Are tired of synchronizing your Pip requirements (`requirements.txt`) and Conda requirements (`environment.yaml`).
- Want a low-effort, comprehensive development environment setup.

### **Q: Just show me a full example!**

**A:** Check out the [`example` folder](https://github.com/basnijholt/unidep/tree/main/example).

### **Q: How is this different from conda/mamba/pip?**

**A:** UniDep uses pip and conda under the hood to install dependencies, but it is not a replacement for them. UniDep will print the commands it runs, so you can see exactly what it is doing.

### **Q: I found a project using unidep, now what?**

**A:** You can install it like *any other Python package* using `pip install`.
However, to take full advantage of UniDep's functionality, clone the repository and run `unidep install-all -e` in the project directory.
This installs all dependencies in editable mode in the current Conda environment.

### **Q: How to handle local dependencies that do not use UniDep?**

**A:** You can use the `local_dependencies` field in the `requirements.yaml` or `pyproject.toml` file to specify local dependencies.
However, *if* a local dependency is *not* managed by UniDep, it will skip installing its dependencies!

To include all its dependencies, either convert the package to use UniDep (🏆), or maintain a separate `requirements.yaml` file, e.g., for a package called `foo` create, `foo-requirements.yaml`:

```yaml
dependencies:
  # List the dependencies of foo here
  - numpy
  - scipy
  - matplotlib
  - bar
local_dependencies:
  - ./path/to/foo  # This is the path to the package
```

Then, in the `requirements.yaml` or `pyproject.toml` file of the package that uses `foo`, list `foo-requirements.yaml` as a local dependency:

```yaml
local_dependencies:
  - ./path/to/foo-requirements.yaml
```

### **Q: Can't Conda already do this?**

**A:** Not quite. Conda can indeed install both Conda and Pip dependencies via an `environment.yaml` file, however, it does not work the other way around.
Pip cannot install the `pip` dependencies from an `environment.yaml` file.
This means, that if you want your package to be installable with `pip install -e .` *and* support Conda, you need to maintain two separate files: `environment.yaml` and `requirements.txt` (or specify these dependencies in `pyproject.toml` or `setup.py`).

### **Q: What is the difference between `conda-lock` and `unidep conda-lock`?**

**A:** [`conda-lock`](https://github.com/conda/conda-lock) is a standalone tool that creates a `conda-lock.yml` file from a `environment.yaml` file.
On the other hand, `unidep conda-lock` is a command within the UniDep tool that also generates a `conda-lock.yml` file (leveraging `conda-lock`), but it does so from one or more `requirements.yaml` or `pyproject.toml` files.
When managing multiple dependent projects (e.g., in a monorepo), a unique feature of `unidep conda-lock` is its ability to create **_consistent_** individual `conda-lock.yml` files for each `requirements.yaml` or `pyproject.toml` file, ensuring consistency with a global `conda-lock.yml` file.
This feature is not available in the standalone `conda-lock` tool.

## :hammer_and_wrench: Troubleshooting

### `pip install` fails with `FileNotFoundError`

When using a project that uses `local_dependencies: [../not/current/dir]` in the `requirements.yaml` file:

```yaml
local_dependencies:
  # File in a different directory than the pyproject.toml file
  - ../common-requirements.yaml
```

You might get an error like this when using a `pip` version older than `22.0`:

```bash
$ pip install /path/to/your/project/using/unidep
  ...
  File "/usr/lib/python3.8/pathlib.py", line 1222, in open
    return io.open(self, mode, buffering, encoding, errors, newline,
  File "/usr/lib/python3.8/pathlib.py", line 1078, in _opener
    return self._accessor.open(self, flags, mode)
FileNotFoundError: [Errno 2] No such file or directory: '/tmp/common-requirements.yaml'
```

The solution is to upgrade `pip` to version `22.0` or newer:

```bash
pip install --upgrade pip
```

## :warning: Limitations

- **Conda-Focused**: Best suited for Conda environments. However, note that having `conda` is not a requirement to install packages that use UniDep.
- **Setuptools and Hatchling only**: Currently only works with setuptools and Hatchling, not flit, poetry, or other build systems. Open an issue if you'd like to see support for other build systems.
- No [logic operators in platform selectors](https://github.com/basnijholt/unidep/issues/5) and [no Python selectors](https://github.com/basnijholt/unidep/issues/7).

* * *

Try `unidep` today for a streamlined approach to managing your Conda environment dependencies across multiple projects! 🎉👏
