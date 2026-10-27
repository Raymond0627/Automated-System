"""Tests for find_remaining.py - input/output comparison and rescue helpers."""

import json
import shutil
from pathlib import Path

import fitz

import find_remaining
from find_remaining import build_report, repair_session, stage_remaining


def _make_pdf(path: Path, pages):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


def _make_visual_pdf(path: Path, page_count: int, seed: int, blank_pages=()):
    """Create a PDF with no text layer, only distinct vector marks per page."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for index in range(page_count):
        page = doc.new_page(width=300, height=400)
        if index in blank_pages:
            continue
        x = 20 + seed * 7
        y = 20 + index * 55
        page.draw_rect(fitz.Rect(x, y, x + 90, y + 45), color=None, fill=(0, 0, 0))
        page.draw_circle(fitz.Point(220, 90 + index * 40), 15 + seed * 4, fill=(0, 0, 0))
    doc.save(str(path))
    doc.close()
    return path


def _copy_visual_pages(src: Path, dst: Path, pages):
    src_doc = fitz.open(str(src))
    out_doc = fitz.open()
    for page_index in pages:
        out_doc.insert_pdf(src_doc, from_page=page_index, to_page=page_index)
    dst.parent.mkdir(parents=True, exist_ok=True)
    out_doc.save(str(dst))
    out_doc.close()
    src_doc.close()


def _doc_words(tag, count=3):
    # A trailing blank page mimics the scanned back sides the pipeline removes.
    return [
        f"{tag}word{index} uniquephrase{tag}{index} sampletext{tag}{index}"
        for index in range(count)
    ] + [""]


def _copy_without_last_page(src: Path, dst: Path):
    src_doc = fitz.open(str(src))
    out_doc = fitz.open()
    out_doc.insert_pdf(src_doc, from_page=0, to_page=max(0, len(src_doc) - 2))
    dst.parent.mkdir(parents=True, exist_ok=True)
    out_doc.save(str(dst))
    out_doc.close()
    src_doc.close()


def _status_by_name(report):
    return {Path(row.input_path).name: row.status for row in report.rows}


def test_classifies_pending_matched_raw_copy_and_missing(tmp_path):
    input_root = tmp_path / "BATCH"
    output_root = tmp_path / "BATCH output"
    input_root.mkdir()

    alpha = _make_pdf(input_root / "a.pdf", _doc_words("alpha"))
    bravo = _make_pdf(input_root / "b.pdf", _doc_words("bravo"))
    charlie = _make_pdf(input_root / "c.pdf", _doc_words("charlie"))
    delta = _make_pdf(input_root / "d.pdf", _doc_words("delta"))

    _copy_without_last_page(alpha, output_root / "2020010001_156_ALPHA.pdf")
    _copy_without_last_page(bravo, output_root / "2020010002_156_BRAVO.pdf")
    shutil.copy2(charlie, output_root / "c.pdf")

    session = {
        "all_flagged_docs": [
            {
                "original_path": str(delta),
                "original_filename": delta.name,
                "division_code": "156",
                "company_name": "DELTA",
                "reviewed": False,
            }
        ],
        "auto_confirmed_docs": [],
    }

    report = build_report(input_root, output_root, session_snapshot=session)

    statuses = _status_by_name(report)
    assert statuses["a.pdf"] == "DONE_TEXT_MATCH"
    assert statuses["b.pdf"] == "DONE_TEXT_MATCH"
    assert statuses["c.pdf"] == "REMAINING_RAW_COPY"
    assert statuses["d.pdf"] == "REMAINING_PENDING"
    assert {Path(p).name for p in report.remaining} == {"c.pdf", "d.pdf"}


def test_matches_with_many_outputs_using_rare_word_index(tmp_path):
    input_root = tmp_path / "BATCH"
    output_root = tmp_path / "BATCH output"
    input_root.mkdir()
    output_root.mkdir()
    alpha = _make_pdf(input_root / "a.pdf", _doc_words("alpha"))

    # More than 40 finalized outputs exercises the rare-word candidate index.
    for index in range(45):
        _make_pdf(
            output_root / f"202001{index:04d}_156_OTHER{index}.pdf",
            _doc_words(f"other{index}"),
        )
    _copy_without_last_page(alpha, output_root / "2020019999_156_ALPHA.pdf")

    report = build_report(input_root, output_root, session_snapshot={})

    assert _status_by_name(report)["a.pdf"] == "DONE_TEXT_MATCH"
    assert report.remaining == []


def test_shared_output_is_done_but_flagged(tmp_path):
    input_root = tmp_path / "BATCH"
    output_root = tmp_path / "BATCH output"
    input_root.mkdir()

    echo = _make_pdf(input_root / "e.pdf", _doc_words("echo"))
    foxtrot = _make_pdf(input_root / "f.pdf", _doc_words("foxtrot"))

    # One output containing the pages of both documents (simulates a merge).
    merged = output_root / "2020010003_156_ECHO_FOXTROT.pdf"
    merged.parent.mkdir(parents=True, exist_ok=True)
    echo_doc = fitz.open(str(echo))
    fox_doc = fitz.open(str(foxtrot))
    out_doc = fitz.open()
    out_doc.insert_pdf(echo_doc)
    out_doc.insert_pdf(fox_doc)
    out_doc.save(str(merged))
    out_doc.close()
    echo_doc.close()
    fox_doc.close()

    report = build_report(input_root, output_root, session_snapshot={})

    statuses = _status_by_name(report)
    assert statuses["e.pdf"] == "SHARED_OUTPUT"
    assert statuses["f.pdf"] == "SHARED_OUTPUT"
    assert report.remaining == []


def test_manifest_marks_document_done_even_without_output_match(tmp_path):
    import manifest

    input_root = tmp_path / "BATCH"
    output_root = tmp_path / "BATCH output"
    input_root.mkdir()
    output_root.mkdir()
    alpha = _make_pdf(input_root / "a.pdf", _doc_words("alpha"))

    final = output_root / "2020010001_156_ALPHA.pdf"
    _make_pdf(final, ["completely different final text"])

    manifest.append_manifest_entry(
        output_root,
        {
            "original_path": str(alpha),
            "original_sha256": manifest.sha256_file(alpha),
            "original_size": alpha.stat().st_size,
            "final_filename": final.name,
            "output_path": str(final),
            "division_code": "156",
            "company_name": "ALPHA",
            "document_date": "2020-01-01",
        },
    )

    report = build_report(input_root, output_root, session_snapshot={})

    statuses = _status_by_name(report)
    assert statuses["a.pdf"] == "DONE_MANIFEST"
    assert report.remaining == []


def test_manifest_survives_output_rename(tmp_path):
    import manifest

    input_root = tmp_path / "BATCH"
    output_root = tmp_path / "BATCH output"
    input_root.mkdir()
    output_root.mkdir()
    alpha = _make_pdf(input_root / "a.pdf", _doc_words("alpha"))
    final = _make_pdf(output_root / "2020010001_156_ALPHA.pdf", ["renamed content"])

    manifest.append_manifest_entry(
        output_root,
        {
            "original_path": str(alpha),
            "original_sha256": manifest.sha256_file(alpha),
            "original_size": alpha.stat().st_size,
            "final_filename": final.name,
            "output_path": str(final),
            "output_sha256": manifest.sha256_file(final),
            "division_code": "156",
            "company_name": "ALPHA",
            "document_date": "2020-01-01",
        },
    )
    final.rename(output_root / "renamed_after_finalize.pdf")

    report = build_report(input_root, output_root, session_snapshot={})

    assert _status_by_name(report)["a.pdf"] == "DONE_MANIFEST"
    assert report.remaining == []


def test_manifest_does_not_hide_unfinalized_duplicate(tmp_path):
    import manifest

    input_root = tmp_path / "BATCH"
    output_root = tmp_path / "BATCH output"
    input_root.mkdir()
    output_root.mkdir()
    alpha = _make_pdf(input_root / "a.pdf", _doc_words("alpha"))
    duplicate = input_root / "a_copy.pdf"
    shutil.copy2(alpha, duplicate)
    assert manifest.sha256_file(alpha) == manifest.sha256_file(duplicate)

    final = _make_pdf(output_root / "2020010001_156_ALPHA.pdf", ["different content"])
    manifest.append_manifest_entry(
        output_root,
        {
            "original_path": str(alpha),
            "original_sha256": manifest.sha256_file(alpha),
            "original_size": alpha.stat().st_size,
            "final_filename": final.name,
            "output_path": str(final),
            "output_sha256": manifest.sha256_file(final),
        },
    )

    report = build_report(input_root, output_root, session_snapshot={})

    statuses = _status_by_name(report)
    assert statuses["a.pdf"] == "DONE_MANIFEST"
    assert statuses["a_copy.pdf"] != "DONE_MANIFEST"
    assert [Path(p).name for p in report.remaining] == ["a_copy.pdf"]


def test_manifest_entry_with_missing_output_stays_remaining(tmp_path):
    import manifest

    input_root = tmp_path / "BATCH"
    output_root = tmp_path / "BATCH output"
    input_root.mkdir()
    output_root.mkdir()
    alpha = _make_pdf(input_root / "a.pdf", _doc_words("alpha"))

    manifest.append_manifest_entry(
        output_root,
        {
            "original_path": str(alpha),
            "original_sha256": manifest.sha256_file(alpha),
            "original_size": alpha.stat().st_size,
            "final_filename": "2020010001_156_ALPHA.pdf",
            "output_path": str(output_root / "2020010001_156_ALPHA.pdf"),
            "division_code": "156",
            "company_name": "ALPHA",
            "document_date": "2020-01-01",
        },
    )

    report = build_report(input_root, output_root, session_snapshot={})

    assert _status_by_name(report)["a.pdf"] == "REMAINING_MANIFEST_MISSING_OUTPUT"
    assert [Path(p).name for p in report.remaining] == ["a.pdf"]


def test_stage_remaining_preserves_relative_tree_and_folder_name(tmp_path):
    input_root = tmp_path / "BATCH"
    output_root = tmp_path / "BATCH output"
    nested = input_root / "Company X" / "Sub"
    nested.mkdir(parents=True)
    remaining = _make_pdf(nested / "a.pdf", _doc_words("alpha"))
    _make_pdf(input_root / "done.pdf", _doc_words("done"))

    report = build_report(input_root, output_root, session_snapshot={})
    stage_dir = tmp_path / "stage"

    staged = stage_remaining(report, input_root, output_root, stage_dir)

    destination = stage_dir / "BATCH" / "Company X" / "Sub" / "a.pdf"
    assert destination.exists()
    assert destination.read_bytes() == remaining.read_bytes()
    assert destination in staged
    assert (stage_dir / "RUN_INSTRUCTIONS.txt").exists()

    instructions = (stage_dir / "RUN_INSTRUCTIONS.txt").read_text(encoding="utf-8")
    assert str(stage_dir / "BATCH") in instructions
    assert str(output_root) in instructions


def test_repair_session_reprocess_backs_up_and_clears_pending(tmp_path):
    session_path = tmp_path / "latest_session.json"
    session_path.write_text(
        json.dumps(
            {
                "version": 1,
                "all_flagged_docs": [{"original_path": "old.pdf", "reviewed": False}],
                "auto_confirmed_docs": [{"original_path": "auto.pdf"}],
                "scan": None,
            }
        ),
        encoding="utf-8",
    )
    remaining = [str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf")]

    backup = repair_session(session_path, remaining, all_input_paths=remaining, mode="reprocess")

    assert backup.exists()
    saved = json.loads(session_path.read_text(encoding="utf-8"))
    assert saved["all_flagged_docs"] == []
    assert saved["auto_confirmed_docs"] == []
    assert saved["scan"]["plan"] == remaining
    assert saved["scan"]["processed"] == []


def test_repair_session_review_keeps_pending_and_marks_processed(tmp_path):
    session_path = tmp_path / "latest_session.json"
    pending = {"original_path": str(tmp_path / "a.pdf"), "reviewed": False}
    session_path.write_text(
        json.dumps({"all_flagged_docs": [pending], "auto_confirmed_docs": [], "scan": None}),
        encoding="utf-8",
    )

    repair_session(
        session_path,
        remaining=[pending["original_path"]],
        all_input_paths=[pending["original_path"], str(tmp_path / "done.pdf")],
        done_paths=[str(tmp_path / "done.pdf")],
        mode="review",
    )

    saved = json.loads(session_path.read_text(encoding="utf-8"))
    assert saved["all_flagged_docs"] == [pending]
    assert saved["scan"]["plan"] == [pending["original_path"], str(tmp_path / "done.pdf")]
    assert saved["scan"]["processed"] == [str(tmp_path / "done.pdf")]


def test_write_reports_creates_csv_and_text(tmp_path):
    input_root = tmp_path / "BATCH"
    output_root = tmp_path / "BATCH output"
    input_root.mkdir()
    _make_pdf(input_root / "a.pdf", _doc_words("alpha"))

    report = build_report(input_root, output_root, session_snapshot={})
    csv_path, txt_path = find_remaining.write_reports(report, tmp_path / "reports")

    assert csv_path.exists() and txt_path.exists()
    content = csv_path.read_text(encoding="utf-8")
    assert "input_file" in content
    assert "a.pdf" in content
    assert "REMAINING_NO_OUTPUT" in txt_path.read_text(encoding="utf-8")


def test_matches_image_only_pdf_when_page_was_removed(tmp_path):
    input_root = tmp_path / "BATCH"
    output_root = tmp_path / "BATCH output"
    input_root.mkdir()

    alpha = _make_visual_pdf(input_root / "a.pdf", 2, seed=1)
    _copy_visual_pages(alpha, output_root / "0001_156_ALPHA.pdf", [0])

    report = build_report(input_root, output_root, session_snapshot={})

    statuses = _status_by_name(report)
    assert statuses["a.pdf"] == "DONE_IMAGE_MATCH"
    assert report.remaining == []


def test_does_not_match_unrelated_image_only_pdf(tmp_path):
    input_root = tmp_path / "BATCH"
    output_root = tmp_path / "BATCH output"
    input_root.mkdir()

    _make_visual_pdf(input_root / "b.pdf", 2, seed=2)
    _make_visual_pdf(output_root / "0001_156_BRAVO.pdf", 2, seed=9)

    report = build_report(input_root, output_root, session_snapshot={})

    statuses = _status_by_name(report)
    assert statuses["b.pdf"] == "REMAINING_NO_OUTPUT"
    assert [Path(p).name for p in report.remaining] == ["b.pdf"]


def test_template_page_does_not_hide_unprocessed_duplicate(tmp_path):
    input_root = tmp_path / "BATCH"
    output_root = tmp_path / "BATCH output"
    input_root.mkdir()

    full = _make_pdf(
        input_root / "full.pdf",
        ["cover page alpha uniqueone", "body alpha uniquetwo"],
    )
    # A one-page duplicate of the shared cover must not be reported as done
    # just because the genuine document also matches the same output.
    template = _make_pdf(input_root / "template.pdf", ["cover page alpha uniqueone"])
    output_root.mkdir()
    shutil.copy2(full, output_root / "0001_156_ALPHA.pdf")

    report = build_report(input_root, output_root, session_snapshot={})

    statuses = _status_by_name(report)
    assert statuses["full.pdf"] == "DONE_TEXT_MATCH"
    assert statuses["template.pdf"] == "BORDERLINE"
    assert [Path(p).name for p in report.remaining] == ["template.pdf"]


def test_json_output_includes_image_match_fields(tmp_path, capsys):
    input_root = tmp_path / "BATCH"
    output_root = tmp_path / "BATCH output"
    input_root.mkdir()

    alpha = _make_visual_pdf(input_root / "a.pdf", 2, seed=3)
    _copy_visual_pages(alpha, output_root / "0001_156_ALPHA.pdf", [0])

    assert find_remaining.main(["--input", str(input_root), "--output", str(output_root), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["rows"][0]["status"] == "DONE_IMAGE_MATCH"
    assert payload["rows"][0]["matched_output_pages"] == [0]


def test_plan_for_pipeline_splits_done_and_remaining(tmp_path):
    rows = [
        find_remaining.Row(status="DONE_TEXT_MATCH", input_path=str(tmp_path / "a.pdf"), rel_path="a.pdf"),
        find_remaining.Row(status="DONE_IMAGE_MATCH", input_path=str(tmp_path / "b.pdf"), rel_path="b.pdf"),
        find_remaining.Row(status="SHARED_OUTPUT", input_path=str(tmp_path / "c.pdf"), rel_path="c.pdf"),
        find_remaining.Row(status="REMAINING_NO_OUTPUT", input_path=str(tmp_path / "d.pdf"), rel_path="d.pdf"),
        find_remaining.Row(status="BORDERLINE", input_path=str(tmp_path / "e.pdf"), rel_path="e.pdf"),
    ]
    report = find_remaining.Report(str(tmp_path), str(tmp_path / "out"), rows, [])

    plan, existing = find_remaining.plan_for_pipeline(report)

    assert plan == [str(tmp_path / f"{name}.pdf") for name in ("a", "b", "c", "d", "e")]
    assert existing == {str(tmp_path / f"{name}.pdf") for name in ("a", "b", "c")}


def test_finalized_name_detection():
    assert find_remaining.is_finalized_name("1982080001_156_COMPANY.pdf")
    assert find_remaining.is_finalized_name("0001_156_COMPANY.pdf")
    assert not find_remaining.is_finalized_name("0009___(18).pdf")
    assert not find_remaining.is_finalized_name("random.pdf")
