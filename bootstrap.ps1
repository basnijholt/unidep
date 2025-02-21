<#
.SYNOPSIS
    Bootstrap installer for micromamba, uv, and unidep on Windows.

.DESCRIPTION
    This script installs:
      - micromamba (for fast Conda environment management)
      - uv (for fast pip installations)
      - unidep (to manage unified Conda and Pip dependencies)
    It mirrors the functionality of the Unix bootstrap script, but is tailored for Windows.
    When run in non-interactive mode (using -NonInteractive), micromamba is initialized with the default prefix.

.INSTRUCTIONS:
    To run this script directly from the web, open PowerShell and execute:
        iwr -UseBasicParsing -Uri https://raw.githubusercontent.com/basnijholt/unidep/main/bootstrap.ps1 | iex

    Alternatively, download the file locally and run it with:
        Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass; .\bootstrap.ps1

    Ensure that your execution policy allows running scripts or use the bypass flag as shown.
#>

Write-Host "Downloading and installing micromamba and uv..."

# Install micromamba in non-interactive mode
powershell -NonInteractive -ExecutionPolicy Bypass -Command "irm 'https://raw.githubusercontent.com/basnijholt/micromamba-releases/refs/heads/defaults/install.ps1' | iex"

# Install uv in non-interactive mode
powershell -NonInteractive -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"

# Install unidep using uv
# Note: uv should now be available in your PATH; if not, restart PowerShell or add its install location.
uv.exe tool install --quiet -U "unidep[all]"

Write-Host "Done installing micromamba, uv, and unidep"
