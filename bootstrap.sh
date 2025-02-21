#!/usr/bin/env bash
# Run this script with:
#   curl -LsSf https://raw.githubusercontent.com/basnijholt/unidep/main/bootstrap.sh | bash
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
# Otherwise, piping this script directly to bash ensures everything is installed in one go.

if [ -z "$BASH_VERSION" ]; then
  exec bash "$0" "$@"
fi

echo "Downloading and installing micromamba to ~/.local/bin/micromamba and uv to ~/.local/bin/uv"

# Download the micromamba installer to a temporary file and execute it
TMP_MAMBA=$(mktemp)
curl -LsSf micro.mamba.pm/install.sh -o "$TMP_MAMBA"
VERSION="1.5.12-0" "${SHELL}" "$TMP_MAMBA" < /dev/null
rm "$TMP_MAMBA"

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install unidep using uv
~/.local/bin/uv tool install unidep

echo "Done installing micromamba, uv, and unidep"
