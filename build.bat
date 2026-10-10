@echo off
echo ============================================
echo  Lumeed QScan - Build Installer Script
echo  Produces Output\LumeedQScan_Setup_1.8.4.exe
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
echo [1/3] Building Lumeed QScan package with PyInstaller...
echo.

if exist dist\LumeedQScan rmdir /s /q dist\LumeedQScan

pyinstaller --noconfirm --clean LumeedQScan.spec
if errorlevel 1 (
    echo.
    echo BUILD FAILED
    pause
    exit /b 1
)

echo.
echo [2/3] Bundling Tesseract OCR...
set TESSERACT_SRC=C:\Program Files\Tesseract-OCR
set TESSERACT_DST=dist\LumeedQScan\tesseract

if exist "%TESSERACT_SRC%" (
    if not exist "%TESSERACT_DST%" mkdir "%TESSERACT_DST%"
    xcopy "%TESSERACT_SRC%\*" "%TESSERACT_DST%" /E /Q /Y >nul
    echo     Tesseract bundled to %TESSERACT_DST%
) else (
    echo WARNING: Tesseract not found at %TESSERACT_SRC%
    echo Users will need to install Tesseract or configure path in Settings
)

echo.
echo [3/3] Compiling installer with Inno Setup...
set ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" (
    set ISCC=C:\Program Files\Inno Setup 6\ISCC.exe
)
if not exist "%ISCC%" (
    echo ERROR: Inno Setup 6 not found. Install it from https://jrsoftware.org/isinfo.php
    pause
    exit /b 1
)

"%ISCC%" installer.iss
if errorlevel 1 (
    echo.
    echo INSTALLER BUILD FAILED
    pause
    exit /b 1
)

echo.
echo ============================================
echo  BUILD COMPLETE: Output\LumeedQScan_Setup_1.8.4.exe
echo ============================================
echo.
pause
