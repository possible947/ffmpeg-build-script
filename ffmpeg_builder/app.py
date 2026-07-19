"""Main application class for FFmpeg Builder."""
from pathlib import Path
from typing import Optional
from rich.console import Console

from .config import ConfigManager, BuildConfig
from .state import StateManager, ComponentStatus
from .platform_detect import PlatformDetector
from .system_report import SystemReportGenerator
from .components import ComponentRegistry
from .builder import FFmpegBuilder, BuildError, SkipComponent
from .ui.screens import SystemReportScreen, ConfigScreen, BuildProgressScreen, FinalReportScreen
from .ui.error_handler import ErrorHandler


class FFmpegBuilderApp:
    """Main application class."""
    
    def __init__(self, workspace: Optional[Path] = None):
        """Initialize application.
        
        Args:
            workspace: Workspace directory. If None, uses ./workspace.
        """
        self.workspace = workspace or Path("workspace")
        self.packages = Path("packages")
        self.console = Console()
        
        self.config_manager = ConfigManager(Path("build_config.yaml"))
        self.state_manager = StateManager(self.workspace / "build_state.json")
        
        self.platform_detector = PlatformDetector()
        self.system_info, self.platform_info, self.tools = self.platform_detector.detect_all()
        
        report_gen = SystemReportGenerator(self.system_info, self.platform_info, self.tools)
        self.system_report = report_gen.generate()
        
        self.registry = ComponentRegistry()
        
        self.system_screen = SystemReportScreen(self.console)
        self.config_screen = ConfigScreen(self.console)
        self.progress_screen = BuildProgressScreen(self.console)
        self.final_screen = FinalReportScreen(self.console)
        self.error_handler = ErrorHandler(self.console)
    
    def run(self) -> int:
        """Run the application.
        
        Returns:
            Exit code.
        """
        try:
            while True:
                config = self.config_manager.get()
                state = self.state_manager.load()
                
                action = self.system_screen.show(self.system_report, config, state)
                
                if action == "build":
                    self._run_build(config, resume=False)
                elif action == "resume":
                    if state:
                        self._run_build(config, resume=True)
                    else:
                        self.console.print("[red]No previous build to resume.[/red]")
                        continue
                elif action == "config":
                    config = self.config_screen.show(config)
                    self.config_manager.save(config)
                elif action == "cleanup":
                    self._cleanup()
                elif action == "exit":
                    return 0
                
                self.console.print()
                self.console.input("Press Enter to continue...")
        
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Interrupted by user.[/yellow]")
            return 130
        except Exception as e:
            self.console.print(f"\n[red]Fatal error: {e}[/red]")
            return 1
    
    def _run_build(self, config: BuildConfig, resume: bool = False) -> None:
        """Run the build process.
        
        Args:
            config: Build configuration.
            resume: Whether to resume from previous state.
        """
        platform = "darwin" if self.platform_info.is_macos else "linux"
        
        components = self.registry.get_buildable(
            config.gpl_enabled,
            platform,
            self.tools,
            config.disable_lv2,
            config.enable_libvmaf,
            self.platform_info,
        )
        
        if resume:
            state = self.state_manager.get()
            resume_point = self.state_manager.get_resume_point()
            if resume_point:
                idx = next(i for i, c in enumerate(components) if c.name == resume_point)
                components = components[idx:]
                self.console.print(f"[green]Resuming from {resume_point}[/green]")
        
        builder = FFmpegBuilder(
            config,
            self.workspace,
            self.packages,
            self.state_manager,
            self.platform_detector,
        )
        
        state = self.state_manager.get()
        state.config = config.to_dict()
        state.total_steps = len(components)
        self.state_manager.save()
        
        success = True
        error_message = None
        
        idx = 1
        while idx <= len(components):
            component = components[idx - 1]
            self.progress_screen.show(
                component.name,
                component.version,
                "Starting",
                idx,
                len(components),
            )
            
            try:
                self.progress_screen.show(
                    component.name,
                    component.version,
                    "Downloading",
                    idx,
                    len(components),
                )
                
                self.progress_screen.show(
                    component.name,
                    component.version,
                    "Configuring",
                    idx,
                    len(components),
                )
                
                self.progress_screen.show(
                    component.name,
                    component.version,
                    "Building",
                    idx,
                    len(components),
                )
                
                self.progress_screen.show(
                    component.name,
                    component.version,
                    "Installing",
                    idx,
                    len(components),
                )
                
                builder.build_component(component)
                
                self.state_manager.mark_component_status(
                    component.name,
                    ComponentStatus.COMPLETED,
                    component.version,
                )
                
                self.console.print(f"[green]✓ {component.name} {component.version}[/green]")
                idx += 1
            
            except SkipComponent as e:
                self.state_manager.mark_component_status(
                    component.name,
                    ComponentStatus.SKIPPED,
                    component.version,
                    str(e),
                )
                self.console.print(f"[yellow]⊘ {component.name} skipped: {e.message}[/yellow]")
                idx += 1
                continue
            
            except BuildError as e:
                self.state_manager.mark_component_status(
                    component.name,
                    ComponentStatus.FAILED,
                    component.version,
                    str(e),
                    str(e.log_file) if e.log_file else None,
                )
                
                action = self.error_handler.handle_error(
                    component.name,
                    str(e),
                    e.log_file,
                )
                
                if action == "retry":
                    self.state_manager.mark_component_status(
                        component.name,
                        ComponentStatus.PENDING,
                        component.version,
                    )
                    continue
                elif action == "skip":
                    self.state_manager.mark_component_status(
                        component.name,
                        ComponentStatus.SKIPPED,
                        component.version,
                    )
                    self.console.print(f"[yellow]⊘ {component.name} skipped[/yellow]")
                    idx += 1
                    continue
                elif action == "abort":
                    success = False
                    error_message = str(e)
                    break
        
        self.final_screen.show(
            self.state_manager.get(),
            self.workspace,
            success,
            error_message,
        )
    
    def _cleanup(self) -> None:
        """Cleanup workspace and packages."""
        import shutil
        
        self.state_manager.reset()
        self.console.print("[green]Reset build state[/green]")
        
        if self.workspace.exists():
            shutil.rmtree(self.workspace)
            self.console.print(f"[green]Removed {self.workspace}[/green]")
        
        if self.packages.exists():
            shutil.rmtree(self.packages)
            self.console.print(f"[green]Removed {self.packages}[/green]")
