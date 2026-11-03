"""Text generator/overlay using the same rich-text engine as the note editor."""

import math

from tools.ensure import ensure

import fx
from fx import Channel_RGBA, Color, Depth_Float32, NodeProxy, Point, Rect, Recti


class TextNode(NodeProxy):
	"""Generate titles, labels, credits and burn-ins with rich-text formatting.

	Use the note editor for fonts, sizes, styles and per-character colours. Anchor
	text on a viewer point, or wrap it inside a fixed session-coordinate box.
	An optional image input supplies the background; Text Only renders RGBA text
	and its drop shadow over transparency.
	"""

	TYPE = "Text"

	_DEFAULT = ('<!DOCTYPE HTML><html><head><meta name="qrichtext" content="1" /></head>'
				'<body style="font-family:Arial; font-size:64pt; color:#ffffff;">'
				'<p style="margin:0;">Text</p></body></html>')

	def __init__(self, *args, **kwargs):
		self.input = self.addInput("input", "Input", aux=True)
		self.output = self.addOutput("output", "Output", cache=True)
		self.message = self.addProperty("message", {
			"label": "Text", "type": "note", "value": self._DEFAULT,
			"tooltip": "Use Formatted Text to edit fonts, sizes, styles and colors."})
		self.fontScale = self.addProperty("fontScale", {
			"label": "Global Font Scale", "value": 1.0, "range": [0.001, 100.0],
			"displayRange": [0.1, 4.0],
			"tooltip": "Scale every character's font size, including rich text."})
		self.opacity = self.addProperty("opacity", {
			"label": "Opacity", "value": 1.0, "range": [0.0, 1.0]})
		self.premultiply = self.addProperty("premultiply", {
			"label": "Premultiply", "value": True,
			"tooltip": "Multiply output RGB by alpha. Disable for straight RGBA output."})
		self.textOnly = self.addProperty("textOnly", {
			"label": "Text Only", "value": False,
			"tooltip": "Output text and its shadow over transparency."})

		self.addProperty("layout", {"label": "Layout", "type": "group"})
		self.mode = self.addProperty("mode", {
			"label": "Mode", "value": 0, "items": ["Position", "Box"],
			"group": "layout", "constant": True, "supervise": True})
		self.box = self.addProperty("box", {
			"label": "Box", "type": "Rect", "rect_type": "world", "group": "layout",
			"value": Rect(-0.25, -0.25, 0.5, 0.5), "min": -1000.0, "max": 1000.0,
			"display_min": -2.0, "display_max": 2.0,
			"tooltip": "Fixed rectangle in session world coordinates, displayed in pixels. Position does not move this box."})
		self.xjustify = self.addProperty("xjustify", {
			"label": "Horizontal Align", "value": 1, "group": "layout",
			"items": ["Left", "Center", "Right", "Justify"]})
		self.yjustify = self.addProperty("yjustify", {
			"label": "Vertical Align", "value": 1, "group": "layout",
			"items": ["Top", "Center", "Bottom"]})
		self.wrap = self.addProperty("wrap", {
			"label": "Word Wrap", "value": True, "group": "layout"})
		self.clip = self.addProperty("clip", {
			"label": "Clip to Box", "value": False, "group": "layout"})
		self.leading = self.addProperty("leading", {
			"label": "Line Spacing", "value": 1.0, "range": [0.1, 10.0],
			"displayRange": [0.5, 3.0], "group": "layout",
			"tooltip": "Multiplier on each line's natural height."})
		self.tracking = self.addProperty("tracking", {
			"label": "Character Spacing", "value": 0.0, "range": [-1000.0, 1000.0],
			"displayRange": [-10.0, 30.0], "group": "layout",
			"tooltip": "Additional spacing in pixels before Global Font Scale."})

		self.addProperty("transform", {"label": "Transform", "type": "group"})
		self.position = self.addProperty("position", {
			"label": "Position", "value": Point(0, 0), "type": "position", "group": "transform",
			"tooltip": "Text origin in world coordinates. (0, 0) is screen center; alignment determines how the text extends from this point."})
		self.rotate = self.addProperty("rotate", {
			"label": "Rotate", "value": 0.0, "type": "angle", "group": "transform"})
		self.scale = self.addProperty("scale", {
			"label": "Scale", "value": Point(1, 1), "type": "scale", "group": "transform"})
		self.center = self.addProperty("center", {
			"label": "Center (Pivot)", "value": Point(0.5, 0.5), "type": "unit", "group": "transform",
			"tooltip": "Rotation/scale pivot within the layout, in normalized frame coordinates. (0.5, 0.5) is the layout origin; Position moves the pivot with the text."})

		self.addProperty("shadow", {"label": "Drop Shadow", "type": "group"})
		self.shadowEnabled = self.addProperty("shadowEnabled", {
			"label": "Enable", "value": False, "group": "shadow", "supervise": True})
		self.shadowColor = self.addProperty("shadowColor", {
			"label": "Color", "value": Color(0, 0, 0, 0.7), "group": "shadow"})
		self.shadowX = self.addProperty("shadowX", {
			"label": "Offset X", "value": 5.0, "range": [-10000.0, 10000.0],
			"displayRange": [-100.0, 100.0], "group": "shadow"})
		self.shadowY = self.addProperty("shadowY", {
			"label": "Offset Y", "value": 5.0, "range": [-10000.0, 10000.0],
			"displayRange": [-100.0, 100.0], "group": "shadow"})
		self.shadowSoftness = self.addProperty("shadowSoftness", {
			"label": "Softness", "value": 0.0, "range": [0.0, 1000.0],
			"displayRange": [0.0, 50.0], "group": "shadow"})

	def setup(self):
		# Node setup runs on the main thread; first imports of compiled packages
		# must not happen on a render worker. Qt documents/images stay render-local.
		self._np = ensure("numpy")
		from PySide6 import QtCore, QtGui
		self._QtCore, self._QtGui = QtCore, QtGui

	def updatePropertyState(self):
		boxMode = int(self.mode.getValue(0)) == 1
		self.box.enabled = boxMode
		for prop in (self.position, self.rotate, self.scale, self.center):
			prop.enabled = not boxMode
		for prop in (self.shadowColor, self.shadowX, self.shadowY, self.shadowSoftness):
			prop.enabled = bool(self.shadowEnabled.getValue(0))

	def query(self, rc, options, type, port):
		# Register the background dependency, even when Text Only is enabled.
		info = dict(self.input.query(rc, options, type) or {})
		# Like Nuke's default "clip to format", output stays inside the frame.
		info.update(dod=Recti(size=rc.proxy.renderSize), channels=Channel_RGBA)
		return info

	def process(self, rc, options, type, port):
		roi = options.roi.clip(Recti(size=rc.proxy.renderSize))
		if not roi.valid:
			return None
		frame = options.frame
		image = None if self.textOnly.getValue(frame) else self.input.eval(rc, options.at(roi=roi), type)
		opacity = min(1.0, max(0.0, self.opacity.getValue(frame)))
		message = self.message.getValue(frame)
		out = self.allocate(rc, roi=roi, channels=Channel_RGBA, format=Depth_Float32)
		out.clear()
		np = self._np
		pixels = np.asarray(out)
		if message and opacity > 0 and not rc.canceled:
			QtCore, QtGui = self._QtCore, self._QtGui

			size = rc.proxy.sessionSize
			boxMode = int(self.mode.getValue(frame)) == 1
			box = (self._qrect(self.box.getValue(frame), rc.session, QtCore) if boxMode
				   else QtCore.QRectF(0, 0, size.width, size.height))
			if box.width() > 0 and box.height() > 0:
				fontScale = max(0.001, self.fontScale.getValue(frame))
				aspect = max(0.001, rc.pixelAspect)
				width = box.width() * aspect / fontScale
				height = box.height() / fontScale
				# Resolve HTML point sizes into reference pixels before layout,
				# independent of screen DPI, proxy and the current render tile.
				doc = self._document(message, frame, width, QtGui, QtCore)
				y = (height - doc.size().height()) * (0.0, 0.5, 1.0)[int(self.yjustify.getValue(frame))]
				matrix = self._matrix(rc, frame, box, fontScale, aspect, y, boxMode, QtGui)
				clip = QtCore.QRectF(0, -y, width, height) if self.clip.getValue(frame) else None

				if self.shadowEnabled.getValue(frame):
					blur = max(0.0, self.shadowSoftness.getValue(frame)) * rc.proxy.scale
					# Three box passes approximate a Gaussian. Their exact, finite
					# support supplies the halo, including outside the image format.
					radius = int((math.sqrt(1 + 4 * blur * blur) - 1) / 2)
					high = round((blur * blur - radius * (radius + 1)) * 3 / (2 * (radius + 1)))
					radii = [radius] * (3 - high) + [radius + 1] * high
					pad = sum(radii)
					work = Recti(roi.left - pad, roi.top - pad, roi.right + pad, roi.bottom + pad)
					shadow = self._paint(doc, matrix, clip, work, QtGui, QtCore,
						self.shadowX.getValue(frame) * rc.proxy.scale,
						self.shadowY.getValue(frame) * rc.proxy.scale)
					shadowRaster = self._raster(shadow, work)
					coverage = np.asarray(shadowRaster)[:, :, 3]
					for radius in radii:
						if radius:
							coverage = self._boxBlur(coverage, radius, np)
							coverage = self._boxBlur(coverage.T, radius, np).T
					c = self.shadowColor.getValue(frame)
					pixels[:, :, 3] = np.clip(coverage, 0, 1) * min(1.0, max(0.0, c.a))
					pixels[:, :, :3] = pixels[:, :, 3:4] * (c.r, c.g, c.b)

				text = self._raster(self._paint(doc, matrix, clip, roi, QtGui, QtCore), roi)
				foreground = np.asarray(text)
				pixels *= 1.0 - foreground[:, :, 3:4]
				pixels += foreground
		pixels *= opacity

		if image is not None:
			# Raster.composite() replaces the background alpha and expects straight
			# foreground RGB. Use explicit source-over for a transparent background
			# and our premultiplied text; keep input HDR values intact.
			background = self.allocate(rc, roi=roi, channels=Channel_RGBA, format=Depth_Float32)
			background.clear()
			background.copy(image, roi=roi)  # RGB -> RGBA with opaque alpha
			bg = np.asarray(background)
			remain = 1.0 - pixels[:, :, 3:4]
			pixels[:, :, :3] += bg[:, :, :3] * bg[:, :, 3:4] * remain
			pixels[:, :, 3:4] += bg[:, :, 3:4] * remain
		if not self.premultiply.getValue(frame):
			alpha = pixels[:, :, 3:4]
			np.divide(pixels[:, :, :3], alpha, out=pixels[:, :, :3], where=alpha > 0)
		return out

	@staticmethod
	def _boxBlur(values, radius, np):
		# Valid horizontal convolution in O(pixels), independent of radius.
		# Float64 sums prevent cancellation from corrupting faint shadow tails.
		width = radius * 2 + 1
		sums = np.pad(np.cumsum(values, axis=1, dtype=np.float64), ((0, 0), (1, 0)))
		return (sums[:, width:] - sums[:, :-width]) / width

	@staticmethod
	def _qrect(rect, session, QtCore):
		if not rect.valid:
			return QtCore.QRectF()
		tl = session.worldToImage(Point(rect.left, rect.top))
		br = session.worldToImage(Point(rect.right, rect.bottom))
		return QtCore.QRectF(tl.x, tl.y, br.x - tl.x, br.y - tl.y)

	@staticmethod
	def _canvas(width, height, QtGui):
		image = QtGui.QImage(width, height, QtGui.QImage.Format_RGBA32FPx4_Premultiplied)
		if image.isNull():
			raise MemoryError("Text: could not allocate the text image")
		image.setDotsPerMeterX(round(72 / 0.0254))
		image.setDotsPerMeterY(round(72 / 0.0254))
		image.fill(0)
		return image

	def _document(self, message, frame, width, QtGui, QtCore):
		doc = QtGui.QTextDocument()
		doc.setDocumentMargin(0)
		font = QtGui.QFont("Arial")
		font.setPixelSize(64)
		font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, self.tracking.getValue(frame))
		doc.setDefaultFont(font)
		opt = doc.defaultTextOption()
		opt.setUseDesignMetrics(True)
		opt.setWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere if self.wrap.getValue(frame)
						else QtGui.QTextOption.NoWrap)
		doc.setDefaultTextOption(opt)
		# This is NoteEditor::isRichText's contract; plain '<b>text</b>' must
		# stay literal when the editor's Formatted Text switch is off.
		if message.startswith("<!"):
			doc.setHtml(message)
		else:
			doc.setPlainText(message)
		cursor = QtGui.QTextCursor(doc)
		cursor.select(QtGui.QTextCursor.Document)
		fmt = QtGui.QTextBlockFormat()
		fmt.setAlignment((QtCore.Qt.AlignLeft, QtCore.Qt.AlignHCenter,
			QtCore.Qt.AlignRight, QtCore.Qt.AlignJustify)[int(self.xjustify.getValue(frame))])
		fmt.setLineHeight(max(0.1, self.leading.getValue(frame)) * 100,
			QtGui.QTextBlockFormat.ProportionalHeight.value)
		cursor.mergeBlockFormat(fmt)
		spacing = QtGui.QTextCharFormat()
		spacing.setFontLetterSpacingType(QtGui.QFont.AbsoluteSpacing)
		spacing.setFontLetterSpacing(self.tracking.getValue(frame))
		cursor.mergeCharFormat(spacing)
		# Resolve editor point sizes to reference pixels before shaping. Avoid
		# binding a paint device to the layout: Qt 6.5's device-font conversion
		# drops letter spacing in headless rendering, even for pixel-sized fonts.
		formats = []
		block = doc.begin()
		while block.isValid():
			it = block.begin()
			while not it.atEnd():
				fragment = it.fragment()
				fmt = fragment.charFormat()
				if fmt.hasProperty(QtGui.QTextFormat.FontPointSize):
					fmt.setProperty(QtGui.QTextFormat.FontPixelSize, max(1, round(fmt.fontPointSize())))
					fmt.clearProperty(QtGui.QTextFormat.FontPointSize)
					formats.append((fragment.position(), fragment.length(), fmt))
				it += 1
			block = block.next()
		for position, length, fmt in formats:
			cursor.setPosition(position)
			cursor.setPosition(position + length, QtGui.QTextCursor.KeepAnchor)
			cursor.setCharFormat(fmt)
		doc.setTextWidth(width)
		return doc

	def _matrix(self, rc, frame, box, fontScale, aspect, y, boxMode, QtGui):
		m = QtGui.QTransform()
		m.scale(rc.proxy.scale / aspect, rc.proxy.scale)
		if boxMode:
			# Box is a fixed session rectangle, independent of Position/Transform.
			m.translate(box.left() * aspect, box.top())
			m.scale(fontScale, fontScale)
			m.translate(0, y)
			return m
		center = self.center.getValue(frame)
		size = rc.proxy.sessionSize
		origin = rc.session.worldToImage(self.position.getValue(frame))
		# Position locates the text's world origin; alignment anchors its box there.
		# Center is a local pivot offset: moving Position carries it with the text.
		cx, cy = (center.x - 0.5) * size.width * aspect, (center.y - 0.5) * size.height
		m.translate(origin.x * aspect + cx, origin.y + cy)
		m.rotate(self.rotate.getValue(frame))
		scale = self.scale.getValue(frame)
		m.scale(scale.x, scale.y)
		m.translate(-cx, -cy)
		anchorX = (0.0, 0.5, 1.0, 0.0)[int(self.xjustify.getValue(frame))] * box.width()
		anchorY = (0.0, 0.5, 1.0)[int(self.yjustify.getValue(frame))] * box.height()
		m.translate((box.left() - anchorX) * aspect, box.top() - anchorY)
		m.scale(fontScale, fontScale)
		m.translate(0, y)
		return m

	def _paint(self, doc, matrix, clip, roi, QtGui, QtCore, dx=0, dy=0):
		image = self._canvas(roi.width, roi.height, QtGui)
		p = QtGui.QPainter(image)
		try:
			p.setRenderHint(QtGui.QPainter.Antialiasing)
			p.setRenderHint(QtGui.QPainter.TextAntialiasing)
			p.translate(-roi.left + dx, -roi.top + dy)
			p.setWorldTransform(matrix, True)
			if clip is not None:
				p.setClipRect(clip)
			ctx = QtGui.QAbstractTextDocumentLayout.PaintContext()
			ctx.palette.setColor(QtGui.QPalette.Text, QtCore.Qt.white)
			doc.documentLayout().draw(p, ctx)
		finally:
			p.end()
		return image

	@staticmethod
	def _raster(image, roi):
		# RGBA float QImage rows are tightly packed; fromBuffer copies and converts
		# the explicit RGBA order into the host's native channel order. No 8-bit
		# intermediate, file I/O or borrowed pixel storage is involved.
		pixels = image.constBits().cast("f", shape=[image.height(), image.width(), 4])
		return fx.Raster.fromBuffer(pixels, dod=roi)


fx.addNode(name=TextNode.TYPE, label="Text", type=TextNode, categories=["Image"],
	intents=["text", "title", "credits", "rich text", "burn-in"], dataTypes=["image"], obey_matte=True)


if __name__ == "__main__" and fx.gui:
	from tools.dev import test_node
	test_node(fx.Node(TextNode.TYPE))
