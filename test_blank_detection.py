"""
Blank Page Detection Test Script
Visualizes detection results on PDFs for threshold tuning.
"""

import os
import sys
import csv
import json
import argparse
from pathlib import Path
from datetime import datetime

import cv2
import numpy as np
import fitz
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from blank_page_detector import detect_blank_page


def pdf_page_to_image(pdf_path: str, page_num: int, dpi: int = 200):
    """Convert a PDF page to numpy array."""
    try:
        doc = fitz.open(pdf_path)
        if page_num >= len(doc):
            doc.close()
            return None
        page = doc[page_num]
        pix = page.get_pixmap(dpi=dpi)
        img_data = pix.tobytes("png")
        img = Image.open(__import__("io").BytesIO(img_data))
        img_np = np.array(img)
        if len(img_np.shape) == 3:
            if img_np.shape[2] == 4:
                img_np = cv2.cvtColor(img_np, cv2.COLOR_RGBA2BGR)
            else:
                img_np = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        doc.close()
        return img_np
    except Exception as e:
        print(f"Error reading {pdf_path} page {page_num}: {e}")
        return None


def create_annotated_image(image: np.ndarray, result: dict, page_num: int, total_pages: int):
    """Create annotated image with detection overlay."""
    annotated = image.copy()
    h, w = annotated.shape[:2]

    # Determine border color based on classification
    if result["is_blank"] is True:
        border_color = (0, 0, 255)  # Red
        label = "BLANK"
    elif result["is_blank"] is False:
        border_color = (0, 255, 0)  # Green
        label = "CONTENT"
    else:
        border_color = (0, 255, 255)  # Yellow
        label = "REVIEW"

    # Draw thick border
    cv2.rectangle(annotated, (10, 10), (w - 10, h - 10), border_color, 4)

    # Create semi-transparent overlay for text background
    overlay = annotated.copy()
    cv2.rectangle(overlay, (15, 15), (320, 160), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.7, annotated, 0.3, 0, annotated)

    # Add metrics text
    metrics = result.get("metrics", {})
    y_offset = 40
    line_height = 22

    texts = [
        f"Page {page_num + 1}/{total_pages}",
        f"WHITE: {metrics.get('white_ratio', 0)*100:.1f}%",
        f"CONTENT: {metrics.get('content_area', 0)*100:.2f}%",
        f"EDGES: {metrics.get('edge_ratio', 0)*100:.3f}%",
        f"STD_DEV: {metrics.get('std_dev', 0):.1f}",
        f"CLASS: {label} ({result['confidence']}%)",
    ]

    for i, text in enumerate(texts):
        cv2.putText(annotated, text, (25, y_offset + i * line_height),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

    return annotated


def process_folder(input_folder: str, output_folder: str, max_pages: int = 0):
    """Process all PDFs in folder and generate reports."""
    input_path = Path(input_folder)
    output_path = Path(output_folder)
    annotated_path = output_path / "annotated"
    annotated_path.mkdir(parents=True, exist_ok=True)

    # Find all PDFs
    pdfs = []
    for pdf_file in input_path.rglob("*.pdf"):
        rel_path = pdf_file.relative_to(input_path)
        pdfs.append((pdf_file, rel_path))

    print(f"Found {len(pdfs)} PDF files")

    # CSV output
    csv_rows = []
    csv_headers = [
        "filename", "relative_path", "page_num", "total_pages",
        "white_ratio", "content_area", "edge_ratio", "std_dev",
        "num_components", "classification", "confidence", "reason"
    ]

    summary = {"total_pages": 0, "blank": 0, "content": 0, "review": 0}

    for pdf_idx, (pdf_file, rel_path) in enumerate(pdfs):
        print(f"[{pdf_idx + 1}/{len(pdfs)}] Processing: {rel_path}")

        doc = fitz.open(str(pdf_file))
        total_pages = len(doc)
        doc.close()

        pages_to_check = total_pages if max_pages == 0 else min(total_pages, max_pages)

        for page_num in range(pages_to_check):
            img = pdf_page_to_image(str(pdf_file), page_num, dpi=200)
            if img is None:
                continue

            # Run detection with debug
            result = detect_blank_page(img, debug=True)

            # Classify
            classification = "BLANK" if result["is_blank"] is True else (
                "NOT_BLANK" if result["is_blank"] is False else "NEEDS_REVIEW"
            )

            # Update summary
            summary["total_pages"] += 1
            if classification == "BLANK":
                summary["blank"] += 1
            elif classification == "NOT_BLANK":
                summary["content"] += 1
            else:
                summary["review"] += 1

            # CSV row
            metrics = result.get("metrics", {})
            csv_rows.append({
                "filename": pdf_file.name,
                "relative_path": str(rel_path),
                "page_num": page_num + 1,
                "total_pages": total_pages,
                "white_ratio": f"{metrics.get('white_ratio', 0):.4f}",
                "content_area": f"{metrics.get('content_area', 0):.6f}",
                "edge_ratio": f"{metrics.get('edge_ratio', 0):.6f}",
                "std_dev": f"{metrics.get('std_dev', 0):.2f}",
                "num_components": metrics.get("num_components", 0),
                "classification": classification,
                "confidence": result["confidence"],
                "reason": result["reason"],
            })

            # Save annotated image
            annotated = create_annotated_image(img, result, page_num, total_pages)
            ann_filename = f"{rel_path.parent}_{pdf_file.stem}_p{page_num + 1}.png"
            ann_filename = ann_filename.replace("/", "_").replace("\\", "_")
            cv2.imwrite(str(annotated_path / ann_filename), annotated)

    # Write CSV
    csv_path = output_path / "summary.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_headers)
        writer.writeheader()
        writer.writerows(csv_rows)
    print(f"\nCSV saved: {csv_path}")

    # Write HTML report
    html_path = output_path / "report.html"
    write_html_report(html_path, csv_rows, summary, annotated_path)

    # Print summary
    print(f"\n{'='*50}")
    print(f"SUMMARY")
    print(f"{'='*50}")
    print(f"Total pages analyzed: {summary['total_pages']}")
    print(f"BLANK: {summary['blank']} ({summary['blank']/max(1,summary['total_pages'])*100:.1f}%)")
    print(f"NOT_BLANK: {summary['content']} ({summary['content']/max(1,summary['total_pages'])*100:.1f}%)")
    print(f"NEEDS_REVIEW: {summary['review']} ({summary['review']/max(1,summary['total_pages'])*100:.1f}%)")
    print(f"\nAnnotated images: {annotated_path}")
    print(f"HTML report: {html_path}")


def write_html_report(html_path: Path, rows: list, summary: dict, annotated_path: Path):
    """Generate HTML report with thumbnails."""
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Blank Page Detection Report</title>
    <style>
        body {{ font-family: 'Segoe UI', sans-serif; background: #1a1b2e; color: #e0e0f0; margin: 20px; }}
        h1 {{ color: #4a6fa5; }}
        .summary {{ background: #252640; padding: 15px; border-radius: 8px; margin-bottom: 20px; }}
        .summary span {{ margin-right: 20px; font-weight: bold; }}
        .blank {{ color: #ff5555; }}
        .content {{ color: #55ff55; }}
        .review {{ color: #ffff55; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #2d2e45; padding: 8px; text-align: left; }}
        th {{ background: #252640; }}
        tr:nth-child(even) {{ background: #1e1f35; }}
        tr:hover {{ background: #2d3055; }}
        img {{ max-width: 200px; border-radius: 4px; }}
    </style>
</head>
<body>
    <h1>Blank Page Detection Report</h1>
    <div class="summary">
        <span>Total: {summary['total_pages']}</span>
        <span class="blank">BLANK: {summary['blank']}</span>
        <span class="content">NOT_BLANK: {summary['content']}</span>
        <span class="review">NEEDS_REVIEW: {summary['review']}</span>
    </div>
    <table>
        <tr>
            <th>Preview</th>
            <th>File</th>
            <th>Page</th>
            <th>White %</th>
            <th>Content %</th>
            <th>Edges %</th>
            <th>StdDev</th>
            <th>Classification</th>
            <th>Confidence</th>
        </tr>
"""
    for row in rows:
        ann_filename = f"{row['relative_path']}_{row['filename']}_p{row['page_num']}.png"
        ann_filename = ann_filename.replace("/", "_").replace("\\", "_")
        color_class = "blank" if row["classification"] == "BLANK" else (
            "content" if row["classification"] == "NOT_BLANK" else "review"
        )
        html += f"""        <tr>
            <td><img src="annotated/{ann_filename}" alt="preview"></td>
            <td>{row['filename']}</td>
            <td>{row['page_num']}/{row['total_pages']}</td>
            <td>{float(row['white_ratio'])*100:.1f}%</td>
            <td>{float(row['content_area'])*100:.2f}%</td>
            <td>{float(row['edge_ratio'])*100:.3f}%</td>
            <td>{row['std_dev']}</td>
            <td class="{color_class}">{row['classification']}</td>
            <td>{row['confidence']}%</td>
        </tr>
"""
    html += """    </table>
</body>
</html>"""

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test blank page detection on PDFs")
    parser.add_argument("input_folder", help="Folder containing PDFs")
    parser.add_argument("--output", "-o", default="test_output/blank_detection", help="Output folder")
    parser.add_argument("--max-pages", "-m", type=int, default=0, help="Max pages per PDF (0=all)")

    args = parser.parse_args()

    if not os.path.isdir(args.input_folder):
        print(f"Error: Input folder not found: {args.input_folder}")
        sys.exit(1)

    process_folder(args.input_folder, args.output, args.max_pages)
