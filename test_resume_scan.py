import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).parent))

import session
import ui.widgets as widgets
from pipeline import PipelineConfig, parse_folder_structure, save_confirmed_documents
from ui.widgets import PipelineThread


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


_FAKE_FLAG = {
    "original_path": "PLACEHOLDER",
    "original_filename": "PLACEHOLDER.pdf",
    "division_code": "TEST",
    "company_name": "Acme",
    "reviewed": False,
}


def _fake_worker(info, config, render_dpi):
    flagged = dict(_FAKE_FLAG)
    flagged["original_path"] = info["original_path"]
    flagged["original_filename"] = info["original_filename"]
    return {
        "status": "flagged",
        "flagged_data": flagged,
        "passed_data": None,
        "log_messages": [f"stub {info['original_filename']}"],
        "blank_pages": [],
        "docsep_pages": [],
        "total_pages": 1,
    }


class TestScanInfoRoundTrip(unittest.TestCase):
    def setUp(self):
        session.clear_scan_info()

    def test_serialize_apply_roundtrip_scan(self):
        class FakeRT:
            all_flagged_docs = []
            auto_confirmed_docs = []
            active_tab = "pending"
            active_index = -1
            view_mode = "variable"
            zoom_level = 100
            document_modified = False
            current_pdf_doc = None

            def _get_current_doc(self):
                return None

        session.set_scan_info({"plan": ["a.pdf", "b.pdf"], "processed": ["a.pdf"]})
        snap = session.serialize(FakeRT())
        self.assertEqual(snap["scan"]["processed"], ["a.pdf"])

        rt = FakeRT()
        result = session.apply(rt, snap)
        self.assertEqual(result["scan"]["plan"], ["a.pdf", "b.pdf"])
        self.assertEqual(session.get_scan_info()["plan"], ["a.pdf", "b.pdf"])

    def test_discard_clears_scan(self):
        session.set_scan_info({"plan": ["x"]})
        session.discard_snapshot()
        self.assertIsNone(session.get_scan_info())


class TestPipelineResumeMode(unittest.TestCase):
    def setUp(self):
        _app()
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.input_root = base / "input"
        self.output_root = base / "output"
        self.flagged_root = base / "flagged"
        self.input_root.mkdir()
        self.flagged_root.mkdir()
        self.config = {
            "input_root": str(self.input_root),
            "output_root": str(self.output_root),
            "flagged_root": str(self.flagged_root),
            "confidence_threshold": 70,
            "page_index": 0,
            "earliest_year": 1990,
            "ocr_engine": "tesseract",
            "render_dpi": 150,
            "max_workers": 2,
            "batch_size": 10,
        }
        self._orig_worker = widgets._process_doc_worker
        widgets._process_doc_worker = _fake_worker

    def tearDown(self):
        widgets._process_doc_worker = self._orig_worker
        self.tmp.cleanup()

    def test_resume_runs_only_remaining_and_merges_index(self):
        plan = [str(self.input_root / "a.pdf"), str(self.input_root / "b.pdf"), str(self.input_root / "c.pdf")]
        existing = [str(self.input_root / "a.pdf")]
        old_flag = {"original_path": plan[0], "original_filename": "a.pdf", "division_code": "D", "reviewed": False}

        # pre-existing index content should be preserved until merged
        self.flagged_root.mkdir(parents=True, exist_ok=True)
        (self.flagged_root / "flagged_index.json").write_text("[999]", encoding="utf-8")

        thread = PipelineThread(
            self.config,
            0,
            plan=plan,
            existing_paths=set(existing),
            merged_flagged=[old_flag],
            merged_confirmed=[],
        )
        doc_updates = []
        completed = []
        finished = []
        thread.doc_processed.connect(lambda kind, data: doc_updates.append((kind, data)))
        thread.doc_completed.connect(lambda kind, path: completed.append((kind, path)))
        thread.finished_signal.connect(lambda flagged: finished.append(flagged))

        thread.run()

        self.assertEqual(len(doc_updates), 2)
        done_paths = [p for kind, p in completed if kind == "doc"]
        self.assertEqual(sorted(done_paths), sorted([plan[1], plan[2]]))
        self.assertNotIn(plan[0], done_paths)
        self.assertTrue(completed[0][0] == "plan")

        flagged_index = json.loads((self.flagged_root / "flagged_index.json").read_text(encoding="utf-8"))
        self.assertEqual(len(flagged_index), 3)
        self.assertEqual([d["original_path"] for d in flagged_index].count(plan[0]), 1)
        self.assertEqual(len(finished), 1)
        self.assertEqual(len(finished[0]), 3)

    def test_fresh_mode_clears_index_and_emits_plan(self):
        self._orig_parse = widgets.parse_folder_structure
        from pipeline import Document, DivisionBatch

        doc = Document(
            original_path=str(self.input_root / "a.pdf"),
            original_filename="a.pdf",
            division_code="D",
            company_name="Acme",
        )
        widgets.parse_folder_structure = lambda root: [DivisionBatch("D", [doc])]

        plan_events = []
        thread = PipelineThread(self.config, 0)
        thread.scan_plan.connect(lambda paths: plan_events.append(paths))
        try:
            thread.run()
        finally:
            widgets.parse_folder_structure = self._orig_parse
        self.assertEqual(len(plan_events), 1)
        self.assertEqual(len(plan_events[0]), 1)
        flagged_index = json.loads((self.flagged_root / "flagged_index.json").read_text(encoding="utf-8"))
        self.assertEqual(len(flagged_index), 1)

    def test_confirmed_docs_emit_passed(self):
        def fake_confirmed_worker(info, config, render_dpi):
            return {
                "status": "confirmed",
                "flagged_data": None,
                "passed_data": {
                    "original_path": info["original_path"],
                    "original_filename": info["original_filename"],
                    "division_code": "D",
                    "company_name": "Acme",
                    "detected_date": "2020-01-15",
                    "confidence": 90,
                    "method": "auto",
                },
                "log_messages": ["stub confirmed"],
                "blank_pages": [],
                "docsep_pages": [],
                "total_pages": 1,
            }

        plan = [str(self.input_root / "a.pdf"), str(self.input_root / "b.pdf")]
        thread = PipelineThread(self.config, 0, plan=plan, existing_paths=set(), merged_flagged=[], merged_confirmed=[])
        updates = []
        thread.doc_processed.connect(lambda kind, data: updates.append((kind, data)))
        try:
            widgets._process_doc_worker = fake_confirmed_worker
            thread.run()
        finally:
            widgets._process_doc_worker = self._orig_worker

        self.assertEqual([k for k, _ in updates], ["passed", "passed"])
        self.assertEqual(len(updates), 2)
        self.assertEqual(updates[0][1]["detected_date"], "2020-01-15")


if __name__ == "__main__":
    unittest.main()