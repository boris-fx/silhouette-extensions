"""Color Chips -- sample colours off the image and label them with swatch chips.

Up to four chips, each a draggable viewer point (optionally driven by a Tracker
or Layer on the Transform input): the chip averages the block of
pixels under its point and hangs a swatch card beside it showing the nearest
XKCD colour-survey name and the sRGB value.  In the spirit of Lewis Saunders'
Ls_Chips Flame Matchbox (a fresh implementation -- no code is shared).

Colour handling: a float project is assumed to be scene-linear and an 8-bit one
sRGB, so samples are encoded to sRGB before naming and read-out, and the chip
graphics are decoded back into the working space before drawing.  Names are
matched by perceptual distance (CIELAB delta E) rather than raw RGB.

Background modes: Image (the input), Blocky (the input averaged at the Block
Size -- exactly what each chip averages), Palette (every named colour as a grid,
which the chips then sample -- handy for browsing), Diagram (a Voronoi
diagram of the chip colours), and None (just the chips, over transparent black).

Show Color Name / Show Color Value toggle the card's two text lines; the label
strip shrinks to fit, and with both off the card is the bare swatch.

The swatch, card, shadow and text are all fx.draw fills; text is shaped
with Font.layout().  Everything is laid out in
proxy-render space, so it sizes at any proxy level.
"""

import math
import os

import fx
from fx import *

from tools.ensure import ensure


CHIPS = 4

# appended only: the saved Background value is the index
BACKGROUNDS = ["Image", "Blocky", "Palette", "Diagram", "None"]
BG_IMAGE, BG_BLOCKY, BG_PALETTE, BG_DIAGRAM, BG_NONE = range(5)

READOUTS = ["0-255", "0-1", "Hex"]
RO_8BIT, RO_FLOAT, RO_HEX = range(3)

# default point positions in Silhouette world units: (0,0) = frame centre, one
# unit = frame height (x in display units, so pixel aspect applies), y down.
# Spread over the frame so enabling a chip shows it apart from the others.
DEFAULT_POS = [(-0.45, -0.15), (-0.1, -0.3), (0.25, 0.05), (0.55, -0.25)]

# chip layout, as fractions of the frame width (scaled by Chip Size)
CHIP_W       = 0.10		# card width
CHIP_ASPECT  = 1.1		# card height / width
SWATCH_FRAC  = 0.64		# swatch share of the card height (the rest is the label strip)
# label strip rows, as fractions of the full strip: top pad, name line, gap,
# value line, and the chip-number row below them.  A hidden line drops out of
# the strip, so it shrinks to fit (and with no text there is no strip).
STRIP_TOP    = 0.12
STRIP_NAME   = 0.30
STRIP_GAP    = 0.06
STRIP_VALUE  = 0.17
STRIP_NUMBER = 0.35
OFFSET       = 0.006	# card top-left offset from the point, down and right
MARGIN       = 0.01		# keep cards this far inside the frame
CORNER       = 0.05		# corner radius, x card width
SHADOW_BLUR  = 0.06		# shadow blur radius, x card width
SHADOW_DROP  = 0.014	# shadow drop (downward offset), x card height

STRIP_GREY   = 0.9		# label strip (display sRGB)
NAME_GREY    = 0.05		# colour name
VALUE_GREY   = 0.4		# read-out and chip number

NAME_MATCH_DE = 1.0		# with Nearest Name off, only an (imperceptibly) exact match is named

# the XKCD colour-survey names (CC0), shipped with the extension
_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "xkcd_rgb.txt")

# numpy and the name table, loaded ONCE ON THE MAIN THREAD by _warmup(): not at
# module scope (a startup-loaded module that imports numpy locks its compiled
# modules into the process, so Preferences > Packages can no longer update it)
# and never from process(), which runs on a render thread.
_NUMPY = None
_NAMES = None


def _warmup():
	global _NUMPY, _NAMES
	if _NUMPY is None:
		_NUMPY = ensure("numpy")
	if _NAMES is None:
		_NAMES = _Names(_NUMPY)


# --- colour maths (numpy) -----------------------------------------------------

# linear sRGB -> CIE XYZ (D65), and the D65 white point
_RGB_TO_XYZ = ((0.4124564, 0.3575761, 0.1804375),
			   (0.2126729, 0.7151522, 0.0721750),
			   (0.0193339, 0.1191920, 0.9503041))
_WHITE = (0.95047, 1.0, 1.08883)


def _encode(np, lin):
	"""linear -> sRGB transfer (clamped to >= 0)."""
	lin = np.maximum(lin, 0.0)
	return np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.power(lin, 1.0 / 2.4) - 0.055)


def _decode(np, srgb):
	"""sRGB transfer -> linear."""
	return np.where(srgb <= 0.04045, srgb / 12.92, np.power((np.asarray(srgb) + 0.055) / 1.055, 2.4))


def _lab(np, srgb):
	"""display sRGB (..., 3) in 0-1 -> CIELAB (..., 3)."""
	lin = _decode(np, np.clip(srgb, 0.0, 1.0))
	xyz = lin @ np.array(_RGB_TO_XYZ).T / np.array(_WHITE)
	f = np.where(xyz > (6.0 / 29.0) ** 3, np.cbrt(xyz), xyz / (3.0 * (6.0 / 29.0) ** 2) + 4.0 / 29.0)
	return np.stack([116.0 * f[..., 1] - 16.0,
					 500.0 * (f[..., 0] - f[..., 1]),
					 200.0 * (f[..., 1] - f[..., 2])], axis=-1)


class _Names:
	"""The XKCD colour-survey names, with their sRGB values and CIELAB coordinates."""

	def __init__(self, np):
		names, rgb = [], []
		with open(_DATA, encoding="utf-8") as fh:
			for line in fh:
				if line.startswith("#") or "\t" not in line:
					continue
				name, hexv = line.split("\t")[:2]
				hexv = hexv.strip().lstrip("#")
				names.append(name.strip())
				rgb.append([int(hexv[i:i + 2], 16) / 255.0 for i in (0, 2, 4)])
		self.names = names
		self.rgb = np.array(rgb, dtype=np.float64)
		self.lab = _lab(np, self.rgb)

		# the Palette background order: greys first (dark to light), then the
		# colours in hue bands, dark to light within each band
		hsv = [_hsv(*c) for c in rgb]
		self.order = sorted(range(len(names)), key=lambda i: (
			(0, 0, hsv[i][2]) if hsv[i][1] < 0.12 else (1, int(hsv[i][0] * 24.0), hsv[i][2])))

	def nearest(self, np, srgb):
		"""(index, delta E) of the name closest to a display-sRGB colour."""
		d = np.sum((self.lab - _lab(np, np.asarray(srgb, dtype=np.float64))) ** 2, axis=1)
		i = int(np.argmin(d))
		return i, float(math.sqrt(d[i]))


def _hsv(r, g, b):
	hi, lo = max(r, g, b), min(r, g, b)
	d = hi - lo
	if d <= 0.0:
		return 0.0, 0.0, hi
	if hi == r:
		h = ((g - b) / d) % 6.0
	elif hi == g:
		h = (b - r) / d + 2.0
	else:
		h = (r - g) / d + 4.0
	return h / 6.0, d / hi, hi


class ColorChipsNode(NodeProxy):
	"""Overlay colour-sampling chips (swatch + name + sRGB value) on the image.

	Each chip averages the Block Size block of pixels under its point (the same
	block-grid Blocky shows).  With Nearest Name on, the chip shows the nearest
	XKCD name and that name's colour; off, it shows the exact sample, named only
	on a (near-)exact match and otherwise by its hex code.  When the image has an
	alpha channel, the block's average alpha is shown after the RGB read-out.
	Override replaces the sample with a colour of your choosing.
	"""

	TYPE = "ColorChips"

	def __init__(self, *args, **kwargs):
		self.input  = self.addInput("input", "Input")
		# Trackers / Layers plugged in here can drive the chip points: each
		# Position's Property View menu lists the transforms found through this
		# port, and a linked point's getValue() comes back already transformed.
		self.transform = self.addInput("transformInput", "Transform", dataTypes=["objects", "transform"])
		self.output = self.addOutput("output", "Output", cache=True)

		# one group per chip.  Enable and Override are constant + supervised: they
		# drive the enable state of the chip's other controls (updatePropertyState),
		# and a disabled Position point also drops out of the viewer.
		for i in range(CHIPS):
			n = i + 1
			g = "chip%d" % n
			self.addProperty(g, {"label": "Chip %d" % n, "type": "group", "expand": n == 1})
			self.addProperty(g + "Enable", {"label": "Enable", "value": n == 1, "group": g,
											"constant": True, "supervise": True})
			self.addProperty(g + "Pos", {"label": "Position", "value": Point(*DEFAULT_POS[i]), "group": g,
										 "transformable": True})
			self.addProperty(g + "Nearest", {"label": "Nearest Name", "value": True, "group": g})
			self.addProperty(g + "Override", {"label": "Override", "value": False, "group": g,
											  "constant": True, "supervise": True})
			self.addProperty(g + "Color", {"label": "Override Color", "value": Color(0.5, 0.5, 0.5, 1),
										   "group": g})

		self.addProperty("options", {"label": "Options", "type": "group", "expand": True})
		self.addProperty("blockSize", {"label": "Block Size", "value": 8, "type": "int",
									   "range": [1, 128], "group": "options"})
		self.addProperty("background", {"label": "Background", "value": BG_IMAGE,
										"items": BACKGROUNDS, "group": "options"})
		self.addProperty("showName", {"label": "Show Color Name", "value": True, "group": "options"})
		# constant + supervised: drives the Readout control's enable state
		self.addProperty("showValue", {"label": "Show Color Value", "value": True, "group": "options",
									   "constant": True, "supervise": True})
		self.addProperty("readout", {"label": "Readout", "value": RO_8BIT,
									 "items": READOUTS, "group": "options"})
		self.addProperty("chipSize", {"label": "Chip Size", "value": 1.0,
									  "range": [0.25, 4.0], "group": "options"})
		self.addProperty("shadow", {"label": "Shadow", "value": 0.5,
									"range": [0.0, 1.0], "group": "options"})

		self._fonts = {}		# Font cache, keyed by rounded pixel size
		self._paletteCache = None	# ((W, H, linear), full-frame palette array)

	# numpy and the name table (module caches -- see _warmup)
	@property
	def _np(self):
		return _NUMPY

	@property
	def _names(self):
		return _NAMES

	# Main-thread warm-up, from BOTH setup() and updatePropertyState(): setup()
	# fires when the node joins a session, but reloading the extension re-imports
	# this module (emptying the caches) with no setup() to refill them;
	# updatePropertyState() runs at load and on every supervised change, so the
	# caches heal themselves.  Once filled this is a global read.
	def _warmup(self):
		try:
			_warmup()
		except Exception as e:
			print("[Color Chips] numpy is unavailable:", e)

	def setup(self):
		self._warmup()

	# gray out a chip's controls while it is disabled, its override colour while
	# Override is off, and Readout while Show Color Value is off (all constant
	# switches, so frame 0)
	def updatePropertyState(self):
		self._warmup()
		for i in range(CHIPS):
			g = "chip%d" % (i + 1)
			on = bool(self.property(g + "Enable").getValue(0))
			for suffix in ("Pos", "Nearest", "Override"):
				self.property(g + suffix).enabled = on
			self.property(g + "Color").enabled = on and bool(self.property(g + "Override").getValue(0))
		self.property("readout").enabled = bool(self.property("showValue").getValue(0))

	# QUERY -- cards may hang outside the input's DOD, so always a full-frame RGBA
	# result; querying the input registers the dependency.
	def query(self, rc, options, type, port):
		self.input.query(rc, options, type)
		return {"dod": Recti(size=rc.proxy.renderSize), "channels": Channel_RGBA}

	def process(self, rc, options, type, port):
		roi = options.roi
		if not roi.valid:
			return None
		np = self._np
		if np is None or self._names is None:
			rc.setError("Color Chips: numpy is unavailable")
			return None

		frame = options.frame
		size  = rc.proxy.renderSize
		W, H  = size.width, size.height
		scale = rc.proxy.scale
		# a float project is scene-linear, an 8-bit one is sRGB
		self._linear = rc.format != fx.Depth_8

		bg      = int(self.property("background").getValue(frame))
		readout = int(self.property("readout").getValue(frame))
		block   = max(1, int(round(int(self.property("blockSize").getValue(frame)) * scale)))
		csize   = max(0.05, float(self.property("chipSize").getValue(frame)))
		shadow  = float(self.property("shadow").getValue(frame))
		showName  = bool(self.property("showName").getValue(frame))
		showValue = bool(self.property("showValue").getValue(0))

		# world (Position) -> proxy pixels.  The session's own transform, so the
		# pixel aspect is honoured -- world x is in display units, so a point's
		# pixel column is W/2 + x*H/pixelAspect, not W/2 + x*H.
		w2i = self.node.worldToImageTransform
		# pixel aspect (width / height of a pixel).  Cards are laid out and drawn in
		# DISPLAY units -- x multiplied by the aspect -- so they keep their shape on
		# screen; the renderer maps them back to pixels (see _pixels()).
		pa = rc.pixelAspect if rc.pixelAspect > 0.0 else 1.0

		# sample and lay out the enabled chips first: Diagram's background needs
		# their colours
		chips = []
		for i in range(CHIPS):
			g = "chip%d" % (i + 1)
			if not self.property(g + "Enable").getValue(0):
				continue
			pos = self.property(g + "Pos").getValue(frame)
			p = w2i * Point(pos.x, pos.y)			# full-res image pixels, y down
			px, py = p.x * scale, p.y * scale
			# a point off the frame (dragged out, or tracked out of shot) has no
			# chip: nothing to sample, so nothing to show
			if not (0.0 <= px < W and 0.0 <= py < H):
				continue
			if bool(self.property(g + "Override").getValue(0)):
				c = self.property(g + "Color").getValue(frame)
				sample, alpha = np.array([c.r, c.g, c.b]), None
			else:
				sample, alpha = self._sample(rc, options, type, px, py, block, bg, W, H)
			chip = self._describe(np, sample, bool(self.property(g + "Nearest").getValue(frame)),
								  readout, alpha)
			chip.update(self._layout(px, py, W, H, csize, pa, showName, showValue))
			chip["n"] = i + 1
			chips.append(chip)

		# nothing to draw over the plain image: pass it straight through
		if not chips and bg == BG_IMAGE:
			image = self.input.eval(rc, options, type)
			if image is None or (image.channelMask == Channel_RGBA and image.format == rc.format):
				return image

		out = self._base(np, rc, options, type, bg, block, chips, roi, W, H)
		if not chips:
			return out

		# The cards and their shadow falloff cover a few percent of the frame, so
		# each chip is rendered, blurred and blended over just its own region,
		# straight into the output (all shadows first, then all cards, so a card
		# always sits above every shadow).  The one frame-sized pass is the copy
		# of the background into the output.
		dst = np.asarray(out)
		if shadow > 0.0:
			# the blur is round on screen: a non-square pixel covers more (or less) of
			# the picture horizontally, so the x radius in pixels is y / aspect
			ry = max(1.0, chips[0]["w"] * SHADOW_BLUR)
			rx = max(1.0, ry / pa)
			for chip in chips:
				self._drawShadow(np, dst, roi, rc, chip, shadow, rx, ry, W, H)
		for chip in chips:
			region = self._bounds([chip], 2, roi)
			if region is None:
				continue
			r = fx.draw.Renderer(size, region, scale=scale, channels=Channel_RGBA, format=fx.Depth_F32)
			with r.transform(self._pixels(pa)):
				self._drawChip(r, chip, H)
			self._over(np, dst, roi, r.result())
		return out

	# display units -> pixels: x / pixel aspect (y is the same in both)
	def _pixels(self, pa):
		m = Matrix()
		m.scale(1.0 / pa, 1.0)
		return m

	# the chips' cards (and their dropped shadows) in PIXELS, grown by `pad` and
	# clipped to `clip`
	def _bounds(self, chips, pad, clip):
		x0 = int(math.floor(min(c["x"] / c["pa"] for c in chips))) - pad
		y0 = int(math.floor(min(c["y"] for c in chips))) - pad
		x1 = int(math.ceil(max((c["x"] + c["w"]) / c["pa"] for c in chips))) + pad
		y1 = int(math.ceil(max(c["y"] + c["h"] * (1.0 + SHADOW_DROP) for c in chips))) + pad
		x0, y0 = max(x0, clip.left), max(y0, clip.top)
		x1, y1 = min(x1, clip.left + clip.width), min(y1, clip.top + clip.height)
		if x1 <= x0 or y1 <= y0:
			return None
		return Recti(x0, y0, width=x1 - x0, height=y1 - y0)

	# --- sampling -----------------------------------------------------------

	# the block-grid cell containing (px, py), clipped to the frame
	def _cell(self, px, py, block, W, H):
		bx = int(math.floor(px / block)) * block
		by = int(math.floor(py / block)) * block
		x0, y0 = max(bx, 0), max(by, 0)
		x1, y1 = min(bx + block, W), min(by + block, H)
		if x1 <= x0 or y1 <= y0:
			return None
		return Recti(x0, y0, width=x1 - x0, height=y1 - y0)

	# the working-space (RGB, alpha) average of the block under the point; alpha is
	# None unless the image actually carries an alpha channel (the Palette has none)
	def _sample(self, rc, options, type, px, py, block, bg, W, H):
		np = self._np
		cell = self._cell(px, py, block, W, H)
		if cell is None:
			return np.zeros(3), None
		has_alpha = False
		if bg == BG_PALETTE:
			a = self._palette(np, cell, W, H)
		else:
			raster = self.input.eval(rc, options.at(roi=cell), type)
			has_alpha = raster is not None and bool(raster.channelMask & Channel_A)
			a = self._read(np, raster, cell)
		mean = a.reshape(-1, 4).mean(axis=0).astype(np.float64)
		return mean[:3], (float(mean[3]) if has_alpha else None)

	# a raster's pixels over `rect` as float32 (h, w, 4), transparent outside its DOD
	def _read(self, np, raster, rect):
		out = np.zeros((rect.height, rect.width, 4), np.float32)
		if raster is None:
			return out
		d = raster.dod
		x0, y0 = max(rect.left, d.left), max(rect.top, d.top)
		x1 = min(rect.left + rect.width, d.left + d.width)
		y1 = min(rect.top + rect.height, d.top + d.height)
		if x1 <= x0 or y1 <= y0:
			return out
		a = np.asarray(raster)
		src = a[y0 - d.top:y1 - d.top, x0 - d.left:x1 - d.left].astype(np.float32)
		if a.dtype == np.uint8:
			src /= 255.0
		n = min(src.shape[2], 4)
		out[y0 - rect.top:y1 - rect.top, x0 - rect.left:x1 - rect.left, :n] = src[..., :n]
		if n < 4:
			out[y0 - rect.top:y1 - rect.top, x0 - rect.left:x1 - rect.left, 3] = 1.0
		return out

	# --- chip content -------------------------------------------------------

	def _toDisplay(self, np, c):
		c = np.asarray(c, dtype=np.float64)
		return np.clip(_encode(np, c) if self._linear else c, 0.0, 1.0)

	def _toWorking(self, np, c):
		c = np.asarray(c, dtype=np.float64)
		return _decode(np, c) if self._linear else c

	# the swatch colour and the two text lines for one sample; a sampled alpha is
	# appended to the read-out in the same style (alpha has no transfer curve, so
	# it is shown as-is in both linear and sRGB projects)
	def _describe(self, np, sample, nearest, readout, alpha=None):
		names = self._names
		disp = self._toDisplay(np, sample)
		i, de = names.nearest(np, disp)
		if nearest:
			disp = names.rgb[i]				# show the named colour itself
			name = names.names[i]
		else:
			name = names.names[i] if de < NAME_MATCH_DE else None

		v8 = [int(round(x * 255.0)) for x in disp]
		hexv = "#%02X%02X%02X" % tuple(v8)
		if readout == RO_FLOAT:
			value = "sRGB: %.3f %.3f %.3f" % tuple(disp)
		elif readout == RO_HEX and name:
			value = "sRGB: " + hexv
		else:
			value = "sRGB: %d %d %d" % tuple(v8)
		if alpha is not None:
			a8 = int(round(min(max(alpha, 0.0), 1.0) * 255.0))
			if readout == RO_FLOAT:
				value += "  A: %.3f" % alpha
			elif readout == RO_HEX and name:
				value += "  A: %02X" % a8
			else:
				value += "  A: %d" % a8
		title = (name[0].upper() + name[1:]) if name else hexv
		return {"swatch": self._toWorking(np, disp), "title": title, "value": value}

	# card geometry in DISPLAY units (x = pixels * pa), so the card keeps its
	# proportions on screen whatever the pixel aspect; sizes follow the frame's
	# display width.  The card hangs just below-right of the point, kept inside
	# the frame.  The swatch is a fixed size; the label strip below it holds only
	# the text lines shown (see STRIP_*), so it is `strip` (the full strip height)
	# scaled by their rows, or absent.
	def _layout(self, px, py, W, H, csize, pa, showName, showValue):
		Wd = W * pa
		cw = CHIP_W * Wd * csize
		sh = cw * CHIP_ASPECT * SWATCH_FRAC
		strip = cw * CHIP_ASPECT - sh
		rows = 0.0
		if showName or showValue:
			rows = STRIP_TOP + STRIP_NUMBER
			rows += STRIP_NAME if showName else 0.0
			rows += STRIP_VALUE if showValue else 0.0
			rows += STRIP_GAP if showName and showValue else 0.0
		ch = sh + strip * rows
		d, m = OFFSET * Wd * csize, MARGIN * Wd
		cx = min(max(px * pa + d, m), max(m, Wd - m - cw))
		cy = min(max(py + d, m), max(m, H - m - ch))
		return {"x": cx, "y": cy, "w": cw, "h": ch, "pa": pa, "sh": sh, "strip": strip,
				"showName": showName, "showValue": showValue}

	# --- backgrounds --------------------------------------------------------

	# the output raster, holding the background.  The Image background is copied
	# in by Raster.copy (in place, converting format/channels -- an RGB input
	# lands opaque), None is just cleared, everything else is built with numpy.
	def _base(self, np, rc, options, type, bg, block, chips, roi, W, H):
		out = self.allocate(rc, roi=roi)
		if bg == BG_IMAGE:
			image = self.input.eval(rc, options, type)
			d = image.dod if image is not None else None
			covered = d is not None and d.left <= roi.left and d.top <= roi.top and \
				d.left + d.width >= roi.left + roi.width and d.top + d.height >= roi.top + roi.height
			if not covered:
				out.clear()
			if image is not None:
				out.copy(image, roi)
		elif bg == BG_NONE:
			out.clear()
		else:
			self._background(np, out, rc, options, type, bg, block, chips, W, H)
		return out

	# fill the allocated output with a generated background (Palette, Diagram,
	# Blocky)
	def _background(self, np, out, rc, options, type, bg, block, chips, W, H):
		roi = out.dod
		dst = np.asarray(out)
		if bg == BG_PALETTE:
			a = self._palette(np, roi, W, H)
		elif bg == BG_DIAGRAM:
			a = self._diagram(np, roi, chips, rc.proxy.scale)
		else:
			a = self._blocky(np, rc, options, type, roi, block, W, H)
		if dst.dtype == np.uint8:
			dst[...] = np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8)
		else:
			dst[...] = a.astype(dst.dtype)

	# the input averaged over the block grid (what each chip samples)
	def _blocky(self, np, rc, options, type, roi, block, W, H):
		x0 = (roi.left // block) * block
		y0 = (roi.top // block) * block
		x1 = min(-(-(roi.left + roi.width) // block) * block, W)
		y1 = min(-(-(roi.top + roi.height) // block) * block, H)
		grid = Recti(x0, y0, width=x1 - x0, height=y1 - y0)
		a = self._read(np, self.input.eval(rc, options.at(roi=grid), type), grid)
		gh, gw = a.shape[:2]
		ny, nx = -(-gh // block), -(-gw // block)
		# pad to whole blocks with NaN so a frame-edge block averages only real pixels
		p = np.full((ny * block, nx * block, 4), np.nan, np.float32)
		p[:gh, :gw] = a
		m = np.nanmean(p.reshape(ny, block, nx, block, 4), axis=(1, 3))
		full = np.repeat(np.repeat(m, block, axis=0), block, axis=1)[:gh, :gw]
		oy, ox = roi.top - y0, roi.left - x0
		return full[oy:oy + roi.height, ox:ox + roi.width]

	# every named colour as a grid shaped to the frame -- built once per frame
	# size and colour space, then sliced
	def _palette(self, np, rect, W, H):
		key = (W, H, self._linear)
		full = self._paletteCache
		if full is None or full[0] != key:
			full = self._paletteCache = (key, self._buildPalette(np, W, H))
		a = full[1]
		return a[rect.top:rect.top + rect.height, rect.left:rect.left + rect.width]

	def _buildPalette(self, np, W, H):
		rect = Recti(0, 0, width=W, height=H)
		names = self._names
		count = len(names.order)
		cols = max(1, int(math.ceil(math.sqrt(count * W / float(H)))))
		rows = int(math.ceil(count / float(cols)))
		colors = np.full((cols * rows, 3), 0.1)
		colors[:count] = names.rgb[names.order]
		colors = self._toWorking(np, colors)
		xs = np.arange(rect.left, rect.left + rect.width) + 0.5
		ys = np.arange(rect.top, rect.top + rect.height) + 0.5
		ci = np.clip((xs * cols / W).astype(int), 0, cols - 1)
		ri = np.clip((ys * rows / H).astype(int), 0, rows - 1)
		a = np.ones((rect.height, rect.width, 4), np.float32)
		a[..., :3] = colors[ri[:, None] * cols + ci[None, :]]
		return a

	# a Voronoi diagram of the chip colours, seeded at the swatch centres, with a
	# one-pixel anti-aliased edge between cells.  Distances are measured in display
	# units (like the card layout), so the cells are true on screen at any pixel
	# aspect.
	def _diagram(self, np, rect, chips, scale):
		a = np.ones((rect.height, rect.width, 4), np.float32)
		if not chips:
			a[..., :3] = 0.0
			return a
		pa = chips[0]["pa"]
		seeds = np.array([[c["x"] + c["w"] * 0.5, c["y"] + c["sh"] * 0.5] for c in chips])
		colors = np.array([c["swatch"] for c in chips])
		if len(chips) == 1:
			a[..., :3] = colors[0]
			return a
		xs = (np.arange(rect.left, rect.left + rect.width) + 0.5) * pa
		ys = np.arange(rect.top, rect.top + rect.height) + 0.5
		d2 = (xs[None, None, :] - seeds[:, 0, None, None]) ** 2 + (ys[None, :, None] - seeds[:, 1, None, None]) ** 2
		order = np.argsort(d2, axis=0)
		i1, i2 = order[0], order[1]
		d1 = np.take_along_axis(d2, i1[None], 0)[0]
		d2b = np.take_along_axis(d2, i2[None], 0)[0]
		sep = np.linalg.norm(seeds[i1] - seeds[i2], axis=-1)
		edge = (d2b - d1) / np.maximum(2.0 * sep, 1e-6)	# distance to the bisector
		t = np.clip(0.5 + edge / max(scale, 1e-3), 0.0, 1.0)[..., None]
		a[..., :3] = colors[i1] * t + colors[i2] * (1.0 - t)
		return a

	# --- drawing ------------------------------------------------------------

	def _paint(self, rgb, alpha=1.0):
		return fx.draw.Paint(color=Color(float(rgb[0]), float(rgb[1]), float(rgb[2]), alpha),
							 fillMode=fx.draw.Fill.ZERO)

	def _grey(self, v):
		g = float(self._toWorking(self._np, [v])[0])
		return (g, g, g)

	# a rectangle with corners of `radius` pixels.  ShapeBuilder.rect's roundness is
	# relative to the rect's short side, so convert: the card and the (shorter)
	# swatch then get identical corners.
	def _rect(self, x, y, w, h, radius=0.0):
		sb = ShapeBuilder()
		short = min(w, h)
		sb.rect(Rect(x, y, w, h), min(1.0, 2.0 * radius / short) if radius > 0.0 and short > 0.0 else 0.0)
		return sb

	# how far a blurred shadow reaches past its card.  Raster.blur(size) runs two
	# box passes of half-width size/2, so the reach is ~size; pad a little past it
	# for the anti-aliased edge.
	def _shadowPad(self, radius):
		return int(math.ceil(radius * 1.5)) + 2

	# One chip's soft drop shadow, darkened straight into the output: a 1-channel
	# coverage mask of the card (dropped a little), blurred, then used to blend
	# towards black.  The mask spans the card plus the blur's reach, clipped only
	# to the frame, so a shadow crossing an ROI edge still blurs in from outside.
	# It is always rendered as 32-bit float: blurring 1-channel half-float data
	# is not reliable.
	def _drawShadow(self, np, dst, roi, rc, c, strength, rx, ry, W, H):
		layer = self._bounds([c], self._shadowPad(max(rx, ry)), Recti(0, 0, width=W, height=H))
		if layer is None:
			return
		r = fx.draw.Renderer(rc.proxy.renderSize, layer, scale=rc.proxy.scale,
							 channels=Channel_A, format=fx.Depth_F32)
		with r.transform(self._pixels(c["pa"])):
			r.fill(self._rect(c["x"], c["y"] + c["h"] * SHADOW_DROP, c["w"], c["h"], c["w"] * CORNER),
				   fx.draw.Paint(color=Color(1, 1, 1, 1), fillMode=fx.draw.Fill.ZERO))
		mask = r.result()
		mask.blur(Point(rx, ry))
		view = self._overlap(dst, roi, mask.dod)
		if view is None:
			return
		d, (y0, y1, x0, x1) = view
		m = np.asarray(mask)
		m = m.reshape(m.shape[0], m.shape[1], -1)[..., -1]
		k = (m[y0:y1, x0:x1] * (0.75 * strength))[..., None]
		self._blend(np, d, np.zeros(3, np.float32), k)

	# a straight-alpha RGBA layer composited OVER the output in place, keeping the
	# output's own alpha (a_out = a + a_bg * (1 - a))
	def _over(self, np, dst, roi, layer):
		view = self._overlap(dst, roi, layer.dod)
		if view is None:
			return
		d, (y0, y1, x0, x1) = view
		L = np.asarray(layer)[y0:y1, x0:x1].astype(np.float32)
		self._blend(np, d, L[..., :3], L[..., 3:4])

	# the output sub-array under `rect`, plus the matching slice of a raster with
	# that DOD; None when they don't meet
	def _overlap(self, dst, roi, rect):
		x0, y0 = max(roi.left, rect.left), max(roi.top, rect.top)
		x1 = min(roi.left + roi.width, rect.left + rect.width)
		y1 = min(roi.top + roi.height, rect.top + rect.height)
		if x1 <= x0 or y1 <= y0:
			return None
		d = dst[y0 - roi.top:y1 - roi.top, x0 - roi.left:x1 - roi.left]
		return d, (y0 - rect.top, y1 - rect.top, x0 - rect.left, x1 - rect.left)

	# d = colour * a + d * (1 - a) on RGB, a + d * (1 - a) on alpha, in place, for
	# whatever the output's dtype is
	def _blend(self, np, d, color, a):
		scale = 255.0 if d.dtype == np.uint8 else 1.0
		f = d.astype(np.float32)
		if scale != 1.0:
			f /= scale
		keep = 1.0 - a
		f[..., :3] = color * a + f[..., :3] * keep
		f[..., 3:4] = a + f[..., 3:4] * keep
		if scale != 1.0:
			d[...] = np.clip(f * scale + 0.5, 0, 255).astype(np.uint8)
		else:
			d[...] = f.astype(d.dtype)

	def _drawChip(self, r, c, H):
		x, y, w, h = c["x"], c["y"], c["w"], c["h"]
		sh, strip = c["sh"], c["strip"]		# swatch height, FULL strip height (sizes the text)
		swatch = self._paint(c["swatch"])
		corner = w * CORNER

		# no text: the card is just the swatch
		if not (c["showName"] or c["showValue"]):
			r.fill(self._rect(x, y, w, h, corner), swatch)
			return

		# card: the whole card in the strip grey, then the swatch over its top --
		# rounded on top, squared off where it meets the strip
		r.fill(self._rect(x, y, w, h, corner), self._paint(self._grey(STRIP_GREY)))
		r.fill(self._rect(x, y, w, sh, corner), swatch)
		r.fill(self._rect(x, y + sh * 0.5, w, sh * 0.5), swatch)

		# text: name and/or read-out, then the chip number bottom-right
		pad = w * 0.06
		avail = w - 2.0 * pad
		top = y + sh + strip * STRIP_TOP
		if c["showName"]:
			f1 = self._fit(c["title"], strip * STRIP_NAME, avail)
			base = top + f1.ascent
			self._text(r, f1, c["title"], x + pad, base, self._paint(self._grey(NAME_GREY)))
			top = base + f1.descent + strip * STRIP_GAP
		if c["showValue"]:
			f2 = self._fit(c["value"], strip * STRIP_VALUE, avail)
			self._text(r, f2, c["value"], x + pad, top + f2.ascent, self._paint(self._grey(VALUE_GREY)))
		f3 = self._font(strip * 0.14)
		self._text(r, f3, str(c["n"]), x + w - pad * 0.6, y + h - pad * 0.5, self._paint(self._grey(VALUE_GREY)),
				   right=True)

	def _font(self, px):
		key = max(1, int(round(px * 4)))		# quarter-pixel steps
		f = self._fonts.get(key)
		if f is None:
			f = self._fonts[key] = Font(family="Arial", pointSize=key / 4.0)
		return f

	# a font at `px`, shrunk if needed so `text` fits in `width`
	def _fit(self, text, px, width):
		f = self._font(px)
		b = f.layout(text).bounds
		if b.valid and b.width > width > 0:
			f = self._font(px * width / b.width)
		return f

	# one line of text with its left (or right) ink edge at x, on baseline y.
	# layout()'s box top sits at the origin, so baseline = origin + ascent.
	def _text(self, r, font, text, x, baseline, paint, right=False):
		builder = font.layout(text)
		b = builder.bounds
		m = Matrix()
		m.translate(x - b.right if right else x, baseline - font.ascent)
		with r.transform(m):
			r.fill(builder, paint)


# "PyColorChips": the type it was first registered under, so early test projects still load
addNode(name=ColorChipsNode.TYPE, label="Color Chips", type=ColorChipsNode, categories=["Image"],
		aliases=["PyColorChips"])
