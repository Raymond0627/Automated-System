"""Tests for the "Document date" renaming setting (document_date_enabled)."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import fitz
import pytest

import pipeline
from date_extractor import DateResult, extract_document_date
from pipeline import (
    DivisionBatch,
    Document,
    PipelineConfig,
    _next_folder_sequence,
    extract_dates_for_batch,
    finalize_division,
    finalize_single_document,
)


def _config(tmp_path, document_date_enabled=True, output_layout="company"):
    cfg = PipelineConfig(
        input_root=str(tmp_path / "in"),
        output_root=str(tmp_path / "out"),
        flagged_root=str(tmp_path / "flagged"),
        division_code="003",
    )
    cfg.document_date_enabled = document_date_enabled
    cfg.output_layout = output_layout
    cfg.enable_qc = False
    cfg.enable_docsep_removal = False
    return cfg


@pytest.fixture(autouse=True)
def _no_hint_learning(monkeypatch):
    """Confirm/rename flows learn hints into the real roster; tests must not."""
    import ui.review_tab as review_tab_mod
    import company_resolver

    monkeypatch.setattr(review_tab_mod, "learn_company_name", lambda *a, **k: None)
    monkeypatch.setattr(company_resolver, "record_folder_hint", lambda *a, **k: None)
    monkeypatch.setattr(company_resolver, "record_letterhead_hint", lambda *a, **k: None)


def _date_result(d=None, confidence=0, method="tesseract_heuristic", page_texts=None):
    return DateResult(
        date=d,
        confidence=confidence,
        method=method,
        candidates=[],
        raw_ocr_text="",
        page_texts=page_texts or [],
    )


# ---------------------------------------------------------------- config


def test_pipeline_config_defaults_to_excluding_document_date(tmp_path):
    cfg = PipelineConfig(input_root=str(tmp_path / "in"), output_root=str(tmp_path / "out"))
    assert cfg.document_date_enabled is False


def test_pipeline_config_accepts_enabled_document_date(tmp_path):
    cfg = PipelineConfig(
        input_root=str(tmp_path / "in"),
        output_root=str(tmp_path / "out"),
        document_date_enabled=True,
    )
    assert cfg.document_date_enabled is True


# ------------------------------------------------------------ build name


def test_build_final_filename_includes_date_prefix_by_default():
    name = pipeline.build_final_filename(7, "003", "CLARK DEVELOPMENT CORPORATION", yyyymm="202101")
    assert name == "2021010007_003_CLARK DEVELOPMENT CORPORATION.pdf"


def test_build_final_filename_omits_date_when_disabled():
    name = pipeline.build_final_filename(
        7, "003", "CLARK DEVELOPMENT CORPORATION", include_date=False, yyyymm="202101"
    )
    assert name == "0007_003_CLARK DEVELOPMENT CORPORATION.pdf"


# ------------------------------------------------------------ parse name


def test_parse_final_stem_reads_dated_format():
    parsed = pipeline.parse_final_stem("2021010047_003_CLARK DEVELOPMENT CORPORATION")
    assert parsed == {
        "yyyymm": "202101",
        "seq": "0047",
        "division": "003",
        "company": "CLARK DEVELOPMENT CORPORATION",
    }


def test_parse_final_stem_reads_dateless_format():
    parsed = pipeline.parse_final_stem("0047_003_CLARK DEVELOPMENT CORPORATION")
    assert parsed == {
        "yyyymm": "",
        "seq": "0047",
        "division": "003",
        "company": "CLARK DEVELOPMENT CORPORATION",
    }


def test_parse_final_stem_rejects_unrelated_names():
    assert pipeline.parse_final_stem("quarterly report final") is None
    assert pipeline.parse_final_stem("2021010047") is None


# ------------------------------------------------------- auto-confirm gate


def test_auto_confirm_ignores_date_when_document_date_disabled(tmp_path):
    cfg = _config(tmp_path, document_date_enabled=False)
    assert pipeline.passes_auto_confirm(cfg, _date_result(d=None, confidence=0), 95, True) is True
    assert (
        pipeline.passes_auto_confirm(cfg, _date_result(d=date(1800, 1, 1), confidence=0), 95, True)
        is True
    )


def test_auto_confirm_still_requires_company_and_qc_when_date_disabled(tmp_path):
    cfg = _config(tmp_path, document_date_enabled=False)
    assert pipeline.passes_auto_confirm(cfg, _date_result(), 40, True) is False
    assert pipeline.passes_auto_confirm(cfg, _date_result(), 95, False) is False


def test_auto_confirm_requires_valid_confident_date_when_date_enabled(tmp_path):
    cfg = _config(tmp_path, document_date_enabled=True)
    assert (
        pipeline.passes_auto_confirm(cfg, _date_result(d=date.today(), confidence=95), 95, True)
        is True
    )
    assert pipeline.passes_auto_confirm(cfg, _date_result(d=None, confidence=95), 95, True) is False
    assert (
        pipeline.passes_auto_confirm(cfg, _date_result(d=date.today(), confidence=40), 95, True)
        is False
    )
    assert (
        pipeline.passes_auto_confirm(cfg, _date_result(d=date(1800, 1, 1), confidence=95), 95, True)
        is False
    )


# ------------------------------------------- finalize_single_document


def _source_pdf(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    d = fitz.open()
    d.new_page().insert_text((72, 72), "hello")
    d.save(str(path))
    d.close()
    return path


def test_finalize_single_document_keeps_date_prefix_when_date_enabled(tmp_path):
    cfg = _config(tmp_path, document_date_enabled=True)
    src = _source_pdf(tmp_path / "in" / "src.pdf")
    result = finalize_single_document(
        {"original_path": str(src), "division_code": "003", "company_name": "Acme Corp",
         "detected_date": "2024-05-15"},
        cfg,
    )
    assert result["success"], result.get("error")
    assert result["final_filename"] == "2024050001_003_ACME CORP.pdf"


def test_finalize_single_document_omits_date_when_disabled(tmp_path):
    cfg = _config(tmp_path, document_date_enabled=False)
    src = _source_pdf(tmp_path / "in" / "src.pdf")
    result = finalize_single_document(
        {"original_path": str(src), "division_code": "003", "company_name": "Acme Corp",
         "detected_date": "2024-05-15"},
        cfg,
    )
    assert result["success"], result.get("error")
    assert result["final_filename"] == "0001_003_ACME CORP.pdf"


# ------------------------------------------------- flat folder sequence


def test_next_folder_sequence_counts_dateless_names(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "0007_003_Acme.pdf").write_bytes(b"%PDF-1.4")
    (out / "2021010002_003_Beta.pdf").write_bytes(b"%PDF-1.4")
    assert _next_folder_sequence(out) == 8


# ------------------------------------------------------ finalize_division


def _confirmed_doc(tmp_path, name, company="Acme", confirmed_date=None, rel_dir=""):
    src = _source_pdf(tmp_path / "in" / name)
    return Document(
        original_path=str(src),
        division_code="003",
        company_name=company,
        original_filename=name,
        confirmed_date=confirmed_date,
        status="confirmed",
        rel_dir=rel_dir,
    )


def test_finalize_division_builds_dated_names_when_date_enabled(tmp_path):
    cfg = _config(tmp_path, document_date_enabled=True)
    doc = _confirmed_doc(tmp_path, "a.pdf", confirmed_date=date(2021, 1, 1))
    batch = DivisionBatch(division_code="003", documents=[doc])
    finalize_division(batch, cfg, None)
    assert doc.final_filename == "2021010001_003_ACME.pdf"
    cfg.output_root / "003" / "Acme" / doc.final_filename


def test_finalize_division_keeps_documents_without_date_when_disabled(tmp_path):
    cfg = _config(tmp_path, document_date_enabled=False)
    doc = _confirmed_doc(tmp_path, "a.pdf", confirmed_date=None)
    batch = DivisionBatch(division_code="003", documents=[doc])
    finalize_division(batch, cfg, None)
    assert doc.final_filename == "0001_003_ACME.pdf"
    assert (cfg.output_root / "003" / "Acme" / doc.final_filename).exists()


def test_save_confirmed_documents_keeps_dateless_docs(tmp_path):
    import json

    cfg = _config(tmp_path, document_date_enabled=False)
    doc = _confirmed_doc(tmp_path, "a.pdf", confirmed_date=None)
    doc.final_filename = "0001_003_Acme.pdf"
    pipeline.save_confirmed_documents([DivisionBatch("003", [doc])], cfg)
    data = json.loads((cfg.flagged_root / "passed_index.json").read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["final_filename"] == "0001_003_Acme.pdf"
    assert data[0]["detected_date"] == ""


# ------------------------------------------------- batch auto-confirm wiring


def _fake_scan(monkeypatch, date_result, company_name="Acme", company_conf=95):
    monkeypatch.setattr(pipeline, "extract_document_date", lambda *a, **k: date_result)
    monkeypatch.setattr(
        pipeline,
        "get_company_name_for_filename",
        lambda *a, **k: {
            "company_name": company_name,
            "company_confidence": company_conf,
            "tier_used": "caption",
        },
    )


def test_batch_confirms_without_date_when_disabled(tmp_path, monkeypatch):
    cfg = _config(tmp_path, document_date_enabled=False)
    _fake_scan(monkeypatch, _date_result(d=None, confidence=0, page_texts=["text"]))
    doc = Document(
        original_path=str(tmp_path / "in" / "a.pdf"),
        division_code="003",
        company_name="",
        original_filename="a.pdf",
    )
    _source_pdf(Path(doc.original_path))
    extract_dates_for_batch(DivisionBatch("003", [doc]), cfg)
    assert doc.status == "confirmed"
    assert doc.confirmed_date is None


def test_batch_flags_without_date_when_enabled(tmp_path, monkeypatch):
    cfg = _config(tmp_path, document_date_enabled=True)
    _fake_scan(monkeypatch, _date_result(d=None, confidence=0, page_texts=["text"]))
    doc = Document(
        original_path=str(tmp_path / "in" / "a.pdf"),
        division_code="003",
        company_name="",
        original_filename="a.pdf",
    )
    _source_pdf(Path(doc.original_path))
    extract_dates_for_batch(DivisionBatch("003", [doc]), cfg)
    assert doc.status == "flagged"


# ------------------------------------------------------- date extractor


def _dated_pdf(path):
    d = fitz.open()
    d.new_page().insert_text((72, 100), "DATE: 15 May 2024")
    d.new_page()
    d.save(str(path))
    d.close()


def test_extract_document_date_finds_date_by_default(tmp_path):
    pdf = tmp_path / "dated.pdf"
    _dated_pdf(pdf)
    result = extract_document_date(str(pdf), 0)
    assert result.date == date(2024, 5, 15)


def test_extract_document_date_skips_date_but_keeps_ocr_and_blank_scan(tmp_path):
    pdf = tmp_path / "dated.pdf"
    _dated_pdf(pdf)
    result = extract_document_date(str(pdf), 0, include_date=False)
    assert result.date is None
    assert result.method == "skipped"
    assert result.blank_pages == [1]
    assert result.page_texts and any("DATE" in t.upper() for t in result.page_texts)


# ---------------------------------------------------------- settings tab


def _settings_tab(config):
    from ui.organize_dialog import _ensure_qapp
    _ensure_qapp()
    from ui.settings_tab import SettingsTab

    return SettingsTab(config)


def test_settings_tab_shows_document_date_checkbox_unchecked_by_default():
    tab = _settings_tab({})
    assert tab.document_date_check.isChecked() is False
    assert "{YYYYMM}" not in tab.pattern_label.text()


def test_settings_tab_saves_document_date_setting(tmp_path, monkeypatch):
    import ui.settings_tab as st_mod

    monkeypatch.setattr(st_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(st_mod, "ensure_data_dir", lambda: None)
    monkeypatch.setattr(
        st_mod.QMessageBox, "information", staticmethod(lambda *a, **k: None)
    )
    config = {}
    tab = _settings_tab(config)
    tab.document_date_check.setChecked(True)
    tab.save_settings()
    assert config["document_date_enabled"] is True
    saved = (tmp_path / "config.json").read_text(encoding="utf-8")
    assert '"document_date_enabled": true' in saved


def test_settings_tab_updates_pattern_label_for_dateless_format():
    tab = _settings_tab({})
    tab.document_date_check.setChecked(False)
    tab._toggle_date_format(False)
    assert "{YYYYMM}" not in tab.pattern_label.text()
    assert "{SEQ}" in tab.pattern_label.text()
    tab.document_date_check.setChecked(True)
    tab._toggle_date_format(True)
    assert "{YYYYMM}" in tab.pattern_label.text()


# ---------------------------------------------------------- review tab


def _review_tab(tmp_path, **extra):
    from ui.organize_dialog import _ensure_qapp
    _ensure_qapp()
    from ui.review_tab import ReviewTab

    config = {
        "input_root": str(tmp_path),
        "output_root": str(tmp_path / "_out_missing"),
        "flagged_root": "",
        "theme": "Dark Mode",
    }
    config.update(extra)
    return ReviewTab(config)


def test_review_tab_hides_date_corrector_when_disabled(tmp_path):
    rt = _review_tab(tmp_path, document_date_enabled=False)
    assert rt.date_label.isHidden()
    assert rt.month_combo.isHidden()
    assert rt.year_spin.isHidden()
    assert not rt.company_input.isHidden()
    assert not rt.confirm_btn.isHidden()


def test_review_tab_hides_date_corrector_when_key_missing(tmp_path):
    rt = _review_tab(tmp_path)
    assert rt.date_label.isHidden()
    assert rt.month_combo.isHidden()
    assert rt.year_spin.isHidden()


def test_review_tab_shows_date_corrector_when_enabled(tmp_path):
    rt = _review_tab(tmp_path, document_date_enabled=True)
    assert not rt.date_label.isHidden()
    assert not rt.month_combo.isHidden()
    assert not rt.year_spin.isHidden()


def test_filename_date_guard_ignores_dateless_names():
    from ui.review_tab import _filename_date

    assert _filename_date("0047_003_CLARK DEVELOPMENT CORPORATION.pdf") == ""
    assert _filename_date("2021010047_003_CLARK DEVELOPMENT CORPORATION.pdf") == "20210100"


def _reviewed_file(tmp_path, filename):
    src = tmp_path / "reviewed" / filename
    src.parent.mkdir(parents=True, exist_ok=True)
    d = fitz.open()
    d.new_page().insert_text((72, 72), "hello")
    d.save(str(src))
    d.close()
    return src


def test_reviewed_rename_keeps_dateless_format_when_disabled(tmp_path):
    rt = _review_tab(tmp_path, document_date_enabled=False)
    src = _reviewed_file(tmp_path, "0047_003_Acme.pdf")
    doc = {
        "original_path": str(src),
        "original_filename": src.name,
        "division_code": "003",
        "company_name": "Acme",
        "detected_date": "",
    }
    rt.active_tab = "reviewed"
    rt.reviewed_docs = [doc]
    rt.active_index = 0
    rt.company_input.setText("Acme Corp")
    rt.confirm_date()
    assert (src.parent / "0047_003_Acme Corp.pdf").exists()
    assert not src.exists()


def test_reviewed_rename_converts_dated_name_when_date_disabled(tmp_path):
    rt = _review_tab(tmp_path, document_date_enabled=False)
    src = _reviewed_file(tmp_path, "2021010047_003_Acme.pdf")
    doc = {
        "original_path": str(src),
        "original_filename": src.name,
        "division_code": "003",
        "company_name": "Acme",
        "detected_date": "2021-01-01",
    }
    rt.active_tab = "reviewed"
    rt.reviewed_docs = [doc]
    rt.active_index = 0
    rt.company_input.setText("Acme Corp")
    rt.confirm_date()
    assert (src.parent / "0047_003_Acme Corp.pdf").exists()


def test_reviewed_rename_keeps_dated_format_when_date_enabled(tmp_path):
    rt = _review_tab(tmp_path, document_date_enabled=True)
    src = _reviewed_file(tmp_path, "2021010047_003_Acme.pdf")
    doc = {
        "original_path": str(src),
        "original_filename": src.name,
        "division_code": "003",
        "company_name": "Acme",
        "detected_date": "2021-01-01",
    }
    rt.active_tab = "reviewed"
    rt.reviewed_docs = [doc]
    rt.active_index = 0
    rt.year_spin.setValue(2021)
    rt.month_combo.setCurrentIndex(0)
    rt.company_input.setText("Acme Corp")
    rt.confirm_date()
    assert (src.parent / "2021010047_003_Acme Corp.pdf").exists()
