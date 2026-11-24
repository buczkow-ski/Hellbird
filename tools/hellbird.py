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


def read_text_pin(filename: str) -> str:
    return (REPO_ROOT / filename).read_text(encoding="utf-8").strip()


def run_cmd(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print(f"+ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=True)


def fetch_upstream(upstream_dir: Path, revision: str, tag: str, *, reference_repo: Path | None = None) -> None:
    """Clone or update upstream Ladybird to the exact pinned revision."""
    if not (upstream_dir / ".git").exists():
        upstream_dir.parent.mkdir(parents=True, exist_ok=True)
        if reference_repo and (reference_repo / ".git").exists():
            run_cmd(["git", "clone", "--no-checkout", str(reference_repo), str(upstream_dir)])
        else:
            run_cmd(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    tag,
                    LADYBIRD_REPO_URL,
                    str(upstream_dir),
                ]
            )
    else:
        # Ensure clean tree before switching revision
        run_cmd(["git", "reset", "--hard", "HEAD"], cwd=upstream_dir)
        run_cmd(["git", "clean", "-fd"], cwd=upstream_dir)

    # Verify revision exists locally; fetch explicitly if needed
    check = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=str(upstream_dir),
        capture_output=True,
        check=False,
    )
    if check.returncode != 0:
        run_cmd(["git", "fetch", "--depth", "1", "origin", revision], cwd=upstream_dir)

    run_cmd(["git", "checkout", "--force", revision], cwd=upstream_dir)
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
    fetch_upstream(upstream_dir, revision, tag, reference_repo=ref)
    print(f"Upstream Ladybird ready at {upstream_dir} ({tag} @ {revision[:12]})")
    return 0


def cmd_prepare(args: argparse.Namespace) -> int:
    upstream_dir = Path(args.upstream).resolve()
    revision = read_text_pin("ladybird_revision.txt")
    tag = read_text_pin("ladybird_version.txt")
    ref = Path(args.reference).resolve() if args.reference else None

    if not args.no_fetch:
        fetch_upstream(upstream_dir, revision, tag, reference_repo=ref)
    else:
        run_cmd(["git", "reset", "--hard", revision], cwd=upstream_dir)
        run_cmd(["git", "clean", "-fd"], cwd=upstream_dir)

    patches = load_all_patches(REPO_ROOT / "patches")
    print(f"Applying {len(patches)} Hellbird patches...")
    apply_patches(upstream_dir, patches, check_only=False, verbose=True)

    print("Applying Hellbird resource overlays...")
    apply_resources(REPO_ROOT / "resources", upstream_dir, dry_run=False, verbose=True)

    # Record pinned upstream revision for non-git builds / metainfo generation
    (upstream_dir / "REVISION").write_text(f"{revision}\n", encoding="utf-8")
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


def cmd_build(args: argparse.Namespace) -> int:
    upstream_dir = Path(args.upstream).resolve()
    if not upstream_dir.is_dir():
        raise RuntimeError(f"Upstream directory does not exist: {upstream_dir}. Run 'prepare' first.")

    preset = args.preset or _default_preset_for_host()
    build_dir = Path(args.build_dir).resolve() if args.build_dir else (upstream_dir / "Build" / preset.lower())
    hellbird_version = read_text_pin("version.txt")
    # Ladybird's CMake validates LADYBIRD_VERSION as X.Y.Z integers
    cmake_version = hellbird_version.split("-", 1)[0]

    env = os.environ.copy()
    env["LADYBIRD_SOURCE_DIR"] = str(upstream_dir)

    configure_cmd = [
        "cmake",
        "--preset",
        preset,
        "-B",
        str(build_dir),
        f"-DLADYBIRD_VERSION={cmake_version}",
        "-DLADYBIRD_ENABLE_UPDATE_CHECK=OFF",
        "-DLADYBIRD_BASELINE_CPU_TARGET=ON",
    ]
    if args.link_pool_size:
        configure_cmd.append(f"-DLAGOM_LINK_POOL_SIZE={args.link_pool_size}")
    if args.build_qt:
        configure_cmd.append("-DLADYBIRD_BUILD_QT=ON")
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


def _package_windows(build_dir: Path, dist_dir: Path, version: str) -> Path:
    bin_dir = build_dir / "bin"
    if not bin_dir.is_dir():
        raise RuntimeError(f"Expected Windows build bin directory at {bin_dir}")

    stage_name = f"hellbird-{version}-Windows-x86_64"
    stage_dir = dist_dir / stage_name
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)

    # Copy binaries, Qt DLLs/plugins deployed by qt_generate_deploy_script, and helper executables
    shutil.copytree(bin_dir, stage_dir / "bin", dirs_exist_ok=True)

    # Copy Lagom resources (share/Lagom) required at runtime
    share_dir = build_dir / "share"
    if share_dir.is_dir():
        shutil.copytree(share_dir, stage_dir / "share", dirs_exist_ok=True)

    # Add convenient top-level launcher script and metadata
    (stage_dir / "Launch Hellbird.cmd").write_text(
        '@echo off\r\nstart "" "%~dp0bin\\Hellbird.exe" %*\r\n',
        encoding="utf-8",
    )
    for meta_file in ("LICENSE", "README.md", "version.txt", "ladybird_version.txt"):
        src = REPO_ROOT / meta_file
        if src.is_file():
            shutil.copy2(src, stage_dir / meta_file)

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
    build_dir = Path(args.build_dir).resolve() if args.build_dir else (upstream_dir / "Build" / preset.lower())
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
    p_fetch.set_defaults(func=cmd_fetch)

    p_prep = sub.add_parser("prepare", help="Fetch upstream Ladybird, apply all patches and resource overlays.")
    p_prep.add_argument("--upstream", default=str(REPO_ROOT / "upstream" / "ladybird"))
    p_prep.add_argument("--reference", default=None, help="Optional local Ladybird git clone to clone from.")
    p_prep.add_argument("--no-fetch", action="store_true", help="Reset existing upstream directory without cloning.")
    p_prep.set_defaults(func=cmd_prepare)

    p_check = sub.add_parser("check", help="Validate patch series, resources, and optionally verify git apply.")
    p_check.add_argument("--upstream", default=None, help="Local Ladybird git repo to verify patches against.")
    p_check.set_defaults(func=cmd_check)

    p_build = sub.add_parser("build", help="Configure and build Hellbird with CMake.")
    p_build.add_argument("--upstream", default=str(REPO_ROOT / "upstream" / "ladybird"))
    p_build.add_argument("--preset", default=None, help="CMake preset (default: Release on Windows, Distribution on Linux).")
    p_build.add_argument("--build-dir", default=None, help="Override CMake build directory.")
    p_build.add_argument("--jobs", "-j", type=int, default=None, help="Parallel build jobs.")
    p_build.add_argument("--link-pool-size", type=int, default=None, help="Set LAGOM_LINK_POOL_SIZE for low-RAM builds.")
    p_build.add_argument("--build-qt", action="store_true", help="Pass -DLADYBIRD_BUILD_QT=ON (Linux with Qt < 6.10).")
    p_build.add_argument("--target", default=None, help="Optional specific CMake target (e.g. ladybird).")
    p_build.add_argument("--extra-cmake-arg", action="append", default=[], help="Extra -D... argument passed to CMake.")
    p_build.set_defaults(func=cmd_build)

    p_pkg = sub.add_parser("package", help="Package Hellbird for Windows (.zip) or Linux (.tar.xz / .deb).")
    p_pkg.add_argument("--upstream", default=str(REPO_ROOT / "upstream" / "ladybird"))
    p_pkg.add_argument("--preset", default=None)
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
    except (PatchError, ResourceError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
