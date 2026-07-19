# Changelog

All notable changes to the FFmpeg Builder project.

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
