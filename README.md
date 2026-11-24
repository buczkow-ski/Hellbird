<div align="center">
  <img src="resources/branding/hellbird-256.png" alt="Hellbird Logo" width="128" />
  <h1>Hellbird</h1>
  <p><strong>Helium's privacy, consent, and minimalist UX — powered by the independent Ladybird web engine.</strong></p>
</div>

---

## Overview

**Hellbird** combines the design ethos and ergonomic workflow of [Helium](https://github.com/imputnet/helium) with the from-scratch [Ladybird](https://github.com/ladybirdbrowser/ladybird) browser engine (pinned to Ladybird Alpha **`2026.47.7`**).

First-class build targets:
- **Linux (x86_64)** — portable `.tar.xz` archive and `.deb` package
- **Windows (x86_64)** — portable `.zip` archive (native ClangCL + Qt 6)

### Key Principles
- **Privacy & Consent First**: Global Privacy Control (`Sec-GPC: 1`) enabled by default, zero unsolicited upstream update checks or telemetry/report pings, privacy-respecting content blocking and search defaults.
- **Helium Ergonomics**: Built-in native `!bangs` (`!gh`, `!ddg`, `!w`, `!mdn`, `!so`, `!yt`, `!k`, and more), privacy-first search presets, fast keyboard shortcuts (`Ctrl+Shift+C` to copy current URL), and clean tab management.
- **Minimalist Visual Chrome**: Calm monochrome/slate palette with a subtle ember accent, crisp borders, uncluttered toolbar/tab bar, and a serene zero-bloat New Tab Page.
- **Reproducible Patch-Overlay Architecture**: Just like Helium, Hellbird keeps upstream Ladybird pinned cleanly and manages all changes as ordered, auditable patch stages (`patches/build`, `patches/privacy`, `patches/features`, `patches/ui`) plus declarative resource overlays (`resources/manifest.json`).

---

## Repository Structure

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full technical layout and [docs/UX-DESIGN.md](docs/UX-DESIGN.md) for the Helium-to-Ladybird UI/UX specification.

| Directory | Purpose |
|---|---|
| `patches/build/` | CMake branding, packaging (`.tar.xz`, `.deb`, Windows `.ico`/`.zip`), Freedesktop integration |
| `patches/privacy/` | Zero-telemetry defaults, Global Privacy Control, privacy-first settings |
| `patches/features/` | Native `!bangs`, privacy search engine presets, Helium shortcuts & tab actions |
| `patches/ui/` | Minimalist Qt chrome theme/layout, New Tab Page, `about:version` & `about:settings` polish |
| `resources/` | Hellbird SVG/PNG/ICO brand icons & declarative overlay manifests |
| `tools/` | Cross-platform Python CLI (`hellbird.py`) for fetching, patching, checking, building, and packaging |
| `.github/workflows/` | Automated patch verification and full Linux + Windows CI/Release build pipelines |

---

## Quick Start

### 1. Verify patches and tooling locally (works on any machine, ~8 GB RAM is plenty)

```bash
python3 -m unittest discover -s tests -v
python3 tools/hellbird.py status
python3 tools/hellbird.py check --upstream /path/to/ladybird
```

### 2. Prepare the patched source tree

```bash
python3 tools/hellbird.py prepare
```

This checks out Ladybird `2026.47.7` (`4e9acf4c02ca`), applies all patch stages in order, and installs Hellbird's branding and resource overlays into `upstream/ladybird/`.

### 3. Build & Package

> **Hardware note:** Compiling Ladybird from source requires a C++23 compiler (Clang 22 / GCC 16 on Linux, Clang-CL + Visual Studio 2022 on Windows), Rust, CMake 3.30+, Ninja, and at least 16 GB RAM recommended (or use `--link-pool-size 1` on lower-RAM machines). For machines without a local C++/Rust toolchain, push to GitHub and trigger the included **Build Linux (x64)** / **Build Windows (x64)** GitHub Actions workflows!

#### Linux (x86_64 — Ubuntu 24.04 LTS with bundled Qt 6.10, or Ubuntu 26.04)
```bash
# Ubuntu 24.04 (bundles Qt 6.10 via vcpkg):
python3 tools/hellbird.py prepare
python3 tools/hellbird.py bootstrap-vcpkg --preset Distribution
python3 tools/hellbird.py build --preset Distribution --build-qt --link-pool-size 1 --no-tests
python3 tools/hellbird.py package --preset Distribution --format all --out dist

# Ubuntu 26.04 / ghcr.io/ladybirdbrowser/ladybird-ci:2026.11.21 (system Qt 6.10):
python3 tools/hellbird.py build --preset Distribution --link-pool-size 1 --no-tests
```

#### Windows (x86_64 — Visual Studio x64 Developer Command Prompt)
```bat
python tools\hellbird.py prepare
python tools\hellbird.py bootstrap-vcpkg --preset Distribution
python tools\hellbird.py build --preset Distribution --no-tests --target ladybird
python tools\hellbird.py package --preset Distribution --out dist
```

Output portable archive: `dist/hellbird-0.1.0-alpha.1-Windows-x86_64.zip`

---

## Automated Builds (GitHub Actions)

Four workflows are pre-configured in `.github/workflows/`:
1. **`verify.yml`** — Runs on every push/PR: Python unit tests, resource checks, and full patch stack apply + reverse-apply verification against Ladybird `2026.47.7`.
2. **`build-linux.yml`** — Builds Hellbird on Linux x64 using Ladybird's CI container and uploads `.tar.xz` and `.deb` artifacts.
3. **`build-windows.yml`** — Builds Hellbird natively on `windows-2025` with `clang-cl` + vcpkg + Qt 6 and uploads the portable `.zip` archive.
4. **`release.yml`** — Runs both Linux and Windows builds on `v*` git tags and publishes a GitHub prerelease with SHA-256 checksums.

---

## Credits & Licensing

- **Engine**: [Ladybird Browser Initiative](https://ladybird.org/) (BSD-2-Clause)
- **UI/UX & Privacy Inspiration**: [Helium Browser](https://github.com/imputnet/helium) by imputnet
- **Hellbird**: Original patches, tooling, and assets are provided under the terms of the project license while preserving all upstream licenses.
