import os
import sys
import shutil
import json
import csv
import re
from pathlib import Path
from datetime import date, datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict, field

from date_extractor import extract_document_date, DateResult
from auto_qc import run_qc_on_pdf, detect_docsep_flag


@dataclass
class Document:
    original_path: str
    division_code: str
    company_name: str
    original_filename: str
    date_result: Optional[DateResult] = None
    confirmed_date: Optional[date] = None
    confirmed_method: str = ""
    sequence_number: int = 0
    final_filename: str = ""
    status: str = "pending"
    flagged_data: Optional[Dict] = None
    blank_pages: List[int] = field(default_factory=list)
    docsep_pages: List[int] = field(default_factory=list)


@dataclass
class DivisionBatch:
    division_code: str
    documents: List[Document]


class PipelineConfig:
    def __init__(
        self,
        input_root: str,
        output_root: str,
        flagged_root: str,
        confidence_threshold: int = 70,
        page_index: int = 0,
        sequence_start: int = 1,
        earliest_year: int = 1990,
        ocr_engine: str = "tesseract",
    ):
        self.input_root = Path(input_root)
        self.output_root = Path(output_root)
        self.flagged_root = Path(flagged_root)
        self.confidence_threshold = confidence_threshold
        self.page_index = page_index
        self.sequence_start = sequence_start
        self.earliest_year = earliest_year
        self.ocr_engine = ocr_engine
        self.enable_qc = None  # None = use config default
        self.qc_blank_threshold = 1.5
        self.qc_rotation_threshold = 65
        self.qc_mirror_threshold = 15
        self.enable_docsep_removal = True
        self.enable_blank_removal = True
        self.rename_enabled = True
        self.audit_enabled = True
        self.render_dpi = 150
        
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.flagged_root.mkdir(parents=True, exist_ok=True)


def normalize_division_code(division: str) -> str:
    return division.zfill(3)


def sanitize_company_name(company: str) -> str:
    words = company.strip().split()
    title_words = [w.capitalize() for w in words]
    return "_".join(title_words)


def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', '_', name)


def parse_folder_structure(root: Path) -> List[DivisionBatch]:
    divisions = []
    
    skip_names = {"passed", "failed", "processed", "flagged", "logs", "data", "output"}
    
    for div_dir in sorted(root.iterdir()):
        if not div_dir.is_dir():
            continue
        
        division_code = normalize_division_code(div_dir.name)
        documents = []
        
        for item in sorted(div_dir.iterdir()):
            # Skip certain directories that are not company folders
            if item.is_dir() and item.name.lower() in skip_names:
                continue
            
            # If it's a directory, treat it as a company folder
            if item.is_dir():
                company_name = sanitize_company_name(item.name)
                
                for pdf_file in sorted(item.glob("*.pdf")):
                    doc = Document(
                        original_path=str(pdf_file),
                        division_code=division_code,
                        company_name=company_name,
                        original_filename=pdf_file.name,
                    )
                    documents.append(doc)
            # If it's a file (not a directory), it might be a PDF directly in the division folder
            # or if it's in the company folder already, we handle it above
            elif item.suffix.lower() == '.pdf':
                # PDF directly in division folder - unusual but handle it
                company_name = sanitize_company_name(div_dir.name)
                doc = Document(
                    original_path=str(item),
                    division_code=division_code,
                    company_name=company_name,
                    original_filename=item.name,
                )
                documents.append(doc)
        
        if documents:
            divisions.append(DivisionBatch(division_code=division_code, documents=documents))
    
    return divisions


def extract_dates_for_batch(batch: DivisionBatch, config: PipelineConfig) -> None:
    for doc in batch.documents:
        try:
            # DOCSEP detection: tag separator pages (removal happens at finalize)
            if config.enable_docsep_removal:
                try:
                    ds_result = detect_docsep_flag(doc.original_path)
                    doc.docsep_pages = ds_result["docsep_pages"]
                except Exception:
                    pass

            result = extract_document_date(doc.original_path, config.page_index, engine=config.ocr_engine)
            doc.date_result = result
            doc.blank_pages = result.blank_pages or []
            
            qc_result = None
            if config.enable_qc and not result.all_blank:
                try:
                    qc_result = run_qc_on_pdf(
                        doc.original_path,
                        config.qc_blank_threshold,
                        config.qc_rotation_threshold,
                        config.qc_mirror_threshold,
                    )
                except Exception:
                    pass
            
            # Handle all-blank documents
            if result.all_blank:
                doc.status = "failed"
                doc.flagged_data = create_flagged_data(doc, result, error="Document is entirely blank")
                if qc_result:
                    doc.flagged_data["qc"] = qc_result
                continue
            
            # Handle documents with some blank pages
            if result.blank_pages:
                doc.flagged_data = doc.flagged_data or {}
                doc.flagged_data["blank_pages"] = result.blank_pages
            
            # Determine if QC passes
            qc_passed = True
            if qc_result and qc_result.get("qc_status") in ("failed", "needs_review"):
                qc_passed = False
            
            if result.confidence >= config.confidence_threshold and result.date:
                if result.date.year >= config.earliest_year and result.date <= date.today():
                    if qc_passed:
                        doc.confirmed_date = result.date
                        doc.confirmed_method = "auto"
                        doc.status = "confirmed"
                    else:
                        doc.status = "flagged"
                        doc.flagged_data = create_flagged_data(doc, result)
                        if qc_result:
                            doc.flagged_data["qc"] = qc_result
                else:
                    doc.status = "flagged"
                    doc.flagged_data = create_flagged_data(doc, result)
                    if qc_result:
                        doc.flagged_data["qc"] = qc_result
            else:
                doc.status = "flagged"
                doc.flagged_data = create_flagged_data(doc, result)
                if qc_result:
                    doc.flagged_data["qc"] = qc_result
                
        except Exception as e:
            doc.status = "error"
            doc.flagged_data = create_flagged_data(doc, None, str(e))


def create_flagged_data(doc: Document, result: Optional[DateResult], error: str = "") -> Dict:
    data = {
        "original_path": doc.original_path,
        "division_code": doc.division_code,
        "company_name": doc.company_name,
        "original_filename": doc.original_filename,
        "error": error,
    }
    
    if result:
        data.update({
            "best_guess_date": result.date.isoformat() if result.date else None,
            "confidence": result.confidence,
            "method": result.method,
            "raw_ocr_text": result.raw_ocr_text,
            "blank_pages": result.blank_pages,
            "all_blank": result.all_blank,
            "candidates": [
                {
                    "matched_text": c.matched_text,
                    "parsed_date": c.parsed_date.isoformat() if c.parsed_date else None,
                    "bbox": c.bbox,
                    "confidence": c.confidence,
                    "keywords_nearby": c.keywords_nearby,
                    "source_psm": c.source_psm,
                    "source_text": c.source_text,
                }
                for c in result.candidates
            ],
        })
    
    return data


def save_flagged_documents(batches: List[DivisionBatch], config: PipelineConfig) -> None:
    flagged_data = []
    
    for batch in batches:
        for doc in batch.documents:
            if doc.status in ("flagged", "failed") and doc.flagged_data:
                flagged_data.append(doc.flagged_data)
                
                flagged_div_dir = config.flagged_root / doc.division_code / doc.company_name
                flagged_div_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(doc.original_path, flagged_div_dir / doc.original_filename)
    
    flagged_index = config.flagged_root / "flagged_index.json"
    with open(flagged_index, "w", encoding="utf-8") as f:
        json.dump(flagged_data, f, indent=2, ensure_ascii=False)


def save_confirmed_documents(batches: List[DivisionBatch], config: PipelineConfig) -> None:
    confirmed_data = []

    for batch in batches:
        for doc in batch.documents:
            if doc.status == "confirmed" and doc.confirmed_date:
                qc = doc.flagged_data.get("qc", {}) if doc.flagged_data else {}
                entry = {
                    "original_path": doc.original_path,
                    "division_code": doc.division_code,
                    "company_name": doc.company_name,
                    "original_filename": doc.original_filename,
                    "detected_date": doc.confirmed_date.isoformat(),
                    "confidence": doc.date_result.confidence if doc.date_result else 100,
                    "method": doc.confirmed_method,
                    "blank_pages": doc.blank_pages or [],
                    "docsep_pages": doc.docsep_pages or [],
                    "final_filename": doc.final_filename or "",
                }
                if qc:
                    entry["qc_status"] = qc.get("qc_status", "")
                    entry["qc_failure_reasons"] = qc.get("qc_failure_reasons", "")
                    entry["qc_blank_detected"] = qc.get("blank_detected", False)
                    entry["qc_rotation_detected"] = qc.get("rotation_detected", "none")
                    entry["qc_mirrored_detected"] = qc.get("mirrored_detected", False)
                confirmed_data.append(entry)

    passed_index = config.flagged_root / "passed_index.json"
    with open(passed_index, "w", encoding="utf-8") as f:
        json.dump(confirmed_data, f, indent=2, ensure_ascii=False)


def load_flagged_index(config: PipelineConfig) -> List[Dict]:
    flagged_index = config.flagged_root / "flagged_index.json"
    if flagged_index.exists():
        with open(flagged_index, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def update_document_from_review(doc: Document, review_data: Dict) -> None:
    if "confirmed_date" in review_data and review_data["confirmed_date"]:
        try:
            doc.confirmed_date = date.fromisoformat(review_data["confirmed_date"])
            doc.confirmed_method = "manual"
            doc.status = "confirmed"
        except ValueError:
            pass


def finalize_division(batch: DivisionBatch, config: PipelineConfig, log_writer) -> None:
    confirmed_docs = [d for d in batch.documents if d.status == "confirmed" and d.confirmed_date]
    confirmed_docs.sort(key=lambda d: d.confirmed_date)
    
    for i, doc in enumerate(confirmed_docs, start=config.sequence_start):
        try:
            doc.sequence_number = i

            yyyymm = doc.confirmed_date.strftime("%Y%m")
            seq = f"{i:04d}"
            div = doc.division_code
            company = sanitize_filename(doc.company_name)

            doc.final_filename = f"{yyyymm}{seq}_{div}_{company}.pdf"

            output_div_dir = config.output_root / doc.division_code
            output_div_dir.mkdir(parents=True, exist_ok=True)

            output_path = output_div_dir / doc.final_filename

            counter = 1
            original_output_path = output_path
            while output_path.exists():
                stem = original_output_path.stem
                output_path = original_output_path.parent / f"{stem}_{counter}{original_output_path.suffix}"
                counter += 1

            shutil.copy2(doc.original_path, output_path)

            blank_removed_count = 0
            docsep_removed_count = 0

            all_remove = set()
            if config.enable_blank_removal and doc.blank_pages:
                all_remove.update(doc.blank_pages)
            if config.enable_docsep_removal and doc.docsep_pages:
                all_remove.update(doc.docsep_pages)

            if all_remove:
                import fitz, tempfile
                d = fitz.open(str(output_path))
                for pn in reversed(sorted(all_remove)):
                    if pn < len(d):
                        d.delete_page(pn)
                        if pn in doc.blank_pages:
                            blank_removed_count += 1
                        if pn in doc.docsep_pages:
                            docsep_removed_count += 1
                if blank_removed_count > 0 or docsep_removed_count > 0:
                    tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
                    tmp.close()
                    d.save(tmp.name, incremental=False, garbage=4, deflate=True)
                    d.close()
                    shutil.move(tmp.name, str(output_path))
                else:
                    d.close()

            log_writer.writerow({
                "timestamp": datetime.now().isoformat(),
                "original_path": doc.original_path,
                "new_filename": output_path.name,
                "new_path": str(output_path),
                "division_code": doc.division_code,
                "company_name": doc.company_name,
                "document_date": doc.confirmed_date.isoformat(),
                "yyyymm": yyyymm,
                "sequence_number": doc.sequence_number,
                "confidence": doc.date_result.confidence if doc.date_result else 100,
                "method": doc.confirmed_method,
                "blank_pages": str(doc.blank_pages) if doc.blank_pages else "",
                "blank_removed": blank_removed_count,
                "docsep_pages": str(doc.docsep_pages) if doc.docsep_pages else "",
                "docsep_removed": docsep_removed_count,
                "status": "copied",
            })
        except Exception as e:
            # Don't let one bad document abort the rest of the batch/run.
            print(f"[finalize_division] Failed to finalize '{doc.original_path}': {e}")
            try:
                log_writer.writerow({
                    "timestamp": datetime.now().isoformat(),
                    "original_path": doc.original_path,
                    "new_filename": "",
                    "new_path": "",
                    "division_code": doc.division_code,
                    "company_name": doc.company_name,
                    "document_date": doc.confirmed_date.isoformat() if doc.confirmed_date else "",
                    "yyyymm": "",
                    "sequence_number": doc.sequence_number,
                    "confidence": doc.date_result.confidence if doc.date_result else "",
                    "method": doc.confirmed_method,
                    "blank_pages": str(doc.blank_pages) if doc.blank_pages else "",
                    "blank_removed": 0,
                    "docsep_pages": str(doc.docsep_pages) if doc.docsep_pages else "",
                    "docsep_removed": 0,
                    "status": f"error: {e}",
                })
            except Exception:
                pass
            continue


def run_pipeline(config: PipelineConfig, progress_callback=None) -> List[DivisionBatch]:
    if progress_callback:
        progress_callback({"stage": "scanning", "message": "Scanning input folder...", "progress": 5})
    
    print(f"Scanning input folder: {config.input_root}")
    batches = parse_folder_structure(config.input_root)
    print(f"Found {len(batches)} divisions with documents")
    
    total_docs = sum(len(b.documents) for b in batches)
    print(f"Total documents: {total_docs}")
    
    if progress_callback:
        progress_callback({"stage": "extracting", "message": f"Extracting dates from {total_docs} documents...", "progress": 10})
    
    print("Extracting dates from documents...")
    for i, batch in enumerate(batches):
        if progress_callback:
            progress_callback({"stage": "extracting", "message": f"Processing division {batch.division_code}...", "progress": 10 + int(70 * i / max(1, len(batches)))})
        extract_dates_for_batch(batch, config)
    
    if progress_callback:
        progress_callback({"stage": "saving", "message": "Saving results...", "progress": 85})
    
    confirmed_count = sum(1 for b in batches for d in b.documents if d.status == "confirmed")
    flagged_count = sum(1 for b in batches for d in b.documents if d.status == "flagged")
    error_count = sum(1 for b in batches for d in b.documents if d.status == "error")
    
    print(f"Auto-confirmed: {confirmed_count}")
    print(f"Flagged for review: {flagged_count}")
    print(f"Errors: {error_count}")
    
    if flagged_count > 0:
        print("Saving flagged documents for review...")
        save_flagged_documents(batches, config)
        print(f"Flagged documents saved to: {config.flagged_root}")
    
    if progress_callback:
        progress_callback({"stage": "complete", "message": f"Done: {confirmed_count} confirmed, {flagged_count} flagged", "progress": 100})
    
    return batches


def finalize_all_divisions(batches: List[DivisionBatch], config: PipelineConfig) -> None:
    log_path = config.output_root / "rename_log.csv"
    log_exists = log_path.exists()

    # NOTE: fieldnames must include every key written by finalize_division's
    # log_writer.writerow(...) calls (including the error-path row), or
    # csv.DictWriter raises ValueError mid-run and silently aborts the
    # remaining divisions/documents.
    fieldnames = [
        "timestamp", "original_path", "new_filename", "new_path",
        "division_code", "company_name", "document_date", "yyyymm",
        "sequence_number", "confidence", "method", "blank_pages",
        "blank_removed", "docsep_pages", "docsep_removed", "status"
    ]

    with open(log_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        if not log_exists:
            writer.writeheader()
        
        for batch in batches:
            finalize_division(batch, config, writer)
    
    print(f"Finalization complete. Log saved to: {log_path}")


def move_confirmed_to_passed(config: PipelineConfig, batches: List[DivisionBatch]) -> None:
    passed_root = config.output_root / "passed"
    passed_root.mkdir(parents=True, exist_ok=True)
    
    for batch in batches:
        for doc in batch.documents:
            if doc.status == "confirmed" and doc.final_filename:
                src = config.output_root / doc.division_code / doc.final_filename
                if src.exists():
                    dst = passed_root / doc.division_code / doc.company_name / doc.final_filename
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dst))
    
    print(f"Confirmed documents moved to: {passed_root}")


if __name__ == "__main__":
    config_path = Path(__file__).parent / "config.json"
    
    if config_path.exists() and len(sys.argv) <= 1:
        with open(config_path) as f:
            cfg = json.load(f)
        config = PipelineConfig(
            input_root=cfg.get("input_root", ""),
            output_root=cfg.get("output_root", ""),
            flagged_root=cfg.get("flagged_root", ""),
            confidence_threshold=cfg.get("confidence_threshold", 70),
            page_index=cfg.get("page_index", 0),
            earliest_year=cfg.get("earliest_year", 1990),
            ocr_engine=cfg.get("ocr_engine", "tesseract"),
        )
        config.enable_qc = cfg.get("enable_qc", None)
        config.enable_docsep_removal = cfg.get("enable_docsep_removal", True)
        config.enable_blank_removal = cfg.get("enable_blank_removal", True)
        config.qc_blank_threshold = cfg.get("qc_blank_threshold", 1.5)
        config.qc_rotation_threshold = cfg.get("qc_rotation_threshold", 65)
        config.qc_mirror_threshold = cfg.get("qc_mirror_threshold", 15)
        config.render_dpi = cfg.get("render_dpi", 150)
    else:
        import argparse
        
        parser = argparse.ArgumentParser(description="Lumeed QScan Pipeline")
        parser.add_argument("input_root", help="Root folder containing division/company/PDF structure")
        parser.add_argument("output_root", help="Output folder for renamed PDFs")
        parser.add_argument("flagged_root", help="Folder for flagged documents awaiting review")
        parser.add_argument("--threshold", type=int, default=70, help="Confidence threshold (0-100)")
        parser.add_argument("--page", type=int, default=0, help="PDF page index to OCR (0-based)")
        parser.add_argument("--earliest-year", type=int, default=1990, help="Earliest valid document year")
        parser.add_argument("--ocr-engine", type=str, default="tesseract", choices=["tesseract", "paddle"], help="OCR engine to use")
        
        args = parser.parse_args()
        
        config = PipelineConfig(
            input_root=args.input_root,
            output_root=args.output_root,
            flagged_root=args.flagged_root,
            confidence_threshold=args.threshold,
            page_index=args.page,
            earliest_year=args.earliest_year,
            ocr_engine=args.ocr_engine,
        )
    
    batches = run_pipeline(config)
    finalize_all_divisions(batches, config)