"""Tests for the rename manifest written when a document is finalized."""

from pathlib import Path

import fitz

import manifest
from pipeline import PipelineConfig, finalize_single_document


def _source_pdf(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "hello manifest")
    doc.save(str(path))
    doc.close()
    return path


def _config(tmp_path):
    cfg = PipelineConfig(
        input_root=str(tmp_path / "in"),
        output_root=str(tmp_path / "out"),
        flagged_root=str(tmp_path / "flagged"),
        division_code="003",
    )
    cfg.document_date_enabled = True
    cfg.output_layout = "flat"
    return cfg


def test_finalize_single_document_writes_manifest_entry(tmp_path):
    cfg = _config(tmp_path)
    src = _source_pdf(tmp_path / "in" / "src.pdf")

    result = finalize_single_document(
        {
            "original_path": str(src),
            "division_code": "003",
            "company_name": "Acme Corp",
            "detected_date": "2024-05-15",
        },
        cfg,
    )

    assert result["success"], result.get("error")
    rows = manifest.load_manifest(cfg.output_root)
    assert len(rows) == 1
    row = rows[0]
    assert row["original_path"] == str(src)
    assert row["original_sha256"] == manifest.sha256_file(src)
    assert row["original_size"] == str(src.stat().st_size)
    assert row["final_filename"] == result["final_filename"]
    assert row["output_path"] == result["output_path"]
    assert Path(result["output_path"]).exists()


def test_finalize_single_document_succeeds_when_manifest_write_fails(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    src = _source_pdf(tmp_path / "in" / "src.pdf")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(manifest, "append_manifest_entry", boom)

    result = finalize_single_document(
        {
            "original_path": str(src),
            "division_code": "003",
            "company_name": "Acme Corp",
            "detected_date": "2024-05-15",
        },
        cfg,
    )

    assert result["success"], result.get("error")
    assert Path(result["output_path"]).exists()


def test_finalize_division_writes_manifest_entry(tmp_path):
    from datetime import date

    from pipeline import Document, DivisionBatch, finalize_division

    cfg = _config(tmp_path)
    src = _source_pdf(tmp_path / "in" / "src.pdf")
    doc = Document(
        original_path=str(src),
        original_filename=src.name,
        division_code="003",
        company_name="Acme Corp",
        confirmed_date=date(2024, 5, 15),
        status="confirmed",
    )

    finalize_division(DivisionBatch(division_code="003", documents=[doc]), cfg, None)

    rows = manifest.load_manifest(cfg.output_root)
    assert len(rows) == 1
    assert rows[0]["original_path"] == str(src)
    assert rows[0]["final_filename"] == doc.final_filename


def test_append_manifest_entry_keeps_one_header_and_appends(tmp_path):
    out = tmp_path / "out"
    first = {"original_path": "a.pdf", "original_sha256": "aaa", "final_filename": "1_003_A.pdf"}
    second = {"original_path": "b.pdf", "original_sha256": "bbb", "final_filename": "2_003_B.pdf"}

    manifest.append_manifest_entry(out, first)
    manifest.append_manifest_entry(out, second)

    path = manifest.manifest_path(out)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("timestamp,")
    assert sum(1 for line in lines if line.startswith("timestamp,")) == 1
    rows = manifest.load_manifest(out)
    assert [row["original_path"] for row in rows] == ["a.pdf", "b.pdf"]
