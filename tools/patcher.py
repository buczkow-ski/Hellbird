#!/usr/bin/env python3
"""Ordered multi-stage patch loader and git-apply runner for Hellbird."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys

PATCH_STAGES: tuple[str, ...] = (
    "build",
    "privacy",
    "features",
    "ui",
)


@dataclass(frozen=True)
class PatchEntry:
    stage: str
    relative_path: str
    absolute_path: Path

    @property
    def label(self) -> str:
        return f"{self.stage}/{self.relative_path}"


class PatchError(RuntimeError):
    """Raised when a patch series or patch application fails."""


def load_stage_series(patches_root: Path, stage: str) -> list[PatchEntry]:
    """Load ordered patch entries for a single stage."""
    stage_dir = patches_root / stage
    series_file = stage_dir / "series"
    if not series_file.exists():
        return []

    entries: list[PatchEntry] = []
    seen: set[Path] = set()

    for line_no, raw_line in enumerate(series_file.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Allow inline comments separated by whitespace + '#'
        if " #" in line:
            line = line.split(" #", 1)[0].strip()

        patch_path = (stage_dir / line).resolve()
        try:
            patch_path.relative_to(stage_dir.resolve())
        except ValueError as exc:
            raise PatchError(
                f"{series_file}:{line_no}: patch path escapes stage directory: {line}"
            ) from exc

        if not patch_path.is_file():
            raise PatchError(f"{series_file}:{line_no}: missing patch file: {patch_path}")
        if patch_path in seen:
            raise PatchError(f"{series_file}:{line_no}: duplicate patch entry: {line}")

        seen.add(patch_path)
        entries.append(PatchEntry(stage=stage, relative_path=line, absolute_path=patch_path))

    return entries


def load_all_patches(patches_root: Path, stages: tuple[str, ...] = PATCH_STAGES) -> list[PatchEntry]:
    """Load all patches across stages in canonical order."""
    entries: list[PatchEntry] = []
    for stage in stages:
        entries.extend(load_stage_series(patches_root, stage))
    return entries


def find_unlisted_patches(patches_root: Path, stages: tuple[str, ...] = PATCH_STAGES) -> list[Path]:
    """Return any .patch files on disk that are not referenced in their stage series file."""
    listed = {entry.absolute_path.resolve() for entry in load_all_patches(patches_root, stages)}
    unlisted: list[Path] = []
    for stage in stages:
        stage_dir = patches_root / stage
        if not stage_dir.is_dir():
            continue
        for candidate in sorted(stage_dir.rglob("*.patch")):
            if candidate.resolve() not in listed:
                unlisted.append(candidate)
    return unlisted


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def apply_patches(
    target_dir: Path,
    patches: list[PatchEntry],
    *,
    check_only: bool = False,
    reverse: bool = False,
    verbose: bool = True,
) -> list[PatchEntry]:
    """Apply (or dry-run check) patches against a Ladybird source tree.

    Note: In check_only mode, multiple patches that touch overlapping files cannot be
    validated purely with sequential `--check` without actually applying them, so
    callers verifying a full stack should apply into a clean throwaway worktree/copy
    or use `verify_patches_in_worktree`.
    """
    ordered = list(reversed(patches)) if reverse else list(patches)
    applied: list[PatchEntry] = []

    for entry in ordered:
        cmd = ["apply", "--whitespace=nowarn"]
        if reverse:
            cmd.append("--reverse")
        if check_only:
            cmd.append("--check")
        cmd.append(str(entry.absolute_path))

        proc = _run_git(cmd, cwd=target_dir)
        if proc.returncode != 0:
            action = "reverse-check" if (reverse and check_only) else (
                "reverse-apply" if reverse else ("check" if check_only else "apply")
            )
            detail = (proc.stderr or proc.stdout).strip()
            raise PatchError(f"Failed to {action} patch {entry.label}:\n{detail}")

        applied.append(entry)
        if verbose:
            verb = "CHECK" if check_only else ("REVERT" if reverse else "APPLY")
            print(f"  [{verb}] {entry.label}")

    return applied


def _extract_touched_paths(patches: list[PatchEntry]) -> set[str]:
    """Extract all repo-relative file paths referenced by unified diffs."""
    touched: set[str] = set()
    for entry in patches:
        for line in entry.absolute_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("--- a/"):
                touched.add(line[6:].split("\t", 1)[0])
            elif line.startswith("+++ b/"):
                touched.add(line[6:].split("\t", 1)[0])
    return touched


def verify_patches_in_worktree(
    upstream_repo: Path,
    revision: str,
    patches: list[PatchEntry],
    worktree_dir: Path,
    *,
    verbose: bool = True,
) -> None:
    """Create a lightweight detached temporary git worktree at `revision` checking out
    only the files touched by `patches`, apply all patches in order, confirm
    reverse-application cleanliness, and tear down the worktree cleanly."""
    worktree_dir = worktree_dir.resolve()
    if worktree_dir.exists():
        raise PatchError(f"Temporary worktree path already exists: {worktree_dir}")

    add_proc = _run_git(
        ["worktree", "add", "--no-checkout", "--detach", str(worktree_dir), revision],
        cwd=upstream_repo,
    )
    if add_proc.returncode != 0:
        raise PatchError(
            f"Could not create verification worktree at {revision}:\n{add_proc.stderr.strip()}"
        )

    try:
        touched = sorted(_extract_touched_paths(patches))
        existing_in_rev: list[str] = []
        if touched:
            ls_proc = _run_git(["ls-tree", "-r", "--name-only", revision, "--", *touched], cwd=worktree_dir)
            if ls_proc.returncode == 0:
                existing_in_rev = [p for p in ls_proc.stdout.splitlines() if p.strip()]
        if existing_in_rev:
            co_proc = _run_git(["checkout", revision, "--", *existing_in_rev], cwd=worktree_dir)
            if co_proc.returncode != 0:
                raise PatchError(
                    f"Could not materialize patched files at {revision}:\n{co_proc.stderr.strip()}"
                )

        apply_patches(worktree_dir, patches, check_only=False, reverse=False, verbose=verbose)
        # Verify reverse-apply cleanliness as a round-trip sanity check
        apply_patches(worktree_dir, patches, check_only=False, reverse=True, verbose=False)
        diff_proc = _run_git(["diff", "--", *existing_in_rev] if existing_in_rev else ["diff"], cwd=worktree_dir)
        if diff_proc.stdout.strip():
            raise PatchError(
                "Modified files were not restored cleanly after reverse-applying all patches:\n"
                + diff_proc.stdout.strip()
            )
        untracked = _run_git(["ls-files", "--others", "--exclude-standard"], cwd=worktree_dir)
        if untracked.stdout.strip():
            raise PatchError(
                "Untracked files remained after reverse-applying all patches:\n"
                + untracked.stdout.strip()
            )
    finally:
        rm_proc = _run_git(["worktree", "remove", "--force", str(worktree_dir)], cwd=upstream_repo)
        if rm_proc.returncode != 0:
            print(
                f"Warning: failed to remove temporary worktree {worktree_dir}: {rm_proc.stderr.strip()}",
                file=sys.stderr,
            )
