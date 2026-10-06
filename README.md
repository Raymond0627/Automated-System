# Lumeed QScan

Automatically extracts document dates from PDFs using OCR and renames files according to a standardized naming convention.

## Features

- **OCR Date Extraction**: Extracts dates from scanned PDFs using bundled Tesseract
- **Blank Page Detection**: Automatically detects and skips blank pages (back sides of documents)
- **Desktop GUI**: Modern PyQt6 interface with dark theme for reviewing flagged documents
- **Batch Processing**: Process entire folders of PDFs with automatic date extraction
- **Duplicate & Delete**: Right-click a document to duplicate (Ctrl+D) or delete (Ctrl+Z to undo)
- **Auto-Create Output**: If Output is left blank, it is created inside the input folder on Scan/Run

## Installation

### Option A — Installer (recommended for end users)

Run `Output\LumeedQScan_Setup_1.8.3.exe` and follow the wizard. It installs to
`C:\Program Files\LumeedQScan`, bundles Tesseract OCR, and adds Start Menu /
optional Desktop shortcuts. User data (config and sessions) is stored in
`%APPDATA%\LumeedQScan`.

### Option B — From source (developers)

1. Install Python 3.10+ and Tesseract OCR (`C:\Program Files\Tesseract-OCR`).
2. Navigate to the project directory:
```bash
cd "Automated System"
```
3. Install Python dependencies:
```bash
pip install -r requirements.txt
```
4. Run the app:
```bash
python desktop_app.py
```

## Building the Installer

Ensure PyInstaller and Inno Setup 6 are installed, then run:

```bat
build.bat
```

This rebuilds the PyInstaller package, bundles Tesseract, and compiles
`Output\LumeedQScan_Setup_1.8.3.exe` via `installer.iss`.

## Project Structure

```
Automated System/
├── ui/                   # PyQt6 interface (dashboard, review, settings)
├── blank_page_detector.py # Blank page detection module
├── date_extractor.py     # OCR date extraction module
├── pipeline.py           # Processing pipeline
├── desktop_app.py        # PyQt6 desktop GUI entry point
├── paths.py              # Resource/data path resolution
├── installer.iss         # Inno Setup installer script
├── LumeedQScan.spec      # PyInstaller build spec
├── build.bat             # One-click installer build script
├── requirements.txt      # Python dependencies
└── README.md             # This file
```

## Input Folder Structure

Organize your PDFs in this structure:
```
data/
├── 154/
│   ├── Pru Life UK Insurance Corp/
│   │   ├── 001.pdf
│   │   └── 002.pdf
│   └── Another Company/
│       └── 001.pdf
└── 031/
    └── Company Name/
        └── 001.pdf
```

## Usage

### Step 1: Run the Desktop Application

```bash
python desktop_app.py
```

### Step 2: Configure and Process

1. Open the **Dashboard** tab (opens by default)
2. Set your **Input** folder (contains division/company subfolders with PDFs)
3. Set your **Output** folder, or leave it blank to auto-create one inside the input folder
4. Click **Scan Folder** to verify, then **Run Pipeline** to process all PDFs

### Step 3: Review Flagged Documents

1. Open the **Review** tab
2. Select a document from the list (or use the **Up/Down** arrow keys to move through documents)
3. Enter the correct date (month/year) in the date editor
4. Click **OK** to confirm
5. Use **Next** to move to the next document
6. Right-click a document to **Duplicate** (Ctrl+D) or **Delete** (Ctrl+Z to undo)

### Step 4: Check Results

- Renamed PDFs are in the output folder
- Rename log is at `{output_folder}/rename_log.csv`

## Blank Page Detection

The system automatically detects blank pages (back sides of documents) and:

- **Skips blank pages** during OCR processing
- **Marks entire blank documents** as failed and flags them for review
- **Shows blank page indicators** in the PDF preview with "BLANK PAGE" overlay
- **Displays blank page count** on document cards

Blank pages are detected by analyzing:
- Pixel density in the inner content area (ignoring scanner artifacts)
- Connected component analysis for text/stamps
- Edge detection for form lines and logos

## Output Filename Format

```
{YYYYMM}{SEQ}_{DIVISION}_{Company_Name}.pdf
```

Example: `2026010001_031_Pru_Life_Uk_Insurance_Corp.pdf`

- `YYYYMM` - Document date (year/month)
- `SEQ` - Sequence number (0001, 0002, etc.)
- `DIVISION` - 3-digit division code
- `Company_Name` - Title_Case company name

## Troubleshooting

### Tesseract not found
Ensure Tesseract is installed and added to your system PATH.

### Low confidence scores
- Try adjusting the confidence threshold in Settings tab
- Check if PDFs are clear enough for OCR
- Manually review flagged documents

### Import errors
Ensure all dependencies are installed:
```bash
pip install -r requirements.txt
```

## Future Enhancements

The system is designed to support future VLM (Vision Language Model) integration:
- Swap `date_extractor.py` with Qwen2-VL via Ollama
- The `extract_document_date()` function signature remains the same
- No changes needed in pipeline or desktop app
