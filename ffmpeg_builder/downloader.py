"""Download management with progress tracking."""
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import requests
from tqdm import tqdm


class Downloader:
    """Downloads files with progress tracking."""

    def __init__(self, packages_dir: Path):
        """Initialize downloader.

        Args:
            packages_dir: Directory for downloaded files.
        """
        self.packages_dir = packages_dir
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        self._locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def download(
        self,
        url: str,
        filename: Optional[str] = None,
        max_retries: int = 3,
        show_progress: bool = True,
    ) -> Path:
        """Download a file with progress bar.

        Args:
            url: Download URL.
            filename: Target filename. If None, extracted from URL.
            max_retries: Maximum number of retry attempts.
            show_progress: Whether to render a progress bar.

        Returns:
            Path to downloaded file.

        Raises:
            RuntimeError: If download fails after all retries.
        """
        if filename is None:
            filename = url.split("/")[-1].split("?")[0]

        target_path = self.packages_dir / filename
        lock = self._get_lock(filename)

        with lock:
            if target_path.exists() and target_path.stat().st_size > 0:
                return target_path

            for attempt in range(max_retries):
                try:
                    self._download_file(url, target_path, show_progress)
                    return target_path
                except Exception as e:
                    part_path = target_path.with_name(f"{target_path.name}.part")
                    if part_path.exists():
                        part_path.unlink()
                    if attempt < max_retries - 1:
                        if show_progress:
                            print(f"Download failed (attempt {attempt + 1}/{max_retries}): {e}")
                            print("Retrying in 10 seconds...")
                        time.sleep(10)
                    else:
                        raise RuntimeError(
                            f"Failed to download {url} after {max_retries} attempts: {e}"
                        )

            raise RuntimeError(f"Failed to download {url}")

    def _get_lock(self, filename: str) -> threading.Lock:
        with self._locks_guard:
            if filename not in self._locks:
                self._locks[filename] = threading.Lock()
            return self._locks[filename]

    def _download_file(self, url: str, target_path: Path, show_progress: bool) -> None:
        """Download a single file.

        Args:
            url: Download URL.
            target_path: Target file path.
            show_progress: Whether to render a progress bar.
        """
        part_path = target_path.with_name(f"{target_path.name}.part")
        response = requests.get(url, stream=True, timeout=300)
        response.raise_for_status()

        total_size = int(response.headers.get("content-length", 0))
        progress = tqdm(
            total=total_size,
            unit="B",
            unit_scale=True,
            desc=target_path.name,
            leave=False,
            disable=not show_progress,
        )

        with open(part_path, "wb") as f:
            with progress as pbar:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))

        if part_path.stat().st_size == 0:
            part_path.unlink()
            raise RuntimeError(f"Downloaded file is empty: {target_path}")

        part_path.replace(target_path)


class AsyncDownloadManager:
    """Background source archive download manager."""

    def __init__(self, downloader: Downloader, max_workers: int):
        """Initialize async download manager.

        Args:
            downloader: Shared downloader instance.
            max_workers: Maximum concurrent downloads.
        """
        self.downloader = downloader
        self.max_workers = max(1, int(max_workers))
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        self.futures: Dict[str, Future] = {}
        self._lock = threading.Lock()

    def prefetch(self, components: Iterable[Any]) -> None:
        """Queue component archive downloads.

        Args:
            components: Components to prefetch.
        """
        for component in components:
            self.schedule(component)

    def schedule(self, component: Any) -> None:
        """Queue one component archive download.

        Args:
            component: Component to prefetch.
        """
        filename = component.get_archive_filename()
        target_path = self.downloader.packages_dir / filename
        if target_path.exists() and target_path.stat().st_size > 0:
            return

        with self._lock:
            future = self.futures.get(filename)
            if future is not None and not future.done():
                return
            self.futures[filename] = self.executor.submit(
                self.downloader.download,
                component.get_url(),
                filename,
                3,
                False,
            )

    def get(self, component: Any) -> Path:
        """Return component archive, waiting for background download if needed.

        Args:
            component: Component to get archive for.

        Returns:
            Path to downloaded archive.
        """
        filename = component.get_archive_filename()
        with self._lock:
            future = self.futures.get(filename)

        if future is None:
            return self.downloader.download(component.get_url(), filename)

        try:
            return future.result()
        except Exception:
            with self._lock:
                if self.futures.get(filename) is future:
                    del self.futures[filename]
            raise

    def retry(self, component: Any) -> None:
        """Queue a fresh download after a failed attempt.

        Args:
            component: Component to retry.
        """
        filename = component.get_archive_filename()
        with self._lock:
            future = self.futures.pop(filename, None)
        if future is not None and not future.done():
            future.cancel()
        self.schedule(component)

    def shutdown(self, wait: bool = True) -> None:
        """Stop background download workers.

        Args:
            wait: Whether to wait for running downloads.
        """
        try:
            self.executor.shutdown(wait=wait, cancel_futures=not wait)
        except TypeError:
            self.executor.shutdown(wait=wait)
