import pytest
from pathlib import Path
from PyQt6.QtWidgets import QApplication, QTreeWidgetItem
from ui.review_tab import ReviewTab

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])

def test_populate_reviewed_tree_preserves_scroll_and_selection(qapp, tmp_path):
    tab = ReviewTab(config={"theme": "Dark", "output_root": str(tmp_path)})
    tab.resize(1000, 600)
    tab.show()
    qapp.processEvents()

    docs = [{
        "original_path": str(tmp_path / f"doc_{i}.pdf"),
        "original_filename": f"doc_{i}.pdf",
        "division_code": f"DIV_{i // 5}",
        "company_name": f"Company_{i // 5}",
    } for i in range(40)]
    tab.reviewed_docs = list(docs)
    tab.active_tab = "reviewed"
    tab._populate_reviewed_tree(docs)
    qapp.processEvents()

    assert len(tab.reviewed_index_to_item) == 40
    # Select doc 25 and scroll tree
    item = tab.reviewed_index_to_item[25]
    tab.reviewed_tree.setCurrentItem(item)
    tab.active_index = 25
    tab.reviewed_selected_indices = [25]
    tab.reviewed_tree.verticalScrollBar().setValue(40)
    scroll_val = tab.reviewed_tree.verticalScrollBar().value()

    # Re-populating the tree (e.g. after scan / edit / organize in reviewed tab)
    tab._populate_reviewed_tree(docs)
    qapp.processEvents()

    # Must preserve selected item and scroll position!
    current = tab.reviewed_tree.currentItem()
    assert current is not None
    assert current.data(0, 0x0100) == 25  # Qt.ItemDataRole.UserRole
    assert tab.active_index == 25
    assert tab.reviewed_tree.verticalScrollBar().value() == scroll_val
