"""Interactive error handling."""
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt


class ErrorHandler:
    """Handles build errors interactively."""
    
    def __init__(self, console: Console):
        """Initialize error handler.
        
        Args:
            console: Rich console instance.
        """
        self.console = console
    
    def handle_error(
        self,
        component_name: str,
        error_message: str,
        log_file: Optional[Path] = None,
    ) -> str:
        """Handle build error interactively.
        
        Args:
            component_name: Name of failed component.
            error_message: Error message.
            log_file: Path to log file.
            
        Returns:
            User choice: "retry", "skip", or "abort".
        """
        self.console.print()
        self.console.print(Panel(
            f"[bold red]Build failed for {component_name}[/bold red]\n\n"
            f"{error_message}",
            title="Error",
            border_style="red",
        ))
        
        if log_file and log_file.exists():
            self.console.print(f"\n[dim]Log file: {log_file}[/dim]")
            
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                
                last_lines = lines[-20:] if len(lines) > 20 else lines
                self.console.print("\n[bold]Last 20 lines of log:[/bold]")
                self.console.print("".join(last_lines), style="dim")
            except Exception:
                pass
        
        while True:
            self.console.print("\n[bold]What would you like to do?[/bold]")
            self.console.print("  [1] Retry build")
            self.console.print("  [2] Skip this component")
            self.console.print("  [3] Abort build")
            if log_file:
                self.console.print("  [4] Show full log")
            
            choice = Prompt.ask("Choice", choices=["1", "2", "3", "4"] if log_file else ["1", "2", "3"])
            
            if choice == "1":
                return "retry"
            elif choice == "2":
                return "skip"
            elif choice == "3":
                return "abort"
            elif choice == "4" and log_file:
                self._show_full_log(log_file)
    
    def _show_full_log(self, log_file: Path) -> None:
        """Show full log file.
        
        Args:
            log_file: Path to log file.
        """
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                content = f.read()
            
            self.console.print("\n[bold]Full log:[/bold]")
            self.console.print(Panel(content, border_style="dim"))
            
            Prompt.ask("\nPress Enter to continue")
        except Exception as e:
            self.console.print(f"[red]Failed to read log file: {e}[/red]")
