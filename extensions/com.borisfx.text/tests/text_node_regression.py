"""Run run() inside Silhouette. Uses detached sessions; never activates a project."""

from pathlib import Path
from importlib import import_module

import fx


def run(output_dir=None):
	import numpy as np

	import_module("extensions.com_borisfx_text.text_node")
	session = fx.Session(label="Text regression", width=640, height=360, duration=2)
	node = fx.Node("Text")
	session.addNode(node)
	assert node.property("position").type == "Point"
	assert node.property("position").getValue(0) == fx.Point(0, 0)
	assert node.property("translateX") is None and node.property("translateY") is None
	assert isinstance(node.property("box").getValue(0), fx.Rect), "Rebuild Silhouette for world-coordinate Rect properties"

	def world_box(left, top, width, height):
		tl = session.imageToWorld(fx.Point(left, top))
		br = session.imageToWorld(fx.Point(left + width, top + height))
		return fx.Rect(tl.x, tl.y, br.x - tl.x, br.y - tl.y)

	def setp(**values):
		for name, value in values.items():
			node.property(name).value = value
		# Detached headless sessions have no ProjectHandler to deliver UI edits.
		node.invalidate()

	def render(frame=0, **kwargs):
		image = node.render(frame, depth=fx.Depth_Float32, **kwargs)
		assert image is not None, "Text produced no image"
		return image, np.array(image)

	def bounds(a):
		y, x = np.nonzero(a[:, :, 3] > 0.01)
		return x.min(), y.min(), x.max(), y.max()

	def tiles_match(a, atol=2e-5, **kwargs):
		for roi in (fx.Recti(250, 110, 380, 220), fx.Recti(280, 150, 500, 300)):
			_, b = render(roi=roi, **kwargs)
			np.testing.assert_allclose(b, a[roi.top:roi.bottom, roi.left:roi.right], atol=atol)

	_, a = render()
	assert a.shape == (360, 640, 4) and a[:, :, 3].sum() > 0
	l, t, r, b = bounds(a)
	assert abs((l + r) * 0.5 - 320) < 4 and abs((t + b) * 0.5 - 180) < 8, "Default text must center on Position"
	setp(xjustify=0, yjustify=0)
	_, left_top = render()
	l, t, _, _ = bounds(left_top)
	assert 320 <= l < 335 and 180 <= t < 220
	setp(xjustify=2, yjustify=2)
	_, right_bottom = render()
	l, t, r, b = bounds(right_bottom)
	assert l < r <= 320 and t < b <= 180
	setp(xjustify=1, yjustify=1)
	_, a = render()
	l, t, r, b = bounds(a)
	assert 240 < l < 350 and 100 < t < 200
	tiles_match(a)
	setp(opacity=0.25)
	_, quarter = render()
	np.testing.assert_allclose(quarter, a * 0.25, atol=1e-6)
	setp(premultiply=False)
	_, straight = render()
	np.testing.assert_allclose(straight[:, :, :3] * straight[:, :, 3:4], quarter[:, :, :3], atol=1e-6)
	setp(premultiply=True)
	print("PASS: default, alignment, tile offsets, premultiplied/straight color and opacity")

	# A point control supplies world coordinates; verify that its image-space
	# location is the layout origin and that animation moves the text with it.
	setp(opacity=1.0)
	_, original = render()
	position = node.property("position")
	position.constant = False
	position.setValue(fx.Point(0, 0), frame=0)
	position.setValue(session.imageToWorld(fx.Point(340, 195)), frame=1)
	node.invalidate()
	_, moved = render(frame=1)
	np.testing.assert_allclose(moved[15:, 20:], original[:-15, :-20], atol=2e-5)
	position.constant = True
	setp(position=fx.Point(0, 0))
	_, restored = render()
	np.testing.assert_array_equal(restored, original)
	setp(center=fx.Point(0.25, 0.25))
	_, changed_pivot = render()
	np.testing.assert_array_equal(changed_pivot, original)
	setp(center=fx.Point(0.5, 0.5))
	print("PASS: world-space Position, point animation, and independent rotation/scale pivot")

	setp(opacity=1.0, shadowEnabled=True, shadowColor=fx.Color(1, 0, 0, 0.7), shadowSoftness=6.0)
	image, shadow = render()
	assert shadow[:, :, 3].sum() > a[:, :, 3].sum()
	tiles_match(shadow)
	if output_dir:
		Path(output_dir).mkdir(parents=True, exist_ok=True)
		image.save("PNG", str(Path(output_dir) / "text-shadow.png"))
	print("PASS: soft shadow, retained shadow alpha and tile halo")

	setp(shadowEnabled=False, mode=1, box=world_box(100, 80, 440, 200),
		message="one two three four five six seven eight", clip=True, xjustify=0, yjustify=0)
	_, wrapped = render()
	setp(position=fx.Point(0.3, 0.2), rotate=35.0, scale=fx.Point(2, 2))
	_, fixed_box = render()
	np.testing.assert_array_equal(fixed_box, wrapped)
	setp(position=fx.Point(0, 0), rotate=0.0, scale=fx.Point(1, 1))
	l, t, r, b = bounds(wrapped)
	assert 100 <= l < r < 540 and 80 <= t < b < 280
	setp(wrap=False)
	_, unwrapped = render()
	assert bounds(unwrapped)[3] < b
	setp(message="one\ntwo")
	_, lines = render()
	assert bounds(lines)[3] > bounds(unwrapped)[3]
	setp(leading=1.5, tracking=6.0)
	_, spaced = render()
	assert bounds(spaced)[2] > bounds(lines)[2]
	assert bounds(spaced)[3] > bounds(lines)[3]
	print("PASS: box, clipping, wrapping, newlines and spacing")

	rich = ('<!DOCTYPE HTML><html><body><p style="margin:0;">'
		'<span style="font-family:Arial;font-size:44pt;color:#ff0000;font-weight:700;">Red</span>'
		'<span style="font-size:28pt;color:#00ff00;font-style:italic;text-decoration:underline;"> Green</span>'
		'</p></body></html>')
	setp(mode=0, message=rich, leading=1.0, tracking=0.0, wrap=True,
		xjustify=1, yjustify=1, clip=False)
	image, a = render()
	assert np.any((a[:, :, 0] > 0.9) & (a[:, :, 1] < 0.1) & (a[:, :, 3] > 0.5))
	assert np.any((a[:, :, 1] > 0.9) & (a[:, :, 0] < 0.1) & (a[:, :, 3] > 0.5))
	if output_dir:
		image.save("PNG", str(Path(output_dir) / "text-rich.png"))
	_, half = render(resolution=fx.Proxy_2_1)
	hb, fb = bounds(half), bounds(a)
	assert max(abs(hb[i] * 2 - fb[i]) for i in range(4)) <= 3
	setp(position=session.imageToWorld(fx.Point(355, 160)), rotate=17.0, scale=fx.Point(1.2, 0.8))
	_, transformed = render()
	assert not np.array_equal(a, transformed)
	# Qt's rotated glyph coverage can differ by one 8-bit antialiasing step
	# when the paint device clips the transformed outline to a different tile.
	tiles_match(transformed, atol=1.0 / 255.0 + 1e-6)
	print("PASS: rich font/size/color/style, proxy scale and transforms")

	setp(position=fx.Point(0, 0), rotate=0.0, scale=fx.Point(1, 1),
		message="<b>literal</b>", xjustify=0, yjustify=0)
	_, literal = render()
	setp(message="literal")
	_, plain = render()
	assert bounds(literal)[2] > bounds(plain)[2]
	setp(message="")
	_, empty = render()
	assert np.count_nonzero(empty) == 0
	setp(message=rich, opacity=0.0)
	_, empty = render()
	assert np.count_nonzero(empty) == 0
	print("PASS: literal plain text, empty text and zero opacity")

	# Exercise the real input port with RGB/HDR and translucent RGBA backgrounds.
	class Background(fx.NodeProxy):
		def __init__(self, *args, **kwargs):
			self.output = self.addOutput("output", "Output")
			self.rgb = self.addProperty("rgb", {"label": "RGB", "value": True})

		def query(self, rc, options, type, port):
			return {"dod": fx.Recti(size=rc.proxy.renderSize),
				"channels": fx.Channel_RGB if self.rgb.getValue(0) else fx.Channel_RGBA}

		def process(self, rc, options, type, port):
			channels = fx.Channel_RGB if self.rgb.getValue(0) else fx.Channel_RGBA
			out = self.allocate(rc, roi=options.roi, channels=channels, format=fx.Depth_Float32)
			a = np.asarray(out)
			a[:, :, :3] = (2.0, 0.25, 0.125)
			# Host RGB rasters use four storage channels; their unused alpha must
			# become opaque when copied into the Text node's RGBA background.
			if a.shape[2] == 4:
				a[:, :, 3] = 0.0 if channels == fx.Channel_RGB else 0.35
			return out

	fx.addNode(name="TextRegressionBackground", label="Text Regression Background", type=Background,
		categories=["Image"], dataTypes=["image"])
	bg = fx.Node("TextRegressionBackground")
	session.addNode(bg)
	bg.port("output").connect(node.port("input"))
	_, a = render()
	np.testing.assert_allclose(a[300, 600], (2.0, 0.25, 0.125, 1.0), atol=1e-6)
	bg.property("rgb").value = False
	bg.invalidate()
	node.invalidate()
	_, a = render()
	np.testing.assert_allclose(a[300, 600], (0.7, 0.0875, 0.04375, 0.35), atol=1e-6)
	setp(premultiply=False)
	_, straight = render()
	np.testing.assert_allclose(straight[300, 600], (2.0, 0.25, 0.125, 0.35), atol=1e-6)
	setp(premultiply=True)
	setp(opacity=0.5)
	_, a = render()
	assert a[:, :, 3].max() > 0.35
	tiles_match(a)
	setp(textOnly=True)
	_, a = render()
	assert a[300, 600, 3] == 0
	print("PASS: RGB opaque alpha, HDR, RGBA source-over and Text Only")
	print("Text node regression checks passed")


def run_layout():
	"""Exercise both layouts before the rebuilt native Rect binding is available.

	Runs the actual Python node methods, Qt text engine and Raster buffers with
	control/context fixtures. Native metadata and project I/O have separate tests.
	"""
	import types
	import numpy as np

	cls = import_module("extensions.com_borisfx_text.text_node").TextNode

	class Value:
		def __init__(self, md):
			self.value = md.get("value", md.get("default"))
			self.enabled = True
		def getValue(self, frame):
			return self.value

	class Harness:
		def addProperty(self, name, md):
			return Value(md)
		def addInput(self, *args, **kwargs):
			return types.SimpleNamespace(eval=lambda *a: None, query=lambda *a: {})
		def addOutput(self, *args, **kwargs):
			return None
		def allocate(self, rc, roi, channels, format):
			return fx.Raster(size=rc.proxy.renderSize, format=format, channelMask=channels, roi=roi)

	text = Harness()
	text._DEFAULT = cls._DEFAULT
	for name in ("_boxBlur", "_qrect", "_canvas", "_raster"):
		setattr(text, name, getattr(cls, name))
	for name in ("_document", "_matrix", "_paint"):
		setattr(text, name, types.MethodType(getattr(cls, name), text))
	cls.__init__(text)
	cls.setup(text)
	session = fx.Session(width=640, height=360, pixelAspect=1.0)
	rc = types.SimpleNamespace(session=session, pixelAspect=1.0, canceled=False,
		proxy=types.SimpleNamespace(sessionSize=fx.Size(640, 360), renderSize=fx.Size(640, 360), scale=1.0))
	options = types.SimpleNamespace(frame=0, roi=fx.Recti(0, 0, 640, 360))
	options.at = lambda **kw: options

	def render():
		return np.array(cls.process(text, rc, options, "image", None))

	def bounds(a):
		y, x = np.nonzero(a[:, :, 3] > 0.01)
		return np.array((x.min(), y.min(), x.max(), y.max()))

	centered = render()
	text.position.value = session.imageToWorld(fx.Point(350, 200))
	moved = render()
	np.testing.assert_allclose(moved[20:, 30:], centered[:-20, :-30], atol=2e-5)
	text.mode.value = 1
	boxed = render()
	text.position.value = fx.Point(-0.25, -0.1)
	text.rotate.value = 30.0
	text.scale.value = fx.Point(2, 2)
	np.testing.assert_array_equal(render(), boxed)
	cls.updatePropertyState(text)
	assert text.box.enabled and not text.position.enabled and not text.rotate.enabled

	tl = session.imageToWorld(fx.Point(100.125, 80.25))
	br = session.imageToWorld(fx.Point(540.125, 280.25))
	text.box.value = fx.Rect(tl.x, tl.y, br.x - tl.x, br.y - tl.y)
	text.message.value = "long text for a fixed clipping rectangle"
	text.wrap.value = False
	text.clip.value = True
	text.xjustify.value = text.yjustify.value = 0
	full = render()
	b = bounds(full)
	assert b[0] >= 100 and b[1] >= 80 and b[2] <= 540 and b[3] <= 280
	rc.proxy.renderSize, rc.proxy.scale = fx.Size(320, 180), 0.5
	options.roi = fx.Recti(0, 0, 320, 180)
	half = render()
	assert max(abs(bounds(half) * 2 - b)) <= 3
	print("PASS: Position moves text; world-coordinate Box stays fixed, clips and scales with proxy")
