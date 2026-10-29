import pytest
from PyQt6.QtWidgets import QApplication
from ui.update_dialog import UpdateDialog

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])

def test_update_dialog_init(qapp):
    dialog = UpdateDialog(
        new_version="v1.9.5",
        release_notes="Feature 1\nFeature 2",
        download_url="https://example.com/setup.exe"
    )
    assert dialog.notes_edit.toPlainText() == "Feature 1\nFeature 2"
    assert dialog.update_btn.text() == "Update Now"
    assert dialog.new_version == "v1.9.5"
