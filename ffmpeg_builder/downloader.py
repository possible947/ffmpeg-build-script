"""Download management with progress tracking."""
import requests
from pathlib import Path
from typing import Optional
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
    
    def download(
        self,
        url: str,
        filename: Optional[str] = None,
        max_retries: int = 3,
    ) -> Path:
        """Download a file with progress bar.
        
        Args:
            url: Download URL.
            filename: Target filename. If None, extracted from URL.
            max_retries: Maximum number of retry attempts.
            
        Returns:
            Path to downloaded file.
            
        Raises:
            RuntimeError: If download fails after all retries.
        """
        if filename is None:
            filename = url.split("/")[-1].split("?")[0]
        
        target_path = self.packages_dir / filename
        
        if target_path.exists() and target_path.stat().st_size > 0:
            return target_path
        
        for attempt in range(max_retries):
            try:
                self._download_file(url, target_path)
                return target_path
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"Download failed (attempt {attempt + 1}/{max_retries}): {e}")
                    print("Retrying in 10 seconds...")
                    import time
                    time.sleep(10)
                else:
                    raise RuntimeError(
                        f"Failed to download {url} after {max_retries} attempts: {e}"
                    )
        
        raise RuntimeError(f"Failed to download {url}")
    
    def _download_file(self, url: str, target_path: Path) -> None:
        """Download a single file.
        
        Args:
            url: Download URL.
            target_path: Target file path.
        """
        response = requests.get(url, stream=True, timeout=300)
        response.raise_for_status()
        
        total_size = int(response.headers.get("content-length", 0))
        
        with open(target_path, "wb") as f:
            with tqdm(
                total=total_size,
                unit="B",
                unit_scale=True,
                desc=target_path.name,
                leave=False,
            ) as pbar:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))
        
        if target_path.stat().st_size == 0:
            target_path.unlink()
            raise RuntimeError(f"Downloaded file is empty: {target_path}")
