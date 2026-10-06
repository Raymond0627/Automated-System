import os
import sys
import shutil
import json
import csv
import re
import gc
from pathlib import Path
from datetime import date, datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict, field

from date_extractor import extract_document_date, DateResult
from auto_qc import run_qc_on_pdf, detect_docsep_flag
from company_extractor import get_company_name_for_filename


# Directories under the input/output roots that are pipeline artifacts, never
# company folders.
SKIP_NAMES = {"passed", "failed", "processed", "flagged", "logs", "data", "output"}


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
    company_confidence: int = 0
    company_tier: str = ""
    # Folder path of this PDF relative to the input root ("" when the PDF sits
    # directly in the input root). Used by the "mirror" output layout.
    rel_dir: str = ""


@dataclass
class DivisionBatch:
    division_code: str
    documents: List[Document]


class PipelineConfig:
    def __init__(
        self,
        input_root: str,
        output_root: str,
        flagged_root: str = "",
        confidence_threshold: int = 70,
        page_index: int = 0,
        sequence_start: int = 1,
        earliest_year: int = 1990,
        ocr_engine: str = "tesseract",
        division_code: str = "",
    ):
        self.input_root = Path(input_root)
        self.output_root = Path(output_root)
        self.flagged_root = Path(flagged_root) if flagged_root else None
        # Division code is user-supplied on the Dashboard (never read from a
        # folder name). Blank means "no division assigned", so the output stays
        # flat instead of nesting under an empty/placeholder directory.
        self.division_code = normalize_division_code(division_code.strip()) if division_code else ""
        self.confidence_threshold = confidence_threshold
        self.page_index = page_index
        self.sequence_start = sequence_start
        self.earliest_year = earliest_year
        self.ocr_engine = ocr_engine
        self.enable_qc = None  # None = use config default
        self.qc_blank_threshold = 1.5
        self.qc_rotation_threshold = 65
        self.enable_docsep_removal = True
        self.enable_blank_removal = True
        self.rename_enabled = True
        self.audit_enabled = True
        self.render_dpi = 150
        self.keep_input_structure = False
        self.output_layout = "flat"
        
        self.output_root.mkdir(parents=True, exist_ok=True)
        if self.flagged_root:
            self.flagged_root.mkdir(parents=True, exist_ok=True)


def normalize_division_code(division: str) -> str:
    division = division.strip()
    return division.zfill(3) if division.isdigit() else division


def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', '_', name)


def default_output_root(input_root) -> Path:
    """
    Sibling of the input folder named "(input folder name) output".

    C:\\Scans\\MyBatch  ->  C:\\Scans\\MyBatch output
    """
    input_path = Path(input_root)
    return input_path.parent / f"{input_path.name} output"


def mirrored_output_dir(config, doc_or_path, rel_dir: str = "") -> Path:
    """
    Recreate the input folder tree under output_root.

    The PDF's folder path relative to input_root is rebuilt inside output_root,
    so parent/child/grandchild folders all reappear exactly as they were.
    """
    if not rel_dir:
        src = Path(getattr(doc_or_path, "original_path", doc_or_path) or "")
        for base in (config.input_root, config.output_root):
            try:
                rel_dir = str(src.relative_to(base).parent).replace("\\", "/")
                break
            except (ValueError, OSError):
                continue
    if rel_dir and rel_dir not in (".", ""):
        return config.output_root / Path(rel_dir)
    return config.output_root


def parse_folder_structure(root: Path, division_code: str = "") -> List[DivisionBatch]:
    """
    Walk {root} recursively for *.pdf at any depth.

    Multi-hierarchy input is supported: every folder level below the input root
    is preserved on the Document as `rel_dir` (the folder path relative to the
    input root), so the mirror output layout can recreate the same tree.

    The division code is supplied by the caller (from the Dashboard field), not
    derived from folder names. Grouping is reported as a single batch carrying
    `division_code`.
    """
    division_code = normalize_division_code(division_code.strip()) if division_code else ""

    documents = []
    if root.exists():
        for path in sorted(root.rglob("*.pdf")):
            if not path.is_file():
                continue
            # Skip pipeline artifact folders (output/flagged/processed/...)
            # wherever they appear in the tree.
            rel_parts = path.relative_to(root).parts
            if any(part.lower() in SKIP_NAMES for part in rel_parts[:-1]):
                continue
            rel_dir_parts = rel_parts[:-1]
            documents.append(Document(
                original_path=str(path),
                division_code=division_code,
                company_name="",
                original_filename=path.name,
                rel_dir="/".join(rel_dir_parts),
            ))

    if not documents:
        return []

    return [DivisionBatch(division_code=division_code, documents=documents)]


def extract_dates_for_batch(batch: DivisionBatch, config: PipelineConfig) -> None:
    for doc in batch.documents:
        try:
            # Module 1: DOCSEP detection
            if config.enable_docsep_removal:
                try:
                    ds_result = detect_docsep_flag(doc.original_path)
                    doc.docsep_pages = ds_result["docsep_pages"]
                except Exception:
                    pass

            # Module 2: Date extraction (blank detection runs inside, no early exit)
            result = extract_document_date(doc.original_path, config.page_index, engine=config.ocr_engine, docsep_pages=doc.docsep_pages)
            doc.date_result = result
            doc.blank_pages = result.blank_pages or []

            # Module 3: Company name extraction from native PDF text
            import fitz
            try:
                _doc = fitz.open(doc.original_path)
                page_texts = [{"page_text": _doc[i].get_text().strip(), "page_idx": i} for i in range(len(_doc))]
                _doc.close()
            except Exception:
                page_texts = [{"page_text": t, "page_idx": i} for i, t in enumerate(result.page_texts)]
            company_result = get_company_name_for_filename(page_texts)
            doc.company_name = company_result.get("company_name", doc.company_name)
            doc.company_confidence = company_result.get("company_confidence", 0)
            doc.company_tier = company_result.get("tier_used", "")

            # Module 4: QC checks
            qc_result = None
            if config.enable_qc and not result.all_blank:
                try:
                    qc_result = run_qc_on_pdf(
                        doc.original_path,
                        blank_threshold=config.qc_blank_threshold,
                        rotation_threshold=config.qc_rotation_threshold,
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

            # --- Auto-confirm: ALL THREE must pass ---
            date_valid = result.date and result.date.year >= config.earliest_year and result.date <= date.today()
            date_conf_pass = result.confidence >= 90
            company_conf_pass = doc.company_confidence >= 90
            qc_passed = not (qc_result and qc_result.get("qc_status") == "failed")
            blank_clean = not any(r.get("is_blank") == "needs_review" for r in [])  # no needs_review pages

            if date_valid and date_conf_pass and company_conf_pass and qc_passed:
                doc.confirmed_date = result.date
                doc.confirmed_method = "auto"
                doc.status = "confirmed"
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
        "flagged_copy_path": "",
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
    if not getattr(config, "flagged_root", None):
        return
    flagged_data = []
    
    for batch in batches:
        for doc in batch.documents:
            if doc.status in ("flagged", "failed") and doc.flagged_data:
                flagged_data.append(doc.flagged_data)

                # company_name is "" until a reviewer resolves it, so the same
                # original filename can arrive from two different company folders.
                # De-collide rather than silently overwriting a staged copy.
                flagged_div_dir = config.flagged_root / doc.division_code / doc.company_name
                flagged_div_dir.mkdir(parents=True, exist_ok=True)
                staged = flagged_div_dir / doc.original_filename
                counter = 1
                while staged.exists():
                    staged = flagged_div_dir / f"{Path(doc.original_filename).stem}_{counter}{Path(doc.original_filename).suffix}"
                    counter += 1
                shutil.copy2(doc.original_path, staged)
                doc.flagged_data["flagged_copy_path"] = str(staged)
    
    flagged_index = config.flagged_root / "flagged_index.json"
    with open(flagged_index, "w", encoding="utf-8") as f:
        json.dump(flagged_data, f, indent=2, ensure_ascii=False)


def save_confirmed_documents(batches: List[DivisionBatch], config: PipelineConfig) -> None:
    if not getattr(config, "flagged_root", None):
        return
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
                    "detected_date": doc.confirmed_date.isoformat() if hasattr(doc.confirmed_date, 'isoformat') else str(doc.confirmed_date),
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
    if not getattr(config, "flagged_root", None):
        return []
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

    mirror = getattr(config, "output_layout", "company") == "mirror"
    if mirror:
        # Number documents within each mirrored folder so every child folder
        # starts its own 0001, 0002, ... sequence.
        seq_by_folder: Dict[str, int] = {}
        for doc in confirmed_docs:
            n = seq_by_folder.get(doc.rel_dir, config.sequence_start - 1) + 1
            seq_by_folder[doc.rel_dir] = n
            doc.sequence_number = n
        doc_pairs = [(seq_by_folder[d.rel_dir], d) for d in confirmed_docs]
    else:
        doc_pairs = list(enumerate(confirmed_docs, start=config.sequence_start))

    for i, doc in doc_pairs:
        try:
            doc.sequence_number = i

            if hasattr(doc.confirmed_date, 'strftime'):
                yyyymm = doc.confirmed_date.strftime("%Y%m")
            else:
                yyyymm = str(doc.confirmed_date)[:7].replace('-', '')
            seq = f"{i:04d}"
            div = doc.division_code
            company = sanitize_filename(doc.company_name)

            doc.final_filename = f"{yyyymm}{seq}_{div}_{company}.pdf"

            if mirror:
                output_div_dir = mirrored_output_dir(config, doc, doc.rel_dir)
            elif config.keep_input_structure:
                try:
                    path_for_rel = getattr(doc, '_original_input_path', doc.original_path)
                    rel = Path(path_for_rel).relative_to(config.input_root)
                    parts = rel.parts
                    subfolder = parts[1] if len(parts) > 2 else ""
                except (ValueError, IndexError):
                    subfolder = ""
                if subfolder:
                    output_div_dir = config.output_root / doc.division_code / sanitize_filename(subfolder)
                else:
                    output_div_dir = config.output_root / doc.division_code
            else:
                output_div_dir = config.output_root / doc.division_code / company
            output_div_dir.mkdir(parents=True, exist_ok=True)

            output_path = output_div_dir / doc.final_filename

            counter = 1
            original_output_path = output_path
            while output_path.exists():
                stem = original_output_path.stem
                output_path = original_output_path.parent / f"{stem}_{counter}{original_output_path.suffix}"
                counter += 1

            pending_bytes = getattr(doc, '_pending_pdf_bytes', None)
            if pending_bytes:
                with open(str(output_path), "wb") as f:
                    f.write(pending_bytes)
            else:
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

            if log_writer:
                log_writer.writerow({
                    "timestamp": datetime.now().isoformat(),
                    "original_path": doc.original_path,
                    "new_filename": output_path.name,
                    "new_path": str(output_path),
                    "division_code": doc.division_code,
                    "company_name": doc.company_name,
                    "document_date": doc.confirmed_date.isoformat() if hasattr(doc.confirmed_date, 'isoformat') else str(doc.confirmed_date),
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
            if log_writer:
                try:
                    log_writer.writerow({
                        "timestamp": datetime.now().isoformat(),
                        "original_path": doc.original_path,
                        "new_filename": "",
                        "new_path": "",
                        "division_code": doc.division_code,
                        "company_name": doc.company_name,
                        "document_date": doc.confirmed_date.isoformat() if hasattr(doc.confirmed_date, 'isoformat') else str(doc.confirmed_date) if doc.confirmed_date else "",
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
    batches = parse_folder_structure(config.input_root, config.division_code)
    print(f"Found {len(batches)} division batch(es) with documents")
    
    total_docs = sum(len(b.documents) for b in batches)
    print(f"Total documents: {total_docs}")
    
    if progress_callback:
        progress_callback({"stage": "extracting", "message": f"Extracting dates from {total_docs} documents...", "progress": 10})
    
    print("Extracting dates from documents...")
    for i, batch in enumerate(batches):
        if progress_callback:
            progress_callback({"stage": "extracting", "message": f"Processing division {batch.division_code}...", "progress": 10 + int(70 * i / max(1, len(batches)))})
        extract_dates_for_batch(batch, config)
        if (i + 1) % 5 == 0:
            gc.collect()
    
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

    if confirmed_count > 0:
        print("Saving confirmed documents...")
        save_confirmed_documents(batches, config)
        print(f"Confirmed documents saved to: {config.flagged_root}")
    
    if progress_callback:
        progress_callback({"stage": "complete", "message": f"Done: {confirmed_count} confirmed, {flagged_count} flagged", "progress": 100})
    
    return batches


def finalize_all_divisions(batches: List[DivisionBatch], config: PipelineConfig) -> None:
    for batch in batches:
        finalize_division(batch, config, None)
    print("Finalization complete.")


def move_confirmed_to_passed(config: PipelineConfig, batches: List[DivisionBatch]) -> None:
    passed_root = config.output_root / "passed"
    passed_root.mkdir(parents=True, exist_ok=True)
    
    for batch in batches:
        for doc in batch.documents:
            if doc.status == "confirmed" and doc.final_filename:
                src = config.output_root / doc.division_code / sanitize_filename(doc.company_name) / doc.final_filename
                if src.exists():
                    dst = passed_root / doc.division_code / doc.company_name / doc.final_filename
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dst))
    
    print(f"Confirmed documents moved to: {passed_root}")


def _next_folder_sequence(output_root: Path) -> int:
    """
    Compute the next continuous sequence number for the flat output folder,
    continuing from the highest existing NNNN found across all files matching
    the YYYYMM####_ filename prefix (regardless of division/company/month).
    """
    max_seq = 0
    if output_root.exists():
        try:
            for p in output_root.glob("*.pdf"):
                m = re.match(r"\d{6}(\d{4})_", p.name)
                if m:
                    max_seq = max(max_seq, int(m.group(1)))
        except OSError:
            pass
    return max_seq + 1


def finalize_single_document(doc_data: dict, config: PipelineConfig) -> dict:
    from datetime import datetime as _dt
    output_root = Path(config.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    original_path = doc_data.get("original_path", "")
    # Normalize here as well as in PipelineConfig: doc dicts carry the raw text
    # the user typed on the Dashboard, and the filename must use the same padded
    # form as the output folder name.
    division_code = normalize_division_code((doc_data.get("division_code") or "").strip())
    company_name = doc_data.get("company_name", "")
    detected_date = doc_data.get("detected_date", "")
    blank_pages = doc_data.get("blank_pages", [])
    docsep_pages = doc_data.get("docsep_pages", [])
    pending_bytes = doc_data.get("_pending_pdf_bytes", None)

    # The company name is resolved from the PDF text, not from the source folder,
    # so it can legitimately still be empty here. Refuse rather than emit a
    # malformed name like "2024010002154___.pdf".
    if not company_name.strip():
        return {"success": False, "error": "Company name is empty - enter a company name before finalizing"}

    try:
        parts = detected_date.split("-")
        yyyymm = f"{parts[0]}{parts[1]}"
    except (ValueError, IndexError):
        yyyymm = _dt.now().strftime("%Y%m")

    company_dir = sanitize_filename(company_name)
    layout = getattr(config, "output_layout", "company")
    if layout == "mirror":
        # Recreate the input tree: <parent> output/<child>/.../<file>.pdf
        output_div_dir = mirrored_output_dir(config, original_path, doc_data.get("rel_dir", ""))
        flat_glob = output_div_dir.glob(f"{yyyymm}*_{division_code}_{company_dir}.pdf")
        seq = len(list(flat_glob)) + 1
    elif layout == "flat":
        output_div_dir = output_root
        seq = _next_folder_sequence(output_root)
    else:
        output_div_dir = output_root / division_code / company_dir
        flat_glob = output_div_dir.glob(f"{yyyymm}*_{division_code}_{company_dir}.pdf")
        seq = len(list(flat_glob)) + 1
    output_div_dir.mkdir(parents=True, exist_ok=True)
    final_filename = f"{yyyymm}{seq:04d}_{division_code}_{company_dir}"
    if doc_data.get("is_duplicate"):
        final_filename += doc_data.get("duplicate_suffix", "")
    final_filename += ".pdf"
    
    output_path = output_div_dir / final_filename

    counter = 1
    original_output_path = output_path
    while output_path.exists():
        stem = original_output_path.stem
        output_path = original_output_path.parent / f"{stem}_{counter}{original_output_path.suffix}"
        counter += 1

    if pending_bytes:
        with open(str(output_path), "wb") as f:
            f.write(pending_bytes)
    elif original_path and os.path.exists(original_path):
        shutil.copy2(original_path, output_path)
    else:
        return {"success": False, "error": "Source file not found"}

    blank_removed_count = 0
    docsep_removed_count = 0
    all_remove = set()
    if config.enable_blank_removal and blank_pages:
        all_remove.update(blank_pages)
    if config.enable_docsep_removal and docsep_pages:
        all_remove.update(docsep_pages)

    if all_remove:
        import fitz as _fitz
        d = _fitz.open(str(output_path))
        for pn in reversed(sorted(all_remove)):
            if pn < len(d):
                d.delete_page(pn)
                if pn in blank_pages:
                    blank_removed_count += 1
                if pn in docsep_pages:
                    docsep_removed_count += 1
        if blank_removed_count > 0 or docsep_removed_count > 0:
            import tempfile
            tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
            tmp.close()
            d.save(tmp.name, incremental=False, garbage=4, deflate=True)
            d.close()
            shutil.move(tmp.name, str(output_path))
        else:
            d.close()

    return {
        "success": True,
        "final_filename": output_path.name,
        "output_path": str(output_path),
        "blank_removed": blank_removed_count,
        "docsep_removed": docsep_removed_count,
    }


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
        parser.add_argument("--ocr-engine", type=str, default="tesseract", choices=["tesseract"], help="OCR engine to use")
        
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