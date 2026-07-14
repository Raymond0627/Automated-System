#!/usr/bin/env python
"""Test script to verify imports work correctly"""
import sys

# Add current directory to path
sys.path.insert(0, '.')

# Test imports
print("Testing imports...")

# Import date_extractor
try:
    from date_extractor import extract_document_date, DateResult, DateCandidate
    print("✓ date_extractor imports OK")
except Exception as e:
    print(f"✗ date_extractor import failed: {e}")
    sys.exit(1)

# Import pipeline
try:
    from pipeline import PipelineConfig, parse_folder_structure
    print("✓ pipeline imports OK")
except Exception as e:
    print(f"✗ pipeline import failed: {e}")
    sys.exit(1)

# Import desktop_app
try:
    from desktop_app import DesktopApp
    print("✓ desktop_app imports OK")
except Exception as e:
    print(f"✗ desktop_app import failed: {e}")
    sys.exit(1)

print("\nAll imports successful!")