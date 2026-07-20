import sys
from pathlib import Path

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent


def configure_tesseract():
    """Set pytesseract.tesseract_cmd to bundled or system Tesseract."""
    import os
    if getattr(sys, 'frozen', False):
        bundled = BASE_DIR / "tesseract" / "tesseract.exe"
        if bundled.exists():
            os.environ["TESSDATA_PREFIX"] = str(BASE_DIR / "tesseract" / "tessdata")
            import pytesseract
            pytesseract.tesseract_cmd = str(bundled)
            return
    system_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if Path(system_path).exists():
        import pytesseract
        pytesseract.tesseract_cmd = system_path
