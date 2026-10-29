import pytest
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from ui.review_tab import ReviewTab

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])

def test_on_candidate_selected_fuzzy_matches_roster(qapp, tmp_path):
    tab = ReviewTab(config={"theme": "Dark", "flagged_root": str(tmp_path)})
    tab.company_input.setText("")

    # 1. OCR text is slightly faded/scrambled: "PHILIPINE BRITISH ASSUR CO INC"
    # Official roster contains: "PHILIPPINE BRITISH ASSURANCE CO INC"
    tab._on_candidate_selected("PHILIPINE BRITISH ASSUR CO INC")

    # Should fuzzy match roster and fill official clean name
    assert tab.company_input.text() == "PHILIPPINE BRITISH ASSURANCE CO INC"

def test_on_candidate_selected_falls_back_to_raw_when_no_match(qapp, tmp_path):
    tab = ReviewTab(config={"theme": "Dark", "flagged_root": str(tmp_path)})
    tab.company_input.setText("")

    # Non-roster company
    tab._on_candidate_selected("RANDOM NONEXISTENT CORP 999")
    assert tab.company_input.text() == "RANDOM NONEXISTENT CORP 999"

def test_page_sort_reorder_calculation(qapp, tmp_path):
    tab = ReviewTab(config={"theme": "Dark", "flagged_root": str(tmp_path)})
    # Test sort reorder calculation logic for both forward and backward page drag
    initial_order = [0, 1, 2, 3, 4]

    # Move page 0 to after page 3 -> should become [1, 2, 3, 0, 4]
    new_order = tab._calculate_new_sort_order(initial_order, dragged_slot=0, target_slot=3, where="after")
    assert new_order == [1, 2, 3, 0, 4]

    # Move page 4 to before page 1 -> should become [0, 4, 1, 2, 3]
    new_order2 = tab._calculate_new_sort_order(initial_order, dragged_slot=4, target_slot=1, where="before")
    assert new_order2 == [0, 4, 1, 2, 3]
