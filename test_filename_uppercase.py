import pytest
from pipeline import build_final_filename, PipelineConfig

def test_build_final_filename_uppercase_default():
    # By default, stem should be capitalized, with lowercase .pdf extension
    filename = build_final_filename(
        seq=1,
        division_code="031",
        company="new zealand ins corp",
        include_date=True,
        yyyymm="202601",
        uppercase=True,
    )
    assert filename == "2026010001_031_NEW ZEALAND INS CORP.pdf"

def test_build_final_filename_uppercase_disabled():
    filename = build_final_filename(
        seq=1,
        division_code="031",
        company="new zealand ins corp",
        include_date=True,
        yyyymm="202601",
        uppercase=False,
    )
    assert filename == "2026010001_031_new zealand ins corp.pdf"

def test_pipeline_config_uppercase_filenames():
    cfg = PipelineConfig(
        input_root="in",
        output_root="out",
        uppercase_filenames=True,
    )
    assert cfg.uppercase_filenames is True
