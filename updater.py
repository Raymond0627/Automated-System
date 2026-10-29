import json
import os
import re
import tempfile
import urllib.request
from typing import Optional, Tuple
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from version import __version__, GITHUB_REPO


def parse_version_tuple(ver_str: str) -> Tuple[int, ...]:
    """Convert version strings like 'v1.9.4' or '1.9.10' into comparable tuple of ints."""
    clean = re.sub(r'^[vV]', '', ver_str.strip())
    nums = []
    for part in clean.split('.'):
        digits = re.match(r'^\d+', part)
        if digits:
            nums.append(int(digits.group(0)))
        else:
            nums.append(0)
    return tuple(nums)


def is_newer_version(current: str, remote: str) -> bool:
    """Return True if remote version is strictly greater than current version."""
    return parse_version_tuple(remote) > parse_version_tuple(current)


class UpdateCheckWorker(QThread):
    """Background worker checking GitHub releases for a newer installer version."""
    update_available = pyqtSignal(str, str, str)  # (version_str, release_notes, download_url)
    no_update = pyqtSignal()
    check_failed = pyqtSignal(str)

    def __init__(self, repo: str = GITHUB_REPO, current_ver: str = __version__, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.current_ver = current_ver

    def run(self):
        url = f"https://api.github.com/repos/{self.repo}/releases/latest"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "LumeedQScan-Updater",
                "Accept": "application/vnd.github.v3+json",
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status != 200:
                    self.check_failed.emit(f"HTTP {response.status}")
                    return
                data = json.loads(response.read().decode("utf-8"))

            tag = data.get("tag_name", "")
            notes = data.get("body", "")
            assets = data.get("assets", [])

            # Find .exe asset
            download_url = ""
            for asset in assets:
                name = asset.get("name", "").lower()
                if name.endswith(".exe"):
                    download_url = asset.get("browser_download_url", "")
                    break

            if tag and is_newer_version(self.current_ver, tag) and download_url:
                self.update_available.emit(tag, notes, download_url)
            else:
                self.no_update.emit()

        except Exception as e:
            self.check_failed.emit(str(e))


class UpdateDownloadWorker(QThread):
    """Worker downloading the installer with real-time progress updates."""
    progress = pyqtSignal(int, float, float)  # (percent, downloaded_mb, total_mb)
    finished = pyqtSignal(str)  # installer_path
    failed = pyqtSignal(str)

    def __init__(self, download_url: str, version_tag: str, parent=None):
        super().__init__(parent)
        self.download_url = download_url
        self.version_tag = version_tag
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        temp_dir = Path(tempfile.gettempdir())
        clean_tag = re.sub(r'^[vV]', '', self.version_tag)
        dest_path = temp_dir / f"LumeedQScan_Setup_{clean_tag}.exe"

        req = urllib.request.Request(
            self.download_url,
            headers={"User-Agent": "LumeedQScan-Updater"}
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as response, open(dest_path, "wb") as out_file:
                total_bytes = int(response.headers.get("Content-Length", 0))
                total_mb = total_bytes / (1024 * 1024) if total_bytes > 0 else 0.0

                downloaded_bytes = 0
                block_size = 65536

                while not self._cancelled:
                    chunk = response.read(block_size)
                    if not chunk:
                        break
                    out_file.write(chunk)
                    downloaded_bytes += len(chunk)
                    downloaded_mb = downloaded_bytes / (1024 * 1024)
                    percent = int((downloaded_bytes / total_bytes) * 100) if total_bytes > 0 else 0
                    self.progress.emit(percent, downloaded_mb, total_mb)

            if self._cancelled:
                try:
                    dest_path.unlink()
                except Exception:
                    pass
                return

            self.finished.emit(str(dest_path))

        except Exception as e:
            self.failed.emit(str(e))
