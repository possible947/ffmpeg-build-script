"""Configuration management for FFmpeg builder."""
import yaml
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass, field, asdict


@dataclass
class MacOSConfig:
    """macOS-specific configuration."""
    clang: str = "macports-clang-17"
    openmp: bool = True


@dataclass
class LinuxConfig:
    """Linux-specific configuration."""
    c_standard: str = "c11"
    cxx_standard: str = "c++17"


@dataclass
class BuildConfig:
    """Build configuration."""
    ffmpeg_version: str = "8.1"
    gpl_enabled: bool = False
    native_build: bool = False
    full_static: bool = False
    enable_libvmaf: bool = True
    disable_lv2: bool = False
    num_jobs: str = "auto"
    macos: MacOSConfig = field(default_factory=MacOSConfig)
    linux: LinuxConfig = field(default_factory=LinuxConfig)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BuildConfig":
        """Create from dictionary."""
        macos_data = data.get("macos", {})
        linux_data = data.get("linux", {})
        
        config = cls(**data)
        config.macos = MacOSConfig(**macos_data)
        config.linux = LinuxConfig(**linux_data)
        
        return config


class ConfigManager:
    """Manages build configuration."""
    
    def __init__(self, config_path: Optional[Path] = None):
        """Initialize config manager.
        
        Args:
            config_path: Path to configuration file. If None, uses default.
        """
        self.config_path = config_path or Path("build_config.yaml")
        self.config: Optional[BuildConfig] = None
    
    def load(self) -> BuildConfig:
        """Load configuration from file.
        
        Returns:
            BuildConfig instance.
        """
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            self.config = BuildConfig.from_dict(data)
        else:
            self.config = BuildConfig()
        
        return self.config
    
    def save(self, config: Optional[BuildConfig] = None) -> None:
        """Save configuration to file.
        
        Args:
            config: Configuration to save. If None, saves current config.
        """
        if config is not None:
            self.config = config
        
        if self.config is None:
            raise ValueError("No configuration to save")
        
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(self.config.to_dict(), f, default_flow_style=False, sort_keys=False)
    
    def get(self) -> BuildConfig:
        """Get current configuration.
        
        Returns:
            Current BuildConfig instance.
        """
        if self.config is None:
            return self.load()
        return self.config
