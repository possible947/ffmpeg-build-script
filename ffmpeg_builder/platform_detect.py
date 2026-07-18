"""Platform detection and system information gathering."""
import platform
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field


@dataclass
class SystemInfo:
    """System information."""
    os_name: str = ""
    os_version: str = ""
    architecture: str = ""
    cpu_model: str = ""
    cpu_cores: int = 0
    ram_gb: float = 0.0
    gpu_info: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "os_name": self.os_name,
            "os_version": self.os_version,
            "architecture": self.architecture,
            "cpu_model": self.cpu_model,
            "cpu_cores": self.cpu_cores,
            "ram_gb": self.ram_gb,
            "gpu_info": self.gpu_info
        }


@dataclass
class ToolInfo:
    """Information about a tool."""
    name: str
    path: Optional[str] = None
    version: Optional[str] = None
    available: bool = False
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "path": self.path,
            "version": self.version,
            "available": self.available
        }


@dataclass
class PlatformInfo:
    """Platform-specific information."""
    is_macos: bool = False
    is_linux: bool = False
    is_arm64: bool = False
    is_wsl2: bool = False
    macports_clang: Optional[ToolInfo] = None
    cuda_available: bool = False
    cuda_path: Optional[str] = None
    cuda_compute_capability: Optional[str] = None
    vaapi_available: bool = False
    qsv_available: bool = False
    amf_available: bool = False
    vulkan_available: bool = False
    opencl_available: bool = False
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "is_macos": self.is_macos,
            "is_linux": self.is_linux,
            "is_arm64": self.is_arm64,
            "is_wsl2": self.is_wsl2,
            "macports_clang": self.macports_clang.to_dict() if self.macports_clang else None,
            "cuda_available": self.cuda_available,
            "cuda_path": self.cuda_path,
            "cuda_compute_capability": self.cuda_compute_capability,
            "vaapi_available": self.vaapi_available,
            "qsv_available": self.qsv_available,
            "amf_available": self.amf_available,
            "vulkan_available": self.vulkan_available,
            "opencl_available": self.opencl_available
        }


class PlatformDetector:
    """Detects platform and system information."""
    
    def __init__(self):
        """Initialize platform detector."""
        self.system_info = SystemInfo()
        self.platform_info = PlatformInfo()
        self.tools: Dict[str, ToolInfo] = {}
    
    def detect_all(self) -> Tuple[SystemInfo, PlatformInfo, Dict[str, ToolInfo]]:
        """Detect all system information.
        
        Returns:
            Tuple of (SystemInfo, PlatformInfo, tools dict).
        """
        self._detect_system_info()
        self._detect_platform_info()
        self._detect_tools()
        
        return self.system_info, self.platform_info, self.tools
    
    def _detect_system_info(self) -> None:
        """Detect system information."""
        self.system_info.os_name = platform.system()
        self.system_info.architecture = platform.machine()
        
        # Get proper OS name and version
        if platform.system() == "Linux":
            try:
                with open("/etc/os-release", "r") as f:
                    for line in f:
                        if line.startswith("PRETTY_NAME="):
                            pretty_name = line.split("=", 1)[1].strip().strip('"')
                            self.system_info.os_name = pretty_name
                            self.system_info.os_version = ""
                            break
            except Exception:
                self.system_info.os_version = platform.version()
        elif platform.system() == "Darwin":
            self.system_info.os_version = platform.mac_ver()[0]
        else:
            self.system_info.os_version = platform.version()
        
        # CPU info
        try:
            if platform.system() == "Darwin":
                result = subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                self.system_info.cpu_model = result.stdout.strip()
                
                result = subprocess.run(
                    ["sysctl", "-n", "hw.ncpu"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                self.system_info.cpu_cores = int(result.stdout.strip())
                
            elif platform.system() == "Linux":
                with open("/proc/cpuinfo", "r") as f:
                    content = f.read()
                    for line in content.split("\n"):
                        if "model name" in line:
                            self.system_info.cpu_model = line.split(":")[1].strip()
                            break
                    
                    self.system_info.cpu_cores = content.count("processor")
                
                # RAM info
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if "MemTotal" in line:
                            ram_kb = int(line.split()[1])
                            self.system_info.ram_gb = ram_kb / (1024 * 1024)
                            break
                
        except Exception:
            pass
        
        # macOS RAM
        if platform.system() == "Darwin":
            try:
                result = subprocess.run(
                    ["sysctl", "-n", "hw.memsize"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                ram_bytes = int(result.stdout.strip())
                self.system_info.ram_gb = ram_bytes / (1024 ** 3)
            except Exception:
                pass
    
    def _detect_platform_info(self) -> None:
        """Detect platform-specific information."""
        self.platform_info.is_macos = platform.system() == "Darwin"
        self.platform_info.is_linux = platform.system() == "Linux"
        self.platform_info.is_arm64 = platform.machine() == "arm64"
        
        # Detect WSL2
        if self.platform_info.is_linux:
            self.platform_info.is_wsl2 = self._check_wsl2()
        
        # Detect macports clang on macOS
        if self.platform_info.is_macos:
            self.platform_info.macports_clang = self._find_macports_clang()
        
        # Detect CUDA on Linux
        if self.platform_info.is_linux:
            self._detect_cuda()
            self.platform_info.vaapi_available = self._check_vaapi()
            self.platform_info.qsv_available = self._check_qsv()
            self.platform_info.amf_available = self._check_amf()
            self.platform_info.vulkan_available = self._check_vulkan()
            self.platform_info.opencl_available = self._check_opencl()
    
    def _detect_cuda(self) -> None:
        """Detect CUDA installation."""
        # Try PATH first
        nvcc_path = shutil.which("nvcc")
        if nvcc_path:
            self.platform_info.cuda_available = True
            self.platform_info.cuda_path = nvcc_path
            self._detect_cuda_compute_capability()
            return
        
        # Try common CUDA installation paths
        cuda_paths = [
            Path("/usr/local/cuda/bin/nvcc"),
            Path("/usr/local/cuda-12/bin/nvcc"),
            Path("/usr/local/cuda-11/bin/nvcc"),
        ]
        
        # Also check versioned paths like /usr/local/cuda-12.*
        for cuda_dir in Path("/usr/local").glob("cuda-*/bin/nvcc"):
            cuda_paths.append(cuda_dir)
        
        for nvcc in cuda_paths:
            if nvcc.exists():
                self.platform_info.cuda_available = True
                self.platform_info.cuda_path = str(nvcc)
                self._detect_cuda_compute_capability()
                return
    
    def _detect_cuda_compute_capability(self) -> None:
        """Detect CUDA compute capability using nvidia-smi.
        
        Queries all GPUs and returns the minimum compute capability
        to ensure compatibility with all installed GPUs.
        """
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                capabilities = []
                for line in result.stdout.strip().split("\n"):
                    cap = line.strip()
                    if cap and "." in cap:
                        capabilities.append(cap.replace(".", ""))
                
                if capabilities:
                    self.platform_info.cuda_compute_capability = min(capabilities)
        except Exception:
            pass
    
    def _check_wsl2(self) -> bool:
        """Check if running in WSL2 environment.
        
        Returns:
            True if running in WSL2.
        """
        try:
            with open("/proc/version", "r") as f:
                version_info = f.read().lower()
                return "microsoft" in version_info or "wsl" in version_info
        except Exception:
            return False
    
    def _check_qsv(self) -> bool:
        """Check if Intel QSV is available.
        
        Returns:
            True if Intel QSV is available.
        """
        # QSV is not supported in WSL2
        if self.platform_info.is_wsl2:
            return False
        
        # QSV requires VAAPI
        if not self.platform_info.vaapi_available:
            return False
        
        # Check for Intel GPU via vainfo
        if shutil.which("vainfo"):
            try:
                result = subprocess.run(
                    ["vainfo"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                # Check for Intel driver (iHD or i965)
                if "Intel" in result.stdout or "iHD" in result.stdout or "i965" in result.stdout:
                    return True
            except Exception:
                pass
        
        # Check for Intel GPU via PCI IDs
        try:
            pci_devices = Path("/sys/bus/pci/devices")
            if pci_devices.exists():
                for device in pci_devices.iterdir():
                    vendor_file = device / "vendor"
                    if vendor_file.exists():
                        vendor_id = vendor_file.read_text().strip()
                        # Intel vendor ID is 0x8086
                        if vendor_id == "0x8086":
                            return True
        except Exception:
            pass
        
        return False
    
    def _find_macports_clang(self) -> Optional[ToolInfo]:
        """Find macports clang.
        
        Returns:
            ToolInfo for macports clang or None.
        """
        macports_bin = Path("/opt/local/bin")
        
        # Look for clang-mp-*
        for clang_path in macports_bin.glob("clang-mp-*"):
            version = clang_path.name.replace("clang-mp-", "")
            return ToolInfo(
                name=f"macports-clang-{version}",
                path=str(clang_path),
                version=version,
                available=True
            )
        
        return None
    
    def _check_vaapi(self) -> bool:
        """Check if VAAPI is available.
        
        Returns:
            True if VAAPI is available.
        """
        try:
            result = subprocess.run(
                ["pkg-config", "--exists", "libva"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def _check_amf(self) -> bool:
        """Check if AMF headers are available.
        
        Returns:
            True if AMF is available.
        """
        # Check common locations for AMF headers
        amf_paths = [
            Path("/usr/include/AMF"),
            Path("/usr/local/include/AMF"),
            Path("/opt/AMF/amf/public/include")
        ]
        
        return any(path.exists() for path in amf_paths)
    
    def _check_vulkan(self) -> bool:
        """Check if Vulkan SDK/headers are available.
        
        Returns:
            True if Vulkan is available.
        """
        # Check pkg-config
        try:
            result = subprocess.run(
                ["pkg-config", "--exists", "vulkan"],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass
        
        # Check header files
        vulkan_header_paths = [
            Path("/usr/include/vulkan/vulkan.h"),
            Path("/usr/local/include/vulkan/vulkan.h"),
        ]
        
        if any(path.exists() for path in vulkan_header_paths):
            return True
        
        # Check vulkaninfo command
        if shutil.which("vulkaninfo") is not None:
            return True
        
        return False
    
    def _check_opencl(self) -> bool:
        """Check if OpenCL development files and runtime are available.
        
        Returns:
            True if OpenCL headers, ICD loader, and at least one vendor ICD are available.
        """
        # Check for OpenCL headers
        opencl_header_paths = [
            Path("/usr/include/CL/cl.h"),
            Path("/usr/local/include/CL/cl.h"),
        ]
        
        has_headers = any(path.exists() for path in opencl_header_paths)
        if not has_headers:
            return False
        
        # Check for ICD loader library
        icd_loader_paths = [
            Path("/usr/lib/x86_64-linux-gnu/libOpenCL.so"),
            Path("/usr/lib/x86_64-linux-gnu/libOpenCL.so.1"),
            Path("/usr/lib/libOpenCL.so"),
        ]
        
        has_loader = any(path.exists() for path in icd_loader_paths)
        if not has_loader:
            return False
        
        # Check for at least one vendor ICD file
        icd_vendors_dir = Path("/etc/OpenCL/vendors")
        if icd_vendors_dir.exists() and any(icd_vendors_dir.glob("*.icd")):
            return True
        
        # Check WSL NVIDIA OpenCL (not available in WSL)
        wsl_lib = Path("/usr/lib/wsl/lib")
        if wsl_lib.exists():
            if any(wsl_lib.glob("libnvidia-opencl*")):
                return True
            # WSL without OpenCL implementation
            return False
        
        return False
    
    def _detect_tools(self) -> None:
        """Detect available tools."""
        tool_names = [
            "make", "g++", "clang++", "gcc", "clang",
            "pkg-config", "nasm", "yasm", "cmake",
            "python3", "meson", "ninja",
            "cargo", "rustc",
            "curl", "git"
        ]
        
        for tool_name in tool_names:
            self.tools[tool_name] = self._detect_tool(tool_name)
    
    def _detect_tool(self, tool_name: str) -> ToolInfo:
        """Detect a single tool.
        
        Args:
            tool_name: Name of the tool.
            
        Returns:
            ToolInfo instance.
        """
        tool_path = shutil.which(tool_name)
        
        if tool_path is None:
            return ToolInfo(name=tool_name, available=False)
        
        # Try to get version
        version = self._get_tool_version(tool_name, tool_path)
        
        return ToolInfo(
            name=tool_name,
            path=tool_path,
            version=version,
            available=True
        )
    
    def _get_tool_version(self, tool_name: str, tool_path: str) -> Optional[str]:
        """Get tool version.
        
        Args:
            tool_name: Name of the tool.
            tool_path: Path to the tool.
            
        Returns:
            Version string or None.
        """
        try:
            if tool_name in ("g++", "gcc", "clang++", "clang"):
                result = subprocess.run(
                    [tool_path, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                output = result.stdout.split("\n")[0]
                # Extract version number
                for part in output.split():
                    if part[0].isdigit():
                        return part.rstrip(")")
            
            elif tool_name in ("cmake", "nasm", "yasm", "python3", "meson", "ninja"):
                result = subprocess.run(
                    [tool_path, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                output = result.stdout.strip()
                # First line often contains version
                for part in output.split():
                    if part[0].isdigit():
                        return part
            
            elif tool_name in ("cargo", "rustc"):
                result = subprocess.run(
                    [tool_path, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                output = result.stdout.strip()
                # Format: "cargo 1.70.0" or "rustc 1.70.0"
                parts = output.split()
                if len(parts) >= 2:
                    return parts[1]
            
            elif tool_name == "pkg-config":
                result = subprocess.run(
                    [tool_path, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                return result.stdout.strip()
            
        except Exception:
            pass
        
        return None
    
    def get_num_jobs(self, config_num_jobs: str = "auto") -> int:
        """Get number of parallel jobs.
        
        Args:
            config_num_jobs: Configuration value ("auto" or number).
            
        Returns:
            Number of jobs.
        """
        if config_num_jobs != "auto":
            return int(config_num_jobs)
        
        return self.system_info.cpu_cores or 4
