# Changelog

All notable changes to the FFmpeg Builder project.

## [Unreleased]

### Added

- **Package-manager style TUI** — Replaced the single-component progress screen with a live dashboard showing all buildable components, their statuses, and a service message log. The start screen now uses letter hotkeys (`b`/`r`/`c`/`w`/`i`/`q` + Enter) and a new `InfoScreen` displays the full component list with pagination. Component statuses are now driven by real builder phases: `pending`, `system`, `downloading`, `config`, `build`, `install`, `complete`, `fail`, `skip`. Added `ComponentStatus.SYSTEM` for components available on the host. The `BUILDING` status is now set after configure succeeds in all build paths (autotools, cmake, meson, make-only, cargo, custom). Download callbacks update the dashboard without tqdm interference. Error handler uses letter keys (`r`/`s`/`a`/`l`). All UI strings are in English.

- **Incremental dashboard rows** — `BuildDashboard` rows now appear in the table only after a component receives its first status update. On a fresh build the table grows as the async download pool queues each archive; on resume the rows restored from `state.components` are revealed immediately so prior progress is visible from the first frame. The viewport pins in-progress rows (downloading / configuring / building / installing) so the user always sees what is currently happening even when the table is taller than the terminal

- **Per-component download progress** — `Downloader.download()` now accepts a `progress_cb(downloaded, total)` callback. `AsyncDownloadManager` synthesises a per-component callback (throttled to 4 Hz) and forwards it to the dashboard, which renders a live `12.3/45.7 MB (27%)` string in the Detail column and updates the step bar. tqdm is suppressed when a callback is provided so the dashboard is the single source of progress for both compile and download phases

- **Phase detail strings** — `StateManager.mark_component_status` accepts a transient `detail` argument that is forwarded to listeners but not persisted. The builder now passes the running command for each phase (e.g. `make -j40`, `cmake`, `ninja -C build`, `ninja install`, `cargo cinstall`, `install headers`), and the dispatcher stamps `queued`/`starting` on the `downloading` row depending on whether the async manager is active

- **Tools / HW acceleration summary on start screen** — The start screen now collapses the per-tool availability table and the HW acceleration table into a single compact "Available Tools" row (`12/14 available · missing: cargo, rustc · HW accel: VAAPI, AMF`) so the screen fits without scrolling on common terminals. The full per-tool listing is still available via the `i` (Component info) screen

- **Key reference help screen** — Every interactive screen now documents its key bindings. The start screen renders an "Actions" table with three columns (Key / Action / Description) so each key's purpose is obvious at a glance. A new `HelpScreen` (key `h`) lists the full key reference for all screens (start, component info, error prompt, build dashboard). The component info screen and the error prompt each have an `h` option to open the same reference inline. The build dashboard row in the reference explains that the dashboard refreshes automatically and that `Ctrl+C` aborts the build

- **Async source downloads** — Source archives are now downloaded in a background thread pool while the previous component is being built. The build loop only blocks on the download for the component it is about to assemble, so network I/O and CPU compilation overlap. New `BuildConfig` fields control the feature: `async_downloads: bool` (default `true`) and `download_workers: int` (default `4`). Both `build_config.yaml` and `profiles/default.yaml` are updated. The interactive `ConfigScreen` exposes the new settings alongside the existing build flags. Implemented as `AsyncDownloadManager` in `ffmpeg_builder/downloader.py`; the per-file lock in `Downloader` and atomic `<archive>.part → <archive>` rename make the background downloads safe to share with the rest of the system. `FFmpegBuilder` gains `prefetch_downloads()`, `retry_download()`, and `shutdown_downloads()`, and the build loop in `app.py` now prefetches all buildable archives up-front, re-queues a download on retry, and stops the executor in a `finally` block on abort/error

### Changed

- **Build dashboard layout** — The header is now a single line (`FFmpeg Builder X.Y - Building | Elapsed: HH:MM:SS`) instead of a multi-line panel. The messages panel is fixed at 8 content lines (10 lines including borders) and stays anchored at the bottom of the screen. The component list occupies the remaining fixed-height area and scrolls upward as new rows appear, so the messages panel is never pushed down by a growing table
- **Viewport follows build order** — The visible component rows are now centered on the active component in the original build order (`1/N`, `2/N`, …) rather than being reordered to pin in-progress rows at the top. This keeps the progression readable while still making the active phase visible

### Fixed

- **macOS x265 merge step** — Multi-bitdepth static archive merge now explicitly uses Apple `libtool` (`xcrun -f libtool` / `/usr/bin/libtool` fallback) instead of GNU `glibtool`, which caused `x265: Merge libs failed` on macOS
- **zimg bootstrap tool detection on macOS** — `build_zimg()` now accepts both `libtoolize` and `glibtoolize` (workspace and system locations). This fixes `libtoolize not found` failures on Homebrew/MacPorts setups where GNU libtool is exposed as `glibtoolize`
- **libjxl deps script on macOS without `realpath`** — `build_libjxl()` now patches `deps.sh` to a portable self-path resolution when `realpath` is unavailable, fixing `libjxl: Deps failed` with `./deps.sh: line 12: realpath: command not found`
- **Xiph mirror download fallback** — Downloader now retries Xiph/OSUOSL archives through HTTP fallback URLs when HTTPS mirror TLS chain validation fails. This stabilizes fetches for `opus`, `libogg`, `libvorbis`, and `libtheora` in affected environments

- **Syntax errors in `BuildDashboard`** — Incorrect indentation in `_header` and `_visible_rows`, and an inverted guard in the `add` helper, prevented the application from starting. The dashboard now compiles and renders correctly
- **`build_libvmaf` skipped `BUILDING` phase** — The status went straight from `configuring` to `installing` between `meson setup` and `ninja -C build`, so the dashboard showed `install` while the long compile was actually running. Now the `BUILDING` status is set after configure succeeds, with detail `ninja -C build`

- **`build_libzmq` skipped `BUILDING` phase** — Same issue as `build_libvmaf`: the status jumped from `configuring` to `installing` between `./configure` and `make`. `BUILDING` is now set with detail `make -jN`

### Notes

- **Verified build still passes** — No functional regressions in the build engine; the [1.0.6] Fedora 44 verified run remains the reference for a complete end-to-end build with these UI changes applied

## [1.0.6] — 2026-07-19

### Fixed

- **svtav1 on GCC 16 / glibc 2.43** — SVT-AV1 4.0.1 includes `<sched.h>`/`<pthread.h>` from a project header (`Source/Lib/Codec/svt_threads.h`) and defines `_GNU_SOURCE` there. On modern glibc this is ignored when the translation unit is compiled with `-std=c11` because GCC defines `__STRICT_ANSI__`, which hides GNU/POSIX extensions (`locale_t`, `clockid_t`, `posix_memalign`, `strcasecmp`, etc.). Added a Linux `platform_overrides` entry that appends `-std=gnu11` to svtav1 CFLAGS, mirroring the existing `gettext` workaround. The same override is what the original `build-ffmpeg` script relies on
- **Out-of-sync default C standard** — `ffmpeg_builder/build_config.yaml` still set `linux.c_standard: c11` despite the documented default in `profiles/default.yaml` and the [1.0.5] release notes already declaring `gnu11` as the default. The runtime state file (`workspace/build_state.json`) inherited the stale `c11` value, which is why the failing build used `-std=c11`. Synced `build_config.yaml` to `gnu11` so a fresh checkout also gets the working default
- **x265 on GCC 15/16 / libstdc++** — `source/dynamicHDR10/json11/json11.cpp` uses `uint8_t` but only includes `<limits>`. Starting with libstdc++ shipped in GCC 15, `<limits>` no longer transitively pulls in `<cstdint>`, so `uint8_t` is undeclared and the 12-bit x265 build fails. Added a source patch in `build_x265()` that injects `#include <cstdint>` right after `<limits>`, matching the `sed -i '23a #include <cstdint>'` line from the original `build-ffmpeg` script
- **FFmpeg configure: `libjxl_threads >= 0.7.0 not found`** — libjxl 0.11.2 ships a `libjxl_threads.pc` that omits `-lstdc++` from `Libs`/`Libs.private`, so FFmpeg's `require_pkg_config` link test fails with undefined references to `std::condition_variable`/`operator new`. Added `-lstdc++` to `extralibs` whenever `libjxl` is in the build set (Linux only; macOS already uses `-lc++` via the `libvmaf` branch). libjxl itself is a C++ library and the C++ runtime is needed for the threads runner even though the pkg-config file does not declare it
- **FFmpeg configure: `SvtAv1Enc >= 0.9.0 not found` and vid.stab link error** — On Fedora/RHEL-family distributions, CMake's default `CMAKE_INSTALL_LIBDIR` is `lib64`, so SVT-AV1 4.0.1 and vid.stab install their libraries and pkg-config files to `<workspace>/lib64/` and `<workspace>/lib64/pkgconfig/`, but the builder only searched `<workspace>/lib/`. Added `<workspace>/lib64/pkgconfig` to `PKG_CONFIG_PATH` and `<workspace>/lib64` to `LDFLAGS` so FFmpeg's `require_pkg_config` resolves `SvtAv1Enc.pc` and the linker finds `libSvtAv1Enc.a` and `libvidstab.a`

### Notes

- **Target environment** — Debugging performed on Fedora Linux 44, dual AMD Instinct MI50, dual Intel Xeon Broadwell, GCC 16.1.1, glibc 2.43, Python 3.14. The svtav1 and x265 failures reproduce deterministically on this environment and are fixed by the changes above
- **Successful Fedora 44 build** — Full FFmpeg 8.1 build (45/57 components, 12 LV2/OpenCL/AMF/VapourSynth-system items skipped on this environment) completed end-to-end on Fedora Linux 44 with GCC 16.1.1 and glibc 2.43. The resulting `ffmpeg` binary statically links `libsvtav1`, `libx264`, `libx265` (multi-bitdepth), `libaom`, `libdav1d`, `libjxl`, `libfdk_aac`, `libvpx`, `libmp3lame`, `libopus`, `libvorbis`, `libtheora`, `libsrt`, `libzmq`, `librav1e`, `libvmaf`, `libwebp`, `libfreetype`, `vid.stab`, `libssl`, `libcrypto`, `libsdl`, `libzmq`, `libopencore-amrnb/wb`, and all GPL/non-free codecs enabled. The build is the first verified run on this exact hardware (AMD Instinct MI50 + Intel Xeon Broadwell, Fedora 44)

## [1.0.5] — 2026-07-19

### Fixed

- **Executable entry point** — Added `ffmpeg_builder/ffmpeg_builder` wrapper so the application can be launched directly without `python -m`
- **gettext on GCC 16 / glibc 2.43** — Added Linux platform override that appends `-std=gnu11` to `gettext` CFLAGS. This works around `__builtin_va_arg_pack()` errors caused by the combination of `gettext 0.22.5`, GCC 16, and glibc 2.43
- **Default Linux C standard** — Changed default `linux.c_standard` from `c11` to `gnu11` in `build_config.yaml` and `profiles/default.yaml` for broader compatibility with modern glibc headers
- **GPU detection** — `system_info.gpu_info` was never populated; now detected via `lspci -nn` (with DRM sysfs fallback) so the system report shows the actual GPU models
- **AMF detection on AMD GPUs** — AMF is now enabled when an AMD GPU is detected, because the `amf` component downloads the required headers from GPUOpen. Previously it was only enabled if system headers existed in `/usr/include/AMF`, which prevented AMF from being built on clean AMD systems
- **Intel QSV false positive** — PCI vendor check now restricts matching to display/3D class devices only. Previously any Intel PCI device (chipset, USB, MEI, etc.) incorrectly enabled QSV, causing `onevpl` to be built on Xeon + AMD systems
- **rav1e build function** — Removed non-existent `custom_build_fn="build_rav1e"`; rav1e now uses the generic `_build_cargo` path as intended
- **Previous build progress display** — Fixed UI to show progress against `total_steps` (e.g., 13/57) instead of only the number of tracked components
- **Platform override application** — Fixed `get_build_env()` condition from `component.name in component.platform_overrides` to `self.platform in component.platform_overrides`. Previously overrides (including the `gettext` `-std=gnu11` fix) were never applied
- **OpenSSL on GCC 16** — OpenSSL `./Configure` forces `-std=c11` on x86_64, which breaks GCC 16's inline-assembly handling in `crypto/bn/asm/x86_64-gcc.c`. Added a post-configure patch that replaces `-std=c11` with `-std=gnu11` in `Makefile` and `configdata.pm`

### Notes

- **Successful WSL2 build** — Full FFmpeg 8.1 build completed successfully on WSL2 (Ubuntu 24.04) with NVIDIA CUDA, Vulkan, and all configured codecs enabled

## [1.0.3] — 2026-07-18

### Fixed

- **CUDA configure flags** — Дополнен набор флагов FFmpeg для CUDA до соответствия оригинальному скрипту: добавлены `--enable-nvdec`, `--enable-cuda-llvm`, `--enable-ffnvcodec`. Флаг `--cuda-sdk` не добавлен (устарел в FFmpeg, используется `--enable-cuda-nvcc`). При недоступности CUDA добавлен флаг `--disable-ffnvcodec`
- **CUDA compute capability auto-detection** — Добавлено автоматическое определение compute capability через `nvidia-smi`. Приоритет: переменная окружения `CUDA_COMPUTE_CAPABILITY` → автоопределение через nvidia-smi → значение по умолчанию 52. Поддержка нескольких GPU (выбирается минимальное значение для совместимости)

## [1.0.2] — 2026-07-18

### Fixed

- **EXTRALIBS conditional linking** — Библиотеки `-lcuda`, `-lvulkan`, `-lva` теперь добавляются только если соответствующие компоненты собраны (nv-codec, vulkan-headers, opencl-icd-loader). Ранее они добавлялись безусловно при наличии аппаратного обеспечения, что могло приводить к ошибкам линковки
- **libvmaf C++ runtime** — Добавлена библиотека `-lstdc++` (Linux) или `-lc++` (macOS) при сборке libvmaf, что необходимо для корректной линковки C++ кода библиотеки
- **libjxl lcms2 dependency** — Добавлена библиотека `-llcms2` при сборке libjxl, которая требуется для работы с цветовыми профилями

## [1.0.1] — 2026-07-18

### Fixed

- **Full-static CXXFLAGS** — Добавлен флаг `-fPIC` для CXXFLAGS при full-static сборке (ранее применялся только к CFLAGS, что вызывало ошибки линковки C++ компонентов)
- **Full-static CXXFLAGS order** — Исправлен порядок инициализации CXXFLAGS: теперь стандарт C++ устанавливается до добавления full-static флагов, предотвращая перезапись
- **x265 full-static patch** — Добавлен sed-патч для x265.pkg: замена `-lgcc_s` на `-lgcc_eh` при full-static сборке (аналогично оригинальному скрипту)
- **VAAPI full-static** — VAAPI теперь отключается при full-static сборке (оригинальный скрипт не поддерживает статическую линковку libva)
- **Native build CXXFLAGS** — Флаги `-march=native -mtune=native` теперь применяются и к CXXFLAGS (ранее только к CFLAGS)

### Added

- **srt component** — Добавлен компонент SRT (Secure Reliable Transport) версии 1.5.4 (GPL). Включает sed-патч для full-static сборки (`-lgcc_s` → `-lgcc_eh`). Добавляет `--enable-libsrt` в конфигурацию FFmpeg

### Notes

- **C++17 standard** — Проверена совместимость c++17 со всеми C++ компонентами (x265, glslang, libvmaf, srt и др.). Все компоненты поддерживают C++11/C++14, поэтому c++17 безопасен и оставлен без изменений

## [1.0.0] — 2026-07-18

### Added

- **Core build system** — Python-based interactive FFmpeg 8.1 builder replacing the bash `build-ffmpeg` script
- **Interactive TUI** — Rich terminal interface with system report, configuration editor, build progress, and final report screens
- **Platform detection** — Automatic detection of OS, architecture, CPU, RAM, compiler, and 16 build tools
- **Hardware acceleration detection**:
  - CUDA — searches PATH and `/usr/local/cuda*/bin/nvcc`
  - Vulkan — pkg-config, headers, vulkaninfo
  - VAAPI — pkg-config libva
  - AMF — header path detection
  - OpenCL — headers + ICD loader + vendor ICD files; WSL2-aware
- **Component registry** — ~50 components with version, URL, build system, dependencies, and platform filtering
- **Build engine** — Supports autotools, CMake, Meson, Cargo, make-only, headers-only, and custom build functions
- **State management** — JSON state file with per-component status tracking; supports build resume after interruption
- **Interactive error handling** — On build failure: retry, skip component, abort, or view full log
- **YAML configuration** — Build profiles with GPL, native build, full static, libvmaf, LV2, parallel jobs settings
- **macOS support** — Macports clang detection, OpenMP, VideoToolbox, glibtool for x265 static lib merge
- **Linux support** — GCC/Clang detection, C11/C++17 standards, full-static builds, CUDA/Vulkan integration
- **CUDA build integration** — Adds CUDA paths to CFLAGS/LDFLAGS/PATH, passes `--enable-cuda-nvcc`, `--enable-cuvid`, `--enable-nvenc`, `--cuda-sdk` to FFmpeg configure
- **Vulkan build integration** — Links libvulkan, builds vulkan-headers and glslang from source, passes `--enable-vulkan`, `--enable-libglslang` to FFmpeg
- **HW component filtering** — nv-codec, vulkan-headers, glslang, amf, opencl-headers, opencl-icd-loader are only built when the corresponding HW acceleration is detected
- **Download manager** — File downloads with progress bars (tqdm), retry logic (3 attempts), and integrity checks
- **Command executor** — Subprocess wrapper with log file generation, timeout support, and stdin support (for `ar -M`)
- **Environment check script** — `scripts/check_python_env.sh` verifies Python environment, checks all dependencies, and suggests installation commands for pip, apt, dnf, pacman, zypper, and MacPorts
- **OS detection fix** — Reads `/etc/os-release` for proper distribution name on Linux (e.g., "Ubuntu 24.04.4 LTS" instead of kernel string)
- **Compiler version fix** — Strips trailing `)` from GCC version strings
- **Default build profile** — `ffmpeg_builder/profiles/default.yaml`

### Fixed

- **Retry logic** — Replaced `for`/`enumerate` loop with `while` loop so that retrying a failed component actually re-builds it (previously `idx -= 1` was silently overwritten by `enumerate`)
- **Cleanup order** — `state_file.unlink()` now runs before `shutil.rmtree(workspace)` to avoid accessing files inside a deleted directory
- **x265 `ar -M`** — The merge script for multi-bitdepth x265 on Linux is now passed via stdin (previously the script was constructed but never sent to the process, causing a hang)
- **gettext version** — Changed from non-existent `1.0` to `0.22.5`
- **waflib archive path** — Added `archive_dirname="autowaf-{version}"` so the extracted directory matches the expected source path
- **FFmpeg GPL flags** — Added `--enable-gpl` and `--enable-nonfree` to FFmpeg configure when GPL is enabled (previously only `--enable-version3` was passed)
- **Config mutation** — `BuildConfig.from_dict()` now uses `.get()` instead of `.pop()` to avoid mutating the input dictionary
- **Private method access** — Renamed `_build_component()` to public `build_component()` in builder; app.py calls the public method
- **Meson and ninja** — Added as system components in the build tools registry with a `build_ninja()` custom build function
- **OpenCL detection** — Rewrote to check headers + ICD loader + vendor ICD files; correctly reports unavailable on WSL2 where NVIDIA does not expose OpenCL through the paravirtualized driver
- **Dead code removal** — Removed unused `ui/progress.py` module

### Component Versions

| Category | Components |
|----------|-----------|
| Build tools | giflib 5.2.2, pkg-config 0.29.2, yasm 1.3.0, nasm 3.01, zlib 1.3.2, m4 1.4.20, autoconf 2.72, automake 1.18.1, libtool 2.5.4, cmake 4.2.3, meson 1.8.2, ninja 1.12.1 |
| Crypto (GPL) | gettext 0.22.5, openssl 3.6.1 |
| Crypto (non-GPL) | gmp 6.3.0, nettle 3.10.2, gnutls 3.8.12 |
| Video | dav1d 1.5.3, svtav1 4.0.1, rav1e 0.8.1, x264 0480cb05, x265 8be7dbf, libvpx 1.16.0, xvidcore 1.3.7, vid.stab 1.1.1, aom 3.12.0, zimg 3.0.6 |
| Audio | lv2 1.18.10, serd 0.32.8, pcre 8.45, zix 0.8.0, sord 0.16.22, sratom 0.6.22, lilv 0.26.4, opencore 0.1.6, lame 3.100, opus 1.6.1, libogg 1.3.6, libvorbis 1.3.7, libtheora 1.2.0, fdk_aac 2.0.3, soxr 0.1.3 |
| Image | libtiff 4.7.1, libpng 1.6.55, lcms2 2.18, libjxl 0.11.2, libwebp 1.6.0 |
| Other | libsdl 2.30.12, freetype 2.14.2, vapoursynth 73, libvmaf 3.0.0, srt 1.5.4, libzmq 4.3.5 |
| HW accel | vulkan-headers 1.4.341.0, glslang 16.2.0, nv-codec 13.0.19.0, amf 1.5.0, opencl-headers 2025.07.22, opencl-icd-loader 2025.07.22 |
| Target | FFmpeg 8.1 |
