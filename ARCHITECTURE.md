# Hellbird Architecture & Contribution Contract

**Hellbird** is a privacy-first, minimalist desktop web browser combining the **UI/UX philosophy and privacy/ergonomic features of [Helium](https://github.com/imputnet/helium)** with the independent **[Ladybird](https://github.com/ladybirdbrowser/ladybird)** web engine (pinned to Ladybird Alpha **`2026.47.8`**, commit `0be55a57cc07ed258362c631a13a10bb4f6d707a`).

First-class target platforms: **Windows (x64)**, **Linux (x64)**, and **macOS (arm64)**.

---

## 1. Repository Layout (Helium-style Patch Overlay)

Instead of vendoring the full ~500k+ LOC Ladybird repository, Hellbird uses a clean, reproducible patch-series and resource-overlay architecture (mirroring `imputnet/helium`):

```text
Hellbird/
├── version.txt                  # Hellbird release version (e.g. 0.1.0-alpha.6)
├── ladybird_version.txt         # Pinned upstream Ladybird tag (2026.47.8)
├── ladybird_revision.txt        # Pinned upstream Ladybird full commit SHA
├── ARCHITECTURE.md              # This contract
├── README.md                    # User & developer documentation
├── LICENSE                      # BSD-2-Clause / upstream attribution
├── patches/
│   ├── build/
│   │   ├── series               # Stage 1: Build, branding & packaging patches
│   │   └── *.patch
│   ├── privacy/
│   │   ├── series               # Stage 2: Privacy-by-default & zero-telemetry patches
│   │   └── *.patch
│   ├── features/
│   │   ├── series               # Stage 3: Helium UX features (!bangs, search, shortcuts, tabs)
│   │   └── *.patch
│   └── ui/
│       ├── series               # Stage 4: Helium visual theme, layout, NTP, about/settings UI
│       └── *.patch
├── resources/
│   ├── manifest.json            # Declarative overlay map (src -> relative path in Ladybird tree)
│   └── branding/                # Hellbird SVG/PNG/ICO brand icons & visual assets
├── tools/
│   ├── hellbird.py              # CLI: fetch, prepare, apply, check, build, package
│   ├── patcher.py               # Ordered multi-stage patch loader & git-apply runner
│   └── resources.py             # Resource overlay installer & verifier
├── tests/
│   └── test_toolchain.py        # Unit & integration tests for patcher/overlay/CLI
├── docs/
│   └── UX-DESIGN.md             # Helium -> Ladybird UI/UX design & feature mapping
└── .github/
    └── workflows/
        ├── verify.yml           # Fast CI: patch verification, resource check, unit tests
        ├── build-linux.yml      # Linux x64 build & .tar.xz / .deb packaging
        ├── build-windows.yml    # Windows x64 (ClangCL + Qt6) build & portable .zip packaging
        ├── build-macos.yml      # macOS arm64 build & .dmg / .tar.xz packaging
        └── release.yml          # Tagged release pipeline (Windows + Linux + macOS)
```

---

## 2. Patch Stage Order & Ownership Partition

Patches are applied strictly in this stage order:
1. `patches/build/series`
2. `patches/privacy/series`
3. `patches/features/series`
4. `patches/ui/series`

Each `series` file lists relative `.patch` paths (one per line; `#` comments and blank lines ignored). Every `.patch` MUST be a standard unified diff (`git diff`) with `a/` and `b/` prefixes that applies cleanly via `git apply --check` against upstream Ladybird `0be55a57cc07ed258362c631a13a10bb4f6d707a` (after preceding patches in order).

### Strict File Ownership Boundaries (Zero Conflict Contract)

| Domain | Repo Paths Owned | Upstream Ladybird Paths Patched |
|---|---|---|
| **Infrastructure, Build & Packaging** | `tools/`, `tests/`, `resources/branding/`, `resources/manifest.json`, `.github/workflows/`, `patches/build/`, `README.md`, `LICENSE` | `CMakeLists.txt`, `UI/CMakeLists.txt`, `UI/cmake/*`, `Meta/CMake/*` |
| **Helium UI/UX, Privacy & Features** | `patches/privacy/`, `patches/features/`, `patches/ui/`, `resources/overlay/` (optional), `docs/UX-DESIGN.md` | `Libraries/*`, `Services/*`, `UI/Qt/*`, `Base/res/*` |

**Cross-platform rule:** All C++/Qt patches under `UI/Qt/`, `Libraries/`, and `Services/` MUST compile cleanly on **Linux (Clang/GCC)**, **Windows (`clang-cl` / MSVC ABI)**, and **macOS (AppleClang)** — avoid POSIX-only headers without `#ifndef AK_OS_WINDOWS` / `#ifdef Q_OS_LINUX` guards, and stick strictly to verified APIs in Ladybird `2026.47.8` (Qt 6.10+, AK, LibWebView, LibURL, LibCore).

---

## 3. Resource Overlay Contract

`resources/manifest.json` maps files inside `resources/` to relative destinations inside the patched Ladybird source tree (e.g. `Base/res/icons/...`, `Meta/CMake/freedesktop/...`). If the UI/UX domain adds extra files under `resources/overlay/`, it may declare them in `resources/overlay/manifest.json` (same JSON format: `{"mappings": [{"src": "...", "dest": "..."}]}`), which `tools/resources.py` merges automatically after `resources/manifest.json`.
