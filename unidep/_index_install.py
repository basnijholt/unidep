"""Package-index install helpers used by the CLI."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from unidep._artifact_metadata import (
    CondaExecutable,
    UnidepMetadata,
    UnidepMetadataError,
    extract_unidep_metadata_from_wheel,
    select_unidep_dependencies,
)
from unidep.utils import (
    dedupe,
    identify_current_platform,
    parse_folder_or_filename,
    split_path_and_extras,
)

if TYPE_CHECKING:
    from unidep.platform_definitions import Platform


@dataclass(frozen=True)
class InstallRuntime:
    """Callbacks and execution hooks provided by the CLI module."""

    maybe_conda_executable: Callable[[], CondaExecutable | None]
    maybe_conda_run: Callable[
        [CondaExecutable | None, str | None, Path | None],
        list[str],
    ]
    python_executable: Callable[
        [CondaExecutable | None, str | None, Path | None],
        str,
    ]
    maybe_create_conda_env_args: Callable[
        [CondaExecutable, str | None, Path | None],
        list[str],
    ]
    maybe_exe: Callable[[CondaExecutable], str]
    format_inline_conda_package: Callable[[str], str]
    use_uv: Callable[[bool], bool]
    identify_current_platform: Callable[[], Platform]
    run_subprocess: Callable[..., Any] = subprocess.run


def _build_pip_install_command(
    *,
    python_executable: str,
    conda_run: list[str],
    no_uv: bool,
    use_uv: Callable[[bool], bool],
) -> list[str]:
    if use_uv(no_uv):
        return [
            *conda_run,
            "uv",
            "pip",
            "install",
            "--python",
            python_executable,
        ]
    return [*conda_run, python_executable, "-m", "pip", "install"]


def _pip_install_packages(
    *packages: str,
    dry_run: bool,
    python_executable: str,
    conda_run: list[str],
    no_uv: bool,
    use_uv: Callable[[bool], bool],
    run_subprocess: Callable[..., Any],
    flags: list[str] | None = None,
    description: str = "pip dependencies",
) -> None:
    """Install package specs with pip/uv."""
    if not packages:
        return
    pip_command = _build_pip_install_command(
        python_executable=python_executable,
        conda_run=conda_run,
        no_uv=no_uv,
        use_uv=use_uv,
    )
    if flags:
        pip_command.extend(flags)
    pip_command.extend(packages)
    print(f"📦 Installing {description} with `{' '.join(pip_command)}`\n")
    if not dry_run:
        run_subprocess(pip_command, check=True)


def classify_install_targets(targets: list[str]) -> tuple[list[Path], list[str]]:
    """Classify install targets as local requirement files or package specs.

    Accepts raw strings so that PEP 508 specifiers (e.g. URLs with ``://``)
    are not corrupted by ``Path`` normalisation.  Local targets are converted
    to ``Path`` only after classification.
    """
    local_targets: list[Path] = []
    package_specs: list[str] = []
    for candidate in targets:
        try:
            parse_folder_or_filename(candidate)
        except FileNotFoundError as exc:  # noqa: PERF203
            candidate_path, _extras = split_path_and_extras(candidate)
            if candidate_path.exists():
                # Existing local paths should never be interpreted as package specs.
                raise ValueError(str(exc)) from exc
            try:
                Requirement(candidate)
            except InvalidRequirement as requirement_exc:
                msg = (
                    f"`{candidate}` is neither a valid package requirement specifier"
                    " nor an existing local path."
                )
                raise ValueError(msg) from requirement_exc
            package_specs.append(candidate)
        else:
            local_targets.append(Path(candidate))
    return local_targets, package_specs


def _download_package_artifact(
    package_spec: str,
    *,
    destination: Path,
    python_executable: str,
    conda_run: list[str],
    dry_run: bool,
    run_subprocess: Callable[..., Any],
) -> Path | None:
    """Download a package artifact with pip for metadata inspection."""
    download_command = [
        *conda_run,
        python_executable,
        "-m",
        "pip",
        "download",
        "--no-deps",
        "--dest",
        str(destination),
        package_spec,
    ]
    print(f"📦 Downloading package artifact with `{' '.join(download_command)}`\n")
    if dry_run:
        print(
            "⚠️  Dry-run: skipping download. UniDep metadata cannot be inspected"
            " without downloading the artifact; the install plan shown below may"
            " differ from an actual run (e.g. conda dependencies may be missing).",
        )
        return None
    run_subprocess(download_command, check=True)

    wheels = sorted(destination.glob("*.whl"))
    if wheels:
        return wheels[-1]

    source_dists = sorted(
        [
            *destination.glob("*.tar.gz"),
            *destination.glob("*.zip"),
            *destination.glob("*.tar.bz2"),
            *destination.glob("*.tar.xz"),
        ],
    )
    if source_dists:
        return source_dists[-1]

    # pip may download nothing when environment markers evaluate to false
    # (e.g. ``pkg; sys_platform == "win32"`` on Linux).  Treat this as a
    # non-fatal case: the caller will fall back to a plain pip install.
    return None


def _parse_requirement_or_none(spec: str) -> Requirement | None:
    try:
        return Requirement(spec)
    except InvalidRequirement:
        return None


def _parse_package_requirements(package_specs: tuple[str, ...]) -> list[Requirement]:
    requirements: list[Requirement] = []
    invalid_specs: list[str] = []
    for spec in package_specs:
        requirement = _parse_requirement_or_none(spec)
        if requirement is None:
            invalid_specs.append(spec)
            continue
        requirements.append(requirement)
    if invalid_specs:
        msg = f"Invalid package requirement specifier(s): {', '.join(invalid_specs)}."
        raise ValueError(msg)
    return requirements


def _warn_ignored_package_install_flags(
    *,
    editable: bool,
    skip_local: bool,
    ignore_pins: list[str] | None,
    overwrite_pins: list[str] | None,
    skip_dependencies: list[str] | None,
) -> None:
    if editable:
        print("⚠️  `--editable` is ignored for package-spec installs.")
    if skip_local:
        print("⚠️  `--skip-local` is ignored for package-spec installs.")
    if ignore_pins:
        print("⚠️  `--ignore-pin` is ignored for package-spec installs.")
    if overwrite_pins:
        print("⚠️  `--overwrite-pin` is ignored for package-spec installs.")
    if skip_dependencies:
        print("⚠️  `--skip-dependency` is ignored for package-spec installs.")


def _load_unidep_metadata_for_spec(
    package_spec: str,
    *,
    destination: Path,
    python_executable: str,
    conda_run: list[str],
    dry_run: bool,
    run_subprocess: Callable[..., Any],
) -> UnidepMetadata | None:
    artifact = _download_package_artifact(
        package_spec,
        destination=destination,
        python_executable=python_executable,
        conda_run=conda_run,
        dry_run=dry_run,
        run_subprocess=run_subprocess,
    )
    if artifact is None:
        return None

    if artifact.suffix != ".whl":
        print(
            f"⚠️  Downloaded source distribution `{artifact.name}` for"
            f" `{package_spec}`. UniDep metadata inspection requires a wheel;"
            " falling back to pip-only install.",
        )
        return None

    try:
        metadata = extract_unidep_metadata_from_wheel(artifact)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
        UnidepMetadataError,
    ) as exc:
        print(
            f"⚠️  Invalid UniDep metadata in `{artifact.name}` ({exc})."
            " Falling back to pip-only install.",
        )
        return None
    if metadata is None:
        print(
            f"⚠️  No UniDep metadata found in `{artifact.name}`."
            " Falling back to pip-only install.",
        )
        return None
    return metadata


def _install_conda_dependencies(
    conda_dependencies: list[str],
    *,
    channels: list[str],
    conda_executable: CondaExecutable,
    conda_env_name: str | None,
    conda_env_prefix: Path | None,
    dry_run: bool,
    maybe_create_conda_env_args: Callable[
        [CondaExecutable, str | None, Path | None],
        list[str],
    ],
    maybe_exe: Callable[[CondaExecutable], str],
    format_inline_conda_package: Callable[[str], str],
    run_subprocess: Callable[..., Any],
) -> None:
    channel_args = ["--override-channels"] if channels else []
    for channel in channels:
        channel_args.extend(["--channel", channel])
    conda_env_args = maybe_create_conda_env_args(
        conda_executable,
        conda_env_name,
        conda_env_prefix,
    )
    conda_command = [
        maybe_exe(conda_executable),
        "install",
        "--yes",
        *channel_args,
        *conda_env_args,
    ]
    to_print = [format_inline_conda_package(pkg) for pkg in conda_dependencies]
    conda_command_str = " ".join((*conda_command, *to_print))
    print(f"📦 Installing conda dependencies with `{conda_command_str}`\n")
    if not dry_run:
        run_subprocess((*conda_command, *conda_dependencies), check=True)


def install_package_specs_command(  # noqa: C901, PLR0912, PLR0915
    *package_specs: str,
    conda_executable: CondaExecutable | None,
    conda_env_name: str | None,
    conda_env_prefix: Path | None,
    conda_lock_file: Path | None,
    dry_run: bool,
    editable: bool,
    skip_local: bool = False,
    skip_pip: bool = False,
    skip_conda: bool = False,
    no_dependencies: bool = False,
    ignore_pins: list[str] | None = None,
    overwrite_pins: list[str] | None = None,
    skip_dependencies: list[str] | None = None,
    no_uv: bool = True,
    verbose: bool = False,
    runtime: InstallRuntime | None = None,
) -> None:
    """Install dependencies and package specs from package index artifacts."""
    del verbose  # currently unused for package-spec installs
    runtime = runtime or InstallRuntime(
        maybe_conda_executable=lambda: None,
        maybe_conda_run=lambda *_args, **_kwargs: [],
        python_executable=lambda *_args, **_kwargs: sys.executable,
        maybe_create_conda_env_args=lambda *_args, **_kwargs: [],
        maybe_exe=lambda c: c,
        format_inline_conda_package=lambda p: p,
        use_uv=lambda _no_uv: False,
        identify_current_platform=identify_current_platform,
    )
    start_time = time.time()
    if conda_lock_file is not None:
        print(
            "❌ `--conda-lock-file` is only supported for local requirements installs.",
        )
        sys.exit(1)
    _warn_ignored_package_install_flags(
        editable=editable,
        skip_local=skip_local,
        ignore_pins=ignore_pins,
        overwrite_pins=overwrite_pins,
        skip_dependencies=skip_dependencies,
    )
    if no_dependencies:
        skip_pip = True
        skip_conda = True

    requirements = _parse_package_requirements(package_specs)

    if not conda_executable:
        conda_executable = runtime.maybe_conda_executable()
    platform_name = runtime.identify_current_platform()

    # Use the *current* interpreter for the download/inspection phase so that
    # we don't require the target conda env to exist yet.  The target env's
    # python is resolved later, after conda install may have created it.
    download_python = sys.executable

    channels: list[str] = []
    conda_deps: list[str] = []
    pip_deps: list[str] = []
    with_metadata: list[str] = []
    # Specs whose metadata contained conda deps — tracked as
    # (no-deps-install-spec, original-user-spec) pairs so that when
    # ``--skip-conda`` is set we can move the package to pip fallback
    # *without* losing extras/markers from the original requirement.
    with_metadata_has_conda: list[tuple[str, str]] = []
    fallback_to_pip: list[str] = []

    with tempfile.TemporaryDirectory(prefix="unidep-download-") as tmpdir:
        download_root = Path(tmpdir)
        for index, req in enumerate(requirements):
            package_spec = package_specs[index]
            destination = download_root / f"pkg-{index}"
            destination.mkdir(parents=True, exist_ok=True)
            metadata = _load_unidep_metadata_for_spec(
                package_spec,
                destination=destination,
                python_executable=download_python,
                conda_run=[],
                dry_run=dry_run,
                run_subprocess=runtime.run_subprocess,
            )

            if metadata is None:
                fallback_to_pip.append(package_spec)
                continue

            try:
                selected = select_unidep_dependencies(
                    metadata,
                    platform=platform_name,
                    extras=sorted(req.extras),
                )
            except UnidepMetadataError as exc:
                print(
                    "⚠️  UniDep metadata for"
                    f" `{package_spec}` is unusable on `{platform_name}` ({exc})."
                    " Falling back to pip-only install.",
                )
                fallback_to_pip.append(package_spec)
                continue

            if selected.missing_extras:
                print(
                    "⚠️  UniDep metadata for"
                    f" `{package_spec}` does not define extra(s):"
                    f" {', '.join(selected.missing_extras)}."
                    " Falling back to pip-only install so pip can resolve"
                    " those extras.",
                )
                fallback_to_pip.append(package_spec)
                continue

            # Validate that the metadata project name matches the requested
            # package.  A mismatch (even just normalisation, e.g. ``my_pkg``
            # vs ``my-pkg``) could cause ``pip install --no-deps`` to resolve
            # a different package on PyPI.
            if canonicalize_name(req.name) != canonicalize_name(metadata.project):
                print(
                    f"⚠️  UniDep metadata project name `{metadata.project}` does not"
                    f" match requested package `{req.name}`."
                    " Falling back to pip-only install.",
                )
                fallback_to_pip.append(package_spec)
                continue

            channels.extend(selected.channels)
            conda_deps.extend(selected.conda)
            pip_deps.extend(selected.pip)
            # Preserve direct references (``name @ url`` / ``name @ file://``)
            # so installation uses the exact artifact that was inspected.
            # For non-direct specs (e.g. ranges), pin to the inspected version
            # to avoid drift between inspection and final ``--no-deps`` install.
            # Use ``req.name`` (the user's requested name) rather than
            # ``metadata.project`` so the pinned spec always matches the
            # canonical PyPI package name the user asked for.
            install_spec = package_spec
            if req.url is None:
                install_spec = f"{req.name}=={metadata.version}"
            with_metadata.append(install_spec)
            if selected.conda:
                with_metadata_has_conda.append((install_spec, package_spec))

    channels = dedupe(channels)
    conda_deps = dedupe(conda_deps)
    pip_deps = dedupe(pip_deps)
    with_metadata = dedupe(with_metadata)
    fallback_to_pip = dedupe(fallback_to_pip)

    if conda_deps and skip_conda:
        print("⚠️  Skipping UniDep Conda dependencies because `--skip-conda` is set.")
        # Packages whose metadata required conda deps cannot be safely
        # installed with ``--no-deps`` when conda is skipped. Move them to
        # the pip fallback list so pip can resolve their dependencies.
        #
        # Use the original requirement specifier for fallback so extras and
        # markers are preserved (e.g. ``pkg[dev]>=1; python_version>='3.10'``).
        removed_no_deps_specs: set[str] = set()
        for install_spec, original_spec in with_metadata_has_conda:
            fallback_to_pip.append(original_spec)
            if (
                install_spec in with_metadata
                and install_spec not in removed_no_deps_specs
            ):
                with_metadata.remove(install_spec)
                removed_no_deps_specs.add(install_spec)
        fallback_to_pip = dedupe(fallback_to_pip)
    if conda_deps and not skip_conda:
        if conda_executable is None:
            print(
                "❌ UniDep metadata requires Conda dependencies, but no conda"
                " executable was found (`conda`, `mamba`, or `micromamba`).",
            )
            print(
                "Install micromamba/conda and retry, or rerun with `--skip-conda` to"
                " force a pip-only attempt.",
            )
            sys.exit(1)
        _install_conda_dependencies(
            conda_deps,
            channels=channels,
            conda_executable=conda_executable,
            conda_env_name=conda_env_name,
            conda_env_prefix=conda_env_prefix,
            dry_run=dry_run,
            maybe_create_conda_env_args=runtime.maybe_create_conda_env_args,
            maybe_exe=runtime.maybe_exe,
            format_inline_conda_package=runtime.format_inline_conda_package,
            run_subprocess=runtime.run_subprocess,
        )

    # Resolve the target env's python *after* conda install, which may have
    # created the environment.
    python_executable = runtime.python_executable(
        conda_executable,
        conda_env_name,
        conda_env_prefix,
    )
    conda_run = runtime.maybe_conda_run(
        conda_executable,
        conda_env_name,
        conda_env_prefix,
    )

    if pip_deps and not skip_pip:
        _pip_install_packages(
            *pip_deps,
            dry_run=dry_run,
            python_executable=python_executable,
            conda_run=conda_run,
            no_uv=no_uv,
            use_uv=runtime.use_uv,
            run_subprocess=runtime.run_subprocess,
            description="pip dependencies from UniDep metadata",
        )

    if with_metadata:
        _pip_install_packages(
            *with_metadata,
            dry_run=dry_run,
            python_executable=python_executable,
            conda_run=conda_run,
            no_uv=no_uv,
            use_uv=runtime.use_uv,
            run_subprocess=runtime.run_subprocess,
            flags=["--no-deps"],
            description="package specs (with UniDep metadata)",
        )

    # Only pass --no-deps for fallback packages when the user explicitly asked
    # to skip *all* dependencies (--no-dependencies).  A bare --skip-pip only
    # suppresses pip deps from UniDep metadata; it should not prevent pip from
    # resolving transitive dependencies for non-UniDep packages.
    fallback_flags = ["--no-deps"] if no_dependencies else None
    if fallback_to_pip:
        _pip_install_packages(
            *fallback_to_pip,
            dry_run=dry_run,
            python_executable=python_executable,
            conda_run=conda_run,
            no_uv=no_uv,
            use_uv=runtime.use_uv,
            run_subprocess=runtime.run_subprocess,
            flags=fallback_flags,
            description="package specs (pip fallback)",
        )

    if not dry_run:
        total_time = time.time() - start_time
        msg = f"✅ All dependencies installed successfully in {total_time:.2f} seconds."
        print(msg)
