import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import company_resolver as cr
from company_resolver import CompanyCandidate, combine, resolve_company


ROSTER = [
    "NEW ZEALAND INSURANCE CORPORATION",
    "SEMIRARA COAL CORPORATION",
    "MAA GENERAL ASSURANCE PHILS INC",
]

LETTERHEAD_TEXT = """
THE
NEW ZEALAND INSURANCE COMPANY LIMITED
(INCORPORATED IN NEW ZEALAND)
MANILA BRANCH ESTABLISHED 1859
Office of the Insurance Commissioner,
Insurance Commission,
Dear Sirs,
SEMIRARA COAL CORPORATION
Further to our letter of the 23rd July, 1980 it is confirmed we wish to invest.
Yours faithfully,
C. R. Ayers
Regional Manager
"""


def _pages(text):
    return [{"page_text": text, "page_idx": 0}]


# ── gazetteer ────────────────────────────────────────────────

def test_gazetteer_matches_present_roster_entry():
    cands = cr.detect_roster_gazetteer(_pages("We refer to MAA GENERAL ASSURANCE PHILS INC."), ROSTER)
    assert any("MAA GENERAL ASSURANCE PHILS INC" in c.name for c in cands)


def test_gazetteer_absent_entry_returns_none():
    cands = cr.detect_roster_gazetteer(_pages("Nothing relevant here at all."), ROSTER)
    assert cands == []


# ── folder hint (weak) ───────────────────────────────────────

def test_folder_hint_matches_roster_folder():
    ctx = {"original_path": "C:/x/_staging/143/NEW ZEALAND INSURANCE CORPORATION/0004.pdf"}
    cands = cr.detect_folder_hint(ctx, ROSTER)
    assert len(cands) == 1
    assert cands[0].source == "folder"
    assert cands[0].weight <= 0.5


def test_folder_hint_alone_does_not_auto_confirm():
    cands = [CompanyCandidate("NEW ZEALAND INSURANCE CORPORATION", "folder", 0.4)]
    res = combine(cands, ROSTER)
    assert res["matched_roster_entry"] == "NEW ZEALAND INSURANCE CORPORATION"
    assert res["company_confidence"] < 90
    assert res["needs_review"] is True


# ── combiner corroboration ───────────────────────────────────

def test_two_sources_agreeing_reaches_autoconfirm():
    cands = [
        CompanyCandidate("NEW ZEALAND INSURANCE COMPANY LIMITED", "letterhead", 0.85),
        CompanyCandidate("NEW ZEALAND INSURANCE CORPORATION", "gazetteer", 0.9),
    ]
    res = combine(cands, ROSTER)
    assert res["matched_roster_entry"] == "NEW ZEALAND INSURANCE CORPORATION"
    assert res["company_confidence"] >= 90
    assert res["needs_review"] is False


def test_no_roster_match_needs_review():
    cands = [CompanyCandidate("TOTALLY UNKNOWN VENTURES", "letterhead", 0.85)]
    res = combine(cands, ROSTER)
    assert res["matched_roster_entry"] is None
    assert res["needs_review"] is True
    assert res["company_confidence"] == 0


# ── letterhead / addressee / body mention ────────────────────

def test_letterhead_detected_and_used():
    cands = cr.detect_letterhead(_pages(LETTERHEAD_TEXT))
    assert any("NEW ZEALAND INSURANCE" in c.name for c in cands)


def test_body_mention_does_not_beat_letterhead():
    res = resolve_company(_pages(LETTERHEAD_TEXT), roster=ROSTER)
    assert res["matched_roster_entry"] == "NEW ZEALAND INSURANCE CORPORATION"


def test_addressee_alone_is_rejected():
    text = "Office of the Insurance Commissioner,\nInsurance Commission,\nDear Sirs,\n"
    cands = cr.detect_letterhead(_pages(text))
    assert cands == []


def test_signature_block_detects_sender_company():
    text = "Please act on this.\nVery truly yours,\nMAA GENERAL ASSURANCE PHILS INC\nManager\n"
    cands = cr.detect_signature_block(_pages(text))
    assert any("MAA GENERAL ASSURANCE" in c.name for c in cands)


def test_full_resolution_picks_letterhead_over_body_mention():
    res = resolve_company(
        _pages(LETTERHEAD_TEXT),
        context={"original_path": "C:/x/_staging/143/NEW ZEALAND INSURANCE CORPORATION/0004.pdf"},
        roster=ROSTER,
    )
    assert res["matched_roster_entry"] == "NEW ZEALAND INSURANCE CORPORATION"
    assert res["company_confidence"] >= 90


# ── suggestion fallback (company not yet in roster) ──────────

def test_no_roster_match_suggests_folder_name():
    res = resolve_company(
        _pages("some body text without a known company"),
        context={"original_path": "C:/x/NEW ZEALAND INSURANCE CORPORATION/1.pdf"},
        roster=["SOME OTHER CO"],
    )
    assert res["company_name"] == "NEW ZEALAND INSURANCE CORPORATION"
    assert res["matched_roster_entry"] is None
    assert res["needs_review"] is True


# ── learning store ───────────────────────────────────────────

def test_learned_folder_hint_is_used(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "HINTS_FILE", tmp_path / "company_hints.json")
    cr.record_folder_hint("ACME INSURANCE CORP", "ACME INSURANCE CORP")
    ctx = {"original_path": "C:/x/ACME INSURANCE CORP/doc.pdf"}
    cands = cr.detect_learned_hint(ctx, _pages("nothing"), ["ACME INSURANCE CORP"])
    assert any(c.source == "learned" for c in cands)


def test_learned_folder_hint_autoconfirms(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "HINTS_FILE", tmp_path / "company_hints.json")
    cr.record_folder_hint("ACME INSURANCE CORP", "ACME INSURANCE CORP")
    res = resolve_company(
        _pages("nothing useful"),
        context={"original_path": "C:/x/ACME INSURANCE CORP/doc.pdf"},
        roster=["ACME INSURANCE CORP"],
    )
    assert res["matched_roster_entry"] == "ACME INSURANCE CORP"
    assert res["needs_review"] is False


# ── auto roster add on confirm ───────────────────────────────

def test_learn_company_hints_adds_new_company_once(tmp_path, monkeypatch):
    import company_extractor as ce
    monkeypatch.setattr(ce, "KNOWN_COMPANIES_FILE", tmp_path / "known_companies.json")
    monkeypatch.setattr(cr, "HINTS_FILE", tmp_path / "company_hints.json")

    from ui.organize_dialog import _ensure_qapp
    _ensure_qapp()
    from ui.review_tab import ReviewTab

    rt = ReviewTab({"input_root": str(tmp_path), "output_root": str(tmp_path / "_out"),
                    "flagged_root": "", "theme": "Dark Mode"})
    doc = {"company_name": "NEW ZEALAND INSURANCE CORPORATION",
           "original_path": str(tmp_path / "NEW ZEALAND INSURANCE CORPORATION" / "1.pdf")}

    rt._learn_company_hints(doc)
    assert "NEW ZEALAND INSURANCE CORPORATION" in ce.load_known_companies()

    # Re-confirming the same company must not duplicate it.
    rt._learn_company_hints(doc)
    assert ce.load_known_companies().count("NEW ZEALAND INSURANCE CORPORATION") == 1


