"""The extension's options controller: curated intent groups and clickable color pots."""
from PySide6 import QtCore, QtGui, QtWidgets
import fx

from .colors import GROUPS, LABELS, DEFAULTS, PREF, encode, mappings


class IntentGroupColorsOptions(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.mapping = mappings() or {}

        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(
            "Changes apply immediately to new nodes. Use Node > Update Colors for selected nodes.\n"
            "The first recognized intent tag chooses the group. Unmapped groups keep the existing color.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter groups…")
        self.search.textChanged.connect(self.filter_rows)
        layout.addWidget(self.search)

        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Intent Group", "Color"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        layout.addWidget(self.table)

        buttons = QtWidgets.QHBoxLayout()
        for text, callback in (("Clear Selected", self.clear_selected),
                               ("Restore Defaults", self.restore_defaults)):
            button = QtWidgets.QPushButton(text)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.setMinimumSize(560, 440)
        self.populate()

    def save(self):
        # A panel can remain open while the extension is disabled.
        if PREF in fx.prefs.keys():
            fx.prefs[PREF] = encode(self.mapping)

    def populate(self):
        self.table.setRowCount(0)
        for group, label, tags, _default in GROUPS:
            row = self.table.rowCount()
            self.table.insertRow(row)
            item = QtWidgets.QTableWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, group)
            item.setToolTip("Intent keywords: " + ", ".join(tags))
            self.table.setItem(row, 0, item)
            pot = QtWidgets.QPushButton()
            pot.setToolTip("Click to choose a color. Clear Selected removes this group's mapping.")
            pot.clicked.connect(lambda checked=False, name=group: self.choose_color(name))
            color = self.mapping.get(group)
            if color:
                swatch = QtGui.QPixmap(32, 16)
                swatch.fill(QtGui.QColor(color))
                pot.setIcon(QtGui.QIcon(swatch))
                pot.setText(color)
            else:
                pot.setText("Default (gray)")
            self.table.setCellWidget(row, 1, pot)
        self.filter_rows(self.search.text())

    def filter_rows(self, text):
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, text.casefold() not in self.table.item(row, 0).text().casefold())

    def choose_color(self, group):
        color = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(self.mapping.get(group, "#808080")), self, LABELS[group])
        if color.isValid():
            self.mapping[group] = color.name()
            self.save()
            self.populate()

    def clear_selected(self):
        for index in self.table.selectionModel().selectedRows():
            self.mapping.pop(self.table.item(index.row(), 0).data(QtCore.Qt.UserRole), None)
        self.save()
        self.populate()

    def restore_defaults(self):
        self.mapping = dict(DEFAULTS)
        self.save()
        self.populate()
