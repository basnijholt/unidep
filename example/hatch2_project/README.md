# Hatchling Integration

> [!TIP]
> - **Standard Installation**: In this example folder, use `pip install .` to install all Python dependencies that are pip-installable, along with the local package itself.
> - **Comprehensive Installation with `unidep`**: To install all dependencies, including those that are not Python-specific, use `unidep install .`. This command performs the following actions in sequence:
>   1. `conda install [dependencies from pyproject.toml]` – Installs all Conda installable dependencies.
>   2. `pip install [dependencies from pyproject.toml]` – Installs remaining pip-only dependencies.
>   3. `pip install .` – Installs the local package.

For projects managed with [Hatch](https://hatch.pypa.io/), `unidep` can be configured fully in `pyproject.toml` including all its dependencies.

**Example Configuration for Hatch**:

```toml
[build-system]
requires = ["hatchling", "unidep[toml]"]  # add "unidep[toml]" here
build-backend = "hatchling.build"

[project]
dynamic = ["dependencies"]  # add "dependencies" here
# Additional project configurations

[tool.hatch]
# Additional Hatch configurations

[tool.hatch.metadata]
allow-direct-references = true  # allow VCS URLs, local paths, etc.

[tool.hatch.metadata.hooks.unidep]  # add this to enable the hook

# Specify pip and conda dependencies here
[tool.unidep]
channels = ["conda-forge"]
dependencies = [
    { conda = "adaptive-scheduler:linux64" },
    { pip = "unidep" },
    "numpy >=1.21",
    "hpc05:linux64",
    "pandas >=1,<3",
    "pexpect:unix",
    "wexpect:win64",
]
```

> [!NOTE]
> See the [`pyproject.toml`](pyproject.toml) for a working example.
