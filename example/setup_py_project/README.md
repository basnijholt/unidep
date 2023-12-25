# `setup.py` integration example

> [!TIP]
> - **Standard Installation**: In this example folder, use `pip install .` to install all Python dependencies that are pip-installable, along with the local package itself.
> - **Comprehensive Installation with `unidep`**: To install all dependencies, including those that are not Python-specific, use `unidep install .`. This command performs the following actions in sequence:
>   1. `conda install [dependencies from requirements.yaml]` – Installs all Conda installable dependencies.
>   2. `pip install [dependencies from requirements.yaml]` – Installs remaining pip-only dependencies.
>   3. `pip install .` – Installs the local package.

For projects using `setuptools` with a `setup.py` file, configure `unidep` in `pyproject.toml` alongside a `requirements.yaml` file.

**Example Configuration for projects using `setup.py`**:

Add this to `pyproject.toml`:

```toml
[build-system]
build-backend = "setuptools.build_meta"
requires = ["setuptools", "unidep"]
```

And just do not use `install_requires` in `setup.py`.

> [!NOTE]
> See the [`pyproject.toml`](pyproject.toml) and [`setup.py`](setup.py) for a working example.
