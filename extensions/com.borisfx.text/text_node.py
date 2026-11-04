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
		# Detect mixed-depth blending once per node, without a host-version guess.
		fg = fx.Raster(size=fx.Size(1, 1), format=fx.Depth_8, channelMask=Channel_RGBA)
		bg = fx.Raster(size=fx.Size(1, 1), format=Depth_Float32, channelMask=Channel_RGBA)
		fg.clear()
		bg.clear()
		try:
			bg.composite(fg, premultiplied=True)
		except (TypeError, ValueError):
			self._nativeComposite = False
		else:
			self._nativeComposite = True

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
		if not roi.valid or roi.width == 0 or roi.height == 0:
			return None
		frame = options.frame
		image = None if self.textOnly.getValue(frame) else self.input.eval(rc, options.at(roi=roi), type)
		opacity = min(1.0, max(0.0, self.opacity.getValue(frame)))
		message = self.message.getValue(frame)
		out = self.allocate(rc, roi=roi, channels=Channel_RGBA, format=Depth_Float32)
		out.clear()
		np = self._np
		premultiply = bool(self.premultiply.getValue(frame))
		if image is not None:
			# RGBx's fourth channel carries Source's Transparent/Opaque fill.
			# convert() preserves it; copy(RGB -> RGBA) would force opaque alpha.
			out.copy(image.convert(Depth_Float32, Channel_RGBA), roi=roi)
			if image.channelMask == Channel_RGBA:
				pixels = np.asarray(out)
				if premultiply:
					pixels[:, :, :3] *= pixels[:, :, 3:4]
				else:
					np.copyto(pixels[:, :, :3], 0, where=pixels[:, :, 3:4] == 0)
		if not message or opacity == 0 or rc.canceled:
			return out
		QtCore, QtGui = self._QtCore, self._QtGui
		size = rc.proxy.sessionSize
		boxMode = int(self.mode.getValue(frame)) == 1
		box = (self._qrect(self.box.getValue(frame), rc.session, QtCore) if boxMode
			   else QtCore.QRectF(0, 0, size.width, size.height))
		if box.width() <= 0 or box.height() <= 0:
			return out
		fontScale = max(0.001, self.fontScale.getValue(frame))
		aspect = max(0.001, rc.pixelAspect)
		width, height = box.width() * aspect / fontScale, box.height() / fontScale
		doc = self._document(message, frame, width, QtGui, QtCore)
		y = (height - doc.size().height()) * (0.0, 0.5, 1.0)[int(self.yjustify.getValue(frame))]
		matrix = self._matrix(rc, frame, box, fontScale, aspect, y, boxMode, QtGui)
		clip = QtCore.QRectF(0, -y, width, height) if self.clip.getValue(frame) else None
		bounds = self._bounds(doc, matrix, clip, QtCore)
		textROI = bounds.clip(roi)
		text = (self._raster(self._paint(doc, matrix, clip, textROI, QtGui, QtCore), textROI, np)
				if textROI.valid and textROI.width > 0 and textROI.height > 0 else None)

		color = self.shadowColor.getValue(frame)
		if not self.shadowEnabled.getValue(frame) or color.a <= 0:
			if text is not None:
				self._over(out, text, opacity, premultiply)
			return out

		# Only the shadow's affected pixels and finite blur halo need painting.
		blur = max(0.0, self.shadowSoftness.getValue(frame)) * rc.proxy.scale
		radius = int((math.sqrt(1 + 4 * blur * blur) - 1) / 2)
		high = round((blur * blur - radius * (radius + 1)) * 3 / (2 * (radius + 1)))
		radii = [radius] * (3 - high) + [radius + 1] * high
		pad = sum(radii)
		dx, dy = self.shadowX.getValue(frame) * rc.proxy.scale, self.shadowY.getValue(frame) * rc.proxy.scale
		shadowROI = Recti(math.floor(bounds.left + dx) - pad, math.floor(bounds.top + dy) - pad,
			math.ceil(bounds.right + dx) + pad, math.ceil(bounds.bottom + dy) + pad).clip(roi) if bounds.valid else Recti()
		if not shadowROI.valid or shadowROI.width == 0 or shadowROI.height == 0:
			if text is not None:
				self._over(out, text, opacity, premultiply)
			return out
		layerROI = shadowROI | text.dod if text is not None else shadowROI
		layer = self.allocate(rc, roi=layerROI, channels=Channel_RGBA, format=Depth_Float32)
		layer.clear()
		work = shadowROI.adjusted(-pad, -pad, pad, pad)
		shadow = self._paint(doc, matrix, clip, work, QtGui, QtCore, dx, dy)
		coverage = np.asarray(self._bytes(shadow))[:, :, 3].astype(np.float32) * (1.0 / 255.0)
		for radius in radii:
			if radius:
				coverage = self._boxBlur(coverage, radius, np)
				coverage = self._boxBlur(coverage.T, radius, np).T
		pixels = np.asarray(layer)
		shadowPixels = pixels[shadowROI.top - layerROI.top:shadowROI.bottom - layerROI.top,
			shadowROI.left - layerROI.left:shadowROI.right - layerROI.left]
		shadowPixels[:, :, 3] = np.clip(coverage, 0, 1) * min(1.0, color.a)
		shadowPixels[:, :, :3] = shadowPixels[:, :, 3:4] * (color.r, color.g, color.b)
		if text is not None:
			self._over(layer, text, 1.0, True)
		# Apply global opacity once, after combining text and shadow.
		self._over(out, layer, opacity, premultiply)
		return out

	def _over(self, background, foreground, opacity, premultiply):
		roi = foreground.dod.clip(background.dod)
		if not roi.valid or roi.width == 0 or roi.height == 0:
			return
		if self._nativeComposite:
			result = background.composite(foreground, roi=roi, opacity=opacity,
				premultiplied=True, background_premultiplied=premultiply)
			background.copy(result, roi=roi)
			return
		# Older 2027 builds: keep the same source-over math on this small patch.
		np = self._np
		bg = np.asarray(background)
		bg = bg[roi.top - background.dod.top:roi.bottom - background.dod.top,
			roi.left - background.dod.left:roi.right - background.dod.left]
		fg = np.asarray(foreground)
		fg = fg[roi.top - foreground.dod.top:roi.bottom - foreground.dod.top,
			roi.left - foreground.dod.left:roi.right - foreground.dod.left].astype(np.float32)
		fg *= opacity / 255.0 if foreground.format == fx.Depth_8 else opacity
		alpha = fg[:, :, 3:4] + bg[:, :, 3:4] * (1.0 - fg[:, :, 3:4])
		bg[:, :, :3] *= (1.0 - fg[:, :, 3:4]) if premultiply else bg[:, :, 3:4] * (1.0 - fg[:, :, 3:4])
		bg[:, :, :3] += fg[:, :, :3]
		if not premultiply:
			np.divide(bg[:, :, :3], alpha, out=bg[:, :, :3], where=alpha > 0)
		bg[:, :, 3:4] = alpha

	@staticmethod
	def _bounds(doc, matrix, clip, QtCore):
		# Natural line bounds retain alignment and decorations; raw glyph boxes
		# include bearings/italic overhang outside those logical line rectangles.
		layout = doc.documentLayout()
		bounds = QtCore.QRectF()
		block = doc.begin()
		while block.isValid():
			textLayout = block.layout()
			position = layout.blockBoundingRect(block).topLeft()
			for i in range(textLayout.lineCount()):
				line = textLayout.lineAt(i)
				bounds = bounds.united(line.naturalTextRect().translated(position))
				for run in line.glyphRuns():
					bounds = bounds.united(run.boundingRect().translated(position))
					font = run.rawFont()
					for glyph, point in zip(run.glyphIndexes(), run.positions()):
						bounds = bounds.united(font.boundingRect(glyph).translated(point + position))
			if block.textList() or block.blockFormat().background().style() != QtCore.Qt.NoBrush:
				bounds = bounds.united(layout.blockBoundingRect(block).adjusted(-doc.indentWidth(), 0, 0, 0))
			block = block.next()
		# Frames/tables and a body background can paint outside the text itself.
		root = doc.rootFrame()
		if root.childFrames() or root.frameFormat().background().style() != QtCore.Qt.NoBrush:
			bounds = bounds.united(layout.frameBoundingRect(root))
		if clip is not None:
			bounds = bounds.intersected(clip)
		if bounds.isEmpty():
			return Recti()
		bounds = matrix.mapRect(bounds)
		# Two device pixels cover antialiasing at transformed edges.
		return Recti(math.floor(bounds.left()) - 2, math.floor(bounds.top()) - 2,
			math.ceil(bounds.right()) + 2, math.ceil(bounds.bottom()) + 2)

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
		image = QtGui.QImage(width, height, QtGui.QImage.Format_RGBA8888_Premultiplied)
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
	def _bytes(image):
		return image.constBits().cast("B", shape=[image.height(), image.width(), 4])

	@staticmethod
	def _raster(image, roi, np):
		# The measured DOD already includes glyph overhang and antialiasing.
		return fx.Raster.fromBuffer(np.asarray(TextNode._bytes(image)), dod=roi)


fx.addNode(name=TextNode.TYPE, label="Text", type=TextNode, categories=["Image"],
	intents=["text", "title", "credits", "rich text", "burn-in"], dataTypes=["image"], obey_matte=True)


if __name__ == "__main__" and fx.gui:
	from tools.dev import test_node
	test_node(fx.Node(TextNode.TYPE))
