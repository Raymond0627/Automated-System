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
    --exclude-module torch ^
    --exclude-module torchvision ^
    --exclude-module torchaudio ^
    --exclude-module sklearn ^
    --exclude-module scikit-learn ^
    --exclude-module transformers ^
    --exclude-module onnxruntime ^
    --exclude-module tensorflow ^
    --exclude-module paddle ^
    --exclude-module paddleocr ^
    --exclude-module paddlepaddle ^
    --exclude-module sympy ^
    --exclude-module scipy ^
    --exclude-module pandas ^
    --exclude-module matplotlib ^
    --exclude-module networkx ^
    --exclude-module lxml ^
    --exclude-module openpyxl ^
    desktop_app.py

if errorlevel 1 (
    echo.
    echo BUILD FAILED
    pause
    exit /b 1
)

echo.
echo Bundling Tesseract OCR (English only)...
set TESSERACT_SRC=C:\Program Files\Tesseract-OCR
set TESSERACT_DST=dist\LumeedQScan\tesseract

if exist "%TESSERACT_SRC%" (
    if not exist "%TESSERACT_DST%" mkdir "%TESSERACT_DST%"
    xcopy "%TESSERACT_SRC%\*" "%TESSERACT_DST%" /E /Q /Y >nul
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
echo QUICK START: >> dist\LumeedQScan\README.txt
echo 1. Double-click Run.bat to start the application >> dist\LumeedQScan\README.txt
echo 2. On first run, set your file paths on the Dashboard tab: >> dist\LumeedQScan\README.txt
echo    - Input Root:   folder containing division folders with PDFs >> dist\LumeedQScan\README.txt
echo    - Flagged Root: folder where flagged/needs-review PDFs are saved >> dist\LumeedQScan\README.txt
echo    - Output Root:  folder where passed/renamed PDFs are saved (optional) >> dist\LumeedQScan\README.txt
echo 3. Click Scan Folder to verify your input folder >> dist\LumeedQScan\README.txt
echo 4. Click Run Pipeline to process documents >> dist\LumeedQScan\README.txt
echo. >> dist\LumeedQScan\README.txt
echo Tesseract OCR is bundled in the tesseract/ folder. >> dist\LumeedQScan\README.txt
echo Settings can be adjusted on the Settings tab. >> dist\LumeedQScan\README.txt

echo.
echo ============================================
echo  BUILD COMPLETE: dist\LumeedQScan\
echo ============================================
echo.
echo Upload the dist\LumeedQScan\ folder to G Drive for distribution.
echo.
pause
