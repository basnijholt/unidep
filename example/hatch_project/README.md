# Hatchling Integration

> [!TIP]
> - **Standard Installation**: In this example folder, use `pip install .` to install all Python dependencies that are pip-installable, along with the local package itself.
> - **Comprehensive Installation with `unidep`**: To install all dependencies, including those that are not Python-specific, use `unidep install .`. This command performs the following actions in sequence:
>   1. `conda install [dependencies from requirements.yaml]` – Installs all Conda installable dependencies.
>   2. `pip install [dependencies from requirements.yaml]` – Installs remaining pip-only dependencies.
>   3. `pip install .` – Installs the local package.

For projects managed with [Hatch](https://hatch.pypa.io/), `unidep` can be configured in `pyproject.toml` to automatically process `requirements.yaml`.

**Example Configuration for Hatch**:

```toml
[build-system]
requires = ["hatchling", "unidep"]  # add "unidep" here
build-backend = "hatchling.build"

[project]
dynamic = ["dependencies"]  # add "dependencies" here
# Additional project configurations

[tool.hatch]
# Additional Hatch configurations

[tool.hatch.metadata.hooks.unidep]  # add this to enable the hook
```

> [!NOTE]
> See the [`pyproject.toml`](pyproject.toml) a working example.
