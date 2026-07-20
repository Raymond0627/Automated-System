@echo off
echo ============================================
echo  Lumeed QScan - Build Script
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH
    pause
    exit /b 1
)

pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

echo.
echo Building Lumeed QScan...
echo.

pyinstaller ^
    --name "LumeedQScan" ^
    --onedir ^
    --noconfirm ^
    --clean ^
    --add-data "Lumeed Logo.png;." ^
    --add-data "config.json;." ^
    --add-data "blank_page_detector.py;." ^
    --add-data "date_extractor.py;." ^
    --add-data "auto_qc.py;." ^
    --add-data "pipeline.py;." ^
    --add-data "paths.py;." ^
    --add-data "ui;ui" ^
    --hidden-import pytesseract ^
    --hidden-import cv2 ^
    --hidden-import numpy ^
    --hidden-import fitz ^
    --hidden-import PIL ^
    --hidden-import dateparser ^
    --hidden-import dateutil ^
    --hidden-import PyQt6 ^
    --collect-all PyQt6 ^
    desktop_app.py

if errorlevel 1 (
    echo.
    echo BUILD FAILED
    pause
    exit /b 1
)

echo.
echo Bundling Tesseract OCR...
set TESSERACT_SRC=C:\Program Files\Tesseract-OCR
set TESSERACT_DST=dist\LumeedQScan\tesseract

if exist "%TESSERACT_SRC%" (
    xcopy "%TESSERACT_SRC%" "%TESSERACT_DST%" /E /I /Q /Y >nul
    echo Tesseract bundled to %TESSERACT_DST%
) else (
    echo WARNING: Tesseract not found at %TESSERACT_SRC%
    echo Users will need to install Tesseract or configure path in Settings
)

echo @echo off > dist\LumeedQScan\Run.bat
echo cd /d "%%~dp0" >> dist\LumeedQScan\Run.bat
echo LumeedQScan.exe >> dist\LumeedQScan\Run.bat

echo Lumeed QScan > dist\LumeedQScan\README.txt
echo ============== >> dist\LumeedQScan\README.txt
echo. >> dist\LumeedQScan\README.txt
echo 1. Run "Run.bat" to start the application >> dist\LumeedQScan\README.txt
echo 2. On first run, go to Settings and configure paths >> dist\LumeedQScan\README.txt
echo 3. Tesseract OCR is bundled in the tesseract/ folder >> dist\LumeedQScan\README.txt

echo.
echo ============================================
echo  BUILD COMPLETE: dist\LumeedQScan\
echo ============================================
echo.
pause
