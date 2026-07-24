# FFmpeg Builder

Interactive Python-based build system for FFmpeg 8.1 on macOS and Linux, with a prepared bootstrap path for Windows 11 + MSYS2 UCRT64.

FFmpeg Builder replaces the traditional bash `build-ffmpeg` script with a modern, interactive interface featuring real-time progress tracking, configuration management, platform-aware hardware acceleration detection, and resumable builds.

## Features

- **Interactive TUI** — Rich terminal interface with system report, component info, configuration editor, and live package-manager style build dashboard
- **Platform Detection** — Automatic detection of CPU, RAM, GPU, compilers, and build tools
- **Hardware Acceleration** — Detects and configures CUDA, Vulkan, VAAPI, AMF, and OpenCL support
- **Resumable Builds** — JSON state file tracks progress; interrupted builds can be resumed
- **Async Downloads** — Source archives are fetched in a background thread pool while the current component compiles, so network and CPU work overlap
- **Interactive Error Handling** — On failure, choose to retry, skip component, or abort
- **YAML Configuration** — Human-readable build profiles with platform-specific settings
- **~50 Components** — All codecs, libraries, and tools built from source in correct dependency order
- **macOS Support** — Macports clang detection, OpenMP, VideoToolbox, glibtool handling
- **Linux Support** — GCC/Clang detection, C11/C++17 standards, full-static builds

## Requirements

### System

- **Python** >= 3.8
- **OS**: macOS (11.0+) or Linux (x86_64 / arm64)
- **Disk Space**: ~10 GB for sources and build artifacts

### Build Tools (auto-detected, built from source if missing)

| Tool | Purpose |
|------|---------|
| make / g++ or clang++ | Core compilation |
| pkg-config | Library discovery |
| nasm / yasm | Assembly (x264, x265, dav1d) |
| cmake | CMake-based components |
| python3 | Meson-based components |
| meson / ninja | dav1d, libvmaf, lv2 stack |
| cargo / rustc | rav1e (Rust AV1 encoder) |
| curl / git | Source downloads |

### Python Dependencies

```
rich>=13.0.0
tqdm>=4.65.0
pyyaml>=6.0
requests>=2.31.0
packaging>=23.0
psutil>=5.9.0
```

Install all dependencies:

```bash
pip install -e .
```

Or use the environment check script:

```bash
./scripts/check_python_env.sh
```

## Windows 11 + MSYS2 UCRT64 (bootstrap)

Windows support for the same `ffmpeg_builder` program is prepared through an MSYS2 UCRT64 bootstrap flow.
The Windows adaptation target is dual-GPU acceleration (NVIDIA + Intel) with CUDA filters, Vulkan, and OpenCL in one FFmpeg 8.1 build.

From Windows PowerShell (repository root):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows_msys2_ucrt64.ps1
```

What it does:

- installs required MSYS2/UCRT64 toolchain and build packages,
- installs hardware acceleration dependency packages (ffnvcodec, oneVPL/libvpl, Vulkan, OpenCL),
- installs OpenMP runtime support package (`mingw-w64-ucrt-x86_64-llvm-openmp`),
- installs Python runtime dependencies (`rich`, `tqdm`, `pyyaml`, `requests`, `packaging`, `psutil`) from MSYS2 packages,
- creates `.venv-msys2-ucrt64` in the repository (with `--system-site-packages`) if missing,
- installs project package in editable mode (`pip install -e . --no-deps`) into that venv,
- runs `scripts/check_python_env.sh`,
- detects CUDA/Vulkan/OpenCL availability and generates `scripts/env_windows_msys2_ucrt64.sh`.

Then start MSYS2 UCRT64 and run:

```bash
cd /<drive>/<path>/ffmpeg-build-script
source ./scripts/env_windows_msys2_ucrt64.sh   # optional, if generated
source ./.venv-msys2-ucrt64/bin/activate
python -m ffmpeg_builder
```

OpenMP on Windows/UCRT64 is recommended: it improves performance in components that use OpenMP-parallel code paths.  
For GCC builds, `-fopenmp` is provided by the installed UCRT64 toolchain (`libgomp` via GCC). The bootstrap additionally installs `llvm-openmp` to keep Clang/OpenMP runtime available as well.

Implementation details and migration plan are documented in:

- `ffmpeg_builder/docs/Windows-UCRT64-Implementation-Plan.md`

## Quick Start

```bash
# Clone the repository
git clone <repository-url>
cd ffmpeg-build-script

# Install Python dependencies
pip install -e .

# Check your environment
./scripts/check_python_env.sh

# Run the builder
./ffmpeg_builder/ffmpeg_builder
# or
python -m ffmpeg_builder
```

## Usage

### Interactive Mode

```bash
python -m ffmpeg_builder
```

The application starts with a **System Report** screen showing:

- Hardware information (CPU, cores, RAM, GPU)
- Software information (OS, compiler, architecture)
- Available build tools and their versions
- Hardware acceleration status (CUDA, Vulkan, VAAPI, AMF, OpenCL)
- Current build configuration

From the main menu, type a letter key and press Enter:

| Key | Action | Description |
|-----|--------|-------------|
| `b` | **Start new build** | Begin building all components from scratch (resets previous state) |
| `r` | **Resume previous build** | Continue from the last interrupted build when state exists |
| `c` | **Edit configuration** | Modify build settings interactively |
| `w` | **Cleanup workspace** | Remove all build artifacts and state |
| `i` | **Component info** | Show the current buildable component set and non-selected components |
| `h` | **Help** | Show the full key reference for all screens (start, info, error prompt, dashboard) |
| `q` | **Exit** | Exit the application |

During a build, the live dashboard shows a compact header line (`FFmpeg Builder X.Y - Building | Elapsed: HH:MM:SS`), a per-component table with `n/total`, name, a 10-segment progress bar, percentage, status (`pending`/`system`/`downloading`/`config`/`build`/`install`/`complete`/`fail`/`skip`), and a Detail column showing the running command (e.g. `make -j40`) or live download progress (e.g. `12.3/45.7 MB (27%)`). The component table occupies a fixed-height area and scrolls upward as new rows appear, so the messages panel below it stays anchored at the bottom with a fixed size (8 lines of content). The viewport follows the active component in build order, keeping the progression readable while the current phase remains visible. Rows appear as downloads are queued and builds start (not pre-populated). The layout automatically adapts to the terminal height. All status colors follow the same legend: complete green, build magenta, config yellow, downloading/system cyan, install blue, fail red, skip yellow dim, pending dim.

On failure, type `r` to retry, `s` to skip, `a` to abort, or `l` to view the full log when available.

### Configuration

Build configuration is stored in `build_config.yaml`:

```yaml
ffmpeg_version: "8.1"
gpl_enabled: false
native_build: false
full_static: false
enable_libvmaf: true
disable_lv2: false
num_jobs: "auto"
async_downloads: true
download_workers: 4

macos:
  clang: "macports-clang-17"
  openmp: true

linux:
  c_standard: "gnu11"
  cxx_standard: "c++17"
```

### Command Line

```bash
# Show help
python -m ffmpeg_builder --help

# Use custom workspace
python -m ffmpeg_builder --workspace /path/to/workspace

# Use custom config
python -m ffmpeg_builder --config /path/to/config.yaml
```

## Components

### Build Tools (system or built from source)

giflib, pkg-config, yasm, nasm, zlib, m4, autoconf, automake, libtool, cmake, meson, ninja

### Crypto

| GPL | Non-GPL |
|-----|---------|
| gettext, openssl | gmp, nettle, gnutls |

### Video Codecs

dav1d, svtav1, rav1e, x264 (GPL), x265 (GPL), libvpx, xvidcore (GPL), vid.stab (GPL), aom, zimg

### Audio Codecs

lv2 stack (lv2, serd, pcre, zix, sord, sratom, lilv), opencore, lame, opus, libogg, libvorbis, libtheora, fdk_aac (GPL), soxr

### Image Codecs

libtiff, libpng, lcms2, libjxl, libwebp

### Other Libraries

libsdl, freetype, vapoursynth, libvmaf, srt (GPL), libzmq, giflib

### Hardware Acceleration

vulkan-headers, glslang, nv-codec (Linux), amf (Linux), opencl-headers, opencl-icd-loader, onevpl (Linux)

### Target

FFmpeg 8.1

## Hardware Acceleration

The builder automatically detects available hardware acceleration and configures FFmpeg accordingly:

| Technology | Platform | Detection Method |
|------------|----------|------------------|
| **CUDA** | Linux | nvcc in PATH or `/usr/local/cuda*/bin/nvcc` |
| **Vulkan** | Linux/macOS | pkg-config, headers, vulkaninfo |
| **VAAPI** | Linux | pkg-config libva |
| **Intel QSV** | Linux | vainfo or PCI Intel GPU (requires VAAPI, disabled in WSL2) |
| **AMF** | Linux | AMD GPU detected via `lspci` or DRM sysfs; AMF headers are downloaded from GPUOpen |
| **OpenCL** | Linux | Headers + ICD vendor files |
| **VideoToolbox** | macOS | Always available |

### CUDA Notes

- CUDA toolkit must be installed (not just the driver)
- On WSL2, OpenCL is not available through the paravirtualized driver
- When CUDA is detected, the builder adds: `--enable-cuda-nvcc`, `--enable-cuvid`, `--enable-nvdec`, `--enable-nvenc`, `--enable-cuda-llvm`, `--enable-ffnvcodec`
- CUDA compute capability is automatically detected via `nvidia-smi` (queries all GPUs and uses the minimum value for compatibility)
- Priority: `CUDA_COMPUTE_CAPABILITY` environment variable → auto-detection via nvidia-smi → default value 52
- Example: `export CUDA_COMPUTE_CAPABILITY=75` to override for Turing GPUs
- The builder passes `--nvccflags=-gencode arch=compute_XX,code=sm_XX -O2` to FFmpeg configure
- When CUDA is not available, `--disable-ffnvcodec` is added to prevent build failures
- CUDA include/lib paths are automatically added to the build environment

### Vulkan Notes

- Requires Vulkan SDK or at minimum vulkan-headers and loader
- The builder compiles vulkan-headers and glslang from source when Vulkan is available
- Adds `--enable-vulkan` and `--enable-libglslang` to FFmpeg configure

### Intel QSV Notes

- Intel QSV requires VAAPI (libva) as backend
- The builder detects Intel GPU via `vainfo` or PCI vendor ID (0x8086)
- Intel oneVPL (libvpl) is built from source when QSV is available
- Adds `--enable-libvpl` and `--enable-vaapi` to FFmpeg configure
- **VAAPI is disabled in full-static builds** (libva does not support static linking)
- QSV is disabled in WSL2 environment (not supported)
- Requires Intel Media Driver (`intel-media-va-driver`) or i965 driver installed on the system

## Build Output

After a successful build, binaries are located in:

```
workspace/bin/ffmpeg
workspace/bin/ffprobe
workspace/bin/ffplay
```

Build logs are stored in:

```
workspace/logs/<component>_<step>.log
```

## Verified Environments

The following environments have been verified to complete a full FFmpeg build:

| Date | OS | Environment | Configuration | Result |
|------|------|-------------|---------------|--------|
| 2026-07-19 | Ubuntu 24.04 (WSL2) | x86_64, NVIDIA CUDA | GPL + non-free, native build | Successful build of FFmpeg 8.1 with all configured components enabled |
| 2026-07-19 | Fedora Linux 44 | x86_64, dual AMD Instinct MI50, dual Intel Xeon Broadwell, GCC 16.1.1, glibc 2.43 | GPL + non-free, native build | Successful build of FFmpeg 8.1 (45/57 components; 12 LV2/OpenCL/Vulkan/AMF/VapourSynth items not built because the corresponding runtime libraries are not present on this system) |

Build configuration for the verified run:

```yaml
ffmpeg_version: "8.1"
gpl_enabled: true
native_build: true
full_static: false
enable_libvmaf: true
disable_lv2: false
num_jobs: auto
```

Verified FFmpeg capabilities included:

- CUDA/NVENC/NVDEC: `--enable-cuda-nvcc`, `--enable-cuvid`, `--enable-nvdec`, `--enable-nvenc`, `--enable-cuda-llvm`, `--enable-ffnvcodec`
- Video codecs: `libx264`, `libx265`, `libvpx`, `libaom`, `libsvtav1`, `libdav1d`, `libxvid`, `libwebp`, `libjxl`, `libzimg`
- Audio codecs: `libmp3lame`, `libopus`, `libvorbis`, `libtheora`, `libfdk-aac`, `libsoxr`, `libopencore_amrnb`, `libopencore_amrwb`
- Streaming/protocols: `openssl`, `libsrt`, `libzmq`
- Other: `libvmaf`, `libvidstab`, `libfreetype`, `vapoursynth`, `lv2`, `vulkan`, `libglslang`

Note: `rav1e` was skipped because the installed `rustc` version (1.75.0) was too old for the latest `cargo-c`. This is handled automatically by the builder.

## Project Structure

```
ffmpeg_builder/
    __main__.py              Entry point
    app.py                   Main application class, screen orchestration
    config.py                YAML configuration management
    state.py                 JSON build state management
    system_report.py         Environment report generation
    components.py            Component registry (~50 components)
    builder.py               Build engine: download, configure, make, install
    platform_detect.py       OS, architecture, tools, HW acceleration detection
    executor.py              Subprocess wrapper with logging
    downloader.py            File downloads with progress (requests + tqdm)
    ui/
        screens.py           TUI screens: system report, config, info, final
        dashboard.py         Live build dashboard (header / table / messages)
        error_handler.py     Interactive error handling (retry/skip/abort)
    profiles/
        default.yaml         Default build profile
scripts/
    check_python_env.sh      Environment and dependency checker
```

## Troubleshooting

### Build fails at a specific component

1. Check the log file: `workspace/logs/<component>_<step>.log`
2. Use the interactive error handler to retry or skip
3. Resume the build after fixing the issue

### OpenSSL fails on GCC 15/16

If OpenSSL fails with errors in `crypto/bn/asm/x86_64-gcc.c` (e.g. `expected ')' before ':' token`), the OpenSSL `./Configure` script has forced `-std=c11`. The builder now patches the generated `configdata.pm` and regenerates the `Makefile` with `-std=gnu11` automatically.

### x265 fails with "uint8_t does not name a type" in `json11.cpp`

On GCC 15/16 with the bundled libstdc++, `<limits>` no longer transitively pulls in `<cstdint>`, so `uint8_t` is undeclared in `source/dynamicHDR10/json11/json11.cpp`. The builder now injects `#include <cstdint>` after `#include <limits>` before configuring the build, matching the original `build-ffmpeg` script.

### SVT-AV1 fails with "unknown type name 'locale_t' / 'clockid_t'"

SVT-AV1 4.0.1 includes `<sched.h>`/`<pthread.h>` from a project header (`Source/Lib/Codec/svt_threads.h`) and defines `_GNU_SOURCE` there. On modern glibc this is ignored when compiled with `-std=c11` because GCC defines `__STRICT_ANSI__`. The builder applies a Linux `platform_overrides` entry that appends `-std=gnu11` to svtav1 CFLAGS, the same workaround already used for `gettext`.

### CUDA not detected

- Ensure CUDA toolkit is installed (not just the driver)
- Check that `nvcc` is in PATH or at `/usr/local/cuda/bin/nvcc`
- On WSL2, CUDA is available but OpenCL is not

### OpenCL not detected

- Install OpenCL headers: `sudo apt install opencl-headers ocl-icd-dev`
- On WSL2, OpenCL is not available through the paravirtualized driver
- On native Linux with NVIDIA: `sudo apt install nvidia-opencl-icd-<driver-version>`

### Missing Python dependencies

```bash
./scripts/check_python_env.sh    # Check what's missing
pip install -e .                  # Install all dependencies
```

## License

MIT
