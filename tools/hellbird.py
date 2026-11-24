#!/usr/bin/env python3
"""Hellbird build, patch, verification, and packaging CLI (Windows & Linux)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from patcher import (  # noqa: E402
    PATCH_STAGES,
    PatchError,
    apply_patches,
    find_unlisted_patches,
    load_all_patches,
    verify_patches_in_worktree,
)
from resources import ResourceError, apply_resources, load_resource_mappings  # noqa: E402

LADYBIRD_REPO_URL = "https://github.com/LadybirdBrowser/ladybird.git"
MANAGED_MARKER_REL = Path(".git") / "hellbird-managed"

# Matches Meta/CMake/presets/CMakeBasePresets.json binaryDir values
PRESET_BUILD_DIR_MAP: dict[str, str] = {
    "Release": "release",
    "Debug": "debug",
    "Sanitizer": "sanitizer",
    "ThreadSanitizer": "tsan",
    "All_Debug": "alldebug",
    "Distribution": "distribution",
    "Fuzzers": "fuzzers",
}

# Maps (host_system, preset) -> (vcpkg_type_suffix, ladybird_py_vcpkg_preset)
# On Windows, Distribution inherits Release_base (LADYBIRD_VCPKG_TYPE=release) so it uses
# shared libraries from Build/vcpkg-release instead of static-only vcpkg-distribution.
WINDOWS_VCPKG_MAP: dict[str, tuple[str, str]] = {
    "Release": ("release", "Release"),
    "Distribution": ("release", "Release"),
    "Debug": ("debug", "Debug"),
    "Sanitizer": ("sanitizer", "Sanitizer"),
}

UNIX_VCPKG_MAP: dict[str, tuple[str, str]] = {
    "Release": ("release", "Release"),
    "ThreadSanitizer": ("release", "Release"),
    "Debug": ("debug", "Debug"),
    "All_Debug": ("debug", "Debug"),
    "Sanitizer": ("sanitizer", "Sanitizer"),
    "Distribution": ("distribution", "Distribution"),
    "Fuzzers": ("distribution", "Distribution"),
}

REQUIRED_WINDOWS_HELPERS: tuple[str, ...] = (
    "WebContent.exe",
    "RequestServer.exe",
    "ImageDecoder.exe",
    "Compositor.exe",
)


def read_text_pin(filename: str) -> str:
    return (REPO_ROOT / filename).read_text(encoding="utf-8").strip()


def resolve_build_dir(upstream_dir: Path, preset: str, override: str | None = None) -> Path:
    if override:
        return Path(override).resolve()
    subdir = PRESET_BUILD_DIR_MAP.get(preset, preset.lower())
    return upstream_dir / "Build" / subdir


def resolve_vcpkg_config(preset: str, host_system: str | None = None) -> tuple[str, str]:
    """Return (vcpkg_type_dir_suffix, ladybird_py_preset) for a given preset & OS."""
    system = host_system or platform.system()
    table = WINDOWS_VCPKG_MAP if system == "Windows" else UNIX_VCPKG_MAP
    if preset not in table:
        raise RuntimeError(f"Unsupported CMake preset '{preset}' for host '{system}'")
    return table[preset]


def format_debian_version(hellbird_version: str) -> str:
    """Convert SemVer prerelease (0.1.0-alpha.1) to Debian-sortable version (0.1.0~alpha.1)."""
    if "-" in hellbird_version:
        base, pre = hellbird_version.split("-", 1)
        return f"{base}~{pre}"
    return hellbird_version


def run_cmd(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print(f"+ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=True)


def _assert_safe_upstream_dir(upstream_dir: Path, *, allow_existing_unmanaged: bool = False) -> None:
    """Guard against wiping Hellbird itself or a user's unrelated git repository."""
    resolved = upstream_dir.resolve()
    repo_resolved = REPO_ROOT.resolve()

    if resolved == repo_resolved or resolved in repo_resolved.parents:
        raise RuntimeError(
            f"Refusing destructive git operation on '{resolved}': path is Hellbird's own repository or an ancestor."
        )

    if resolved.exists():
        # Verify git toplevel matches upstream_dir exactly (never act on an enclosing repo!)
        probe = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(resolved),
            text=True,
            capture_output=True,
            check=False,
        )
        if probe.returncode == 0:
            toplevel = Path(probe.stdout.strip()).resolve()
            if toplevel != resolved:
                raise RuntimeError(
                    f"Refusing to operate on '{resolved}': it is inside another git repository ({toplevel}), "
                    "not a standalone Ladybird checkout."
                )
            marker = resolved / MANAGED_MARKER_REL
            if not marker.exists() and not allow_existing_unmanaged:
                raise RuntimeError(
                    f"Refusing to reset/clean '{resolved}' because it was not created by hellbird.py "
                    f"(missing {MANAGED_MARKER_REL}). Pass --force to override."
                )


def _mark_managed(upstream_dir: Path) -> None:
    marker = upstream_dir / MANAGED_MARKER_REL
    if marker.parent.is_dir():
        marker.write_text("managed-by-hellbird\n", encoding="utf-8", newline="\n")


def fetch_upstream(
    upstream_dir: Path,
    revision: str,
    tag: str,
    *,
    reference_repo: Path | None = None,
    force: bool = False,
) -> None:
    """Clone or reset upstream Ladybird to the exact pinned revision."""
    _assert_safe_upstream_dir(upstream_dir, allow_existing_unmanaged=force)

    if not (upstream_dir / ".git").exists():
        upstream_dir.parent.mkdir(parents=True, exist_ok=True)
        if reference_repo and (reference_repo / ".git").exists():
            run_cmd(
                [
                    "git",
                    "-c",
                    "core.longpaths=true",
                    "clone",
                    "--no-checkout",
                    str(reference_repo),
                    str(upstream_dir),
                ]
            )
        else:
            run_cmd(
                [
                    "git",
                    "-c",
                    "core.longpaths=true",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    tag,
                    LADYBIRD_REPO_URL,
                    str(upstream_dir),
                ]
            )
        run_cmd(["git", "config", "core.longpaths", "true"], cwd=upstream_dir)
        _mark_managed(upstream_dir)
    else:
        run_cmd(["git", "config", "core.longpaths", "true"], cwd=upstream_dir)
        run_cmd(["git", "-c", "core.longpaths=true", "reset", "--hard", "HEAD"], cwd=upstream_dir)
        run_cmd(["git", "-c", "core.longpaths=true", "clean", "-fd"], cwd=upstream_dir)

    check = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=str(upstream_dir),
        capture_output=True,
        check=False,
    )
    if check.returncode != 0:
        run_cmd(["git", "-c", "core.longpaths=true", "fetch", "--depth", "1", "origin", revision], cwd=upstream_dir)

    run_cmd(["git", "-c", "core.longpaths=true", "checkout", "--force", revision], cwd=upstream_dir)
    actual = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(upstream_dir), text=True
    ).strip()
    if actual != revision:
        raise RuntimeError(f"Upstream revision mismatch: expected {revision}, got {actual}")


def cmd_status(_args: argparse.Namespace) -> int:
    hellbird_version = read_text_pin("version.txt")
    lb_version = read_text_pin("ladybird_version.txt")
    lb_rev = read_text_pin("ladybird_revision.txt")
    patches = load_all_patches(REPO_ROOT / "patches")
    unlisted = find_unlisted_patches(REPO_ROOT / "patches")
    resources = load_resource_mappings(REPO_ROOT / "resources")

    print(f"Hellbird version  : {hellbird_version}")
    print(f"Ladybird upstream : {lb_version} ({lb_rev[:12]})")
    print(f"Stages            : {', '.join(PATCH_STAGES)}")
    print(f"Active patches    : {len(patches)}")
    for entry in patches:
        print(f"  - {entry.label}")
    print(f"Overlay resources : {len(resources)}")
    for item in resources:
        print(f"  - {item.source.relative_to(REPO_ROOT)} -> {item.destination_rel}")
    if unlisted:
        print("WARNING - unlisted .patch files:")
        for path in unlisted:
            print(f"  ! {path.relative_to(REPO_ROOT)}")
        return 1
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    upstream_dir = Path(args.upstream).resolve()
    revision = read_text_pin("ladybird_revision.txt")
    tag = read_text_pin("ladybird_version.txt")
    ref = Path(args.reference).resolve() if args.reference else None
    fetch_upstream(upstream_dir, revision, tag, reference_repo=ref, force=args.force)
    print(f"Upstream Ladybird ready at {upstream_dir} ({tag} @ {revision[:12]})")
    return 0


def cmd_prepare(args: argparse.Namespace) -> int:
    upstream_dir = Path(args.upstream).resolve()
    revision = read_text_pin("ladybird_revision.txt")
    tag = read_text_pin("ladybird_version.txt")
    ref = Path(args.reference).resolve() if args.reference else None

    if not args.no_fetch:
        fetch_upstream(upstream_dir, revision, tag, reference_repo=ref, force=args.force)
    else:
        if not (upstream_dir / ".git").exists():
            raise RuntimeError(f"--no-fetch requires an existing git checkout at {upstream_dir}")
        _assert_safe_upstream_dir(upstream_dir, allow_existing_unmanaged=args.force)
        run_cmd(["git", "config", "core.longpaths", "true"], cwd=upstream_dir)
        run_cmd(["git", "-c", "core.longpaths=true", "reset", "--hard", revision], cwd=upstream_dir)
        run_cmd(["git", "-c", "core.longpaths=true", "clean", "-fd"], cwd=upstream_dir)

    patches = load_all_patches(REPO_ROOT / "patches")
    print(f"Applying {len(patches)} Hellbird patches...")
    apply_patches(upstream_dir, patches, check_only=False, verbose=True)

    print("Applying Hellbird resource overlays...")
    apply_resources(REPO_ROOT / "resources", upstream_dir, dry_run=False, verbose=True)

    (upstream_dir / "REVISION").write_text(f"{revision}\n", encoding="utf-8", newline="\n")
    print("Hellbird source tree prepared successfully.")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    revision = read_text_pin("ladybird_revision.txt")
    patches_root = REPO_ROOT / "patches"
    resources_root = REPO_ROOT / "resources"

    unlisted = find_unlisted_patches(patches_root)
    if unlisted:
        print("Error: found unlisted .patch files not in any series file:", file=sys.stderr)
        for p in unlisted:
            print(f"  {p.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 1

    patches = load_all_patches(patches_root)
    mappings = load_resource_mappings(resources_root)
    print(f"Validated {len(patches)} patches and {len(mappings)} resource mappings.")

    if args.upstream:
        upstream_repo = Path(args.upstream).resolve()
        with tempfile.TemporaryDirectory(prefix="hellbird-check-") as tmp:
            wt = Path(tmp) / "worktree"
            print(f"Verifying patch stack cleanly in temporary worktree against {revision[:12]}...")
            verify_patches_in_worktree(upstream_repo, revision, patches, wt, verbose=True)
            print("All patches applied and reverse-applied cleanly!")
    else:
        print("Tip: pass --upstream <path-to-ladybird-git> to test full git apply & reverse-apply.")

    return 0


def _default_preset_for_host() -> str:
    return "Distribution"


def cmd_bootstrap_vcpkg(args: argparse.Namespace) -> int:
    upstream_dir = Path(args.upstream).resolve()
    if not upstream_dir.is_dir():
        raise RuntimeError(f"Upstream directory does not exist: {upstream_dir}. Run 'prepare' first.")
    preset = args.preset or _default_preset_for_host()
    vcpkg_type, ladybird_preset = resolve_vcpkg_config(preset)
    vcpkg_root = upstream_dir / "Build" / f"vcpkg-{vcpkg_type}"

    env = os.environ.copy()
    env["LADYBIRD_SOURCE_DIR"] = str(upstream_dir)
    # Overwrite any Visual Studio bundled VCPKG_ROOT so Ladybird's pinned vcpkg is always used
    env["VCPKG_ROOT"] = str(vcpkg_root)

    run_cmd([sys.executable, "Meta/ladybird.py", "vcpkg", "--preset", ladybird_preset], cwd=upstream_dir, env=env)
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    upstream_dir = Path(args.upstream).resolve()
    if not upstream_dir.is_dir():
        raise RuntimeError(f"Upstream directory does not exist: {upstream_dir}. Run 'prepare' first.")

    preset = args.preset or _default_preset_for_host()
    build_dir = resolve_build_dir(upstream_dir, preset, args.build_dir)
    vcpkg_type, _ = resolve_vcpkg_config(preset)
    vcpkg_root = upstream_dir / "Build" / f"vcpkg-{vcpkg_type}"

    hellbird_version = read_text_pin("version.txt")
    ladybird_version = read_text_pin("ladybird_version.txt")
    deb_version = format_debian_version(hellbird_version)

    env = os.environ.copy()
    env["LADYBIRD_SOURCE_DIR"] = str(upstream_dir)
    env["LADYBIRD_MAIN_SOURCE_DIR"] = str(upstream_dir)
    # Always pin VCPKG_ROOT to our bootstrapped vcpkg (prevents VS bundled vcpkg override)
    env["VCPKG_ROOT"] = str(vcpkg_root)

    configure_cmd = [
        "cmake",
        "--preset",
        preset,
        "-B",
        str(build_dir),
        f"-DLADYBIRD_VERSION={ladybird_version}",
        f"-DHELLBIRD_FULL_VERSION={hellbird_version}",
        f"-DHELLBIRD_PACKAGE_VERSION={deb_version}",
        "-DLADYBIRD_ENABLE_UPDATE_CHECK=OFF",
        "-DLADYBIRD_BASELINE_CPU_TARGET=ON",
    ]
    if args.link_pool_size:
        configure_cmd.append(f"-DLAGOM_LINK_POOL_SIZE={args.link_pool_size}")
    if args.build_qt:
        configure_cmd.append("-DLADYBIRD_BUILD_QT=ON")
    if args.no_tests:
        configure_cmd.append("-DBUILD_TESTING=OFF")
    if args.extra_cmake_arg:
        configure_cmd.extend(args.extra_cmake_arg)

    run_cmd(configure_cmd, cwd=upstream_dir, env=env)

    build_cmd = ["cmake", "--build", str(build_dir)]
    if args.jobs:
        build_cmd.extend(["--parallel", str(args.jobs)])
    if args.target:
        build_cmd.extend(["--target", args.target])
    run_cmd(build_cmd, cwd=upstream_dir, env=env)
    return 0


def _copy_msvc_crt_dlls(dest_bin_dir: Path) -> list[str]:
    """Copy app-local MSVC C/C++ runtime DLLs if VCToolsRedistDir is available."""
    redist_env = os.environ.get("VCToolsRedistDir")
    if not redist_env:
        return []
    redist_root = Path(redist_env) / "x64"
    if not redist_root.is_dir():
        return []

    copied: list[str] = []
    for crt_dir in sorted(redist_root.glob("Microsoft.VC*.CRT")):
        for dll in sorted(crt_dir.glob("*.dll")):
            shutil.copy2(dll, dest_bin_dir / dll.name)
            copied.append(dll.name)
    return copied


def _package_windows(build_dir: Path, dist_dir: Path, version: str) -> Path:
    bin_dir = build_dir / "bin"
    if not bin_dir.is_dir():
        raise RuntimeError(f"Expected Windows build bin directory at {bin_dir}")

    main_exe = bin_dir / "Hellbird.exe"
    if not main_exe.is_file():
        raise RuntimeError(f"Expected main executable at {main_exe}")

    missing_helpers = [h for h in REQUIRED_WINDOWS_HELPERS if not (bin_dir / h).is_file()]
    if missing_helpers:
        raise RuntimeError(f"Missing required Windows helper executables in {bin_dir}: {', '.join(missing_helpers)}")

    share_lagom = build_dir / "share" / "Lagom"
    if not share_lagom.is_dir():
        raise RuntimeError(f"Expected runtime resources directory at {share_lagom}")

    license_file = REPO_ROOT / "LICENSE"
    if not license_file.is_file():
        raise RuntimeError(f"Required LICENSE file missing at {license_file}")

    stage_name = f"hellbird-{version}-Windows-x86_64"
    stage_dir = dist_dir / stage_name
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)

    staged_bin = stage_dir / "bin"
    shutil.copytree(bin_dir, staged_bin, dirs_exist_ok=True)
    copied_crt = _copy_msvc_crt_dlls(staged_bin)
    if copied_crt:
        print(f"Bundled {len(copied_crt)} MSVC runtime DLLs into portable archive.")
    else:
        print("Warning: VCToolsRedistDir not set; archive relies on system VC++ Redistributable.", file=sys.stderr)

    shutil.copytree(build_dir / "share", stage_dir / "share", dirs_exist_ok=True)

    # Use newline="" so explicit \r\n is written verbatim without becoming \r\r\n on Windows
    (stage_dir / "Launch Hellbird.cmd").write_text(
        '@echo off\r\nstart "" /D "%~dp0bin" "%~dp0bin\\Hellbird.exe" %*\r\n',
        encoding="utf-8",
        newline="",
    )
    for meta_file in ("LICENSE", "README.md", "version.txt", "ladybird_version.txt"):
        src = REPO_ROOT / meta_file
        if src.is_file():
            shutil.copy2(src, stage_dir / meta_file)

    generated_licenses = share_lagom / "ladybird" / "LICENSES.txt"
    if generated_licenses.is_file():
        shutil.copy2(generated_licenses, stage_dir / "THIRD_PARTY_LICENSES.txt")

    zip_path = dist_dir / f"{stage_name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for file_path in sorted(stage_dir.rglob("*")):
            if file_path.is_file():
                zf.write(file_path, arcname=file_path.relative_to(dist_dir))

    print(f"Created Windows portable archive: {zip_path}")
    return zip_path


def _package_linux(build_dir: Path, dist_dir: Path, fmt: str) -> None:
    dist_dir.mkdir(parents=True, exist_ok=True)
    if fmt in ("txz", "all"):
        run_cmd(
            [
                "cpack",
                "-G",
                "TXZ",
                "-B",
                str(dist_dir),
                "-D",
                "CPACK_COMPONENTS_ALL=ladybird_Runtime",
                "-D",
                "CPACK_ARCHIVE_COMPRESSION_LEVEL=9",
                "-D",
                "CPACK_COMPONENT_INCLUDE_TOPLEVEL_DIRECTORY=ON",
            ],
            cwd=build_dir,
        )
    if fmt in ("deb", "all"):
        run_cmd(
            [
                "cpack",
                "-G",
                "DEB",
                "-B",
                str(dist_dir),
                "-D",
                "CPACK_COMPONENTS_ALL=ladybird_Runtime;ladybird_SystemIntegration",
            ],
            cwd=build_dir,
        )


def cmd_package(args: argparse.Namespace) -> int:
    upstream_dir = Path(args.upstream).resolve()
    preset = args.preset or _default_preset_for_host()
    build_dir = resolve_build_dir(upstream_dir, preset, args.build_dir)
    dist_dir = Path(args.out).resolve()
    dist_dir.mkdir(parents=True, exist_ok=True)
    version = read_text_pin("version.txt")

    if platform.system() == "Windows":
        _package_windows(build_dir, dist_dir, version)
    else:
        _package_linux(build_dir, dist_dir, args.format)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hellbird",
        description="Hellbird patch, overlay, build, and packaging CLI (Windows & Linux).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Show pinned versions, active patches, and resource overlays.")
    p_status.set_defaults(func=cmd_status)

    p_fetch = sub.add_parser("fetch", help="Clone or reset upstream Ladybird to the pinned revision.")
    p_fetch.add_argument("--upstream", default=str(REPO_ROOT / "upstream" / "ladybird"))
    p_fetch.add_argument("--reference", default=None, help="Optional local Ladybird git clone to clone from.")
    p_fetch.add_argument("--force", action="store_true", help="Allow resetting an existing unmanaged checkout.")
    p_fetch.set_defaults(func=cmd_fetch)

    p_prep = sub.add_parser("prepare", help="Fetch upstream Ladybird, apply all patches and resource overlays.")
    p_prep.add_argument("--upstream", default=str(REPO_ROOT / "upstream" / "ladybird"))
    p_prep.add_argument("--reference", default=None, help="Optional local Ladybird git clone to clone from.")
    p_prep.add_argument("--no-fetch", action="store_true", help="Reset existing upstream directory without cloning.")
    p_prep.add_argument("--force", action="store_true", help="Allow resetting an existing unmanaged checkout.")
    p_prep.set_defaults(func=cmd_prepare)

    p_check = sub.add_parser("check", help="Validate patch series, resources, and optionally verify git apply.")
    p_check.add_argument("--upstream", default=None, help="Local Ladybird git repo to verify patches against.")
    p_check.set_defaults(func=cmd_check)

    p_vcpkg = sub.add_parser("bootstrap-vcpkg", help="Bootstrap Ladybird's pinned vcpkg matching the chosen preset.")
    p_vcpkg.add_argument("--upstream", default=str(REPO_ROOT / "upstream" / "ladybird"))
    p_vcpkg.add_argument("--preset", default=None, help="CMake preset (default: Distribution).")
    p_vcpkg.set_defaults(func=cmd_bootstrap_vcpkg)

    p_build = sub.add_parser("build", help="Configure and build Hellbird with CMake.")
    p_build.add_argument("--upstream", default=str(REPO_ROOT / "upstream" / "ladybird"))
    p_build.add_argument("--preset", default=None, help="CMake preset (default: Distribution on Windows and Linux).")
    p_build.add_argument("--build-dir", default=None, help="Override CMake build directory.")
    p_build.add_argument("--jobs", "-j", type=int, default=None, help="Parallel build jobs.")
    p_build.add_argument("--link-pool-size", type=int, default=None, help="Set LAGOM_LINK_POOL_SIZE for low-RAM builds.")
    p_build.add_argument("--build-qt", action="store_true", help="Pass -DLADYBIRD_BUILD_QT=ON (Linux with Qt < 6.10).")
    p_build.add_argument("--no-tests", action="store_true", help="Pass -DBUILD_TESTING=OFF to reduce build time and RAM.")
    p_build.add_argument("--target", default=None, help="Optional specific CMake target (e.g. ladybird).")
    p_build.add_argument("--extra-cmake-arg", action="append", default=[], help="Extra -D... argument passed to CMake.")
    p_build.set_defaults(func=cmd_build)

    p_pkg = sub.add_parser("package", help="Package Hellbird for Windows (.zip) or Linux (.tar.xz / .deb).")
    p_pkg.add_argument("--upstream", default=str(REPO_ROOT / "upstream" / "ladybird"))
    p_pkg.add_argument("--preset", default=None, help="CMake preset (default: Distribution).")
    p_pkg.add_argument("--build-dir", default=None)
    p_pkg.add_argument("--out", default=str(REPO_ROOT / "dist"))
    p_pkg.add_argument("--format", choices=("txz", "deb", "all"), default="all", help="Linux package format.")
    p_pkg.set_defaults(func=cmd_package)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (
        PatchError,
        ResourceError,
        RuntimeError,
        FileNotFoundError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
