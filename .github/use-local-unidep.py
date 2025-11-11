"""Update `pyproject.toml` in each example project to use local `unidep`."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DIR = REPO_ROOT / "example"
PROJECT_DIRS = [p for p in EXAMPLE_DIR.iterdir() if p.name.endswith("_project")]
REPO_ROOT_URI = REPO_ROOT.resolve().as_uri()

print(
    f"REPO_ROOT: {REPO_ROOT}, EXAMPLE_DIR: {EXAMPLE_DIR}, PROJECT_DIRS: {PROJECT_DIRS}",
)

for project_dir in PROJECT_DIRS:
    # Find the line with `requires = [` in `pyproject.toml` and replace
    # `unidep`/`unidep[toml]` entries with file:// references to the repo root.
    pyproject_toml = project_dir / "pyproject.toml"
    lines = pyproject_toml.read_text().splitlines()
    for i, line in enumerate(lines):
        if "requires = [" not in line:
            continue
        if "unidep[toml]" in line:
            lines[i] = line.replace(
                "unidep[toml]",
                f"unidep[toml] @ {REPO_ROOT_URI}",
            )
        elif "unidep" in line:
            lines[i] = line.replace("unidep", f"unidep @ {REPO_ROOT_URI}")
        break
    pyproject_toml.write_text("\n".join(lines))
