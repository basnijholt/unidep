import os
import sys
from pathlib import Path

package_path = Path("../..").resolve()
sys.path.insert(0, str(package_path))
PYTHON_PATH = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = f"{package_path}:{PYTHON_PATH}"

docs_path = Path("..").resolve()
sys.path.insert(1, str(docs_path))

import unidep

project = "unidep"
copyright = "2023, Bas Nijholt"
author = "Bas Nijholt"

version = unidep.__version__
release = unidep.__version__

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.autosectionlabel",
    "sphinx.ext.intersphinx",
    "sphinx.ext.mathjax",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
    "myst_parser",
    "sphinx_fontawesome",
    "sphinx_autodoc_typehints",
]

source_parsers = {}  # type: ignore[var-annotated]
templates_path = ["_templates"]
source_suffix = [".rst", ".md"]
master_doc = "index"
language = "en"
exclude_patterns = []  # type: ignore[var-annotated]
pygments_style = "sphinx"
html_theme = "furo"
html_static_path = ["_static"]
htmlhelp_basename = "adaptivedoc"
default_role = "autolink"
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "adaptive": ("https://adaptive.readthedocs.io/en/stable/", None),
    "pandas": ("https://pandas.pydata.org/pandas-docs/stable/", None),
    "mpi4py": ("https://mpi4py.readthedocs.io/en/stable/", None),
    "ipyparallel": ("https://ipywidgets.readthedocs.io/en/stable/", None),
    "dask-mpi": ("http://mpi.dask.org/en/latest/", None),
    "distributed": ("https://distributed.dask.org/en/latest/", None),
    "dask": ("https://docs.dask.org/en/latest/", None),
}

nb_execution_mode = "cache"
nb_execution_timeout = 180
nb_execution_raise_on_error = True


def replace_named_emojis(input_file: Path, output_file: Path) -> None:
    """Replace named emojis in a file with unicode emojis."""
    import emoji

    with input_file.open("r") as infile:
        content = infile.read()
        content_with_emojis = emoji.emojize(content, language="alias")

        with output_file.open("w") as outfile:
            outfile.write(content_with_emojis)


def edit_text(input_text):
    # Splitting the text into lines
    lines = input_text.split("\n")

    # Placeholder for the edited text
    edited_text = []

    # Mapping of markdown markers to their new format
    mapping = {
        "IMPORTANT": "important",
        "NOTE": "note",
        "TIP": "tip",
        "WARNING": "caution",
    }

    # Variable to keep track of the current block type
    current_block_type = None

    for line in lines:
        # Check if the line starts with any of the markers
        if any(line.strip().startswith(f"> [!{marker}]") for marker in mapping):
            # Find the marker and set the current block type
            current_block_type = next(
                marker for marker in mapping if f"> [!{marker}]" in line
            )
            # Start of a new block
            edited_text.append(":::{" + mapping[current_block_type] + "}")
        elif current_block_type and line.strip() == ">":
            # Empty line within the block, skip it
            continue
        elif current_block_type and not line.strip().startswith(">"):
            # End of the current block
            edited_text.append(":::")
            edited_text.append(line)  # Add the current line as it is
            current_block_type = None  # Reset the block type
        elif current_block_type:
            # Inside the block, so remove '>' and add the line
            edited_text.append(line.lstrip("> ").rstrip())
        else:
            # Outside any block, add the line as it is
            edited_text.append(line)

    # Join the edited lines back into a single string
    return "\n".join(edited_text)


def replace_blocks(input_file: Path, output_file: Path) -> None:
    with input_file.open("r") as infile:
        content = infile.read()
        new_content = edit_text(content)

        with output_file.open("w") as outfile:
            outfile.write(new_content)


input_file = package_path / "README.md"
output_file = docs_path / "source" / "README.md"
replace_named_emojis(input_file, output_file)
replace_blocks(output_file, output_file)


def setup(app):
    pass
