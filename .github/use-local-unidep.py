from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DIR = REPO_ROOT / "examples"
PROJECT_DIRS = [p for p in EXAMPLE_DIR.iterdir() if p.name.endswith("_project")]

for project_dir in PROJECT_DIRS:
    # find the line with `requires = [` in `pyproject.toml` in each project
    # directory and replace `"unidep"` or `"unidep[toml]"` with
    # `"unidep @ file://<abs-path-to-repo-root>"`` or
    # `"unidep[toml] @ file://<abs-path-to-repo-root>"` respectively
    pyproject_toml = project_dir / "pyproject.toml"
    lines = pyproject_toml.read_text().splitlines()
    for i, line in enumerate(lines):
        if "requires = [" in line:
            if "unidep[toml]" in line:
                lines[i] = line.replace(
                    "unidep[toml]",
                    f"unidep[toml] @ file://{REPO_ROOT}",
                )
            elif "unidep" in line:
                lines[i] = line.replace("unidep", f"unidep @ file://{REPO_ROOT}")
            break
    pyproject_toml.write_text("\n".join(lines))
