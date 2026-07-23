# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('Lumeed Logo.png', '.'), ('config.json', '.'), ('blank_page_detector.py', '.'), ('date_extractor.py', '.'), ('auto_qc.py', '.'), ('pipeline.py', '.'), ('paths.py', '.'), ('ui', 'ui')]
binaries = []
hiddenimports = ['pytesseract', 'cv2', 'numpy', 'fitz', 'PIL', 'dateparser', 'dateutil', 'PyQt6']
tmp_ret = collect_all('PyQt6')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['desktop_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'torchvision', 'torchaudio', 'sklearn', 'scikit-learn', 'transformers', 'onnxruntime', 'tensorflow', 'paddle', 'paddleocr', 'paddlepaddle', 'sympy', 'scipy', 'pandas', 'matplotlib', 'networkx', 'lxml', 'openpyxl'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LumeedQScan',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LumeedQScan',
)
