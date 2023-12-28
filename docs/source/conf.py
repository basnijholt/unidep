"""Spinx configuration file for the unidep documentation."""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

package_path = Path("../..").resolve()
sys.path.insert(0, str(package_path))
PYTHON_PATH = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = f"{package_path}:{PYTHON_PATH}"

docs_path = Path("..").resolve()
sys.path.insert(1, str(docs_path))

import unidep  # noqa: E402

project = "unidep"
copyright = "2023, Bas Nijholt"  # noqa: A001
author = "Bas Nijholt"

version = unidep.__version__
release = unidep.__version__

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.autosectionlabel",
    "sphinx.ext.intersphinx",
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
html_logo = "https://github.com/basnijholt/nijho.lt/raw/2cf0045f9609a176cb53422c591fde946459669d/content/project/unidep/unidep-logo.webp"


def replace_named_emojis(input_file: Path, output_file: Path) -> None:
    """Replace named emojis in a file with unicode emojis."""
    import emoji

    with input_file.open("r") as infile:
        content = infile.read()
    content_with_emojis = emoji.emojize(content, language="alias")

    with output_file.open("w") as outfile:
        outfile.write(content_with_emojis)


def _change_alerts_to_admonitions(input_text: str) -> str:
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
    """Change markdown alerts to admonitions.

    For example, changes
    > [!NOTE]
    > This is a note.
    to
    ```{note}
    This is a note.
    ```
    """
    with input_file.open("r") as infile:
        content = infile.read()
    new_content = _change_alerts_to_admonitions(content)

    with output_file.open("w") as outfile:
        outfile.write(new_content)


def replace_example_links(input_file: Path, output_file: Path) -> None:
    """Replace relative links to `example/` files with absolute links to GitHub."""
    with input_file.open("r") as infile:
        content = infile.read()
    new_content = content.replace(
        "(example/",
        "(https://github.com/basnijholt/unidep/tree/main/example/",
    )
    with output_file.open("w") as outfile:
        outfile.write(new_content)


def fix_anchors_with_named_emojis(input_file: Path, output_file: Path) -> None:
    """Fix anchors with named emojis.

    WARNING: this currently hardcodes the emojis to remove.
    """
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
    readme_path: Path,
    out_folder: Path,
    to_skip: tuple[str, ...] = ("Table of Contents",),
) -> list[str]:
    """Split a markdown file into individual files based on headers."""
    with readme_path.open(encoding="utf-8") as file:
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
        toctree_entries.append(f"section_{i}")
        with fname.open("w", encoding="utf-8") as file:
            file.write(section)

    return toctree_entries


def replace_header(file_path: Path, new_header: str) -> None:
    """Replace the first-level header in a markdown file."""
    with file_path.open("r", encoding="utf-8") as file:
        content = file.read()

    # Find the first-level header (indicated by '# ')
    # We use a regular expression to match the first occurrence of '# '
    # and any following characters until a newline
    content = re.sub(
        r"^# .+?\n",
        f"# {new_header}\n",
        content,
        count=1,
        flags=re.MULTILINE,
    )

    with file_path.open("w", encoding="utf-8") as file:
        file.write(content)


def extract_toc_links(md_file_path: Path) -> dict[str, str]:
    """Extracts the table of contents with title to link mapping from the given README content.

    Parameters
    ----------
    md_file_path
        Markdown file path.

    Returns
    -------
    A dictionary where keys are section titles and values are the corresponding links.
    """
    with md_file_path.open("r") as infile:
        readme_content = infile.read()
    toc_start = "<!-- START doctoc generated TOC please keep comment here to allow auto update -->"
    toc_end = "<!-- END doctoc generated TOC please keep comment here to allow auto update -->"

    # Extract the TOC section
    toc_section = re.search(f"{toc_start}(.*?){toc_end}", readme_content, re.DOTALL)
    if not toc_section:
        msg = "Table of Contents section not found."
        raise RuntimeError(msg)

    toc_content = toc_section.group(1)

    # Regular expression to match the markdown link syntax
    link_regex = re.compile(r"- \[([^]]+)\]\(([^)]+)\)")

    # Extracting links
    return {
        match.group(1).strip(): match.group(2)
        for match in link_regex.finditer(toc_content)
    }


def extract_headers_from_markdown(md_file_path: Path) -> list[tuple[int, str]]:
    """Extracts all headers from a markdown file.

    Parameters
    ----------
    md_file_path
        Path to the markdown file.

    Returns
    -------
    A list of tuples containing the level of the header and the header text.
    """
    with md_file_path.open("r") as infile:
        content = infile.read()

    # Regex to match markdown headers (e.g., ## Header)
    header_regex = re.compile(r"^(#+)\s+(.+)$", re.MULTILINE)

    # Extract headers
    return [
        (len(match.group(1)), match.group(2).strip())
        for match in header_regex.finditer(content)
    ]


def replace_links_in_markdown(
    md_file_path: Path,
    headers_mapping: dict[str, list[tuple[int, str]]],
    links: dict[str, str],
) -> None:
    """Replaces markdown links with updated links that point to the correct file and header anchor.

    Parameters
    ----------
    md_file_path
        Path to the markdown file to process.
    headers_mapping
        A dictionary where keys are markdown file names and values are lists of headers.
    links
        A dictionary of original header texts mapped to their slug (anchor) in the original README.
    """
    with md_file_path.open("r") as infile:
        content = infile.read()

    # Replace links based on headers_mapping and links dictionary
    for file_name, headers in headers_mapping.items():
        for _header_level, header_text in headers:
            # Find the original slug for this header text from the links dictionary
            original_slug = links.get(header_text, "")
            if original_slug:
                # Remove the '#' from the slug and update the link in the content
                original_slug = original_slug.lstrip("#")
                content = content.replace(
                    f"(#{original_slug})",
                    f"({file_name}#{original_slug})",
                )

    # Write updated content back to file
    with md_file_path.open("w") as outfile:
        outfile.write(content)


def process_readme_for_sphinx_docs(readme_path: Path, docs_path: Path) -> None:
    """Process the README.md file for Sphinx documentation generation.

    Parameters
    ----------
    readme_path
        Path to the original README.md file.
    docs_path
        Path to the Sphinx documentation source directory.
    """
    # Step 1: Copy README.md to the Sphinx source directory and apply transformations
    output_file = docs_path / "source" / "README.md"
    replace_named_emojis(readme_path, output_file)
    change_alerts_to_admonitions(output_file, output_file)
    replace_example_links(output_file, output_file)
    fix_anchors_with_named_emojis(output_file, output_file)

    # Step 2: Extract the table of contents links from the processed README
    links = extract_toc_links(output_file)

    # Step 3: Split the README into individual sections for Sphinx
    src_folder = docs_path / "source"
    for md_file in src_folder.glob("sections_*.md"):
        md_file.unlink()
    split_markdown_by_headers(output_file, src_folder)
    output_file.unlink()  # Remove the original README file from Sphinx source

    # Step 4: Extract headers from each section for link replacement
    headers_in_files = {}
    for md_file in src_folder.glob("*.md"):
        headers = extract_headers_from_markdown(md_file)
        headers_in_files[md_file.name] = headers

    # Rename the first section to 'intro.md' and update its header
    shutil.move(src_folder / "section_0.md", src_folder / "intro.md")  # type: ignore[arg-type]
    replace_header(src_folder / "intro.md", new_header="🌟 Introduction")

    # Step 5: Replace links in each markdown file to point to the correct section
    for md_file in (*src_folder.glob("*.md"), src_folder / "intro.md"):
        replace_links_in_markdown(md_file, headers_in_files, links)


readme_path = package_path / "README.md"
process_readme_for_sphinx_docs(readme_path, docs_path)
