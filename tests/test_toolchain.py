#!/usr/bin/env python3
"""Unit and integration tests for Hellbird's patcher, resource overlay, and CLI."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import bench_ram  # noqa: E402
import hellbird  # noqa: E402
import patcher  # noqa: E402
import resources  # noqa: E402


class PatcherTests(unittest.TestCase):
    def test_load_stage_series_ignores_comments_and_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "patches"
            stage = root / "build"
            stage.mkdir(parents=True)
            (stage / "01-first.patch").write_text("", encoding="utf-8")
            (stage / "02-second.patch").write_text("", encoding="utf-8")
            (stage / "series").write_text(
                "# Header comment\n\n01-first.patch\n02-second.patch\t# tab inline comment\n",
                encoding="utf-8",
            )

            entries = patcher.load_stage_series(root, "build")
            self.assertEqual([e.relative_path for e in entries], ["01-first.patch", "02-second.patch"])

    def test_load_stage_series_rejects_missing_and_escaping_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "patches"
            stage = root / "build"
            stage.mkdir(parents=True)
            (stage / "series").write_text("../escape.patch\n", encoding="utf-8")
            with self.assertRaises(patcher.PatchError):
                patcher.load_stage_series(root, "build")

            (stage / "series").write_text("does-not-exist.patch\n", encoding="utf-8")
            with self.assertRaises(patcher.PatchError):
                patcher.load_stage_series(root, "build")

    def test_apply_and_verify_roundtrip_including_new_file_and_rename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            upstream = tmp_path / "upstream"
            upstream.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=upstream, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=upstream, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=upstream, check=True)
            (upstream / "hello.txt").write_text("Ladybird\n", encoding="utf-8")
            (upstream / "old_name.txt").write_text("move me\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=upstream, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=upstream, check=True, capture_output=True)
            rev = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=upstream, text=True).strip()

            (upstream / "hello.txt").write_text("Hellbird\n", encoding="utf-8")
            (upstream / "added.txt").write_text("brand new\n", encoding="utf-8")
            subprocess.run(["git", "mv", "old_name.txt", "new_name.txt"], cwd=upstream, check=True)
            subprocess.run(["git", "add", "-N", "added.txt"], cwd=upstream, check=True)
            diff_text = subprocess.check_output(["git", "diff", "HEAD"], cwd=upstream, text=True)
            subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=upstream, check=True, capture_output=True)
            subprocess.run(["git", "clean", "-fd"], cwd=upstream, check=True, capture_output=True)

            patches_root = tmp_path / "patches"
            (patches_root / "build").mkdir(parents=True)
            (patches_root / "build" / "01-combined.patch").write_text(diff_text, encoding="utf-8")
            (patches_root / "build" / "series").write_text("01-combined.patch\n", encoding="utf-8")

            entries = patcher.load_all_patches(patches_root)
            patcher.verify_patches_in_worktree(
                upstream, rev, entries, tmp_path / "wt-check", verbose=False
            )
            self.assertFalse((tmp_path / "wt-check").exists())


class ResourceOverlayTests(unittest.TestCase):
    def test_apply_and_merge_root_and_overlay_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            res_root = tmp_path / "resources"
            (res_root / "branding").mkdir(parents=True)
            (res_root / "overlay").mkdir(parents=True)
            (res_root / "branding" / "icon.png").write_bytes(b"PNGDATA")
            (res_root / "overlay" / "style.css").write_text("body{}\n", encoding="utf-8")

            (res_root / "manifest.json").write_text(
                json.dumps({"mappings": [{"src": "branding/icon.png", "dest": "UI/Icons/ladybird.png"}]}),
                encoding="utf-8",
            )
            (res_root / "overlay" / "manifest.json").write_text(
                json.dumps({"mappings": [{"src": "style.css", "dest": "Base/res/ladybird/extra.css"}]}),
                encoding="utf-8",
            )

            target = tmp_path / "target"
            target.mkdir()
            applied = resources.apply_resources(res_root, target, verbose=False)
            self.assertEqual(len(applied), 2)
            self.assertEqual((target / "UI/Icons/ladybird.png").read_bytes(), b"PNGDATA")
            self.assertEqual((target / "Base/res/ladybird/extra.css").read_text(encoding="utf-8"), "body{}\n")

    def test_rejects_duplicate_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            res_root = Path(tmp) / "resources"
            (res_root / "branding").mkdir(parents=True)
            (res_root / "overlay").mkdir(parents=True)
            (res_root / "branding" / "a.txt").write_text("a", encoding="utf-8")
            (res_root / "overlay" / "b.txt").write_text("b", encoding="utf-8")
            (res_root / "manifest.json").write_text(
                json.dumps({"mappings": [{"src": "branding/a.txt", "dest": "same.txt"}]}),
                encoding="utf-8",
            )
            (res_root / "overlay" / "manifest.json").write_text(
                json.dumps({"mappings": [{"src": "b.txt", "dest": "same.txt"}]}),
                encoding="utf-8",
            )
            with self.assertRaises(resources.ResourceError):
                resources.load_resource_mappings(res_root)


class BuildAndPackagingLogicTests(unittest.TestCase):
    def test_vcpkg_and_build_dir_preset_mappings(self) -> None:
        # C1 regression guard: Windows Distribution uses release vcpkg, Linux/macOS use distribution
        self.assertEqual(hellbird.resolve_vcpkg_config("Distribution", "Windows"), ("release", "Release"))
        self.assertEqual(hellbird.resolve_vcpkg_config("Distribution", "Linux"), ("distribution", "Distribution"))
        self.assertEqual(hellbird.resolve_vcpkg_config("Distribution", "Darwin"), ("distribution", "Distribution"))
        self.assertEqual(hellbird.resolve_vcpkg_config("Release", "Windows"), ("release", "Release"))
        self.assertEqual(hellbird.resolve_vcpkg_config("Release", "Darwin"), ("release", "Release"))
        self.assertEqual(hellbird.resolve_vcpkg_config("Debug", "Windows"), ("debug", "Debug"))

        root = Path("/tmp/lb")
        self.assertEqual(hellbird.resolve_build_dir(root, "ThreadSanitizer").name, "tsan")
        self.assertEqual(hellbird.resolve_build_dir(root, "Distribution").name, "distribution")

    def test_debian_prerelease_version_formatting(self) -> None:
        self.assertEqual(hellbird.format_debian_version("0.1.0-alpha.1"), "0.1.0~alpha.1")
        self.assertEqual(hellbird.format_debian_version("1.2.3"), "1.2.3")

    def test_safe_upstream_dir_guards(self) -> None:
        # Must refuse Hellbird repo root and ancestors
        with self.assertRaises(RuntimeError):
            hellbird._assert_safe_upstream_dir(REPO_ROOT)
        with self.assertRaises(RuntimeError):
            hellbird._assert_safe_upstream_dir(REPO_ROOT.parent)

        # Must refuse unmanaged standalone git repo unless forced
        with tempfile.TemporaryDirectory() as tmp:
            personal = Path(tmp) / "personal-repo"
            personal.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=personal, check=True, capture_output=True)
            with self.assertRaises(RuntimeError):
                hellbird._assert_safe_upstream_dir(personal, allow_existing_unmanaged=False)
            hellbird._assert_safe_upstream_dir(personal, allow_existing_unmanaged=True)

            # Once marked managed, allowed without force
            hellbird._mark_managed(personal)
            hellbird._assert_safe_upstream_dir(personal, allow_existing_unmanaged=False)

    def test_package_windows_validates_layout_and_exact_crlf_cmd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            build_dir = tmp_path / "Build" / "distribution"
            bin_dir = build_dir / "bin"
            share_dir = build_dir / "share" / "Lagom" / "ladybird"
            bin_dir.mkdir(parents=True)
            share_dir.mkdir(parents=True)

            # Missing Hellbird.exe must raise
            with self.assertRaises(RuntimeError):
                hellbird._package_windows(build_dir, tmp_path / "dist", "0.1.0-alpha.1")

            (bin_dir / "Hellbird.exe").write_bytes(b"MZ")
            for helper in hellbird.REQUIRED_WINDOWS_HELPERS:
                (bin_dir / helper).write_bytes(b"MZ")
            (share_dir / "LICENSES.txt").write_text("licenses\n", encoding="utf-8")

            zip_path = hellbird._package_windows(build_dir, tmp_path / "dist", "0.1.0-alpha.1")
            self.assertTrue(zip_path.is_file())
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = set(zf.namelist())
                prefix = "hellbird-0.1.0-alpha.1-Windows-x86_64/"
                self.assertIn(f"{prefix}bin/Hellbird.exe", names)
                self.assertIn(f"{prefix}LICENSE", names)
                self.assertIn(f"{prefix}THIRD_PARTY_LICENSES.txt", names)
                cmd_bytes = zf.read(f"{prefix}Launch Hellbird.cmd")
                self.assertNotIn(b"\r\r\n", cmd_bytes)
                self.assertIn(b"\r\n", cmd_bytes)


class RepoIntegrityTests(unittest.TestCase):
    def test_pins_and_branding_assets_are_valid(self) -> None:
        self.assertRegex(hellbird.read_text_pin("version.txt"), r"^\d+\.\d+\.\d+")
        self.assertRegex(hellbird.read_text_pin("ladybird_version.txt"), r"^\d+\.\d+\.\d+$")
        self.assertRegex(hellbird.read_text_pin("ladybird_revision.txt"), r"^[0-9a-f]{40}$")
        self.assertTrue((REPO_ROOT / "LICENSE").is_file())

        mappings = resources.load_resource_mappings(REPO_ROOT / "resources")
        self.assertGreaterEqual(len(mappings), 6)
        self.assertEqual(patcher.find_unlisted_patches(REPO_ROOT / "patches"), [])

        png_sig = b"\x89PNG\r\n\x1a\n"
        self.assertTrue((REPO_ROOT / "resources/branding/hellbird-48.png").read_bytes().startswith(png_sig))
        self.assertTrue((REPO_ROOT / "resources/branding/hellbird-256.png").read_bytes().startswith(png_sig))
        self.assertTrue((REPO_ROOT / "resources/branding/macos/app_icon.iconset/icon_512x512@2x.png").read_bytes().startswith(png_sig))
        self.assertTrue((REPO_ROOT / "resources/branding/hellbird.ico").read_bytes().startswith(b"\x00\x00\x01\x00"))
        self.assertIn("VERSIONINFO", (REPO_ROOT / "resources/branding/hellbird.rc").read_text(encoding="utf-8"))


class BenchRamTests(unittest.TestCase):
    def test_classify_process_role(self) -> None:
        self.assertEqual(bench_ram.classify_process_role("Hellbird.exe"), "browser-ui")
        self.assertEqual(bench_ram.classify_process_role("WebContent.exe"), "webcontent")
        self.assertEqual(bench_ram.classify_process_role("Compositor"), "compositor")
        self.assertEqual(bench_ram.classify_process_role("RequestServer.exe"), "requestserver")
        self.assertEqual(bench_ram.classify_process_role("VideoDecoderSer"), "videodecoderserver")
        self.assertEqual(bench_ram.classify_process_role("AudioServer.exe"), "audioserver")
        self.assertEqual(
            bench_ram.classify_process_role("helium.exe", "helium.exe --type=renderer"),
            "renderer",
        )
        self.assertEqual(
            bench_ram.classify_process_role(
                "helium.exe",
                "helium.exe --type=utility --utility-sub-type=network.mojom.NetworkService",
            ),
            "network",
        )
        self.assertNotIn("chrome.exe", bench_ram.HELIUM_PROCESS_NAMES)
        cmd = bench_ram._build_bench_command("hellbird", Path("Hellbird.exe"), Path("tmp_prof"), ["https://example.com"])
        self.assertIn("--profile-path", cmd)

    def test_parse_linux_smaps_rollup_and_budgets(self) -> None:
        sample = (
            "00400000-7fff0000 ---p 00000000 00:00 0 [rollup]\n"
            "Rss:              102400 kB\n"
            "Pss:               51200 kB\n"
            "Private_Clean:     10240 kB\n"
            "Private_Dirty:     30720 kB\n"
        )
        pss_mb, priv_mb = bench_ram.parse_linux_smaps_rollup(sample)
        self.assertEqual(pss_mb, 50.0)
        self.assertEqual(priv_mb, 40.0)

        snap = bench_ram.BrowserSnapshot(
            browser="hellbird",
            timestamp_epoch=1.0,
            process_count=4,
            total_private_ws_mb=120.0,
            total_commit_mb=600.0,
            total_peak_commit_mb=700.0,
            total_pss_mb=None,
            total_gpu_committed_mb=50.0,
            system=bench_ram.SystemMemoryInfo(8192.0, 2048.0, 10000.0, 20000.0),
            processes=[
                bench_ram.ProcessSample(100, "Hellbird.exe", "browser-ui", 20.0, 100.0, 150.0, None, 10.0),
                bench_ram.ProcessSample(200, "WebContent.exe", "webcontent", 100.0, 500.0, 550.0, None, 40.0),
            ],
        )
        table = bench_ram.format_snapshot_table(snap)
        self.assertIn("HELLBIRD (4 processes)", table)
        self.assertEqual(bench_ram.check_budgets(snap, max_commit_mb=700.0, max_processes=6), [])
        failures = bench_ram.check_budgets(snap, max_commit_mb=500.0, max_private_ws_mb=100.0, max_processes=3)
        self.assertEqual(len(failures), 3)

        empty_snap = bench_ram.BrowserSnapshot(
            browser="hellbird",
            timestamp_epoch=1.0,
            process_count=0,
            total_private_ws_mb=0.0,
            total_commit_mb=0.0,
            total_peak_commit_mb=0.0,
            total_pss_mb=None,
            total_gpu_committed_mb=None,
            system=bench_ram.SystemMemoryInfo(8192.0, 2048.0, 10000.0, 20000.0),
            processes=[],
        )
        self.assertEqual(len(bench_ram.check_budgets(empty_snap, max_commit_mb=500.0)), 1)


if __name__ == "__main__":
    unittest.main()
