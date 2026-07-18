# FFmpeg Builder — Developer Guide

This document describes the internal architecture, module responsibilities, data flow, and extension points of the FFmpeg Builder system.

## Architecture Overview

```
                    +-----------------+
                    |   __main__.py   |
                    +--------+--------+
                             |
                    +--------v--------+
                    |     app.py      |  FFmpegBuilderApp
                    |  (orchestrator) |  - owns all managers
                    +--------+--------+
                             |
            +----------------+----------------+
            |                |                |
   +--------v------+ +------v-------+ +------v--------+
   | platform_     | |  config.py   | |   state.py    |
   | detect.py     | | ConfigManager| | StateManager  |
   | SystemInfo    | | BuildConfig  | | BuildState    |
   | PlatformInfo  | | YAML R/W     | | JSON R/W      |
   | ToolInfo      | +--------------+ +---------------+
   +--------+------+
            |
   +--------v------+
   | system_       |
   | report.py     |
   | SystemReport  |
   +--------+------+
            |
   +--------v--------+      +------------------+
   |   components.py |      |    builder.py    |
   | Component       |<---->|  FFmpegBuilder   |
   | ComponentRegistry|     |  - download      |
   | BuildSystem     |      |  - configure     |
   +-----------------+      |  - make          |
                            |  - install       |
                            +---+-------+------+
                                |       |
                    +-----------+   +---v----------+
                    |               | executor.py  |
           +--------v------+       | CommandExec  |
           | downloader.py |       | - subprocess |
           | Downloader    |       | - logging    |
           | requests+tqdm |       | - stdin      |
           +---------------+       +--------------+
```

## Module Reference

### `__main__.py`

Entry point. Creates the workspace directory and instantiates `FFmpegBuilderApp`.

### `app.py` — `FFmpegBuilderApp`

Central orchestrator. Responsibilities:

- Initialize all managers (config, state, platform detection, component registry)
- Run the main event loop: show system report, handle user actions
- Coordinate the build process: iterate components, handle errors, update state
- Manage cleanup

Key methods:

| Method | Description |
|--------|-------------|
| `run()` | Main loop — shows screens, dispatches actions |
| `_run_build(config, resume)` | Build loop with retry/skip/abort error handling |
| `_cleanup()` | Remove workspace, packages, and state file |

Build loop logic:

```
idx = 1
while idx <= len(components):
    try:
        builder.build_component(components[idx - 1])
        mark_completed()
        idx += 1
    except BuildError:
        action = error_handler.handle_error(...)
        if retry:  continue          # same idx
        if skip:   mark_skipped(); idx += 1
        if abort:  break
```

### `config.py` — Configuration Management

**`BuildConfig`** — dataclass holding all build settings:

```python
@dataclass
class BuildConfig:
    ffmpeg_version: str = "8.1"
    gpl_enabled: bool = False
    native_build: bool = False
    full_static: bool = False
    enable_libvmaf: bool = True
    disable_lv2: bool = False
    num_jobs: str = "auto"
    macos: MacOSConfig          # clang version, openmp
    linux: LinuxConfig          # c_standard, cxx_standard
```

**`ConfigManager`** — loads/saves YAML configuration:

- `load()` — reads `build_config.yaml`, returns `BuildConfig`
- `save(config)` — writes YAML
- `get()` — returns current config, loading from file if needed

### `state.py` — Build State Management

**`BuildState`** — dataclass representing the full build state:

```python
@dataclass
class BuildState:
    build_id: str               # UUID
    started_at: str             # ISO 8601
    config: dict
    components: dict[str, ComponentState]
    current_step: int
    total_steps: int
```

**`ComponentState`** — per-component state:

```python
@dataclass
class ComponentState:
    status: ComponentStatus     # pending/downloading/configuring/building/installing/completed/failed/skipped
    version: str
    built_at: str
    error_message: str
    log_file: str
```

**`StateManager`** — JSON persistence:

| Method | Description |
|--------|-------------|
| `load()` | Load state from `workspace/build_state.json` |
| `save()` | Persist current state to disk |
| `mark_component_status()` | Update component status, auto-save |
| `get_resume_point()` | Find first incomplete component |
| `is_component_completed()` | Check if component is already built (version match) |
| `update_progress()` | Update step counter |

### `platform_detect.py` — Environment Detection

Detects the full system environment in a single `detect_all()` call.

**Data classes:**

| Class | Fields |
|-------|--------|
| `SystemInfo` | os_name, os_version, architecture, cpu_model, cpu_cores, ram_gb |
| `PlatformInfo` | is_macos, is_linux, is_arm64, is_wsl2, macports_clang, cuda_available, cuda_path, vaapi_available, qsv_available, amf_available, vulkan_available, opencl_available |
| `ToolInfo` | name, path, version, available |

**Detection methods:**

| Method | What it detects |
|--------|-----------------|
| `_detect_system_info()` | OS (reads `/etc/os-release` on Linux), CPU (`/proc/cpuinfo` or `sysctl`), RAM |
| `_detect_platform_info()` | Platform flags, HW acceleration |
| `_detect_cuda()` | nvcc in PATH, then `/usr/local/cuda*/bin/nvcc` |
| `_check_wsl2()` | WSL2 environment via `/proc/version` (Microsoft/WSL) |
| `_check_qsv()` | Intel QSV via vainfo or PCI vendor ID 0x8086 (requires VAAPI, disabled in WSL2) |
| `_check_vulkan()` | pkg-config, headers, vulkaninfo |
| `_check_opencl()` | Headers + ICD loader + vendor ICD files; WSL-aware |
| `_check_vaapi()` | pkg-config libva |
| `_check_amf()` | Header paths |
| `_detect_tools()` | 16 tools via `shutil.which()` + version extraction |

**WSL awareness:** `_check_wsl2()` detects WSL2 by checking `/proc/version` for "Microsoft" or "WSL". `_check_opencl()` detects WSL by checking `/usr/lib/wsl/lib/` and verifies that `libnvidia-opencl*` exists there. `_check_qsv()` disables QSV in WSL2 environments. WSL2 does not expose OpenCL or QSV through its paravirtualized driver.

### `system_report.py` — Report Generation

**`SystemReport`** — aggregates all detected information:

- `get_hardware_acceleration_status()` — returns dict of HW accel availability
- `get_compiler_info()` — detects GCC/Clang/Macports Clang
- `get_missing_required_tools()` — tools required for build
- `get_optional_tools_status()` — optional tools (nasm, cmake, meson, etc.)

**`SystemReportGenerator`** — creates `SystemReport` from raw detection data, adds build environment variables (PATH, PKG_CONFIG_PATH, CFLAGS, etc.).

### `components.py` — Component Registry

**`Component`** — dataclass defining a single build component:

```python
@dataclass
class Component:
    name: str
    version: str
    url: str
    category: ComponentCategory
    build_system: BuildSystem
    configure_args: list[str]
    depends_on: list[str]
    requires_tools: list[str]
    gpl_only: bool
    non_gpl_only: bool
    linux_only: bool
    macos_only: bool
    system_component: bool
    custom_build_fn: str
    ffmpeg_configure_flag: str
    platform_overrides: dict[str, PlatformOverride]
    # ... and more
```

**`BuildSystem`** enum: `AUTOTOOLS`, `CMAKE`, `MESON`, `CUSTOM`, `HEADERS_ONLY`, `MAKE_ONLY`, `CARGO`

**`ComponentRegistry`** — holds all ~50 components in build order:

| Method | Description |
|--------|-------------|
| `get_all()` | All components in build order |
| `get_buildable(gpl, platform, tools, ...)` | Filtered list based on config and platform |
| `get_ffmpeg_configure_flags(built, gpl, platform)` | Collect `--enable-*` flags from built components |

**HW acceleration filtering** in `get_buildable()`:

| Component | Condition |
|-----------|-----------|
| `nv-codec` | `platform_info.cuda_available` |
| `vulkan-headers`, `glslang` | `platform_info.vulkan_available` |
| `amf` | `platform_info.amf_available` |
| `opencl-headers`, `opencl-icd-loader` | `platform_info.opencl_available` |
| `onevpl` | `platform_info.qsv_available` |
| `onevpl` | `platform_info.qsv_available` |

### `builder.py` — Build Engine

**`FFmpegBuilder`** — orchestrates the build of each component.

**Environment setup** (`_setup_environment()`):

- Sets `CFLAGS`, `CXXFLAGS`, `LDFLAGS`, `LDEXEFLAGS`
- Configures `PKG_CONFIG_PATH` with workspace and system paths
- Adds CUDA paths (`-I`, `-L`, `PATH`) when CUDA is detected
- Adds Vulkan library linkage when Vulkan is detected
- Sets `CC`/`CXX` to macports clang on macOS

**Build dispatch** (`build_component()`):

```
1. Check if already completed (version match) → skip
2. Download and extract source
3. If HEADERS_ONLY → install headers, return
4. If custom_build_fn → call it, return
5. Dispatch by build_system:
   - AUTOTOOLS → _build_autotools()
   - CMAKE     → _build_cmake()
   - MESON     → _build_meson()
   - MAKE_ONLY → _build_make_only()
   - CARGO     → _build_cargo()
```

**Custom build functions** for components with non-standard build processes:

| Function | Component | Notes |
|----------|-----------|-------|
| `build_openssl()` | openssl | Uses `./Configure`, `make install_sw` |
| `build_x264()` | x264 | Adds `-fPIC`, `install-lib-static` |
| `build_x265()` | x265 | Multi-bitdepth (12/10/8), merges static libs with `ar -M` (Linux) or `glibtool` (macOS) |
| `build_libvpx()` | libvpx | Darwin Makefile patching for linker flags |
| `build_zimg()` | zimg | Runs `libtoolize -i`, `autogen.sh` before configure |
| `build_libvorbis()` | libvorbis | Patches `configure.ac`, runs `autogen.sh` |
| `build_libjxl()` | libjxl | Runs `./deps.sh`, cmake with many disabled features |
| `build_libvmaf()` | libvmaf | Meson build in `libvmaf/` subdirectory |
| `build_libzmq()` | libzmq | Patches `proxy.cpp` for C++ compatibility |
| `build_glslang()` | glslang | Runs `update_glslang_sources.py` first |
| `build_ninja()` | ninja | Bootstrap build with `./configure.py --bootstrap` |
| `build_ffmpeg()` | ffmpeg | Collects flags from all built components, adds CUDA/Vulkan flags |

**FFmpeg configure flags** added dynamically:

| Condition | Flags |
|-----------|-------|
| `gpl_enabled` | `--enable-gpl`, `--enable-nonfree` |
| CUDA available | `--enable-cuda-nvcc`, `--enable-cuvid`, `--enable-nvenc`, `--cuda-sdk=<path>` |
| Per built component | `--enable-libdav1d`, `--enable-libx264`, etc. |
| macOS | `--enable-videotoolbox`, `--enable-opencl` |

### `executor.py` — Command Execution

**`CommandExecutor`** — wraps `subprocess.run()` with:

- Environment variable merging
- Working directory control
- Timeout support
- Stdin support (used for `ar -M` in x265 multi-bitdepth merge)
- Log file generation per step

**Key methods:**

| Method | Description |
|--------|-------------|
| `execute()` | Core execution, returns `ExecutionResult` |
| `execute_with_log()` | Execute + write log to `workspace/logs/<component>_<step>.log` |
| `execute_make()` | `make -j<N>` |
| `execute_install()` | `make install` |

### `downloader.py` — Download Management

**`Downloader`** — downloads source archives with:

- Progress bar via `tqdm`
- Retry logic (3 attempts, 10s delay)
- Skip if file already exists and non-empty
- Empty file detection and cleanup

### `ui/screens.py` — TUI Screens

Built with the `rich` library. Four screens:

| Screen | Class | Purpose |
|--------|-------|---------|
| System Report | `SystemReportScreen` | Hardware/software tables, HW accel status, config, menu |
| Configuration | `ConfigScreen` | Interactive yes/no prompts for build settings |
| Build Progress | `BuildProgressScreen` | Current component, step, progress counter |
| Final Report | `FinalReportScreen` | Summary, binaries, install prompt, error details |

### `ui/error_handler.py` — Error Handling

**`ErrorHandler`** — on `BuildError`:

1. Shows error panel with component name and message
2. Displays last 20 lines of the log file
3. Presents menu: Retry / Skip / Abort / Show Full Log
4. Returns user choice to the build loop in `app.py`

## Data Flow

### Build Process

```
app.run()
  |
  +-> detect_environment()
  |     platform_detect.detect_all() -> SystemInfo, PlatformInfo, tools
  |     system_report.generate() -> SystemReport
  |
  +-> load_state()
  |     state_manager.load() -> BuildState (or None)
  |
  +-> show_system_report() -> action
  |
  +-> _run_build(config, resume)
        |
        +-> registry.get_buildable(gpl, platform, tools, ..., platform_info)
        |     -> filtered component list
        |
        +-> FFmpegBuilder(config, workspace, packages, state_mgr, platform_detect)
        |     _setup_environment() -> CFLAGS, LDFLAGS, env
        |
        +-> for each component:
              |
              +-> builder.build_component(component)
              |     |
              |     +-> is_component_completed() -> skip?
              |     +-> _download_and_extract()
              |     +-> dispatch to build system handler
              |     +-> mark_component_status(COMPLETED)
              |
              +-> on BuildError:
                    error_handler.handle_error() -> retry/skip/abort
```

### State Persistence

```
BuildState (in memory)
    |
    +-> state_manager.save() -> workspace/build_state.json
    |
    +-> state_manager.load() <- workspace/build_state.json
```

State is saved after every component status change and progress update.

## Extension Points

### Adding a New Component

1. Add a `Component()` entry in the appropriate `_add_*()` method in `components.py`
2. Set `build_system`, `url`, `version`, `configure_args`
3. If non-standard build, set `custom_build_fn` and implement the method in `builder.py`
4. Set `ffmpeg_configure_flag` if the component adds an `--enable-*` flag to FFmpeg
5. Set `depends_on` for dependency ordering
6. Set `gpl_only`, `linux_only`, etc. for filtering

### Adding a New Build System

1. Add a new value to the `BuildSystem` enum in `components.py`
2. Implement a `_build_<system>()` method in `builder.py`
3. Add dispatch case in `build_component()`

### Adding Platform-Specific Behavior

1. Add detection in `platform_detect.py`
2. Add field to `PlatformInfo` dataclass
3. Use in `builder.py` `_setup_environment()` or custom build functions
4. Add filtering in `components.py` `get_buildable()` if needed

## Testing

Run the environment check:

```bash
./scripts/check_python_env.sh
```

Verify detection:

```bash
python3 -c "
from ffmpeg_builder.platform_detect import PlatformDetector
d = PlatformDetector()
sys_info, plat_info, tools = d.detect_all()
print(f'OS: {sys_info.os_name}')
print(f'CUDA: {plat_info.cuda_available}')
print(f'Vulkan: {plat_info.vulkan_available}')
print(f'OpenCL: {plat_info.opencl_available}')
"
```

Verify component filtering:

```bash
python3 -c "
from ffmpeg_builder.platform_detect import PlatformDetector
from ffmpeg_builder.components import ComponentRegistry
d = PlatformDetector()
_, pi, tools = d.detect_all()
r = ComponentRegistry()
components = r.get_buildable(False, 'linux', tools, platform_info=pi)
print(f'Total buildable: {len(components)}')
hw = [c.name for c in components if c.category.value == 'hw_accel']
print(f'HW components: {hw}')
"
```

## Known Limitations

- **WSL2 OpenCL**: Not available through the paravirtualized NVIDIA driver. The builder correctly detects this and excludes OpenCL components.
- **macOS CI**: No automated testing on macOS; platform-specific code paths (macports clang, glibtool, VideoToolbox) require manual verification.
- **Component versions**: Versions are hardcoded in `components.py`. A future improvement could fetch latest versions from an API or config file.
- **No dependency graph**: Components are built in a fixed order defined in `components.py`. There is no automatic topological sort based on `depends_on`.
