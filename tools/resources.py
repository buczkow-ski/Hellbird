#!/usr/bin/env python3
"""Declarative resource overlay installer and verifier for Hellbird."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil


@dataclass(frozen=True)
class ResourceMapping:
    source: Path
    destination_rel: str
    manifest_path: Path


class ResourceError(RuntimeError):
    """Raised when resource manifests or overlay files are invalid."""


def _load_single_manifest(manifest_path: Path) -> list[ResourceMapping]:
    if not manifest_path.exists():
        return []

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ResourceError(f"Invalid JSON in {manifest_path}: {exc}") from exc

    raw_mappings = data.get("mappings")
    if not isinstance(raw_mappings, list):
        raise ResourceError(f"{manifest_path}: expected top-level 'mappings' list")

    base_dir = manifest_path.parent.resolve()
    result: list[ResourceMapping] = []

    for idx, item in enumerate(raw_mappings):
        if not isinstance(item, dict) or "src" not in item or "dest" not in item:
            raise ResourceError(f"{manifest_path}: mapping #{idx} must contain 'src' and 'dest'")
        src_rel = str(item["src"])
        dest_rel = str(item["dest"]).replace("\\", "/").lstrip("/")

        if not dest_rel or ".." in Path(dest_rel).parts:
            raise ResourceError(f"{manifest_path}: invalid destination path '{dest_rel}'")

        src_path = (base_dir / src_rel).resolve()
        try:
            src_path.relative_to(base_dir)
        except ValueError as exc:
            raise ResourceError(
                f"{manifest_path}: source path escapes manifest directory: {src_rel}"
            ) from exc

        if not src_path.is_file():
            raise ResourceError(f"{manifest_path}: missing source file: {src_path}")

        result.append(
            ResourceMapping(
                source=src_path,
                destination_rel=dest_rel,
                manifest_path=manifest_path,
            )
        )

    return result


def load_resource_mappings(resources_root: Path) -> list[ResourceMapping]:
    """Load root resources/manifest.json plus optional resources/overlay/manifest.json."""
    manifests = [
        resources_root / "manifest.json",
        resources_root / "overlay" / "manifest.json",
    ]
    mappings: list[ResourceMapping] = []
    seen_dests: dict[str, Path] = {}

    for manifest in manifests:
        for entry in _load_single_manifest(manifest):
            if entry.destination_rel in seen_dests:
                raise ResourceError(
                    f"Duplicate overlay destination '{entry.destination_rel}' "
                    f"(first in {seen_dests[entry.destination_rel]}, again in {entry.manifest_path})"
                )
            seen_dests[entry.destination_rel] = entry.manifest_path
            mappings.append(entry)

    return mappings


def apply_resources(
    resources_root: Path,
    target_dir: Path,
    *,
    dry_run: bool = False,
    verbose: bool = True,
) -> list[ResourceMapping]:
    """Copy all declared overlay resources into the target Ladybird source tree."""
    mappings = load_resource_mappings(resources_root)
    target_root = target_dir.resolve()

    for entry in mappings:
        dest_path = (target_root / entry.destination_rel).resolve()
        try:
            dest_path.relative_to(target_root)
        except ValueError as exc:
            raise ResourceError(f"Destination escapes target tree: {entry.destination_rel}") from exc

        if not dry_run:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry.source, dest_path)

        if verbose:
            verb = "CHECK" if dry_run else "COPY"
            print(f"  [{verb}] {entry.source.name} -> {entry.destination_rel}")

    return mappings
