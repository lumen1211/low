from __future__ import annotations
from typing import List, Dict
from PySide6.QtWidgets import QDialog, QVBoxLayout, QListWidget, QListWidgetItem, QDialogButtonBox
from PySide6.QtCore import Qt


class CampaignSettingsDialog(QDialog):
    def __init__(self, campaigns: List[Dict[str, str]], selected: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Campaigns")
        v = QVBoxLayout(self)
        self.list = QListWidget()
        for c in campaigns:
            text = f"{c.get('name', c.get('id'))} ({c.get('game', '-')})"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, c.get("id"))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if c.get("id") in selected else Qt.Unchecked)
            self.list.addItem(item)
        v.addWidget(self.list)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def selected(self) -> List[str]:
        ids: List[str] = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == Qt.Checked:
                ids.append(item.data(Qt.UserRole))
        return ids
