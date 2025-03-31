"""Update `pyproject.toml` in each example project to use local `unidep`."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DIR = REPO_ROOT / "example"
PROJECT_DIRS = [p for p in EXAMPLE_DIR.iterdir() if p.name.endswith("_project")]

print(
    f"REPO_ROOT: {REPO_ROOT}, EXAMPLE_DIR: {EXAMPLE_DIR}, PROJECT_DIRS: {PROJECT_DIRS}",
)

for project_dir in PROJECT_DIRS:
    # find the line with `requires = [` in `pyproject.toml` in each project
    # directory and replace `"unidep"` with
    # `"unidep @ file://<abs-path-to-repo-root>"``
    pyproject_toml = project_dir / "pyproject.toml"
    lines = pyproject_toml.read_text().splitlines()
    repo_root = REPO_ROOT.as_posix()  # convert to posix path for windows
    for i, line in enumerate(lines):
        if "requires = [" in line:
            if "unidep" in line:
                lines[i] = line.replace("unidep", f"unidep @ file://{repo_root}")
            break
    pyproject_toml.write_text("\n".join(lines))
