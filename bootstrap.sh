#!/usr/bin/env bash
# Run this script with:
#   "${SHELL}" <(curl -LsSf raw.githubusercontent.com/basnijholt/unidep/main/bootstrap.sh)
#
# 🚀 UniDep - Unified Conda and Pip Dependency Management 🚀
#
# This script downloads and installs:
#  - micromamba to ~/.local/bin/micromamba (for fast Conda environment management)
#  - uv to ~/.local/bin/uv (for fast pip installations)
#  - unidep (to manage unified Conda and Pip dependencies)
#
# UniDep streamlines Python project dependency management by combining both Conda
# and Pip dependencies into a single system. For more information, visit:
# https://github.com/basnijholt/unidep
#
# If you prefer to run the commands manually, you can execute each section one by one.
# Otherwise, piping this script directly to your default shell ensures everything is installed in one go.

echo "Downloading and installing micromamba to ~/.local/bin/micromamba and uv to ~/.local/bin/uv"

# Install micromamba (https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html)
"${SHELL}" <(curl -LsSf micro.mamba.pm/install.sh) < /dev/null

# Install uv (https://docs.astral.sh/uv/getting-started/installation/)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install unidep using uv
~/.local/bin/uv tool install --quiet -U "unidep[all]"

echo "Done installing micromamba, uv, and unidep"
