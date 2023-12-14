from setuptools import setup

setup(
    name="setup_py_project",
    version="0.1.0",
    description="A short description of your package",
    py_modules=["setup_py_project"],
    # This is not needed because `install_requires` is automatically
    # populated by `unidep` with the dependencies from the `requirements.yaml`
)
