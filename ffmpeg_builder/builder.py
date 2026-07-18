"""Build orchestration engine."""
import os
import tarfile
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Callable
from tqdm import tqdm

from .config import BuildConfig
from .state import StateManager, ComponentStatus
from .components import Component, ComponentRegistry, BuildSystem
from .executor import CommandExecutor, ExecutionResult
from .downloader import Downloader
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


class FFmpegBuilder:
    """Orchestrates FFmpeg build process."""
    
    def __init__(
        self,
        config: BuildConfig,
        workspace: Path,
        packages: Path,
        state_manager: StateManager,
        platform_detector: PlatformDetector,
    ):
        """Initialize builder.
        
        Args:
            config: Build configuration.
            workspace: Workspace directory.
            packages: Packages directory.
            state_manager: State manager instance.
            platform_detector: Platform detector instance.
        """
        self.config = config
        self.workspace = workspace
        self.packages = packages
        self.state_manager = state_manager
        self.platform_detector = platform_detector
        
        self.executor = CommandExecutor(workspace)
        self.downloader = Downloader(packages)
        self.registry = ComponentRegistry()
        
        self.num_jobs = platform_detector.get_num_jobs(config.num_jobs)
        self.platform = "darwin" if platform_detector.platform_info.is_macos else "linux"
        
        self._setup_environment()
    
    def _setup_environment(self) -> None:
        """Setup build environment."""
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.packages.mkdir(parents=True, exist_ok=True)
        
        self.cflags = f"-I{self.workspace}/include -Wno-int-conversion"
        self.ldflags = f"-L{self.workspace}/lib"
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
        pkg_config_path += ":/usr/local/lib/x86_64-linux-gnu/pkgconfig"
        pkg_config_path += ":/usr/local/lib/pkgconfig"
        pkg_config_path += ":/usr/local/share/pkgconfig"
        pkg_config_path += ":/usr/lib/x86_64-linux-gnu/pkgconfig"
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
            if component.name in component.platform_overrides:
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
        
        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.DOWNLOADING,
            component.version,
        )
        
        archive_path = self._download_and_extract(component)
        source_dir = self.packages / component.get_target_dir()
        
        if component.build_system == BuildSystem.HEADERS_ONLY:
            self._install_headers_only(component, source_dir)
            return
        
        self.state_manager.mark_component_status(
            component.name,
            ComponentStatus.CONFIGURING,
            component.version,
        )
        
        if component.custom_build_fn:
            build_fn = getattr(self, component.custom_build_fn, None)
            if build_fn:
                build_fn(component, source_dir)
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
    
    def _download_and_extract(self, component: Component) -> Path:
        """Download and extract component source.
        
        Args:
            component: Component to download.
            
        Returns:
            Path to extracted source.
        """
        url = component.get_url()
        filename = component.get_archive_filename()
        
        archive_path = self.downloader.download(url, filename)
        
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
            arg.replace("{workspace}", str(self.workspace))
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
            arg.replace("{workspace}", str(self.workspace))
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
            arg.replace("{workspace}", str(self.workspace))
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
        )
        
        result, log_file = self.executor.execute_install(
            build_dir,
            env,
            component.name,
        )
        
        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)
    
    def _build_cargo(self, component: Component, source_dir: Path) -> None:
        """Build component with Cargo.
        
        Args:
            component: Component to build.
            source_dir: Source directory.
        """
        env = self.get_build_env(component)
        env["RUSTFLAGS"] = "-C target-cpu=native"
        
        result, log_file = self.executor.execute_with_log(
            ["cargo", "install", "cargo-c"],
            component.name,
            "install-cargo-c",
            source_dir,
            env,
        )
        
        if not result.success:
            raise BuildError(component.name, "Failed to install cargo-c", log_file)
        
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
        
        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )
        
        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)
        
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
        
        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )
        
        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)
        
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
            
            result, log_file = self.executor.execute_with_log(
                ["cmake", "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"] + cmake_args + ["../../.."],
                component.name,
                f"configure-{bitdepth}",
                bitdepth_dir,
                env,
            )
            
            if not result.success:
                raise BuildError(component.name, f"Configure {bitdepth} failed", log_file)
            
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
        
        if self.platform == "darwin":
            libtool = "glibtool" if shutil.which("glibtool") else "libtool"
            result, log_file = self.executor.execute_with_log(
                [libtool, "-static", "-o", str(lib_main), str(lib_main), str(lib_main10), str(lib_main12)],
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
        
        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )
        
        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)
        
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
        env = self.get_build_env(component)
        
        result, log_file = self.executor.execute_with_log(
            [f"{self.workspace}/bin/libtoolize", "-i", "-f", "-q"],
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
        
        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )
        
        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)
        
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
        
        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )
        
        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)
        
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
        
        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )
        
        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)
        
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
        
        result, log_file = self.executor.execute_with_log(
            ["ninja", "-C", "build"],
            component.name,
            "build",
            libvmaf_dir,
            env,
        )
        
        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)
        
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
        
        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )
        
        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)
        
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
        
        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )
        
        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)
        
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
        
        result, log_file = self.executor.execute_with_log(
            ["./configure.py", "--bootstrap"],
            component.name,
            "bootstrap",
            source_dir,
            env,
        )
        
        if not result.success:
            raise BuildError(component.name, "Bootstrap failed", log_file)
        
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
        
        if "nv-codec" in built_components:
            self.extralibs += " -lcuda"
        
        if "vulkan-headers" in built_components:
            self.extralibs += " -lvulkan"
        
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
        
        # Intel QSV support
        if self.platform_detector.platform_info.qsv_available:
            configure_args.append("--enable-libvpl")
            if not self.config.full_static:
                configure_args.append("--enable-vaapi")
        
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
        
        result, log_file = self.executor.execute_make(
            source_dir,
            self.num_jobs,
            env,
            component.name,
        )
        
        if not result.success:
            raise BuildError(component.name, "Build failed", log_file)
        
        result, log_file = self.executor.execute_install(
            source_dir,
            env,
            component.name,
        )
        
        if not result.success:
            raise BuildError(component.name, "Install failed", log_file)
