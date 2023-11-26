from setuptools import setup

setup(
    name="project1",
    version="0.1.0",
    description="A short description of your package",
    py_modules=["project1"],
    # This is not needed because `install_requires` is automatically
    # populated by `unidep` with the dependencies from the `requirements.yaml`
)
