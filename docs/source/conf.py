import os
import re
import sys
import shutil
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
    "sphinx_autodoc_typehints",
]


autosectionlabel_maxdepth = 5
myst_heading_anchors = 0
source_parsers = {}  # type: ignore[var-annotated]
templates_path = ["_templates"]
source_suffix = [".rst", ".md"]
master_doc = "index"
language = "en"
exclude_patterns = []  # type: ignore[var-annotated]
pygments_style = "sphinx"
html_theme = "furo"
html_static_path = ["_static"]
htmlhelp_basename = "unidepdoc"
default_role = "autolink"
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}
html_logo = "https://raw.githubusercontent.com/basnijholt/nijho.lt/basnijholt-patch-1/content/project/unidep/IMG_4542.webp"


def replace_named_emojis(input_file: Path, output_file: Path) -> None:
    """Replace named emojis in a file with unicode emojis."""
    import emoji

    with input_file.open("r") as infile:
        content = infile.read()
    content_with_emojis = emoji.emojize(content, language="alias")

    with output_file.open("w") as outfile:
        outfile.write(content_with_emojis)


def _change_alerts_to_admonitions(input_text):
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
            edited_text.append("```{" + mapping[current_block_type] + "}")
        elif current_block_type and line.strip() == ">":
            # Empty line within the block, skip it
            continue
        elif current_block_type and not line.strip().startswith(">"):
            # End of the current block
            edited_text.append("```")
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


def change_alerts_to_admonitions(input_file: Path, output_file: Path) -> None:
    with input_file.open("r") as infile:
        content = infile.read()
    new_content = _change_alerts_to_admonitions(content)

    with output_file.open("w") as outfile:
        outfile.write(new_content)


def replace_links(input_file: Path, output_file: Path) -> None:
    with input_file.open("r") as infile:
        content = infile.read()
    new_content = content.replace(
        "(example/", "(https://github.com/basnijholt/unidep/tree/main/example/"
    )
    with output_file.open("w") as outfile:
        outfile.write(new_content)


def fix_anchors_with_named_emojis(input_file: Path, output_file: Path) -> None:
    to_remove = [
        "package",
        "memo",
        "jigsaw",
        "desktop_computer",
        "hammer_and_wrench",
        "warning",
    ]
    with input_file.open("r") as infile:
        content = infile.read()
    new_content = content
    for emoji_name in to_remove:
        new_content = new_content.replace(f"#{emoji_name}-", "#")
    with output_file.open("w") as outfile:
        outfile.write(new_content)


def split_markdown_by_headers(
    readme_path: Path, out_folder: Path, to_skip=("Table of Contents",)
):
    with open(readme_path, "r", encoding="utf-8") as file:
        content = file.read()

    # Regex to find second-level headers
    headers = re.finditer(r"\n(## .+?)(?=\n## |\Z)", content, re.DOTALL)

    # Split content based on headers
    split_contents = []
    start = 0
    previous_header = ""
    for header in headers:
        header_title = header.group(1).strip("# ").strip()
        end = header.start()
        if not any(s in previous_header for s in to_skip):
            split_contents.append(content[start:end].strip())
        start = end
        previous_header = header_title

    # Add the last section
    split_contents.append(content[start:].strip())

    # Create individual files for each section
    toctree_entries = []
    for i, section in enumerate(split_contents):
        fname = out_folder / f"section_{i}.md"
        toctree_entries.append(f"sections/section_{i}")
        with open(fname, "w", encoding="utf-8") as file:
            file.write(section)

    return toctree_entries


def replace_header(file_path, new_header):
    with open(file_path, "r", encoding="utf-8") as file:
        content = file.read()

    # Find the first-level header (indicated by '# ')
    # We use a regular expression to match the first occurrence of '# ' and any following characters until a newline
    content = re.sub(
        r"^# .+?\n", f"# {new_header}\n", content, count=1, flags=re.MULTILINE
    )

    with open(file_path, "w", encoding="utf-8") as file:
        file.write(content)


input_file = package_path / "README.md"
output_file = docs_path / "source" / "README.md"
replace_named_emojis(input_file, output_file)
change_alerts_to_admonitions(output_file, output_file)
replace_links(output_file, output_file)
fix_anchors_with_named_emojis(output_file, output_file)
sections_folder = docs_path / "source" / "sections"
shutil.rmtree(sections_folder, ignore_errors=True)
sections_folder.mkdir(exist_ok=True)
split_markdown_by_headers(output_file, sections_folder)
output_file.unlink()
shutil.move(sections_folder / "section_0.md", sections_folder.parent / "intro.md")  # type: ignore[arg-type]
replace_header(sections_folder.parent / "intro.md", new_header="🌟 Introduction")


def setup(app):
    pass
