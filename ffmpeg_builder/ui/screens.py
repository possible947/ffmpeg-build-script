"""UI screens using rich library."""
from pathlib import Path
from typing import Optional, Dict, List
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.prompt import Prompt, Confirm
from rich.layout import Layout
from rich.live import Live

from ..system_report import SystemReport
from ..config import BuildConfig
from ..state import BuildState, ComponentStatus


class UIScreen:
    """Base UI screen."""
    
    def __init__(self, console: Console):
        """Initialize screen.
        
        Args:
            console: Rich console instance.
        """
        self.console = console


class SystemReportScreen(UIScreen):
    """System report screen."""
    
    def show(
        self,
        report: SystemReport,
        config: BuildConfig,
        state: Optional[BuildState] = None,
    ) -> str:
        """Show system report screen.
        
        Args:
            report: System report.
            config: Build configuration.
            state: Previous build state.
            
        Returns:
            User action: "build", "resume", "config", "cleanup", or "exit".
        """
        self.console.clear()
        self.console.print("[bold blue]FFmpeg Builder - System Report[/bold blue]")
        self.console.print()
        
        # Hardware info
        hw_table = Table(title="Hardware", show_header=False)
        hw_table.add_column("Property", style="cyan")
        hw_table.add_column("Value")
        hw_table.add_row("CPU", report.system_info.cpu_model)
        hw_table.add_row("Cores", str(report.system_info.cpu_cores))
        hw_table.add_row("RAM", f"{report.system_info.ram_gb:.1f} GB")
        if report.system_info.gpu_info:
            hw_table.add_row("GPU", ", ".join(report.system_info.gpu_info))
        self.console.print(hw_table)
        self.console.print()
        
        # Software info
        sw_table = Table(title="Software", show_header=False)
        sw_table.add_column("Property", style="cyan")
        sw_table.add_column("Value")
        os_display = report.system_info.os_name
        if report.system_info.os_version:
            os_display = f"{report.system_info.os_name} {report.system_info.os_version}"
        if report.platform_info.is_wsl2:
            os_display += " (WSL2)"
        sw_table.add_row("OS", os_display)
        sw_table.add_row("Architecture", report.system_info.architecture)
        
        compiler = report.get_compiler_info()
        sw_table.add_row("Compiler", f"{compiler['compiler']} {compiler['version']}")
        self.console.print(sw_table)
        self.console.print()
        
        # Tools
        tools_table = Table(title="Available Tools", show_header=True)
        tools_table.add_column("Tool", style="cyan")
        tools_table.add_column("Version")
        tools_table.add_column("Status")
        
        for name, tool in report.tools.items():
            if tool.available:
                status = "[green]Available[/green]"
                version = tool.version or "N/A"
            else:
                status = "[red]Missing[/red]"
                version = "-"
            tools_table.add_row(name, version, status)
        
        self.console.print(tools_table)
        self.console.print()
        
        # Hardware acceleration
        hwaccel = report.get_hardware_acceleration_status()
        if hwaccel:
            hwaccel_table = Table(title="Hardware Acceleration", show_header=True)
            hwaccel_table.add_column("Type", style="cyan")
            hwaccel_table.add_column("Status")
            
            for name, available in hwaccel.items():
                status = "[green]Available[/green]" if available else "[red]Not Available[/red]"
                hwaccel_table.add_row(name, status)
            
            self.console.print(hwaccel_table)
            self.console.print()
        
        # Build configuration
        config_table = Table(title="Build Configuration", show_header=False)
        config_table.add_column("Property", style="cyan")
        config_table.add_column("Value")
        config_table.add_row("FFmpeg Version", config.ffmpeg_version)
        config_table.add_row("GPL Enabled", "Yes" if config.gpl_enabled else "No")
        config_table.add_row("Native Build", "Yes" if config.native_build else "No")
        config_table.add_row("Full Static", "Yes" if config.full_static else "No")
        config_table.add_row("Parallel Jobs", str(config.num_jobs))
        self.console.print(config_table)
        self.console.print()
        
        # Previous build state
        if state:
            completed = sum(1 for c in state.components.values() if c.status == ComponentStatus.COMPLETED)
            total = len(state.components)
            
            state_table = Table(title="Previous Build", show_header=False)
            state_table.add_column("Property", style="cyan")
            state_table.add_column("Value")
            state_table.add_row("Build ID", state.build_id)
            state_table.add_row("Started", state.started_at)
            state_table.add_row("Progress", f"{completed}/{total} components")
            self.console.print(state_table)
            self.console.print()
        
        # Menu
        self.console.print("[bold]Actions:[/bold]")
        self.console.print("  [1] Start new build")
        if state:
            self.console.print("  [2] Resume previous build")
        self.console.print("  [3] Edit configuration")
        self.console.print("  [4] Cleanup workspace")
        self.console.print("  [5] Exit")
        
        choices = ["1", "3", "4", "5"]
        if state:
            choices = ["1", "2", "3", "4", "5"]
        
        choice = Prompt.ask("Choice", choices=choices)
        
        if choice == "1":
            return "build"
        elif choice == "2":
            return "resume"
        elif choice == "3":
            return "config"
        elif choice == "4":
            return "cleanup"
        else:
            return "exit"


class ConfigScreen(UIScreen):
    """Configuration edit screen."""
    
    def show(self, config: BuildConfig) -> BuildConfig:
        """Show configuration edit screen.
        
        Args:
            config: Current configuration.
            
        Returns:
            Updated configuration.
        """
        self.console.clear()
        self.console.print("[bold blue]Edit Build Configuration[/bold blue]")
        self.console.print()
        
        # GPL
        if Confirm.ask("Enable GPL and non-free codecs?", default=config.gpl_enabled):
            config.gpl_enabled = True
        else:
            config.gpl_enabled = False
        
        # Native build
        if Confirm.ask("Enable native CPU optimizations?", default=config.native_build):
            config.native_build = True
        else:
            config.native_build = False
        
        # Full static
        if Confirm.ask("Build full static binary (Linux only)?", default=config.full_static):
            config.full_static = True
        else:
            config.full_static = False
        
        # libvmaf
        if Confirm.ask("Enable libvmaf?", default=config.enable_libvmaf):
            config.enable_libvmaf = True
        else:
            config.enable_libvmaf = False
        
        # LV2
        if Confirm.ask("Disable LV2 libraries?", default=config.disable_lv2):
            config.disable_lv2 = True
        else:
            config.disable_lv2 = False
        
        # Parallel jobs
        jobs = Prompt.ask("Number of parallel jobs", default=str(config.num_jobs))
        config.num_jobs = jobs
        
        self.console.print()
        self.console.print("[green]Configuration updated.[/green]")
        Prompt.ask("Press Enter to continue")
        
        return config


class BuildProgressScreen(UIScreen):
    """Build progress screen."""
    
    def show(
        self,
        component_name: str,
        component_version: str,
        step: str,
        current: int,
        total: int,
    ) -> None:
        """Show build progress.
        
        Args:
            component_name: Current component name.
            component_version: Component version.
            step: Current step.
            current: Current component index.
            total: Total components.
        """
        self.console.clear()
        self.console.print("[bold blue]Building FFmpeg[/bold blue]")
        self.console.print()
        
        progress_table = Table(show_header=False)
        progress_table.add_column("Property", style="cyan")
        progress_table.add_column("Value")
        progress_table.add_row("Progress", f"{current}/{total}")
        progress_table.add_row("Component", f"{component_name} {component_version}")
        progress_table.add_row("Step", step)
        
        self.console.print(progress_table)


class FinalReportScreen(UIScreen):
    """Final build report screen."""
    
    def show(
        self,
        state: BuildState,
        workspace: Path,
        success: bool,
        error_message: Optional[str] = None,
    ) -> None:
        """Show final build report.
        
        Args:
            state: Final build state.
            workspace: Workspace directory.
            success: Whether build succeeded.
            error_message: Error message if failed.
        """
        self.console.clear()
        
        if success:
            self.console.print("[bold green]Build Completed Successfully![/bold green]")
        else:
            self.console.print("[bold red]Build Failed[/bold red]")
        
        self.console.print()
        
        # Summary
        completed = [name for name, c in state.components.items() if c.status == ComponentStatus.COMPLETED]
        failed = [name for name, c in state.components.items() if c.status == ComponentStatus.FAILED]
        skipped = [name for name, c in state.components.items() if c.status == ComponentStatus.SKIPPED]
        
        summary_table = Table(title="Build Summary", show_header=True)
        summary_table.add_column("Status", style="cyan")
        summary_table.add_column("Count")
        summary_table.add_row("[green]Completed[/green]", str(len(completed)))
        summary_table.add_row("[red]Failed[/red]", str(len(failed)))
        summary_table.add_row("[yellow]Skipped[/yellow]", str(len(skipped)))
        self.console.print(summary_table)
        self.console.print()
        
        if success:
            # Binaries
            bin_dir = workspace / "bin"
            binaries = ["ffmpeg", "ffprobe", "ffplay"]
            
            bin_table = Table(title="Built Binaries", show_header=True)
            bin_table.add_column("Binary", style="cyan")
            bin_table.add_column("Path")
            
            for binary in binaries:
                bin_path = bin_dir / binary
                if bin_path.exists():
                    bin_table.add_row(binary, str(bin_path))
                else:
                    bin_table.add_row(binary, "[dim]Not built[/dim]")
            
            self.console.print(bin_table)
            self.console.print()
            
            # Install prompt
            if Confirm.ask("Install binaries to system?", default=False):
                self._install_binaries(bin_dir)
        
        else:
            # Error details
            if error_message:
                self.console.print(Panel(
                    error_message,
                    title="Error Details",
                    border_style="red",
                ))
                self.console.print()
            
            # Failed components
            if failed:
                failed_table = Table(title="Failed Components", show_header=True)
                failed_table.add_column("Component", style="cyan")
                failed_table.add_column("Error")
                
                for name in failed:
                    comp_state = state.components[name]
                    error = comp_state.error_message or "Unknown error"
                    failed_table.add_row(name, error)
                
                self.console.print(failed_table)
                self.console.print()
    
    def _install_binaries(self, bin_dir: Path) -> None:
        """Install binaries to system.
        
        Args:
            bin_dir: Directory containing binaries.
        """
        import platform
        import shutil
        
        if platform.system() == "Darwin":
            install_dir = Path("/usr/local/bin")
        else:
            install_dir = Path.home() / ".local" / "bin"
        
        install_dir.mkdir(parents=True, exist_ok=True)
        
        binaries = ["ffmpeg", "ffprobe", "ffplay"]
        
        for binary in binaries:
            src = bin_dir / binary
            dst = install_dir / binary
            
            if src.exists():
                try:
                    shutil.copy2(src, dst)
                    self.console.print(f"[green]Installed {binary} to {install_dir}[/green]")
                except Exception as e:
                    self.console.print(f"[red]Failed to install {binary}: {e}[/red]")
        
        self.console.print()
        self.console.print("[green]Installation complete.[/green]")
