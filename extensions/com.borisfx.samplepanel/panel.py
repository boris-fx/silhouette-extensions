"""Sample dockable panel contributed by an extension via fx.registerPanel.

This module self-registers on import (the autoload loader imports every public
top-level .py).  Because the extension loader sets the "currently loading
extension" around the import, the panel is tagged with this extension's id and
is torn down automatically when the extension is disabled or uninstalled.
"""
import fx
from PySide6 import QtWidgets

PANEL_ID = "com.borisfx.samplepanel.hello"


def make_panel():
	"""Build the panel's content widget (a plain PySide6 QWidget)."""
	w = QtWidgets.QWidget()
	layout = QtWidgets.QVBoxLayout(w)
	layout.addWidget(QtWidgets.QLabel("Hello from the Sample Panel extension!"))

	count = {"n": 0}
	button = QtWidgets.QPushButton("Click me")

	def on_click():
		count["n"] += 1
		button.setText(f"Clicked {count['n']}x")

	button.clicked.connect(on_click)
	layout.addWidget(button)
	layout.addStretch(1)
	return w


# Re-importing (extension reload / re-enable) re-runs this module, so drop any
# previous registration first to stay idempotent — unregisterPanel is a no-op
# when the id isn't registered.  id is the stable persistence key; the title is
# display-only.
fx.unregisterPanel(PANEL_ID)
fx.registerPanel(PANEL_ID, "Sample Panel", make_panel, area="right", sort=100)
