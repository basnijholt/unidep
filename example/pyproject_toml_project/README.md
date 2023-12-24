# Full `pyproject.toml` integration example

> [!TIP]
> - **Standard Installation**: In this example folder, use `pip install .` to install all Python dependencies that are pip-installable, along with the local package itself.
> - **Comprehensive Installation with `unidep`**: To install all dependencies, including those that are not Python-specific, use `unidep install .`. This command performs the following actions in sequence:
>   1. `conda install [dependencies from pyproject.toml]` – Installs all Conda installable dependencies.
>   2. `pip install [dependencies from pyproject.toml]` – Installs remaining pip-only dependencies.
>   3. `pip install .` – Installs the local package.

For projects using `setuptools` with only a `pyproject.toml` file, configure `unidep` in `pyproject.toml` and specify all dependencies there too.

**Example Configuration for projects using `pyproject.toml`**:

Add this to `pyproject.toml`:

```toml
[build-system]
build-backend = "setuptools.build_meta"
requires = ["setuptools", "unidep[toml]"]  # add "unidep[toml]" here

[project]
dynamic = ["dependencies"]  # add "dependencies" here

[tool.unidep]
channels = ["conda-forge"]
dependencies = [
    "adaptive",
    "pfapack:linux64",
    "pipefunc",
    { pip = "markdown-code-runner" },
    { pip = "home-assistant-streamdeck-yaml" },
]
```

Then, of course, add a `requirements.yaml` and you are good to go! 🎉

> [!NOTE]
> See the [`pyproject.toml`](pyproject.toml) for a working example.
