#!/bin/bash
echo "Downloading and installing micromamba to ~/.local/bin/micromamba and uv to ~/.local/bin/uv"

# micromamba install command from https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html
VERSION="1.5.12-0" "${SHELL}" <(curl -L micro.mamba.pm/install.sh) < /dev/null

# uv install command from https://docs.astral.sh/uv/getting-started/installation/
curl -LsSf https://astral.sh/uv/install.sh | sh

# install unidep
~/.local/bin/uv tool install unidep

echo "Done installing micromamba and uv and unidep"
