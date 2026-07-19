"""Live build dashboard for terminal progress."""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, Dict, List, Optional, Set

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from ..components import Component
from ..state import ComponentStatus


@dataclass
class ComponentRow:
    """UI row for a build component."""
    index: int
    total: int
    name: str
    version: str
    status: ComponentStatus = ComponentStatus.PENDING
    detail: str = ""
    progress: float = 0.0
    first_seen: bool = False


_STATUS_LABELS = {
    ComponentStatus.PENDING: "pending",
    ComponentStatus.SYSTEM: "system",
    ComponentStatus.DOWNLOADING: "downloading",
    ComponentStatus.CONFIGURING: "config",
    ComponentStatus.BUILDING: "build",
    ComponentStatus.INSTALLING: "install",
    ComponentStatus.COMPLETED: "complete",
    ComponentStatus.FAILED: "fail",
    ComponentStatus.SKIPPED: "skip",
}

_STATUS_STYLES = {
    ComponentStatus.PENDING: "dim",
    ComponentStatus.SYSTEM: "cyan",
    ComponentStatus.DOWNLOADING: "cyan",
    ComponentStatus.CONFIGURING: "yellow",
    ComponentStatus.BUILDING: "magenta",
    ComponentStatus.INSTALLING: "blue",
    ComponentStatus.COMPLETED: "green",
    ComponentStatus.FAILED: "red",
    ComponentStatus.SKIPPED: "yellow dim",
}

_STEP_VALUES = {
    ComponentStatus.PENDING: 0,
    ComponentStatus.SYSTEM: 100,
    ComponentStatus.DOWNLOADING: 5,
    ComponentStatus.CONFIGURING: 35,
    ComponentStatus.BUILDING: 65,
    ComponentStatus.INSTALLING: 90,
    ComponentStatus.COMPLETED: 100,
    ComponentStatus.FAILED: 100,
    ComponentStatus.SKIPPED: 100,
}

_IN_PROGRESS = {
    ComponentStatus.DOWNLOADING,
    ComponentStatus.CONFIGURING,
    ComponentStatus.BUILDING,
    ComponentStatus.INSTALLING,
}


class BuildDashboard:
    """Rich renderable build dashboard."""

    def __init__(self, console: Console, ffmpeg_version: str, jobs: int, download_workers: int):
        self.console = console
        self.ffmpeg_version = ffmpeg_version
        self.jobs = jobs
        self.download_workers = download_workers
        self._rows_by_name: Dict[str, ComponentRow] = {}
        self.rows: List[ComponentRow] = []
        self.messages: Deque[str] = deque(maxlen=10)
        self.active_index = 0
        self.started_at = datetime.now()
        self._lock = threading.RLock()
        self._total = 0
        self._revealed: Set[str] = set()
        self._show_all_immediately = False

    def set_components(self, components: List[Component], reveal_all: bool = False) -> None:
        """Set planned components and create rows.

        Args:
            components: All buildable components in build order.
            reveal_all: When True, render all rows immediately. When False
                (default), rows only appear in the table after they have
                received at least one status update — so the table grows as
                downloads queue and builds start rather than starting fully
                populated.
        """
        with self._lock:
            self._show_all_immediately = reveal_all
            self._total = len(components)
            self._rows_by_name = {}
            self.rows = []
            self._revealed = set()
            for index, component in enumerate(components, 1):
                row = ComponentRow(index, self._total, component.name, component.version)
                self._rows_by_name[component.name] = row
                self.rows.append(row)
                if reveal_all:
                    self._revealed.add(component.name)
                    row.first_seen = True

    def reveal(self, name: str) -> None:
        """Force a row to be visible regardless of status updates."""
        with self._lock:
            if name in self._revealed:
                return
            self._revealed.add(name)
            row = self._rows_by_name.get(name)
            if row is not None:
                row.first_seen = True

    def _reveal_internal(self, name: str) -> None:
        with self._lock:
            if name in self._revealed:
                return
            self._revealed.add(name)
            row = self._rows_by_name.get(name)
            if row is not None:
                row.first_seen = True

    def update_status(
        self,
        name: str,
        status: ComponentStatus,
        version: Optional[str] = None,
        detail: str = "",
    ) -> None:
        """Update component status."""
        with self._lock:
            row = self._rows_by_name.get(name)
            if row is None:
                return
            if status == ComponentStatus.PENDING and row.status in (
                ComponentStatus.DOWNLOADING,
                ComponentStatus.CONFIGURING,
                ComponentStatus.BUILDING,
                ComponentStatus.INSTALLING,
            ):
                return
            row.status = status
            if version:
                row.version = version
            if status in (
                ComponentStatus.COMPLETED,
                ComponentStatus.FAILED,
                ComponentStatus.SKIPPED,
                ComponentStatus.SYSTEM,
            ):
                row.progress = 100.0
                row.detail = ""
            elif status == ComponentStatus.DOWNLOADING:
                row.progress = max(row.progress, _STEP_VALUES[ComponentStatus.DOWNLOADING])
            elif status == ComponentStatus.CONFIGURING:
                row.progress = max(row.progress, _STEP_VALUES[ComponentStatus.CONFIGURING])
            elif status == ComponentStatus.BUILDING:
                row.progress = max(row.progress, _STEP_VALUES[ComponentStatus.BUILDING])
            elif status == ComponentStatus.INSTALLING:
                row.progress = max(row.progress, _STEP_VALUES[ComponentStatus.INSTALLING])
            if detail:
                row.detail = detail
            try:
                self.active_index = self.rows.index(row)
            except ValueError:
                pass
            self._reveal_internal(name)

    def update_download_status(self, name: str, status: ComponentStatus) -> None:
        """Update a download status without overwriting later build phases."""
        with self._lock:
            row = self._rows_by_name.get(name)
            if row is None:
                return
            if status == ComponentStatus.PENDING and row.status in (
                ComponentStatus.DOWNLOADING,
                ComponentStatus.CONFIGURING,
                ComponentStatus.BUILDING,
                ComponentStatus.INSTALLING,
            ):
                return
            row.status = status
            if status != ComponentStatus.DOWNLOADING:
                row.progress = 0.0
            try:
                self.active_index = self.rows.index(row)
            except ValueError:
                pass
            self._reveal_internal(name)

    def update_download_progress(self, name: str, downloaded: int, total: int) -> None:
        """Update download progress for a component (bytes in/out of total)."""
        if total <= 0:
            return
        with self._lock:
            row = self._rows_by_name.get(name)
            if row is None:
                return
            row.status = ComponentStatus.DOWNLOADING
            pct = max(0.0, min(100.0, downloaded * 100.0 / total))
            row.progress = pct
            d_mb = downloaded / (1024 * 1024)
            t_mb = total / (1024 * 1024)
            if t_mb >= 1.0:
                row.detail = f"{d_mb:.1f}/{t_mb:.1f} MB ({pct:.0f}%)"
            else:
                row.detail = f"{downloaded}/{total} B ({pct:.0f}%)"
            self._reveal_internal(name)

    def log(self, message: str) -> None:
        """Append service message."""
        with self._lock:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.messages.append(f"[{timestamp}] {message}")

    def render(self) -> RenderableType:
        """Render current dashboard."""
        with self._lock:
            return Group(self._header(), self._table(), self._messages())

    def __rich__(self) -> RenderableType:
        return self.render()

    def _header(self) -> Panel:
        total = self._total
        completed = sum(1 for r in self.rows if r.status == ComponentStatus.COMPLETED)
        system = sum(1 for r in self.rows if r.status == ComponentStatus.SYSTEM)
        failed = sum(1 for r in self.rows if r.status == ComponentStatus.FAILED)
        skipped = sum(1 for r in self.rows if r.status == ComponentStatus.SKIPPED)
        downloading = sum(1 for r in self.rows if r.status == ComponentStatus.DOWNLOADING)
        in_progress = sum(1 for r in self.rows if r.status in _IN_PROGRESS)
        elapsed = datetime.now() - self.started_at
        elapsed_text = str(elapsed).split(".")[0]
        text = Text()
        text.append("FFmpeg Builder — Building\n", style="bold blue")
        text.append(
            f"FFmpeg {self.ffmpeg_version} · {completed}/{total} complete · "
            f"{system} system · {failed} failed · {skipped} skip · "
            f"{in_progress} active · {downloading} downloading\n"
        )
        text.append(
            f"Elapsed {elapsed_text} · Jobs {self.jobs} · Async DL workers {self.download_workers}"
        )
        return Panel(text, border_style="blue")

    def _table(self) -> Table:
        table = Table(show_header=True, expand=True)
        table.add_column("#", justify="right", no_wrap=True, width=7)
        table.add_column("Component", overflow="fold")
        table.add_column("Ver", overflow="fold", width=12)
        table.add_column("Status", no_wrap=True, width=12)
        table.add_column("Step", no_wrap=True, width=12)
        table.add_column("Detail", overflow="fold")

        visible_rows = self._visible_rows()
        for row in visible_rows:
            style = _STATUS_STYLES[row.status]
            table.add_row(
                f"{row.index}/{row.total}",
                row.name,
                row.version,
                _STATUS_LABELS[row.status],
                ProgressBar(total=100, completed=row.progress, width=10),
                row.detail,
                style=style,
            )
        return table

    def _messages(self) -> Panel:
        lines = list(self.messages)[-8:]
        content = "\n".join(lines) if lines else "No messages yet."
        return Panel(Text(content), title="Messages", border_style="dim")

    def _visible_rows(self) -> List[ComponentRow]:
        with self._lock:
            if not self.rows:
                return []
            if self._show_all_immediately:
                visible = list(self.rows)
            else:
                visible = [r for r in self.rows if r.first_seen]
            if not visible:
                return []
            height = max(3, self.console.size.height - 14)
            if len(visible) <= height:
                return visible

            try:
                anchor_row = self.rows[self.active_index]
            except IndexError:
                anchor_row = visible[-1]

            try:
                anchor_idx = visible.index(anchor_row)
            except ValueError:
                anchor_idx = len(visible) - 1

            window_set: List[ComponentRow] = []
            seen: Set[int] = set()

            def add(row: ComponentRow) -> bool:
                if id(row) in seen:
                    return False
                if len(window_set) >= height:
                    return False
                window_set.append(row)
                seen.add(id(row))
                return True

            for row in visible:
                if row.status in _IN_PROGRESS:
                    add(row)

            if len(window_set) >= height:
                return window_set[:height]

            capacity = height - len(window_set)
            half = capacity // 2
            start = max(0, anchor_idx - half)
            end = min(len(visible), start + capacity)
            start = max(0, end - capacity)
            for row in visible[start:end]:
                add(row)

            if len(window_set) >= height:
                return window_set[:height]

            capacity = height - len(window_set)
            for offset in range(1, len(visible)):
                for sign in (-1, 1):
                    idx = anchor_idx + sign * offset
                    if 0 <= idx < len(visible):
                        if add(visible[idx]):
                            if len(window_set) >= height:
                                return window_set[:height]
                if len(window_set) >= height:
                    break

            return window_set[:height]
