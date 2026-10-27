"""Tests for the Dashboard pre-run output comparison guard."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import find_remaining
from ui.dashboard_tab import DashboardTab


def _app():
    from ui.organize_dialog import _ensure_qapp

    _ensure_qapp()


def _config(tmp_path):
    return {
        "input_root": str(tmp_path / "input"),
        "output_root": str(tmp_path / "output"),
        "flagged_root": "",
        "division_code": "003",
        "confidence_threshold": 85,
        "earliest_year": 1950,
        "ocr_engine": "tesseract",
        "render_dpi": 100,
        "max_workers": 1,
        "batch_size": 10,
        "theme": "Light Mode",
    }


def _report(tmp_path, done=2, remaining=1):
    rows = []
    for index in range(done):
        rows.append(
            find_remaining.Row(
                status="DONE_TEXT_MATCH" if index % 2 == 0 else "DONE_IMAGE_MATCH",
                input_path=str(tmp_path / "input" / f"done{index}.pdf"),
                rel_path=f"done{index}.pdf",
            )
        )
    for index in range(remaining):
        rows.append(
            find_remaining.Row(
                status="REMAINING_NO_OUTPUT",
                input_path=str(tmp_path / "input" / f"left{index}.pdf"),
                rel_path=f"left{index}.pdf",
            )
        )
    return find_remaining.Report(
        str(tmp_path / "input"), str(tmp_path / "output"), rows, []
    )


class TestOutputGuard(unittest.TestCase):
    def setUp(self):
        _app()
        self.tmp = Path(self._tmpdir())
        self.tab = DashboardTab(_config(self.tmp))

    def _tmpdir(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        return self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_remaining_choice_starts_pipeline_with_plan_and_done_set(self):
        report = _report(self.tmp, done=2, remaining=1)
        with patch.object(self.tab, "_ask_process_scope", return_value="remaining"), patch.object(
            self.tab, "_start_pipeline"
        ) as start:
            self.tab._on_compare_done(report)

        plan, existing = find_remaining.plan_for_pipeline(report)
        start.assert_called_once_with(plan=plan, existing_paths=existing)

    def test_all_choice_starts_a_fresh_pipeline(self):
        report = _report(self.tmp, done=2, remaining=1)
        with patch.object(self.tab, "_ask_process_scope", return_value="all"), patch.object(
            self.tab, "_start_pipeline"
        ) as start:
            self.tab._on_compare_done(report)

        start.assert_called_once_with()

    def test_cancel_choice_starts_nothing(self):
        report = _report(self.tmp, done=2, remaining=1)
        with patch.object(self.tab, "_ask_process_scope", return_value="cancel"), patch.object(
            self.tab, "_start_pipeline"
        ) as start:
            self.tab._on_compare_done(report)

        start.assert_not_called()

    def test_nothing_processed_skips_prompt_and_starts_fresh(self):
        report = _report(self.tmp, done=0, remaining=3)
        with patch.object(self.tab, "_ask_process_scope") as ask, patch.object(
            self.tab, "_start_pipeline"
        ) as start:
            self.tab._on_compare_done(report)

        ask.assert_not_called()
        start.assert_called_once_with()

    def test_everything_processed_only_reprocesses_when_confirmed(self):
        report = _report(self.tmp, done=3, remaining=0)
        with patch.object(self.tab, "_ask_reprocess_all", return_value=False), patch.object(
            self.tab, "_start_pipeline"
        ) as start:
            self.tab._on_compare_done(report)
        start.assert_not_called()

        with patch.object(self.tab, "_ask_reprocess_all", return_value=True), patch.object(
            self.tab, "_start_pipeline"
        ) as start:
            self.tab._on_compare_done(report)
        start.assert_called_once_with()

    def test_output_has_pdfs_detects_nested_pdf(self):
        output = self.tmp / "output"
        self.assertFalse(self.tab._output_has_pdfs(str(output)))
        output.mkdir()
        self.assertFalse(self.tab._output_has_pdfs(str(output)))
        nested = output / "003" / "COMPANY"
        nested.mkdir(parents=True)
        (nested / "0001_003_COMPANY.pdf").write_bytes(b"%PDF-1.4")
        self.assertTrue(self.tab._output_has_pdfs(str(output)))

    def test_compare_failure_falls_back_to_fresh_pipeline(self):
        with patch.object(self.tab, "_start_pipeline") as start:
            self.tab._on_compare_failed("boom")
        start.assert_called_once_with()

    def test_run_pipeline_checks_existing_output_before_scanning(self):
        input_root = self.tmp / "input"
        output_root = self.tmp / "output"
        input_root.mkdir()
        (output_root / "003" / "COMPANY").mkdir(parents=True)
        (output_root / "003" / "COMPANY" / "0001_003_COMPANY.pdf").write_bytes(b"%PDF-1.4")
        self.tab.input_var.setText(str(input_root))
        self.tab.output_var.setText(str(output_root))
        self.tab._output_touched = True

        with patch.object(self.tab, "_save_config"), patch.object(
            self.tab, "_require_division_code", return_value=True
        ), patch.object(self.tab, "_start_output_check") as check, patch.object(
            self.tab, "_start_pipeline"
        ) as start:
            self.tab.run_pipeline()

        check.assert_called_once_with(str(input_root), str(output_root))
        start.assert_not_called()

    def test_run_pipeline_scans_directly_when_output_is_empty(self):
        input_root = self.tmp / "input"
        output_root = self.tmp / "output"
        input_root.mkdir()
        output_root.mkdir()
        self.tab.input_var.setText(str(input_root))
        self.tab.output_var.setText(str(output_root))
        self.tab._output_touched = True

        with patch.object(self.tab, "_save_config"), patch.object(
            self.tab, "_require_division_code", return_value=True
        ), patch.object(self.tab, "_start_output_check") as check, patch.object(
            self.tab, "_start_pipeline"
        ) as start:
            self.tab.run_pipeline()

        check.assert_not_called()
        start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
