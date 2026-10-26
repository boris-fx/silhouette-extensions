"""Slate -- a VFX submission slate, in PIP or Overlay layout."""

import fx
from fx import *
from datetime import date as _date


class SlateNode(NodeProxy):
	"""Build a VFX submission slate from the input clip.

	Follows the two layouts in Netflix's published VFX submission guidelines
	(Slates & Overlays).  A `Mode` enum picks between them:

	* **PIP** (default) — a dark slate card: the entered info on the left, the
	  input clip as a picture-in-picture thumbnail top-right, an SMPTE colour-bar
	  strip, a second column of info, and a vendor-logo box bottom-right.
	* **Overlay** — the input clip full-frame with the same info burnt into the
	  four corners.

	The info fields are node properties.  Left blank they fall back to sensible
	defaults that track the project: `Show` → project name (sans `.sfx`),
	`Shot Name` → session label, `Date` → today, `Frames` → the session range.
	The vendor logo is an optional aux image input.

	Multi-field text columns are emitted with Font.layout(): the fields are
	joined with '\\n' and shaped + positioned in one call (alignment, leading,
	bidi, complex-script shaping and font fallback all handled there), so each
	column is a single fill().  The PIP thumbnail and the logo are placed with
	the Raster.resize(dod=...) overload — resampled straight into their target
	data window so the following composite() drops them in the right spot.
	Everything is laid out in proxy-render space so it sizes at any proxy level.
	"""

	TYPE = "Slate"

	MODE_PIP     = 0
	MODE_OVERLAY = 1
	SUBMIT       = ["WIP", "FINAL"]

	# right panel (PIP + colour bars + metadata column) and the logo box, as
	# fractions of the display window
	P_L     = 0.610
	P_R     = 0.980
	LOGO_L  = 0.780
	LOGO_T  = 0.820
	LOGO_R  = 0.980
	LOGO_B  = 0.955

	OV_MARGIN = 0.028	# overlay-mode title-safe inset, fraction of width

	def __init__(self, *args, **kwargs):
		# the clip (PIP thumbnail in PIP mode, full-frame background in Overlay)
		# and the optional vendor logo, plus a cached composite output (PIP mode
		# resamples + composites real pixels, so caching is worthwhile).
		self.input  = self.addInput("input", "Input")
		self.logo   = self.addInput("logo", "Vendor Logo", aux=True)
		self.output = self.addOutput("output", "Output", cache=True)

		# Mode picks the layout; constant (it changes the output DOD, so it is
		# not keyable).  getValue() returns the selected index.
		self.mode = self.addProperty("mode", {"label": "Mode", "value": 0,
											  "items": ["PIP", "Overlay"], "constant": True})

		# the slate info — all default empty and fall back at render time.
		self.show       = self.addProperty("show",       {"label": "Show",        "value": ""})
		self.submitting = self.addProperty("submitting", {"label": "Submitting For", "value": 0,
														  "items": self.SUBMIT})
		self.version    = self.addProperty("version",    {"label": "Version Name", "value": ""})
		self.date       = self.addProperty("date",       {"label": "Date",        "value": ""})
		self.vendor     = self.addProperty("vendor",     {"label": "Vendor",      "value": ""})
		self.shot       = self.addProperty("shot",       {"label": "Shot Name",   "value": ""})
		self.frames     = self.addProperty("frames",     {"label": "Frames",      "value": ""})
		self.mediaColor = self.addProperty("mediaColor", {"label": "Media Color", "value": ""})

		self._f_height = None		# fonts are cached, keyed by proxy render height

	# QUERY — Overlay passes the input metadata through (forced RGBA so the text
	# alpha composites); PIP is always a full-frame RGBA card, consuming the clip
	# as the thumbnail.
	def query(self, rc, options, type, port):
		if int(self.mode.getValue(0)) == self.MODE_OVERLAY:
			info = self.input.query(rc, options, type)
			dod = info.get("dod") if info else None
			if not dod or not dod.valid:
				return {"dod": Recti(size=rc.proxy.renderSize), "channels": Channel_RGBA}
			info["channels"] = Channel_RGBA
			return info

		# PIP: register the input/logo dependencies, then declare the card.
		self.input.query(rc, options, type)
		self.logo.query(rc, options, type)
		return {"dod": Recti(size=rc.proxy.renderSize), "channels": Channel_RGBA}

	# PROCESS — dispatch on the mode.
	def process(self, rc, options, type, port):
		if not options.roi.valid:
			return None
		if int(self.mode.getValue(0)) == self.MODE_OVERLAY:
			return self._overlay(rc, options, type)
		return self._pip(rc, options, type)

	# --- PIP mode -------------------------------------------------------------
	def _pip(self, rc, options, type):
		roi   = options.roi
		frame = options.frame
		size  = rc.proxy.renderSize
		W, H  = size.width, size.height
		fmt   = rc.format
		self._ensure_fonts(H)

		# pull the whole inputs up front (each is resampled, and the logo
		# presence decides whether to draw the placeholder).
		video = self._eval_full(self.input, rc, options, type)
		logo  = self._eval_full(self.logo, rc, options, type)

		gray  = fx.draw.Paint(color=Color(0.62, 0.62, 0.64, 1), fillMode=fx.draw.Fill.ZERO)
		white = fx.draw.Paint(color=Color(0.96, 0.96, 0.96, 1), fillMode=fx.draw.Fill.ZERO)
		band  = Color(1, 1, 1, 0.05)

		v = self._values(frame)
		first, last, count = self._frame_range()

		r = fx.draw.Renderer(size, roi, scale=rc.proxy.scale, channels=Channel_RGBA, format=fmt)
		self._rect(r, 0, 0, W, H, Color(0.12, 0.12, 0.13, 1))			# dark card

		# header row — Show (left) and Submitting For (right), large values
		lb_left   = 0.020 * W
		label_r   = 0.150 * W					# right edge of the left label column
		value_x   = label_r + W * 0.012
		sub_lbl_r = 0.470 * W
		sub_val_x = sub_lbl_r + W * 0.012
		top_base  = 0.045 * H + self._f_big.ascent
		self._line(r, self._f_field, "Show:", label_r, top_base, gray, "right")
		self._line(r, self._f_big, v["show"], value_x, top_base, white)
		self._line(r, self._f_field, "Submitting For:", sub_lbl_r, top_base, gray, "right")
		self._line(r, self._f_big, v["submit"], sub_val_x, top_base, white)

		# red separator under the header, spanning the left region
		sep_y     = 0.045 * H + self._f_big.height + H * 0.010
		sep_right = self.P_L * W - W * 0.012
		self._rect(r, lb_left, sep_y, sep_right - lb_left, H * 0.0045, Color(0.78, 0.12, 0.12, 1))

		# left field column — labels right-aligned, values left-aligned, one
		# layout() per column (joined with '\n'); a common font + leading keeps
		# the two columns row-aligned.
		lcol_top = sep_y + H * 0.020
		l_labels = self._layout(self._f_field, "Version Name:\nDate:",
								width=label_r - lb_left, align=Font.Align.RIGHT, leading=1.4)
		l_values = self._layout(self._f_field, "%s\n%s" % (v["version"], v["date"]), leading=1.4)
		self._panel(r, l_labels, lb_left, lcol_top, sep_right - lb_left, band, H * 0.006)
		self._fill(r, l_labels, lb_left, lcol_top, gray)
		self._fill(r, l_values, value_x, lcol_top, white)

		# right panel: PIP area (top), colour-bar strip, metadata column
		p_l     = self.P_L * W
		p_r     = self.P_R * W
		panel_w = p_r - p_l
		pip_top = 0.045 * H
		pip_h   = panel_w * 9.0 / 16.0
		bar_top = pip_top + pip_h + H * 0.012
		bar_h   = H * 0.030
		rcol_top = bar_top + bar_h + H * 0.022

		bars = [Color(0.75, 0.75, 0.75, 1), Color(0.75, 0.75, 0.0, 1), Color(0.0, 0.75, 0.75, 1),
				Color(0.0, 0.75, 0.0, 1), Color(0.75, 0.0, 0.75, 1), Color(0.75, 0.0, 0.0, 1),
				Color(0.0, 0.0, 0.75, 1)]
		seg = panel_w / len(bars)
		for i, c in enumerate(bars):
			self._rect(r, p_l + i * seg, bar_top, seg + 1, bar_h, c)

		# metadata column — labels left-aligned, values right-aligned to panel edge
		frames_str = self._text(self.frames, frame, "%d - %d (%d)" % (first, last, count))
		r_labels = self._layout(self._f_field, "Vendor:\nShot Name:\nFrames:\nMedia Color:", leading=1.5)
		r_values = self._layout(self._f_field, "%s\n%s\n%s\n%s" % (v["vendor"], v["shot"], frames_str, v["media"]),
								width=panel_w - W * 0.012, align=Font.Align.RIGHT, leading=1.5)
		self._panel(r, r_labels, p_l, rcol_top, panel_w, band, H * 0.006)
		self._fill(r, r_labels, p_l + W * 0.006, rcol_top, gray)
		self._fill(r, r_values, p_l + W * 0.006, rcol_top, white)

		# vendor-logo box — a white placeholder + label ONLY when nothing is
		# connected; a connected image fills the box itself (drawn after result()).
		lb_x = self.LOGO_L * W
		lb_y = self.LOGO_T * H
		lb_w = (self.LOGO_R - self.LOGO_L) * W
		lb_h = (self.LOGO_B - self.LOGO_T) * H
		if logo is None:
			self._rect(r, lb_x, lb_y, lb_w, lb_h, Color(1, 1, 1, 1))
			placeholder = fx.draw.Paint(color=Color(0.35, 0.35, 0.35, 1), fillMode=fx.draw.Fill.ZERO)
			self._line(r, self._f_field, "Vendor Logo", lb_x + lb_w * 0.5,
					   lb_y + lb_h * 0.5 + self._f_field.capHeight * 0.5, placeholder, "center")

		card = r.result()

		# drop the PIP thumbnail into the top-right panel (aspect preserved)
		if video is not None:
			pip = video.resize(dod=self._fit_box(video.width, video.height,
												 (p_l, pip_top, panel_w, pip_h)))
			if pip is not None:
				card = self._place(card, pip, roi)

		# fit the connected image inside the logo box, preserving its aspect
		# ratio (centred — the card shows through any letterbox margin).
		if logo is not None:
			lg = logo.resize(dod=self._fit_box(logo.width, logo.height,
											   (lb_x, lb_y, lb_w, lb_h)))
			if lg is not None:
				card = self._place(card, lg, roi)

		return card

	# --- Overlay mode ---------------------------------------------------------
	def _overlay(self, rc, options, type):
		roi   = options.roi
		frame = options.frame
		size  = rc.proxy.renderSize
		W, H  = size.width, size.height
		scale = rc.proxy.scale

		image = self.input.eval(rc, options, type)		# full-frame background (may be None)
		fmt   = image.format if image is not None else rc.format
		self._ensure_fonts(H)

		f      = self._f_overlay
		margin = self.OV_MARGIN * W
		top    = margin + f.ascent				# baseline just inside the top edge
		bottom = H - margin - f.descent			# baseline just inside the bottom edge

		v = self._values(frame)
		first, last, _ = self._frame_range()
		current = int(self.node.session.startFrame + frame)
		frames = self._text(self.frames, frame, "%d - %d - %d" % (first, current, last))

		r = fx.draw.Renderer(size, roi, scale=scale, channels=Channel_RGBA, format=fmt)
		self._burn(r, f, v["vendor"], margin, top, "left", scale)
		self._burn(r, f, v["show"], W * 0.5, top, "center", scale)
		self._burn(r, f, v["date"], W - margin, top, "right", scale)
		self._burn(r, f, v["version"], margin, bottom, "left", scale)
		self._burn(r, f, frames, W - margin, bottom, "right", scale)

		overlay = r.result()
		if image is None:
			return overlay						# generator: just the burn-ins
		return image.composite(overlay, roi=roi)

	# --- helpers --------------------------------------------------------------

	# pull a whole input image (over its own DOD, not just the output ROI) so it
	# can be resampled; returns None when nothing is connected.
	def _eval_full(self, port, rc, options, type):
		info = port.query(rc, options, type)
		dod  = info.get("dod") if info else None
		if not dod or not dod.valid:
			return None
		return port.eval(rc, options.at(roi=dod), type)

	# value-or-fallback for a text property (blank/whitespace -> fallback)
	def _text(self, prop, frame, fallback):
		value = prop.getValue(frame)
		if isinstance(value, str):
			value = value.strip()
		return value if value else fallback

	# the common slate fields with their render-time fallbacks
	def _values(self, frame):
		s = self.node.session
		sub = int(self.submitting.getValue(frame))
		return {
			"show":    self._text(self.show, frame, self._project_name()),
			"submit":  self.SUBMIT[sub] if 0 <= sub < len(self.SUBMIT) else "",
			"version": self._text(self.version, frame, ""),
			"date":    self._text(self.date, frame, _date.today().isoformat()),
			"vendor":  self._text(self.vendor, frame, ""),
			"shot":    self._text(self.shot, frame, s.label),
			"media":   self._text(self.mediaColor, frame, ""),
		}

	def _project_name(self):
		project = self.node.session.project
		return project.name if project else ""

	# (first, last, count) of the session's frame range
	def _frame_range(self):
		s = self.node.session
		first = int(s.startFrame)
		count = int(s.duration)
		return first, first + count - 1, count

	# build (and cache, keyed by proxy render height) the slate fonts
	def _ensure_fonts(self, height):
		if self._f_height != height:
			self._f_field   = Font(family="Arial", pointSize=height / 42.0)	# labels + values
			self._f_big     = Font(family="Arial", pointSize=height / 24.0)	# header values
			self._f_overlay = Font(family="Arial", pointSize=height / 30.0)	# overlay burn-ins
			self._f_height  = height

	# fill an axis-aligned rectangle with a solid colour
	def _rect(self, r, x, y, w, h, color):
		sb = ShapeBuilder()
		sb.rect(Rect(x, y, w, h))
		r.fill(sb, fx.draw.Paint(color=color))

	# lay out a (possibly multi-line) text block; returns its ShapeBuilder, or
	# None when there is nothing to draw.  align only applies when width > 0.
	# Font.layout() breaks on the Unicode line separator (U+2028), not on '\n',
	# so translate first to get one line per field.
	def _layout(self, font, text, width=0.0, align=None, leading=1.0):
		if not text:
			return None
		if align is None:
			align = Font.Align.LEFT
		return font.layout(text.replace("\n", "\u2028"), width=width, align=align, leading=leading)

	# fill a laid-out block with its box top-left at (x, top)
	def _fill(self, r, builder, x, top, paint):
		if builder is None:
			return
		m = Matrix()
		m.translate(x, top)
		with r.transform(m):
			r.fill(builder, paint)

	# a faint background panel sized from a laid-out block's bounds
	def _panel(self, r, builder, x, top, width, color, pad):
		if builder is None:
			return
		b = builder.bounds
		if not b.valid:
			return
		self._rect(r, x, top + b.top - pad, width, b.height + 2 * pad, color)

	# draw a single line of text with its ink anchored at x (left/right/center)
	# on baseline y.  layout() (not shape()) gives bidi / shaping / font
	# fallback; its box-top sits at the origin, so baseline = origin + ascent.
	def _line(self, r, font, text, x, baseline, paint, align="left"):
		if not text:
			return
		builder = font.layout(text)
		b = builder.bounds
		if align == "right":
			tx = x - b.right
		elif align == "center":
			tx = x - (b.left + b.right) * 0.5
		else:
			tx = x
		m = Matrix()
		m.translate(tx, baseline - font.ascent)
		with r.transform(m):
			r.fill(builder, paint)

	# overlay burn-in: a dark drop shadow then near-white text (legible over any
	# plate, and never 100% white per the Netflix overlay guidance)
	def _burn(self, r, font, text, x, baseline, align, scale):
		if not text:
			return
		d = max(1.0, scale * 1.5)
		self._line(r, font, text, x + d, baseline + d,
				   fx.draw.Paint(color=Color(0, 0, 0, 0.6), fillMode=fx.draw.Fill.ZERO), align)
		self._line(r, font, text, x, baseline,
				   fx.draw.Paint(color=Color(0.9, 0.9, 0.9, 1), fillMode=fx.draw.Fill.ZERO), align)

	# aspect-preserving centred Recti for src (w,h) fitted inside box (x,y,w,h)
	def _fit_box(self, src_w, src_h, box):
		bx, by, bw, bh = box
		if src_w > 0 and src_h > 0 and bw > 0 and bh > 0:
			src_aspect = float(src_w) / float(src_h)
			box_aspect = bw / bh
			if src_aspect > box_aspect:
				w, h = bw, bw / src_aspect
			else:
				w, h = bh * src_aspect, bh
			bx += (bw - w) * 0.5
			by += (bh - h) * 0.5
			bw, bh = w, h
		return Recti(int(round(bx)), int(round(by)),
					 width=max(1, int(round(bw))), height=max(1, int(round(bh))))

	# match a raster's pixel format to the card (channels already RGBA)
	def _fmt(self, raster, fmt):
		return raster if raster.format == fmt else raster.convert(fmt)

	# place an image inset (PIP / logo) onto the card.  An opaque (no-alpha,
	# RGB) source is copied in — compositing it would read its missing alpha as
	# transparent and show nothing; an RGBA source is composited so its alpha
	# (e.g. a logo's transparency) is honoured.
	def _place(self, dest, image, roi):
		if image.channelMask == Channel_RGB:
			return dest.copy(image, roi)
		return dest.composite(self._fmt(image, dest.format), roi=roi)


# "PySlateNode": the type it had as a development demo, so projects that used it still load
addNode(name=SlateNode.TYPE, label="Slate", type=SlateNode, categories=["Utility"],
		intents=["slate", "submission slate", "burn-in", "overlay"], dataTypes=["image"],
		aliases=["PySlateNode"])
