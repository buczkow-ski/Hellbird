#!/usr/bin/env python3
"""Ordered multi-stage patch loader and git-apply runner for Hellbird."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
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
        # Strip inline comments preceded by whitespace (spaces or tabs)
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if not line:
            continue

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


def _run_git(
    args: list[str],
    cwd: Path,
    *,
    input_bytes: bytes | None = None,
    text: bool = True,
) -> subprocess.CompletedProcess:
    cmd = ["git", "-c", "core.longpaths=true", *args]
    if text:
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        input=input_bytes,
        capture_output=True,
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
    """Apply (or dry-run check) patches against a Ladybird source tree."""
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


def _unquote_git_path(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        # Decode C-style git quoted paths (octal escapes, \", \\)
        inner = raw[1:-1]
        out = bytearray()
        i = 0
        while i < len(inner):
            ch = inner[i]
            if ch == "\\" and i + 1 < len(inner):
                nxt = inner[i + 1]
                if nxt in "01234567" and i + 3 < len(inner):
                    out.append(int(inner[i + 1 : i + 4], 8))
                    i += 4
                    continue
                escapes = {"n": 10, "r": 13, "t": 9, "\\": 92, '"': 34}
                if nxt in escapes:
                    out.append(escapes[nxt])
                    i += 2
                    continue
            out.extend(ch.encode("utf-8"))
            i += 1
        return out.decode("utf-8", errors="surrogateescape")
    return raw


def extract_touched_paths(patches: list[PatchEntry], git_cwd: Path) -> set[str]:
    """Extract all touched paths from patches (modifications, additions, deletions,
    pure renames, copies, mode changes, binary diffs, and quoted paths)."""
    touched: set[str] = set()
    for entry in patches:
        proc = _run_git(["apply", "--numstat", "-z", str(entry.absolute_path)], cwd=git_cwd, text=False)
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            raise PatchError(f"Failed to parse patch metadata for {entry.label}:\n{stderr}")

        parts = proc.stdout.split(b"\x00")
        i = 0
        while i < len(parts):
            token = parts[i]
            if not token:
                i += 1
                continue
            tab_fields = token.split(b"\t", 2)
            if len(tab_fields) == 3:
                if tab_fields[2]:
                    touched.add(tab_fields[2].decode("utf-8", errors="surrogateescape"))
                    i += 1
                else:
                    if i + 2 < len(parts):
                        for raw_p in (parts[i + 1], parts[i + 2]):
                            if raw_p:
                                touched.add(raw_p.decode("utf-8", errors="surrogateescape"))
                    i += 3
            else:
                i += 1

        # Also parse git diff headers to capture rename/copy source paths (`git apply --numstat`
        # omits the source path for pure 100% renames).
        for line in entry.absolute_path.read_text(encoding="utf-8", errors="replace").splitlines():
            for prefix in ("rename from ", "rename to ", "copy from ", "copy to "):
                if line.startswith(prefix):
                    touched.add(_unquote_git_path(line[len(prefix) :]))
                    break
            else:
                if line.startswith("--- a/"):
                    touched.add(_unquote_git_path(line[6:].split("\t", 1)[0]))
                elif line.startswith("+++ b/"):
                    touched.add(_unquote_git_path(line[6:].split("\t", 1)[0]))
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
        touched = sorted(extract_touched_paths(patches, upstream_repo))
        existing_in_rev: list[str] = []
        if touched:
            # Query all existing paths in revision using null-terminated output
            ls_proc = _run_git(["ls-tree", "-r", "-z", "--name-only", revision], cwd=worktree_dir, text=False)
            if ls_proc.returncode != 0:
                stderr = ls_proc.stderr.decode("utf-8", errors="replace").strip()
                raise PatchError(f"Failed to list tree at {revision}:\n{stderr}")
            rev_files = {
                raw.decode("utf-8", errors="surrogateescape")
                for raw in ls_proc.stdout.split(b"\x00")
                if raw
            }
            existing_in_rev = [p for p in touched if p in rev_files]

        if existing_in_rev:
            pathspec_payload = b"\x00".join(p.encode("utf-8", errors="surrogateescape") for p in existing_in_rev) + b"\x00"
            co_proc = _run_git(
                ["checkout", revision, "--pathspec-from-file=-", "--pathspec-file-nul"],
                cwd=worktree_dir,
                input_bytes=pathspec_payload,
                text=False,
            )
            if co_proc.returncode != 0:
                stderr = co_proc.stderr.decode("utf-8", errors="replace").strip()
                raise PatchError(f"Could not materialize patched files at {revision}:\n{stderr}")

        apply_patches(worktree_dir, patches, check_only=False, reverse=False, verbose=verbose)
        apply_patches(worktree_dir, patches, check_only=False, reverse=True, verbose=False)

        if existing_in_rev:
            pathspec_payload = b"\x00".join(p.encode("utf-8", errors="surrogateescape") for p in existing_in_rev) + b"\x00"
            diff_proc = _run_git(
                ["diff", "--pathspec-from-file=-", "--pathspec-file-nul"],
                cwd=worktree_dir,
                input_bytes=pathspec_payload,
                text=False,
            )
            diff_out = diff_proc.stdout.decode("utf-8", errors="replace").strip()
            if diff_out:
                raise PatchError(
                    "Modified files were not restored cleanly after reverse-applying all patches:\n"
                    + diff_out
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
            _run_git(["worktree", "prune"], cwd=upstream_repo)
