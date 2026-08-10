from fx import *

# Both actions use root="Test" (no '|' in the label, so no submenu) and are
# neither hidden nor object-bound — so they're menubar actions.  Their root
# names a top-level menu, which the menubar creates on load and removes when
# the last one goes away on unload.


class TestSayHello(Action):
	"""Say hello from the Test menu."""

	def __init__(self):
		Action.__init__(self, "Say Hello", root="Test", order=1)

	def execute(self):
		print(f"[{self.extension}] Hello from the Test menu")


class TestSayGoodbye(Action):
	"""Say goodbye from the Test menu."""

	def __init__(self):
		Action.__init__(self, "Say Goodbye", root="Test", order=2)

	def execute(self):
		print(f"[{self.extension}] Goodbye from the Test menu")


class TestTreesAction(Action):
	"""Context action shown in the Trees (node graph) view popup.

	root="Trees" places it in that view's context menu; hidden=True keeps it
	out of the menubar.  The Trees menu rebuilds on open, so this appears as
	soon as the extension is (re)loaded — no menu teardown needed.
	"""

	def __init__(self):
		Action.__init__(self, "Test Trees Action", root="Trees", hidden=True)

	def execute(self):
		print(f"[{self.extension}] Test Trees Action — active node: {activeNode()}")


addAction(TestSayHello())
addAction(TestSayGoodbye())
addAction(TestTreesAction())
