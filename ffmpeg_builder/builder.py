"""Build orchestration engine."""
import os
import re
import tarfile
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Callable, Tuple
from tqdm import tqdm

from .config import BuildConfig
from .state import StateManager, ComponentStatus
from .components import Component, ComponentRegistry, BuildSystem
from .executor import CommandExecutor, ExecutionResult
from .downloader import AsyncDownloadManager, Downloader
from .platform_detect import PlatformDetector


class BuildError(Exception):
    """Build error with component context."""

    def __init__(self, component: str, message: str, log_file: Optional[Path] = None):
        """Initialize build error.

        Args:
            component: Component name.
            message: Error message.
            log_file: Path to log file.
        """
        super().__init__(f"{component}: {message}")
        self.component = component
        self.log_file = log_file


class SkipComponent(Exception):
    """Raised when a component should be skipped (not failed)."""

    def __init__(self, component: str, message: str):
        """Initialize skip exception.

        Args:
            component: Component name.
            message: Skip reason.
        """
        super().__init__(f"{component}: {message}")
        self.component = component
        self.message = message


class FFmpegBuilder:
    """Orchestrates FFmpeg build process."""

    def __init__(
        self,
        config: BuildConfig,
        workspace: Path,
        packages: Path,
        state_manager: StateManager,
        platform_detector: PlatformDetector,
        on_download_status: Optional[Callable[[str, str], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_download_progress: Optional[Callable[[str, int, int], None]] = None,
    ):
        """Initialize builder.

        Args:
            config: Build configuration.
            workspace: Workspace directory.
            packages: Packages directory.
            state_manager: State manager instance.
            platform_detector: Platform detector instance.
            on_download_status: Optional download status callback.
            on_log: Optional message callback.
            on_download_progress: Optional per-component download progress
                callback receiving (component_name, downloaded_bytes, total_bytes).
        """
        self.config = config
        self.workspace = workspace.absolute()
        self.packages = packages.absolute()
        self.state_manager = state_manager
        self.platform_detector = platform_detector

        self.executor = CommandExecutor(self.workspace)
        self.downloader = Downloader(self.packages)
        self.on_download_status = on_download_status
        self.on_log = on_log
        self.on_download_progress = on_download_progress
        self.async_download_manager = None
        if config.async_downloads:
            self.async_download_manager = AsyncDownloadManager(
                self.downloader,
                config.download_workers,
                on_download_status,
                on_log,
                on_download_progress,
            )
        self.registry = ComponentRegistry()

        self.num_jobs = platform_detector.get_num_jobs(config.num_jobs)
        self.platform = "darwin" if platform_detector.platform_info.is_macos else "linux"

        self._setup_environment()

    def _setup_environment(self) -> None:
        """Setup build environment."""
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.packages.mkdir(parents=True, exist_ok=True)

        self.cflags = f"-I{self.workspace}/include -Wno-int-conversion"
        self.ldflags = f"-L{self.workspace}/lib -L{self.workspace}/lib64"
        self.ldexeflags = ""
        self.extralibs = "-ldl -lpthread -lm -lz"

        if self.platform == "linux":
            self.cflags += f" -std={self.config.linux.c_standard}"
            self.cxxflags = f"-std={self.config.linux.cxx_standard}"
        else:
            self.cxxflags = ""

        if self.config.full_static:
            if self.platform == "linux":
                self.ldexeflags = "-static -fPIC"
                self.cflags += " -fPIC"
                self.cxxflags += " -fPIC"

        if self.config.native_build:
            self.cflags += " -march=native -mtune=native"
            self.cxxflags += " -march=native -mtune=native"

        pkg_config_path = f"{self.workspace}/lib/pkgconfig"
        # Some CMake-based components (e.g. SVT-AV1) honour
        # CMAKE_INSTALL_LIBDIR and install to <prefix>/lib64 on distributions
        # like Fedora. Add the lib64 pkgconfig directory so FFmpeg's
        # `require_pkg_config` lookups find them.
        pkg_config_path += f":{self.workspace}/lib64/pkgconfig"

        # Add architecture-specific paths for Linux
        if self.platform == "linux":
            multiarch = self.platform_detector.get_multiarch_dir()
            if multiarch:
                pkg_config_path += f":/usr/local/lib/{multiarch}/pkgconfig"
                pkg_config_path += f":/usr/lib/{multiarch}/pkgconfig"

        # Add generic paths
        pkg_config_path += ":/usr/local/lib/pkgconfig"
        pkg_config_path += ":/usr/local/share/pkgconfig"
        pkg_config_path += ":/usr/lib/pkgconfig"
        pkg_config_path += ":/usr/share/pkgconfig"
        pkg_config_path += ":/usr/lib64/pkgconfig"

        self.env = {
            "PATH": f"{self.workspace}/bin:{os.environ.get('PATH', '')}",
            "PKG_CONFIG_PATH": pkg_config_path,
            "CFLAGS": self.cflags,
            "CXXFLAGS": self.cxxflags,
            "LDFLAGS": self.ldflags,
            "LDEXEFLAGS": self.ldexeflags,
        }

        if self.platform == "darwin" and self.platform_detector.platform_info.macports_clang:
            clang_path = self.platform_detector.platform_info.macports_clang.path
            self.env["CC"] = clang_path
            self.env["CXX"] = clang_path.replace("clang", "clang++")

        # CUDA paths
        if self.platform_detector.platform_info.cuda_available:
            cuda_path = self.platform_detector.platform_info.cuda_path
            if cuda_path:
                cuda_home = str(Path(cuda_path).parent.parent)
                self.cflags += f" -I{cuda_home}/include"
                self.ldflags += f" -L{cuda_home}/lib64"
                if self.platform_detector.platform_info.is_wsl2:
                    self.ldflags += " -L/usr/lib/wsl/lib"
                self.env["PATH"] = f"{cuda_home}/bin:{self.env['PATH']}"
                self.env["CFLAGS"] = self.cflags
                self.env["LDFLAGS"] = self.ldflags

        # Vulkan paths
        if self.platform_detector.platform_info.vulkan_available:
            self.env["CFLAGS"] = self.cflags
            self.env["LDFLAGS"] = self.ldflags

        # VAAPI paths (required for QSV)
        if self.platform_detector.platform_info.vaapi_available:
            self.env["CFLAGS"] = self.cflags
            self.env["LDFLAGS"] = self.ldflags

    def get_build_env(self, component: Optional[Component] = None) -> Dict[str, str]:
        """Get build environment for a component.

        Args:
            component: Component instance.

        Returns:
            Environment dictionary.
        """
        env = self.env.copy()

        if component:
            if self.platform in component.platform_overrides:
                override = component.platform_overrides[self.platform]
                env.update(override.extra_env)

                if override.extra_cflags:
                    env["CFLAGS"] += f" {override.extra_cflags}"
                if override.extra_cxxflags:
                    env["CXXFLAGS"] += f" {override.extra_cxxflags}"
                if override.extra_ldflags:
                    env["LDFLAGS"] += f" {override.extra_ldflags}"

            env.update(component.extra_env)

        return env

    def prefetch_downloads(self, components: List[Component]) -> None:
        """Start background downloads for buildable source archives.

        Args:
            components: Components to prefetch.
        """
        if self.async_download_manager is None:
            return

        prefetch_components = []
        for component in components:
            if self.state_manager.is_component_completed(component.name, component.version):
                continue
            if component.system_component and component.system_tool_name:
                if self._is_system_component_available(component.system_tool_name):
                    continue
            prefetch_components.append(component)

        self.async_download_manager.prefetch(prefetch_components)
        if self.on_log is not None and prefetch_components:
            self.on_log(f"Prefetch queued {len(prefetch_components)} archives")

    def retry_download(self, component: Component) -> None:
        """Retry a background download for a component.

        Args:
            component: Component to retry.
        """
        if self.async_download_manager is not None:
            self.async_download_manager.retry(component)

    def shutdown_downloads(self, wait: bool = True) -> None:
        """Shutdown background download workers.

        Args:
            wait: Whether to wait for running downloads.
        """
        if self.async_download_manager is not None:
            self.async_download_manager.shutdown(wait)

    def build_all(self, components: List[Component]) -> List[str]:
        """Build all components.

        Args:
            components: List of components to build.

        Returns:
            List of successfully built component names.
        """
        built = []
        total = len(components)

        state = self.state_manager.get()
        state.config = self.config.to_dict()
        state.total_steps = total
        self.state_manager.save()

        with tqdm(total=total, desc="Building FFmpeg", unit="component") as pbar:
            for idx, component in enumerate(components, 1):
                state.current_step = idx
                self.state_manager.save()

                try:
                    self.build_component(component)
                    built.append(component.name)
                    self.state_manager.mark_component_status(
                        component.name,
                        ComponentStatus.COMPLETED,
                        component.version,
                    )
                except BuildError as e:
                    self.state_manager.mark_component_status(
                        component.name,
                        ComponentStatus.FAILED,
                        component.version,
                        str(e),
                        str(e.log_file) if e.log_file else None,
                    )
                    raise

                pbar.update(1)
                pbar.set_postfix_str(component.name)

        return built

    def build_component(self, component: Component) -> None:
        """Build a single component.

        Args:
            component: Component to build.
        """
        if self.state_manager.is_component_completed(component.name, component.version):
            return

        # Skip system components if already available
        if component.system_component and component.system_tool_name:
            if self._is_system_component_available(component.system_tool_name):
                self.state_manager.mark_component_status(
                    component.name,
                    ComponentStatus.SYSTEM,
                    component.version,
                )
                return

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.DOWNLOADING,
            component.version,
            detail="queued" if self.async_download_manager is not None else "starting",
        )

        archive_path = self._download_and_extract(component)
        source_dir = self.packages / component.get_target_dir()

        if component.build_system == BuildSystem.HEADERS_ONLY:
            self.state_manager.mark_component_status(
                component.name,
                ComponentStatus.INSTALLING,
                component.version,
                detail="install headers",
            )
            self._install_headers_only(component, source_dir)
            self._execute_post_install(component, source_dir)
            return

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.CONFIGURING,
            component.version,
            detail=self._configure_detail(component),
        )

        if component.custom_build_fn:
            build_fn = getattr(self, component.custom_build_fn, None)
            if build_fn:
                build_fn(component, source_dir)
                self._execute_post_install(component, source_dir)
                return

        if component.build_system == BuildSystem.AUTOTOOLS:
            self._build_autotools(component, source_dir)
        elif component.build_system == BuildSystem.CMAKE:
            self._build_cmake(component, source_dir)
        elif component.build_system == BuildSystem.MESON:
            self._build_meson(component, source_dir)
        elif component.build_system == BuildSystem.MAKE_ONLY:
            self._build_make_only(component, source_dir)
        elif component.build_system == BuildSystem.CARGO:
            self._build_cargo(component, source_dir)
        else:
            raise BuildError(component.name, f"Unknown build system: {component.build_system}")

        self._execute_post_install(component, source_dir)

    def _configure_detail(self, component: Component) -> str:
        """Return a short human-readable description of the configure step."""
        system = component.build_system
        if system == BuildSystem.AUTOTOOLS:
            return "./configure"
        if system == BuildSystem.CMAKE:
            return "cmake"
        if system == BuildSystem.MESON:
            return "meson setup"
        if system == BuildSystem.MAKE_ONLY:
            return "make"
        if system == BuildSystem.CARGO:
            return "cargo"
        return ""

    def _execute_post_install(self, component: Component, source_dir: Path) -> None:
        """Execute post-install commands if defined.

        Args:
            component: Component to process.
            source_dir: Source directory.
        """
        if not component.post_install:
            return

        cmd = component.post_install.replace("{workspace}", str(self.workspace.absolute()))
        env = self.get_build_env(component)

        result, log_file = self.executor.execute_with_log(
            ["sh", "-c", cmd],
            component.name,
            "post-install",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, f"Post-install failed: {cmd}", log_file)

    def _is_system_component_available(self, tool_name: str) -> bool:
        """Check if a system component is already available.

        Args:
            tool_name: Name of the tool/library to check.

        Returns:
            True if available in system, False otherwise.
        """
        import shutil
        import subprocess

        # Check if it's a known tool
        if tool_name in self.platform_detector.tools:
            tool_info = self.platform_detector.tools[tool_name]
            if tool_info.available:
                return True

        # Check if tool exists in PATH
        if shutil.which(tool_name):
            return True

        # Check via pkg-config for libraries
        try:
            result = subprocess.run(
                ["pkg-config", "--exists", tool_name],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

        # Check for common library headers
        lib_headers = {
            "giflib": "/usr/include/gif_lib.h",
            "zlib": "/usr/include/zlib.h",
        }

        if tool_name in lib_headers:
            from pathlib import Path
            if Path(lib_headers[tool_name]).exists():
                return True

        return False

    def _download_and_extract(self, component: Component) -> Path:
        """Download and extract component source.

        Args:
            component: Component to download.

        Returns:
            Path to extracted source.
        """
        url = component.get_url()
        filename = component.get_archive_filename()

        try:
            if self.async_download_manager is None:
                if self.on_download_status is not None:
                    self.on_download_status(component.name, "downloading")
                archive_path = self.downloader.download(
                    url,
                    filename,
                    show_progress=self.on_download_status is None,
                )
            else:
                archive_path = self.async_download_manager.get(component)
        except Exception as e:
            raise BuildError(component.name, f"Failed to download archive: {e}")

        target_dir = self.packages / component.get_target_dir()
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True)

        try:
            with tarfile.open(archive_path, "r:*") as tar:
                if component.archive_strip_components == 1:
                    for member in tar.getmembers():
                        member_path = Path(member.name)
                        if len(member_path.parts) > 1:
                            member.name = str(Path(*member_path.parts[1:]))
                            if member.name:
                                tar.extract(member, target_dir)
                else:
                    tar.extractall(target_dir)
        except Exception as e:
            raise BuildError(component.name, f"Failed to extract archive: {e}")

        return archive_path

    def _build_autotools(self, component: Component, source_dir: Path) -> None:
        """Build component with autotools.

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        build_dir = source_dir
        if component.workdir:
            build_dir = source_dir / component.workdir

        configure_args = [
            arg.replace("{workspace}", str(self.workspace.absolute()))
            .replace("{num_jobs}", str(self.num_jobs))
            for arg in component.configure_args
        ]

        env = self.get_build_env(component)

        result, log_file = self.executor.execute_with_log(
            ["./configure"] + configure_args,
            component.name,
            "configure",
            build_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Configure failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.BUILDING,
            component.version,
            detail=f"make -j{self.num_jobs}",
        )

        result, log_file = self.executor.execute_make(
            build_dir,
            self.num_jobs,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
            detail="make install",
        )

        result, log_file = self.executor.execute_install(
            build_dir,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)

    def _build_cmake(self, component: Component, source_dir: Path) -> None:
        """Build component with CMake.

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        build_dir = source_dir
        if component.workdir:
            build_dir = source_dir / component.workdir
            build_dir.mkdir(parents=True, exist_ok=True)

        cmake_args = [
            arg.replace("{workspace}", str(self.workspace.absolute()))
            for arg in component.configure_args
        ]

        env = self.get_build_env(component)

        result, log_file = self.executor.execute_with_log(
            ["cmake", "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"] + cmake_args + [str(source_dir)],
            component.name,
            "configure",
            build_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "CMake configure failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.BUILDING,
            component.version,
            detail="make -j" + str(self.num_jobs),
        )

        result, log_file = self.executor.execute_make(
            build_dir,
            self.num_jobs,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
            detail="make install",
        )

        result, log_file = self.executor.execute_with_log(
            ["make", "install"],
            component.name,
            "install",
            build_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)

    def _build_meson(self, component: Component, source_dir: Path) -> None:
        """Build component with Meson.

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        build_dir = source_dir / "build"
        build_dir.mkdir(parents=True, exist_ok=True)

        meson_args = [
            arg.replace("{workspace}", str(self.workspace.absolute()))
            for arg in component.configure_args
        ]

        env = self.get_build_env(component)

        result, log_file = self.executor.execute_with_log(
            ["meson", "setup", "build"] + meson_args,
            component.name,
            "configure",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Meson configure failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.BUILDING,
            component.version,
            detail="ninja -C build",
        )

        result, log_file = self.executor.execute_with_log(
            ["ninja", "-C", "build"],
            component.name,
            "build",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
            detail="ninja install",
        )

        result, log_file = self.executor.execute_with_log(
            ["ninja", "-C", "build", "install"],
            component.name,
            "install",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)

    def _build_make_only(self, component: Component, source_dir: Path) -> None:
        """Build component with make only.

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        build_dir = source_dir
        if component.workdir:
            build_dir = source_dir / component.workdir

        env = self.get_build_env(component)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.BUILDING,
            component.version,
            detail="make -j" + str(self.num_jobs),
        )

        build_args = [
            arg.replace("{workspace}", str(self.workspace.absolute()))
            for arg in component.build_args
        ]

        result, log_file = self.executor.execute_with_log(
            ["make", f"-j{self.num_jobs}"] + build_args,
            component.name,
            "build",
            build_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
            detail="make install",
        )

        install_args = [
            arg.replace("{workspace}", str(self.workspace.absolute()))
            for arg in component.install_args
        ]

        result, log_file = self.executor.execute_with_log(
            ["make", "install"] + install_args,
            component.name,
            "install",
            build_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)

    def _get_rustc_version(self) -> Optional[Tuple[int, int, int]]:
        """Get installed rustc version.

        Returns:
            Tuple of (major, minor, patch) or None if not available.
        """
        env = self.get_build_env()
        result = self.executor.execute(["rustc", "--version"], env=env)
        if not result.success:
            return None
        match = re.search(r"rustc\s+(\d+)\.(\d+)\.(\d+)", result.stdout)
        if not match:
            return None
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))

    def _build_cargo(self, component: Component, source_dir: Path) -> None:
        """Build component with Cargo.

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        env = self.get_build_env(component)
        env["RUSTFLAGS"] = "-C target-cpu=native"

        rustc_version = self._get_rustc_version()
        if rustc_version is None:
            raise SkipComponent(
                component.name,
                "rustc is not available or version cannot be determined"
            )

        if rustc_version < (1, 95, 0):
            raise SkipComponent(
                component.name,
                f"rustc {'.'.join(map(str, rustc_version))} is too old. "
                f"cargo-c requires rustc 1.95 or newer"
            )

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.BUILDING,
            component.version,
            detail="cargo install cargo-c",
        )

        result, log_file = self.executor.execute_with_log(
            ["cargo", "install", "cargo-c"],
            component.name,
            "install-cargo-c",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Failed to install cargo-c", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
            detail="cargo cinstall",
        )

        result, log_file = self.executor.execute_with_log(
            [
                "cargo", "cinstall",
                f"--prefix={self.workspace}",
                "--libdir=lib",
                "--library-type=staticlib",
                "--crt-static",
                "--release",
            ],
            component.name,
            "build",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Cargo build failed", log_file)

    def _install_headers_only(self, component: Component, source_dir: Path) -> None:
        """Install headers only.

        Args:
            component: Component to install.
            source_dir: Source directory.
        """
        if component.name == "VapourSynth":
            dest = self.workspace / "include" / "vapoursynth"
            dest.mkdir(parents=True, exist_ok=True)
            src = source_dir / "include"
            if src.exists():
                for item in src.iterdir():
                    dest_item = dest / item.name
                    if item.is_file():
                        shutil.copy2(item, dest_item)
                    elif item.is_dir():
                        shutil.copytree(item, dest_item, dirs_exist_ok=True)

        elif component.name == "amf":
            dest = self.workspace / "include" / "AMF"
            if dest.exists():
                shutil.rmtree(dest)
            dest.mkdir(parents=True)
            src = source_dir / "amf" / "public" / "include"
            if src.exists():
                for item in src.iterdir():
                    dest_item = dest / item.name
                    if item.is_file():
                        shutil.copy2(item, dest_item)
                    elif item.is_dir():
                        shutil.copytree(item, dest_item, dirs_exist_ok=True)

    def build_giflib(self, component: Component, source_dir: Path) -> None:
        """Build giflib.

        Patches Makefile to skip documentation build (requires ImageMagick).

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        env = self.get_build_env(component)

        makefile = source_dir / "Makefile"
        if makefile.exists():
            content = makefile.read_text()
            content = content.replace("$(MAKE) -C doc", "")
            content = content.replace(
                "install: all install-bin install-include install-lib install-man",
                "install: all install-bin install-include install-lib"
            )
            makefile.write_text(content)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.BUILDING,
            component.version,
        detail='make -jself.num_jobs',
        )

        result, log_file = self.executor.execute_make(
            source_dir,
            1,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
        detail='make install',
        )

        result, log_file = self.executor.execute_with_log(
            ["make", f"PREFIX={self.workspace.absolute()}", "install"],
            component.name,
            "install",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)

    def build_openssl(self, component: Component, source_dir: Path) -> None:
        """Build OpenSSL.

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        env = self.get_build_env(component)

        result, log_file = self.executor.execute_with_log(
            [
                "./Configure",
                f"--prefix={self.workspace}",
                f"--openssldir={self.workspace}",
                "--libdir=lib",
                f"--with-zlib-include={self.workspace}/include/",
                f"--with-zlib-lib={self.workspace}/lib",
                "no-shared",
                "zlib",
            ],
            component.name,
            "configure",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Configure failed", log_file)

        # OpenSSL Configure forces -std=c11 on x86_64, which breaks GCC 16's
        # handling of inline assembly in crypto/bn/asm/x86_64-gcc.c. Replace it
        # with -std=gnu11 in the generated config and regenerate the Makefile.
        configdata = source_dir / "configdata.pm"
        if configdata.exists():
            content = configdata.read_text()
            content = content.replace("-std=c11", "-std=gnu11")
            configdata.write_text(content)
            result2 = self.executor.execute(
                ["perl", str(configdata)],
                cwd=source_dir,
                env=env,
            )
            if not result2.success:
                raise BuildError(component.name, "configdata.pm regeneration failed")

        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
        detail='make install_sw',
        )

        result, log_file = self.executor.execute_with_log(
            ["make", "install_sw"],
            component.name,
            "install",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)

    def build_x264(self, component: Component, source_dir: Path) -> None:
        """Build x264.

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        env = self.get_build_env(component)

        configure_args = [
            f"--prefix={self.workspace}",
            "--enable-static",
            "--enable-pic",
        ]

        if self.platform == "linux":
            env["CXXFLAGS"] = f"-fPIC {env.get('CXXFLAGS', '')}"

        result, log_file = self.executor.execute_with_log(
            ["./configure"] + configure_args,
            component.name,
            "configure",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Configure failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.BUILDING,
            component.version,
        detail='make -jself.num_jobs',
        )

        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
        detail='make install',
        )

        result, log_file = self.executor.execute_install(
            source_dir,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)

        result, log_file = self.executor.execute_with_log(
            ["make", "install-lib-static"],
            component.name,
            "install-lib",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Install lib-static failed", log_file)

    def build_x265(self, component: Component, source_dir: Path) -> None:
        """Build x265 (multi-bitdepth).

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        env = self.get_build_env(component)

        if self.platform == "darwin" and self.platform_detector.platform_info.is_arm64:
            env["CXXFLAGS"] = f"-DHAVE_NEON=1 {env.get('CXXFLAGS', '')}"

        # Patch json11.cpp to include <cstdint>. Newer libstdc++ (GCC 15/16)
        # no longer transitively pulls <cstdint> via <limits>, so uint8_t
        # becomes undeclared and the dynamicHDR10 helper fails to compile.
        # Mirrors the original bash script's sed patch.
        json11_cpp = source_dir / "source" / "dynamicHDR10" / "json11" / "json11.cpp"
        if json11_cpp.exists():
            content = json11_cpp.read_text()
            if "#include <cstdint>" not in content:
                lines = content.split("\n")
                insert_idx = None
                for i, line in enumerate(lines):
                    if line.strip() == "#include <limits>":
                        insert_idx = i + 1
                        break
                if insert_idx is not None:
                    lines.insert(insert_idx, "#include <cstdint>")
                    json11_cpp.write_text("\n".join(lines))

        build_linux = source_dir / "build" / "linux"
        if not build_linux.exists():
            raise BuildError(component.name, "Build directory not found")

        for bitdepth in ["12bit", "10bit", "8bit"]:
            bitdepth_dir = build_linux / bitdepth
            bitdepth_dir.mkdir(parents=True, exist_ok=True)

            cmake_args = [
                f"-DCMAKE_INSTALL_PREFIX={self.workspace}",
                "-DENABLE_SHARED=OFF",
                "-DBUILD_SHARED_LIBS=OFF",
            ]

            if bitdepth == "12bit":
                cmake_args.extend([
                    "-DHIGH_BIT_DEPTH=ON",
                    "-DENABLE_HDR10_PLUS=ON",
                    "-DEXPORT_C_API=OFF",
                    "-DENABLE_CLI=OFF",
                    "-DMAIN12=ON",
                ])
            elif bitdepth == "10bit":
                cmake_args.extend([
                    "-DHIGH_BIT_DEPTH=ON",
                    "-DENABLE_HDR10_PLUS=ON",
                    "-DEXPORT_C_API=OFF",
                    "-DENABLE_CLI=OFF",
                ])
            else:
                cmake_args.extend([
                    "-DENABLE_SHARED=OFF",
                    "-DBUILD_SHARED_LIBS=OFF",
                    "-DEXTRA_LIB=x265_main10.a;x265_main12.a;-ldl",
                    "-DEXTRA_LINK_FLAGS=-L.",
                    "-DLINKED_10BIT=ON",
                    "-DLINKED_12BIT=ON",
                ])

                # Copy 10bit and 12bit libraries into 8bit build dir before linking
                shutil.copy(build_linux / "10bit" / "libx265.a", bitdepth_dir / "libx265_main10.a")
                shutil.copy(build_linux / "12bit" / "libx265.a", bitdepth_dir / "libx265_main12.a")

            result, log_file = self.executor.execute_with_log(
                ["cmake", "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"] + cmake_args + ["../../../source"],
                component.name,
                f"configure-{bitdepth}",
                bitdepth_dir,
                env,
            )

            if not result.success:
                raise BuildError(component.name, f"Configure {bitdepth} failed", log_file)

            self.state_manager.mark_component_status(
                component.name,
                ComponentStatus.BUILDING,
                component.version,
            detail='make -jself.num_jobs (multi-bitdepth)',
            )

            result, log_file = self.executor.execute_make(
                bitdepth_dir,
                self.num_jobs,
                env,
                component.name,
                f"build-{bitdepth}",
            )

            if not result.success:
                raise BuildError(component.name, f"Build {bitdepth} failed", log_file)

        eight_dir = build_linux / "8bit"
        lib_main = eight_dir / "libx265.a"
        lib_main10 = eight_dir / "libx265_main10.a"
        lib_main12 = eight_dir / "libx265_main12.a"

        shutil.copy(build_linux / "10bit" / "libx265.a", lib_main10)
        shutil.copy(build_linux / "12bit" / "libx265.a", lib_main12)

        # Rename 8bit library before merging (matching original build-ffmpeg script)
        lib_main_renamed = eight_dir / "libx265_main.a"
        shutil.move(str(lib_main), str(lib_main_renamed))

        if self.platform == "darwin":
            libtool = "glibtool" if shutil.which("glibtool") else "libtool"
            result, log_file = self.executor.execute_with_log(
                [libtool, "-static", "-o", str(lib_main), str(lib_main_renamed), str(lib_main10), str(lib_main12)],
                component.name,
                "merge-libs",
                eight_dir,
                env,
            )
        else:
            m_script = "CREATE libx265.a\nADDLIB libx265_main.a\nADDLIB libx265_main10.a\nADDLIB libx265_main12.a\nSAVE\nEND\n"
            result, log_file = self.executor.execute_with_log(
                ["ar", "-M"],
                component.name,
                "merge-libs",
                eight_dir,
                env,
                stdin=m_script,
            )

        if not result.success:
            raise BuildError(component.name, "Merge libs failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
        detail='make install',
        )

        result, log_file = self.executor.execute_install(
            eight_dir,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)

        if self.config.full_static and self.platform == "linux":
            x265_pc = self.workspace / "lib" / "pkgconfig" / "x265.pc"
            if x265_pc.exists():
                content = x265_pc.read_text()
                content = content.replace("-lgcc_s", "-lgcc_eh")
                x265_pc.write_text(content)

    def build_libvpx(self, component: Component, source_dir: Path) -> None:
        """Build libvpx.

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        env = self.get_build_env(component)

        if self.platform == "darwin":
            makefile = source_dir / "build" / "make" / "Makefile"
            if makefile.exists():
                content = makefile.read_text()
                content = content.replace(",--version-script", "")
                content = content.replace("-Wl,--no-undefined -Wl,-soname", "-Wl,-undefined,error -Wl,-install_name")
                makefile.write_text(content)

        result, log_file = self.executor.execute_with_log(
            [
                "./configure",
                f"--prefix={self.workspace}",
                "--disable-unit-tests",
                "--disable-shared",
                "--disable-examples",
                "--as=yasm",
                "--enable-vp9-highbitdepth",
            ],
            component.name,
            "configure",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Configure failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.BUILDING,
            component.version,
        detail='make -jself.num_jobs',
        )

        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
        detail='make install',
        )

        result, log_file = self.executor.execute_install(
            source_dir,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)

    def build_zimg(self, component: Component, source_dir: Path) -> None:
        """Build zimg.

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        import shutil

        env = self.get_build_env(component)

        # Use workspace libtoolize if available, otherwise fall back to system
        libtoolize = f"{self.workspace}/bin/libtoolize"
        if not Path(libtoolize).exists():
            system_libtoolize = shutil.which("libtoolize")
            if system_libtoolize:
                libtoolize = system_libtoolize
            else:
                raise BuildError(component.name, "libtoolize not found")

        result, log_file = self.executor.execute_with_log(
            [libtoolize, "-i", "-f", "-q"],
            component.name,
            "libtoolize",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Libtoolize failed", log_file)

        result, log_file = self.executor.execute_with_log(
            ["./autogen.sh", f"--prefix={self.workspace}"],
            component.name,
            "autogen",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Autogen failed", log_file)

        result, log_file = self.executor.execute_with_log(
            ["./configure", f"--prefix={self.workspace}", "--enable-static", "--disable-shared"],
            component.name,
            "configure",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Configure failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.BUILDING,
            component.version,
        detail='make -jself.num_jobs',
        )

        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
        detail='make install',
        )

        result, log_file = self.executor.execute_install(
            source_dir,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)

    def build_libvorbis(self, component: Component, source_dir: Path) -> None:
        """Build libvorbis.

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        env = self.get_build_env(component)

        configure_ac = source_dir / "configure.ac"
        if configure_ac.exists():
            content = configure_ac.read_text()
            content = content.replace("-force_cpusubtype_ALL", "")
            configure_ac.write_text(content)

        result, log_file = self.executor.execute_with_log(
            ["./autogen.sh", f"--prefix={self.workspace}"],
            component.name,
            "autogen",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Autogen failed", log_file)

        result, log_file = self.executor.execute_with_log(
            [
                "./configure",
                f"--prefix={self.workspace}",
                f"--with-ogg-libraries={self.workspace}/lib",
                f"--with-ogg-includes={self.workspace}/include/",
                "--enable-static",
                "--disable-shared",
                "--disable-oggtest",
            ],
            component.name,
            "configure",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Configure failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.BUILDING,
            component.version,
        detail='make -jself.num_jobs',
        )

        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
        detail='make install',
        )

        result, log_file = self.executor.execute_install(
            source_dir,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)

    def build_libjxl(self, component: Component, source_dir: Path) -> None:
        """Build libjxl.

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        env = self.get_build_env(component)

        result, log_file = self.executor.execute_with_log(
            ["./deps.sh"],
            component.name,
            "deps",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Deps failed", log_file)

        cmake_args = [
            "-DBUILD_SHARED_LIBS=OFF",
            f"-DCMAKE_INSTALL_PREFIX={self.workspace}",
            "-DCMAKE_INSTALL_LIBDIR=lib",
            "-DCMAKE_INSTALL_BINDIR=bin",
            "-DCMAKE_INSTALL_INCLUDEDIR=include",
            "-DENABLE_SHARED=off",
            "-DENABLE_STATIC=ON",
            "-DCMAKE_BUILD_TYPE=Release",
            "-DJPEGXL_ENABLE_BENCHMARK=OFF",
            "-DJPEGXL_ENABLE_DOXYGEN=OFF",
            "-DJPEGXL_ENABLE_MANPAGES=OFF",
            "-DJPEGXL_ENABLE_TOOLS=OFF",
            "-DJPEGXL_ENABLE_EXAMPLES=OFF",
            "-DJPEGXL_ENABLE_JPEGLI_LIBJPEG=OFF",
            "-DJPEGXL_ENABLE_JPEGLI=ON",
            "-DJPEGXL_TEST_TOOLS=OFF",
            "-DJPEGXL_ENABLE_JNI=OFF",
            "-DBUILD_TESTING=OFF",
            "-DJPEGXL_ENABLE_SKCMS=OFF",
        ]

        result, log_file = self.executor.execute_with_log(
            ["cmake", "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"] + cmake_args + ["."],
            component.name,
            "configure",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Configure failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.BUILDING,
            component.version,
        detail='make -jself.num_jobs',
        )

        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
        detail='make install',
        )

        result, log_file = self.executor.execute_install(
            source_dir,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)

    def build_libvmaf(self, component: Component, source_dir: Path) -> None:
        """Build libvmaf.

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        env = self.get_build_env(component)

        libvmaf_dir = source_dir / "libvmaf"
        if not libvmaf_dir.exists():
            libvmaf_dir = source_dir

        build_dir = libvmaf_dir / "build"
        build_dir.mkdir(parents=True, exist_ok=True)

        result, log_file = self.executor.execute_with_log(
            [
                "meson", "setup", "build",
                f"--prefix={self.workspace}",
                "--buildtype=release",
                "--default-library=static",
                f"--libdir={self.workspace}/lib",
            ],
            component.name,
            "configure",
            libvmaf_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Configure failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.BUILDING,
            component.version,
            "ninja -C build",
        )

        result, log_file = self.executor.execute_with_log(
            ["ninja", "-C", "build"],
            component.name,
            "build",
            libvmaf_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
            "ninja install",
        )

        result, log_file = self.executor.execute_with_log(
            ["ninja", "-C", "build", "install"],
            component.name,
            "install",
            libvmaf_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)

    def build_srt(self, component: Component, source_dir: Path) -> None:
        """Build srt.

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        env = self.get_build_env(component)
        env["OPENSSL_ROOT_DIR"] = str(self.workspace)
        env["OPENSSL_LIB_DIR"] = str(self.workspace / "lib")
        env["OPENSSL_INCLUDE_DIR"] = str(self.workspace / "include")

        cmake_args = [
            f"-DCMAKE_INSTALL_PREFIX={self.workspace}",
            "-DCMAKE_INSTALL_LIBDIR=lib",
            "-DCMAKE_INSTALL_BINDIR=bin",
            "-DCMAKE_INSTALL_INCLUDEDIR=include",
            "-DENABLE_SHARED=OFF",
            "-DENABLE_STATIC=ON",
            "-DENABLE_APPS=OFF",
            "-DUSE_STATIC_LIBSTDCXX=ON",
        ]

        result, log_file = self.executor.execute_with_log(
            ["cmake", "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"] + cmake_args + ["."],
            component.name,
            "configure",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Configure failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
        detail='make install',
        )

        result, log_file = self.executor.execute_with_log(
            ["make", "install"],
            component.name,
            "install",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)

        if self.config.full_static and self.platform == "linux":
            srt_pc = self.workspace / "lib" / "pkgconfig" / "srt.pc"
            if srt_pc.exists():
                content = srt_pc.read_text()
                content = content.replace("-lgcc_s", "-lgcc_eh")
                srt_pc.write_text(content)

    def build_libzmq(self, component: Component, source_dir: Path) -> None:
        """Build libzmq.

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        env = self.get_build_env(component)

        if self.platform == "darwin":
            env["XML_CATALOG_FILES"] = "/usr/local/etc/xml/catalog"

        result, log_file = self.executor.execute_with_log(
            ["./configure", f"--prefix={self.workspace}", "--disable-shared", "--enable-static"],
            component.name,
            "configure",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Configure failed", log_file)

        proxy_cpp = source_dir / "src" / "proxy.cpp"
        if proxy_cpp.exists():
            content = proxy_cpp.read_text()
            content = content.replace(
                "stats_proxy stats = {0}",
                "stats_proxy stats = {{{0, 0}, {0, 0}}, {{0, 0}, {0, 0}}}"
            )
            proxy_cpp.write_text(content)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.BUILDING,
            component.version,
            f"make -j{self.num_jobs}",
        )

        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
            "make install",
        )

        result, log_file = self.executor.execute_install(
            source_dir,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)

    def build_glslang(self, component: Component, source_dir: Path) -> None:
        """Build glslang.

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        env = self.get_build_env(component)

        result, log_file = self.executor.execute_with_log(
            ["./update_glslang_sources.py"],
            component.name,
            "update-sources",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Update sources failed", log_file)

        cmake_args = [
            "-DCMAKE_BUILD_TYPE=Release",
            "-DENABLE_SHARED=OFF",
            "-DBUILD_SHARED_LIBS=OFF",
            f"-DCMAKE_INSTALL_PREFIX={self.workspace}",
        ]

        result, log_file = self.executor.execute_with_log(
            ["cmake", "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"] + cmake_args + ["."],
            component.name,
            "configure",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Configure failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.BUILDING,
            component.version,
        detail='make -jself.num_jobs',
        )

        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
        detail='make install',
        )

        result, log_file = self.executor.execute_install(
            source_dir,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)

    def build_ninja(self, component: Component, source_dir: Path) -> None:
        """Build ninja build system from source.

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        env = self.get_build_env(component)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.BUILDING,
            component.version,
        detail='./configure.py --bootstrap',
        )

        result, log_file = self.executor.execute_with_log(
            ["./configure.py", "--bootstrap"],
            component.name,
            "bootstrap",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Bootstrap failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
        detail='install ninja',
        )

        ninja_bin = source_dir / "ninja"
        dest = self.workspace / "bin" / "ninja"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ninja_bin, dest)

    def build_ffmpeg(self, component: Component, source_dir: Path) -> None:
        """Build FFmpeg.

        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        env = self.get_build_env(component)

        built_components = [
            name for name, state in self.state_manager.get().components.items()
            if state.status == ComponentStatus.COMPLETED
        ]

        # Add libraries conditionally based on built components
        if "libvmaf" in built_components:
            if self.platform == "darwin":
                self.extralibs += " -lc++"
            else:
                self.extralibs += " -lstdc++"

        if "libjxl" in built_components:
            self.extralibs += " -llcms2"
            # libjxl_threads is a C++ library but its pkg-config file does
            # not list -lstdc++ in Libs.private, so FFmpeg's pkg-config
            # check fails to link. Adding -lstdc++ here covers the
            # std::thread / std::condition_variable symbols.
            if self.platform != "darwin":
                self.extralibs += " -lstdc++"

        if "opencl-icd-loader" in built_components:
            self.extralibs += " -lva"

        configure_flags = self.registry.get_ffmpeg_configure_flags(
            built_components,
            self.config.gpl_enabled,
            self.platform,
        )

        configure_args = [
            "--disable-debug",
            "--disable-shared",
            "--enable-pthreads",
            "--enable-static",
            "--enable-version3",
            f"--extra-cflags={self.cflags}",
            f"--extra-ldexeflags={self.ldexeflags}",
            f"--extra-ldflags={self.ldflags}",
            f"--extra-libs={self.extralibs}",
            f"--pkgconfigdir={self.workspace}/lib/pkgconfig",
            "--pkg-config-flags=--static",
            f"--prefix={self.workspace}",
        ]

        if self.config.gpl_enabled:
            configure_args.append("--enable-gpl")
            configure_args.append("--enable-nonfree")

        # CUDA support
        if self.platform_detector.platform_info.cuda_available:
            configure_args.append("--enable-cuda-nvcc")
            configure_args.append("--enable-cuvid")
            configure_args.append("--enable-nvdec")
            configure_args.append("--enable-nvenc")
            configure_args.append("--enable-cuda-llvm")
            configure_args.append("--enable-ffnvcodec")

            cuda_cc = os.environ.get("CUDA_COMPUTE_CAPABILITY")
            if not cuda_cc:
                cuda_cc = self.platform_detector.platform_info.cuda_compute_capability
            if not cuda_cc:
                cuda_cc = "52"

            configure_args.append(
                f"--nvccflags=-gencode arch=compute_{cuda_cc},code=sm_{cuda_cc} -O2"
            )
        else:
            configure_args.append("--disable-ffnvcodec")

        # VAAPI support
        if self.platform_detector.platform_info.vaapi_available and not self.config.full_static:
            configure_args.append("--enable-vaapi")

        # Intel QSV support
        if self.platform_detector.platform_info.qsv_available:
            configure_args.append("--enable-libvpl")

        if self.platform == "darwin":
            configure_args.append(f"--extra-version={component.version}")

        configure_args.extend(configure_flags)

        result, log_file = self.executor.execute_with_log(
            ["./configure"] + configure_args,
            component.name,
            "configure",
            source_dir,
            env,
        )

        if not result.success:
            raise BuildError(component.name, "Configure failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.BUILDING,
            component.version,
        detail='make -jself.num_jobs',
        )

        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)

        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.INSTALLING,
            component.version,
        detail='make install',
        )

        result, log_file = self.executor.execute_install(
            source_dir,
            env,
            component.name,
        )

        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)
