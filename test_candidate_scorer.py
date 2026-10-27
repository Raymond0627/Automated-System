import pytest
from candidate_scorer import extract_company_candidates, find_line_at_point

def test_extract_company_candidates_scores_header_keywords():
    words = [
        {"text": "PHILIPPINE", "bbox": [50, 60, 150, 75], "conf": 95},
        {"text": "BRITISH", "bbox": [155, 60, 220, 75], "conf": 95},
        {"text": "ASSURANCE", "bbox": [225, 60, 320, 75], "conf": 95},
        {"text": "CORP", "bbox": [325, 60, 370, 75], "conf": 95},
        {"text": "Case", "bbox": [50, 300, 90, 312], "conf": 90},
        {"text": "No.", "bbox": [95, 300, 115, 312], "conf": 90},
    ]
    candidates = extract_company_candidates(
        words,
        page_rect=(0, 0, 612, 792),
        roster=["PHILIPPINE BRITISH ASSURANCE CORP"]
    )
    assert len(candidates) > 0
    top = candidates[0]
    assert "ASSURANCE" in top["text"]
    assert top["badge"] == 1
    assert top["is_high_conf"] is True

def test_find_line_at_point():
    words = [
        {"text": "HELLO", "bbox": [10, 10, 50, 25], "conf": 90},
        {"text": "WORLD", "bbox": [55, 10, 100, 25], "conf": 90},
    ]
    line = find_line_at_point((30, 15), words)
    assert line == "HELLO WORLD"
