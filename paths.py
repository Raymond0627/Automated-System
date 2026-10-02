import os
import sys
from pathlib import Path


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


if _is_frozen():
    # Read-only assets bundled by PyInstaller (sits in the _internal folder on
    # onedir builds, or is extracted alongside the onefile exe).
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    # Writable user data. Persisted separately so installs under Program Files
    # (where the app itself is read-only) can still save config and sessions.
    DATA_DIR = Path(
        os.environ.get("APPDATA", str(Path.home()))
    ) / "LumeedQScan"
else:
    RESOURCE_DIR = Path(__file__).parent
    DATA_DIR = Path(__file__).parent

# Backwards-compatible alias used throughout the app for read-only resources.
BASE_DIR = RESOURCE_DIR


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def configure_tesseract():
    """Set pytesseract.tesseract_cmd to bundled or system Tesseract."""
    if _is_frozen():
        candidates = [
            RESOURCE_DIR / "tesseract" / "tesseract.exe",
            Path(sys.executable).parent / "tesseract" / "tesseract.exe",
        ]
        for bundled in candidates:
            if bundled.exists():
                tesseract_dir = str(bundled.parent)
                os.environ["TESSDATA_PREFIX"] = str(bundled.parent / "tessdata")
                os.environ["PATH"] = tesseract_dir + ";" + os.environ.get("PATH", "")
                import pytesseract
                pytesseract.tesseract_cmd = str(bundled)
                return
    system_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if Path(system_path).exists():
        import pytesseract
        pytesseract.tesseract_cmd = system_path
