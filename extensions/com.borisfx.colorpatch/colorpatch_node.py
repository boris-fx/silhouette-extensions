"""Color Patch -- fill a quad or a shape with a smooth blend of colours sampled on it.

Corners mode: four viewer points (arranged in a box by default) each average a
Sample Size block of the input under them; the quad they span is then filled
with the bilinear blend of those four samples, so a marker, blemish or small
object is replaced by a smooth gradient of its surroundings.  A Transform
picked from the Transform input moves all four corners together.

Shapes mode: the shape picked from the Transform input becomes an N-gon patch
(with none picked, the input passes through).  Colours are sampled just outside its edge -- densely along it, or only
at its control points -- and the shape is filled with their mean value
interpolation (Hormann & Floater's mean value coordinates, the trick behind
Farbman et al.'s "instant image cloning"), evaluated on the GPU by a WGSL
shader.  Dense edge samples make the fill meet the image all the way round the
shape, a quick stand-in for a Laplace fill; Outside fills the rest of the frame
instead, from samples just inside the edge.

Each pixel's blend weights come from the INVERSE bilinear map of the quad (the
(u, v) with p = lerp(lerp(TL, TR, u), lerp(BL, BR, u), v)), so the fill follows
the quad's perspective-free shape exactly and each corner gets exactly its own
sample.  The quad's edge is anti-aliased by an fx.draw coverage mask, blurred
by Softness (Roto's softness: a centred blur, in the same units and range); the
Obey Matte then limits where the patch lands.  The patch is composited through
that matte on all four channels, so the output alpha carries it: an RGB input is
promoted to RGBA with a transparent alpha, leaving just the patch's matte.

The corners are transformable: link one to a Tracker or Layer on the Transform
input from its Property View menu and it follows the track.
"""

import math

import fx
from fx import *

from tools.ensure import ensure


# the corners in quad order (TL, TR, BR, BL): the bilinear map runs u along
# TL->TR (and BL->BR) and v along TL->BL (and TR->BR)
CORNERS = [("tl", "Top Left"), ("tr", "Top Right"), ("br", "Bottom Right"), ("bl", "Bottom Left")]

# default corner positions in Silhouette world units: (0,0) = frame centre, one
# unit = frame height, y down -- a box in the middle of the frame
BOX = 0.15
DEFAULT_POS = [(-BOX, -BOX), (BOX, -BOX), (BOX, BOX), (-BOX, BOX)]

# Rec. 709 luma, for the Obey Matte's Luminance channel
LUMA = (0.2126, 0.7152, 0.0722)

# the Mode, Sample and Fill popups
MODE_CORNERS, MODE_SHAPES = 0, 1
SAMPLE_EDGE, SAMPLE_POINTS = 0, 1
FILL_INSIDE, FILL_OUTSIDE = 0, 1

# a shape's outline is flattened to this many points per control point (for its
# coverage and as the curve the edge samples are spaced along)
FLATTEN = 16

# the most edge samples one shape gets (its spacing grows to fit): the samples
# ride in one row of a table texture, well inside WebGPU's 8192 texel limit
MAX_SAMPLES = 4096

# the gather in _sampleBlocks reads at most this many pixels at a time
GATHER_PIXELS = 1 << 22

# Mean value coordinates (Hormann & Floater 2006) over the closed polygons in the
# `edge` table: row 0 holds each vertex (x, y in proxy pixels, x right, y down;
# then the indices of the first and last vertex of its loop), row 1 its RGBA.
# Valid for any simple polygon, convex or not; per vertex i (s_i = v_i - p,
# r_i = |s_i|, a_i the signed angle s_i -> s_i+1, round its own loop):
#   w_i = (tan(a_i-1 / 2) + tan(a_i / 2)) / r_i,   colour = sum w_i c_i / sum w_i
# Several loops sum into one blend (MVC's form for a domain with holes).
# Outside a polygon the signed angles sum to zero, so the weights nearly cancel
# and the blend overshoots wildly away from the edge; U.exterior uses |tan| there
# instead -- every edge weighted by the angle it subtends, a convex blend that
# still meets the edge exactly (an edge's weight goes to infinity on it).
MVC_WGSL = """
struct Params {
    exterior : i32,
}

// tan(a/2) for the signed angle a from a to b (lengths ra, rb), by whichever of
// its two equal forms is better conditioned: sin a / (1 + cos a), or
// (1 - cos a) / sin a -- the second covers p on an edge (a = pi), where the
// weights of the edge's two ends blow up together and the blend stays linear.
fn tanHalf(a : vec2f, ra : f32, b : vec2f, rb : f32) -> f32 {
    let rr = ra * rb;
    let s = a.x * b.y - a.y * b.x;
    let c = dot(a, b);
    if (rr + c > abs(s)) {
        return s / (rr + c);
    }
    return (rr - c) / select(s, 1e-30, s == 0.0);
}

fn halfAngle(a : vec2f, ra : f32, b : vec2f, rb : f32) -> f32 {
    let t = tanHalf(a, ra, b, rb);
    return select(t, abs(t), U.exterior != 0);
}

fn mainImage(uv : vec2f, pos : vec2i) -> vec4f {
    let p = vec2f(uv.x * U.iResolution.x, (1.0 - uv.y) * U.iResolution.y);
    let n = i32(textureDimensions(edge).x);

    var acc = vec4f(0.0);
    var wsum = 0.0;
    var tPrev = 0.0;
    for (var i = 0; i < n; i++) {
        let v = textureLoad(edge, vec2i(i, 0), 0);
        let c = textureLoad(edge, vec2i(i, 1), 0);
        let first = i32(v.z);
        let last = i32(v.w);
        let s = v.xy - p;
        let r = length(s);
        if (r < 1e-3) {
            return c;
        }
        if (i == first) {
            // a loop starts: the angle in from its last vertex
            let sl = textureLoad(edge, vec2i(last, 0), 0).xy - p;
            tPrev = halfAngle(sl, length(sl), s, r);
        }
        let sn = textureLoad(edge, vec2i(select(i + 1, first, i == last), 0), 0).xy - p;
        let t = halfAngle(s, r, sn, length(sn));
        let w = (tPrev + t) / r;
        acc += w * c;
        wsum += w;
        tPrev = t;
    }
    return acc / select(wsum, 1e-30, wsum == 0.0);
}
"""

# numpy, loaded ONCE ON THE MAIN THREAD by _warmup(): not at module scope (a
# startup-loaded module that imports numpy locks its compiled modules into the
# process, so Preferences > Packages can no longer update it) and never from
# process(), which runs on a render thread.
_NUMPY = None


def _warmup():
	global _NUMPY
	if _NUMPY is None:
		_NUMPY = ensure("numpy")


class CornerTool(fx.EditTool):
	"""The viewer's Edit tool, plus the blocks the corners sample.

	Draws the quad and the square each corner samples, with a tie line back to the
	corner when it is offset, and dragging inside a square moves only where that
	corner samples (its sample offset) -- the gradient still meets the corner
	exactly.  A corner handle wins over its square, which surrounds it until it is
	offset; Shift-dragging the handle moves the sample instead.

	Everything is drawn where it renders: with a node-level Transform picked, that
	is the tracked quad.  The stock Edit tool can only draw and drag the corner
	properties where they are stored, so while a Transform is picked this tool
	draws the corner handles itself and maps every drag back through the inverse
	transform (as Pin Warp does); without one, the stock tool keeps the handles.
	Everything is computed in world units (the corners' own space) and mapped with
	ctx.fromWorld, so proxy, pixel aspect and the viewer's aspect correction need
	no special handling.
	"""

	SAMPLE = Color(1.0, 0.85, 0.2, 1.0)
	CORNER = Color(0.0, 1.0, 0.0, 1.0)		# the stock point handle's green
	HOT = Color(1.0, 1.0, 1.0, 1.0)

	def __init__(self, owner):
		fx.EditTool.__init__(self)
		self.owner = owner		# the Python node; owner.node is the fx.Node
		self.hot = None			# ("corner" | "sample", index) under the pointer
		self.edit = None		# the property being dragged, mouse-down .. mouse-up
		self.drag = None		# (kind, index, world anchor, tracked position at mouse-down)

	def _active(self, ctx):
		return self.owner.node.enabled and \
			int(self.owner.property("mode").getValue(ctx.frame)) == MODE_CORNERS

	# the node-level Transform while it applies, else None
	def _matrix(self, ctx):
		return self.owner.cornerMatrix(ctx.frame) if self._active(ctx) else None

	# the half size of a sample square in world units, (x, y): Sample Size is in
	# full-res pixels, so measure a pixel through the node's world-to-image map
	def _halfBlock(self, frame):
		w2i = self.owner.node.worldToImageTransform
		o, ux, uy = w2i * Point(0, 0), w2i * Point(1, 0), w2i * Point(0, 1)
		half = int(self.owner.property("sampleSize").getValue(frame)) * 0.5
		return half / max(abs(ux.x - o.x), 1e-9), half / max(abs(uy.y - o.y), 1e-9)

	# each corner's sample square as four screen points, where it renders (the
	# block is axis-aligned in the image, around the tracked sample position)
	def _squares(self, ctx):
		hx, hy = self._halfBlock(ctx.frame)
		out = []
		for c in self.owner.cornerPositions(ctx.frame, offsets=True):
			out.append([ctx.fromWorld(Point(c.x + dx, c.y + dy))
						for dx, dy in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy))])
		return out

	# the corner whose handle (within handleSize) is under screen point `pos`, or None
	def _cornerAt(self, ctx, pos):
		r = ctx.handleSize
		for i, c in enumerate(self.owner.cornerPositions(ctx.frame)):
			s = ctx.fromWorld(c)
			if abs(s.x - pos.x) <= r and abs(s.y - pos.y) <= r:
				return i
		return None

	# what is under screen point `pos`: a corner handle wins over the sample square
	# around it; None if neither
	def _hit(self, ctx, pos):
		i = self._cornerAt(ctx, pos)
		if i is not None:
			return ("corner", i)
		pad = ctx.pickSize
		for i, sq in enumerate(self._squares(ctx)):
			xs = [p.x for p in sq]
			ys = [p.y for p in sq]
			if min(xs) - pad <= pos.x <= max(xs) + pad and min(ys) - pad <= pos.y <= max(ys) + pad:
				return ("sample", i)
		return None

	def _isHot(self, kind, i):
		if self.drag is not None:
			return self.drag[0] == kind and self.drag[1] == i
		return self.hot == (kind, i)

	def draw(self, ctx):
		if not self._active(ctx):
			super().draw(ctx)
			return
		m = self._matrix(ctx)
		corners = [ctx.fromWorld(p) for p in self.owner.cornerPositions(ctx.frame)]
		ctx.setLineWidth(1.0)
		ctx.setColor(Color(self.SAMPLE.r, self.SAMPLE.g, self.SAMPLE.b, 0.5))
		ctx.drawLineLoop(corners)
		for i, (corner, sq) in enumerate(zip(corners, self._squares(ctx))):
			hot = self._isHot("sample", i)
			ctx.setColor(self.HOT if hot else self.SAMPLE)
			ctx.setLineWidth(2.0 if hot else 1.0)
			ctx.drawLineLoop(sq)
			centre = Point(sum(p.x for p in sq) * 0.25, sum(p.y for p in sq) * 0.25)
			if abs(centre.x - corner.x) + abs(centre.y - corner.y) > 1.0:
				ctx.setLineWidth(1.0)
				ctx.drawLine(corner, centre)
		if m is None:
			super().draw(ctx)					# the stock corner handles, on top
			return
		# tracked: the corner handles, drawn here where they render
		r = ctx.handleSize
		for i, c in enumerate(corners):
			hot = self._isHot("corner", i)
			ctx.setColor(self.HOT if hot else self.CORNER)
			ctx.setLineWidth(2.0 if hot else 1.0)
			ctx.drawLine(Point(c.x - r, c.y), Point(c.x + r, c.y))
			ctx.drawLine(Point(c.x, c.y - r), Point(c.x, c.y + r))
			ctx.drawLineLoop([Point(c.x - r, c.y), Point(c.x, c.y - r), Point(c.x + r, c.y), Point(c.x, c.y + r)])

	def mouseHover(self, ctx, e):
		tracked = self._matrix(ctx) is not None
		# the stock tool hovers the stored corners: only while they are where they render
		changed = False if tracked else super().mouseHover(ctx, e)
		hot = self._hit(ctx, e.pos) if self._active(ctx) else None
		if hot != self.hot:
			self.hot = hot
			ctx.setCursor(fx.Cursor.ARROW if hot is None else fx.Cursor.SIZE_ALL)
			changed = True
			ctx.update()
		if hot is None:
			if tracked:
				ctx.status("")
			return changed
		kind, i = hot
		label = CORNERS[i][1]
		if kind == "sample":
			ctx.status("%s sample: drag to take its colour from elsewhere" % label)
		else:
			ctx.status("%s (Shift-drag to move its sample)" % label)
		return True

	def mouseDown(self, ctx, e):
		if not self._active(ctx):
			return super().mouseDown(ctx, e)
		m = self._matrix(ctx)
		hit = self._hit(ctx, e.pos)
		if hit is None:
			return False
		kind, i = hit
		if kind == "corner" and e.shift:
			kind = "sample"						# Shift on a handle drags its sample
		if kind == "corner" and m is None:
			return super().mouseDown(ctx, e)	# untracked: the stock tool grabs it
		key = CORNERS[i][0] + ("" if kind == "corner" else "Offset")
		self.edit = fx.PropertyEditor(self.owner.property(key))
		tracked = self.owner.cornerPositions(ctx.frame, offsets=(kind == "sample"))[i]
		self.drag = (kind, i, ctx.toWorld(e.pos), tracked)
		return True

	def mouseMove(self, ctx, e):
		if not self.edit:
			super().mouseMove(ctx, e)
			return
		kind, i, anchor, start = self.drag
		p = ctx.toWorld(e.pos)
		# where it should now render, mapped back to the property's own space
		where = Point(start.x + p.x - anchor.x, start.y + p.y - anchor.y)
		if kind == "sample":
			target = self.owner.sampleOffsetFor(i, where, ctx.frame)
		else:
			target = _untrack(self._matrix(ctx), where)		# Prop_Point undoes the corner's own link
		if target is None:
			return								# a degenerate transform: nothing to map back to
		# both properties can be animated: always pass the frame
		self.edit.setValue(target, ctx.frame)
		self.edit.changed(transient=True)
		ctx.update()

	def mouseUp(self, ctx, e):
		if not self.edit:
			super().mouseUp(ctx, e)
			return
		kind, i = self.drag[:2]
		beginUndo("Move %s%s" % (CORNERS[i][1], " Sample" if kind == "sample" else ""))
		try:
			self.edit.execute()
		finally:
			endUndo()
		self.edit = self.drag = None
		ctx.update()

	def cancel(self, ctx):
		# restore() is required: dropping an un-executed editor leaves the values
		# already pushed in place
		if self.edit:
			self.edit.restore()
			self.edit.changed(transient=True)
			self.edit = self.drag = None
			ctx.update()
		super().cancel(ctx)

	def leave(self, ctx):
		if self.hot is not None:
			self.hot = None
			ctx.update()
		super().leave(ctx)


class ColorPatchNode(NodeProxy):
	"""Fill the quad spanned by four points, or a shape picked from the Transform
	input, with a smooth blend of the colours sampled on it.

	Corners: each corner averages the Sample Size block of pixels centred on it (a
	corner off the frame samples the nearest block inside it) and the quad gets
	their bilinear blend; an optional Transform pick moves all four.  Shapes: the
	picked shape is filled with the mean value interpolation of blocks sampled
	just outside its edge, on the GPU.
	The Obey Matte limits where the patch is applied.
	"""

	TYPE = "ColorPatch"

	def __init__(self, *args, **kwargs):
		self._shader = None
		self.input = self.addInput("input", "Input")
		# The objects port behind both pickers below (the node-level Transform and the
		# Shape), and behind each corner's own transform menu: a linked corner's
		# getValue() comes back already transformed.
		self.transform = self.addInput("transformInput", "Transform", dataTypes=["objects", "transform"])
		self.output = self.addOutput("output", "Output", cache=True)

		self.addProperty("mode", {"label": "Mode", "value": MODE_CORNERS, "items": ["Corners", "Shapes"],
								  "supervise": True,
								  "tooltip": "Corners: fill the quad between the four corner points.\n"
											 "Shapes: fill the shape picked from the Transform input."})

		self.addProperty("corners", {"label": "Corners", "type": "group", "expand": True})
		# a tracker or layer that moves all four corners (and their sample offsets)
		# together, applied after any corner's own transform link
		self.addProperty("cornerTransform", {"label": "Transform", "type": "object", "port": "transformInput",
											 "dataTypes": [fx.DataType.TRANSFORM], "group": "corners",
											 "tooltip": "A tracker or layer on the Transform input that "
														"moves all four corners"})
		for (key, label), pos in zip(CORNERS, DEFAULT_POS):
			self.addProperty(key, {"label": label, "value": Point(*pos), "group": "corners",
								   "transformable": True})
		# where each corner SAMPLES, relative to it: the gradient still runs exactly
		# between the corners, but a corner sitting on an edge can take its colour
		# from just off it.  World units like the corners, shown in pixels; the
		# Edit tool (CornerTool) drags them in the viewer.
		self.addProperty("sampleOffsets", {"label": "Sample Offsets", "type": "group", "group": "corners"})
		for key, label in CORNERS:
			self.addProperty(key + "Offset", {"label": label, "value": Point(0.0, 0.0), "type": "offset",
											  "group": "sampleOffsets",
											  "tooltip": "Where this corner's colour is sampled, relative to it"})

		self.addProperty("shapes", {"label": "Shapes", "type": "group", "expand": True})
		# the one shape to fill; with none picked the node passes its input through
		self.addProperty("shape", {"label": "Shape", "type": "object", "port": "transformInput",
								   "dataTypes": [fx.DataType.MASK], "objectTypes": ["Shape"], "group": "shapes",
								   "tooltip": "The shape on the Transform input to fill (none: no patch)"})
		self.addProperty("sample", {"label": "Sample", "value": SAMPLE_EDGE, "items": ["Along Edge", "At Points"],
									"group": "shapes", "supervise": True,
									"tooltip": "Along Edge: sample all the way round the shape, so the fill meets "
											   "the image everywhere.\nAt Points: sample only at the shape's "
											   "control points, for a smooth gradient between their colours."})
		self.addProperty("spacing", {"label": "Edge Spacing", "value": 4.0, "range": [1.0, 64.0],
									 "group": "shapes",
									 "tooltip": "Distance in pixels between the samples along the edge"})
		# where the colours come from, relative to the shape's edge: positive samples
		# further out along its normals, negative further in.  The patch itself still
		# covers exactly the shape, so a roto drawn tight round the subject can take
		# its colours from clean ground a few pixels beyond it.
		self.addProperty("sampleOffset", {"label": "Sample Offset", "value": 0.0, "range": [-200.0, 200.0],
										  "group": "shapes",
										  "tooltip": "Distance in pixels from the shape's edge to where colours are "
													 "sampled: positive outside the shape, negative inside it"})
		self.addProperty("fill", {"label": "Fill", "value": FILL_INSIDE, "items": ["Inside", "Outside"],
								  "group": "shapes",
								  "tooltip": "Inside: replace the shape, from samples just outside it.\n"
											 "Outside: replace everything but the shape, from samples just inside it."})

		self.addProperty("sampleSize", {"label": "Sample Size", "value": 9, "type": "int",
										"range": [1, 256],
										"tooltip": "Width in pixels of the square block each sample averages"})
		# Roto's softness ("alpha.blur"): a "pixels" number, stored as a percentage
		# of the frame height and shown in pixels
		self.addProperty("softness", {"label": "Softness", "value": 0.0, "type": "pixels",
									  "range": [0.0, 100.0],
									  "tooltip": "Blur of the patch's edge, centred on the quad"})

	# Main-thread warm-up, from BOTH setup() and updatePropertyState(): setup()
	# fires when the node joins a session, but reloading the extension re-imports
	# this module (emptying the cache) with no setup() to refill it;
	# updatePropertyState() runs at load and on every supervised change.
	def _warmup(self):
		try:
			_warmup()
		except Exception as e:
			print("[Color Patch] numpy is unavailable:", e)

	def setup(self):
		self._warmup()

	def updatePropertyState(self):
		self._warmup()
		shapes = int(self.property("mode").getValue(0)) == MODE_SHAPES
		self.property("cornerTransform").enabled = not shapes
		for key, _ in CORNERS:
			self.property(key).enabled = not shapes
			self.property(key + "Offset").enabled = not shapes
		for key in ("shape", "sample", "fill", "sampleOffset"):
			self.property(key).enabled = shapes
		self.property("spacing").enabled = shapes and int(self.property("sample").getValue(0)) == SAMPLE_EDGE

	# the compiled MVC shader, built on first use; None (with the reason set on rc)
	# when there is no GPU or it failed to compile
	def _mvcShader(self, rc):
		if self._shader is None:
			self._shader = fx.WGPUShader(MVC_WGSL)
		if not self._shader.valid:
			if not fx.WGPUShader.available():
				rc.setError("Color Patch: Shapes mode needs the GPU (%s)" % fx.WGPUShader.deviceError())
			else:
				rc.setError("Color Patch: " + self._shader.error)
			return None
		return self._shader

	def tools(self):
		return [CornerTool(self)]

	# the node-level Transform at frame as an fx.Matrix (world to world), or None
	# when nothing is picked or the pick doesn't generate a transform
	def cornerMatrix(self, frame):
		t = self.property("cornerTransform").getValue(frame)
		if t is None or not t.generatesDataType(fx.DataType.TRANSFORM):
			return None
		return t.evaluate(fx.DataType.TRANSFORM, frame)

	# a corner's own transform link at frame (the tracker or layer picked in its
	# transform menu) as an fx.Matrix, world to world; None when unlinked
	def cornerLink(self, key, frame):
		obj = getattr(self.property(key), "transformObject", None)		# absent on older builds
		if obj is None or not obj.generatesDataType(fx.DataType.TRANSFORM):
			return None
		return obj.evaluate(fx.DataType.TRANSFORM, frame)

	# the corners in world units, in quad order; with `offsets`, where each one
	# samples instead.  A corner's value already comes back through its own link
	# (L * raw), and its offset is relative to the raw corner, so the sample is
	# L * (raw + offset): it turns and scales with the track like the corner does.
	# `tracked` then applies the node-level Transform -- where they render;
	# untracked is the corners as their properties evaluate.
	def cornerPositions(self, frame, offsets=False, tracked=True):
		m = self.cornerMatrix(frame) if tracked else None
		out = []
		for key, _ in CORNERS:
			v = self.property(key).getValue(frame)
			pos = Point(v.x, v.y)
			if offsets:
				off = self.property(key + "Offset").getValue(frame)
				pos = _offsetThrough(self.cornerLink(key, frame), pos, off)
			out.append(m * pos if m is not None else pos)
		return out

	# the offset that puts corner `i`'s sample at `target` (world units, where it
	# renders): back through the node-level Transform, then through the corner's
	# own link, relative to the raw corner.  None when a transform can't be undone.
	def sampleOffsetFor(self, i, target, frame):
		key = CORNERS[i][0]
		q = _untrack(self.cornerMatrix(frame), target)
		if q is None:
			return None
		v = self.property(key).getValue(frame)
		corner = Point(v.x, v.y)
		link = self.cornerLink(key, frame)
		if link is not None:
			q, corner = _untrack(link, q), _untrack(link, corner)
			if q is None or corner is None:
				return None
		return Point(q.x - corner.x, q.y - corner.y)

	# the corners (or with `offsets`, their sample centres) in proxy pixels (x
	# right, y down), in quad order
	def _corners(self, rc, frame, offsets=False):
		w2i = self.node.worldToImageTransform
		scale = rc.proxy.scale
		out = []
		for pos in self.cornerPositions(frame, offsets):
			p = w2i * pos							# full-res image pixels
			out.append((p.x * scale, p.y * scale))
		return out

	# the pixels the quad touches (its bounds plus a pixel for the anti-aliased
	# edge), clipped to `clip` (unclipped when None); None when they don't meet
	def _quadBounds(self, quad, clip):
		x0 = int(math.floor(min(p[0] for p in quad))) - 1
		y0 = int(math.floor(min(p[1] for p in quad))) - 1
		x1 = int(math.ceil(max(p[0] for p in quad))) + 1
		y1 = int(math.ceil(max(p[1] for p in quad))) + 1
		rect = Recti(x0, y0, width=x1 - x0, height=y1 - y0)
		return rect if clip is None else _clip(rect, clip)

	# the softness blur radii in proxy pixels, (x, y), as Roto computes them: the
	# value is a percentage of the frame height, and x is divided by the pixel
	# aspect so the blur is round on screen
	def _softness(self, rc, frame):
		ry = max(0.0, float(self.property("softness").getValue(frame))) / 100.0 * rc.proxy.renderSize.height
		pa = rc.pixelAspect if rc.pixelAspect > 0.0 else 1.0
		return ry / pa, ry

	# the pixels the patch's matte can touch: the quad's bounds grown by the
	# blur's reach (a little past its radius), clipped to `clip`
	def _matteBounds(self, quad, soft, clip):
		r = self._quadBounds(quad, None)
		px = int(math.ceil(soft[0] * 1.5)) + 2 if soft[0] > 0.0 else 0
		py = int(math.ceil(soft[1] * 1.5)) + 2 if soft[1] > 0.0 else 0
		return _clip(Recti(r.left - px, r.top - py, width=r.width + 2 * px, height=r.height + 2 * py), clip)

	# QUERY -- the input's metadata, RGBA (the patch carries the samples' alpha,
	# and an RGB input is promoted with a transparent alpha), with the DOD grown
	# to cover the patch's matte (clipped to the frame)
	def query(self, rc, options, type, port):
		info = self.input.query(rc, options, type)
		if not info:
			return info
		size = rc.proxy.renderSize
		frame = Recti(size=size)
		dod = info.get("dod")
		if int(self.property("mode").getValue(options.frame)) == MODE_SHAPES:
			# Outside can patch anywhere in the frame; Inside only the picked shape's
			# matte; nothing picked, nothing patched
			picked = self._pickedShape(options.frame)
			if picked is None:
				quad = None
			elif int(self.property("fill").getValue(options.frame)) == FILL_OUTSIDE:
				quad = frame
			else:
				np = _NUMPY
				if np is None:
					quad = frame						# can't flatten the outline yet
				else:
					outline = self._outline(np, picked[0], picked[1], rc.proxy.scale)
					quad = self._matteBounds(_tuples(outline), self._softness(rc, options.frame), frame)
		else:
			quad = self._matteBounds(self._corners(rc, options.frame), self._softness(rc, options.frame), frame)
		if quad is not None:
			dod = quad if not (dod and dod.valid) else dod.union(quad)
		if dod is not None:
			info["dod"] = dod
		info["channels"] = Channel_RGBA
		return info

	def process(self, rc, options, type, port):
		roi = options.roi
		if not roi.valid:
			return None
		np = _NUMPY
		if np is None:
			rc.setError("Color Patch: numpy is unavailable")
			return None

		image = self.input.eval(rc, options, type)
		if image is None:
			return None					# nothing to sample

		frame = options.frame
		if int(self.property("mode").getValue(frame)) == MODE_SHAPES:
			return self._processShapes(np, rc, options, type, image)

		size  = rc.proxy.renderSize
		W, H  = size.width, size.height
		scale = rc.proxy.scale
		quad  = self._corners(rc, frame)
		soft  = self._softness(rc, frame)

		# the matte is built over its whole reach (clipped only to the frame), so
		# a blurred edge crossing an ROI boundary still blurs in from outside;
		# `region` is the part of it in this ROI
		layer = self._matteBounds(quad, soft, Recti(size=size))
		region = _clip(layer, roi) if layer is not None else None
		if region is None:
			# the patch misses this ROI: an RGBA input passes straight through
			if image.channelMask == Channel_RGBA and image.format == rc.format:
				return image

		out = self._copy(np, rc, image, roi)
		if region is None:
			return out

		# the corner samples, (4, 4) RGBA, each taken at its corner's sample offset
		block = max(1, int(round(int(self.property("sampleSize").getValue(frame)) * scale)))
		samples = np.array([self._sample(np, rc, options, type, p, block, W, H)
							for p in self._corners(rc, frame, offsets=True)])

		# coverage (anti-aliased quad, softened), times the Obey Matte
		k = self._coverage(np, rc, [quad], layer, soft, region)
		matte = self._matte(np, rc, options, region)
		if matte is not None:
			k *= matte
		if not k.any():
			return out

		# each pixel centre's (u, v) in the quad -> the bilinear blend of the samples
		xs = np.arange(region.left, region.left + region.width, dtype=np.float64) + 0.5
		ys = np.arange(region.top, region.top + region.height, dtype=np.float64) + 0.5
		px, py = np.meshgrid(xs, ys)
		u, v = _invBilinear(np, quad, px, py)
		wts = np.stack([(1 - u) * (1 - v), u * (1 - v), u * v, (1 - u) * v], axis=-1)
		patch = (wts @ samples).astype(np.float32)			# (h, w, 4)

		d = np.asarray(out)[region.top - roi.top:region.top - roi.top + region.height,
							region.left - roi.left:region.left - roi.left + region.width]
		_mix(np, d, patch, k[..., None])
		return out

	# SHAPES mode: the picked shape gets the mean value interpolation of colours
	# sampled just off its edge -- just outside it for Inside, which replaces the
	# shape, or just inside it for Outside, which replaces the rest of the frame --
	# composited through its softened coverage and the Obey Matte.  With no shape
	# picked (or one that isn't a closed outline) the input passes through.
	def _processShapes(self, np, rc, options, type, image):
		roi   = options.roi
		frame = options.frame
		out   = self._copy(np, rc, image, roi)

		picked = self._pickedShape(frame)
		if picked is None:
			return out
		path, M = picked
		shader = self._mvcShader(rc)
		if shader is None:
			return out

		scale   = rc.proxy.scale
		size    = rc.proxy.renderSize
		W, H    = size.width, size.height
		clip    = Recti(size=size)
		soft    = self._softness(rc, frame)
		block   = max(1, int(round(int(self.property("sampleSize").getValue(frame)) * scale)))
		dense   = int(self.property("sample").getValue(frame)) == SAMPLE_EDGE
		spacing = max(0.5, float(self.property("spacing").getValue(frame)) * scale)
		outside = int(self.property("fill").getValue(frame)) == FILL_OUTSIDE
		# the sampling line: the edge moved along its outward normals (proxy pixels)
		offset  = float(self.property("sampleOffset").getValue(frame)) * scale

		# the outline in proxy pixels, and the pixels the patch can touch
		outline = self._outline(np, path, M, scale)
		poly = _tuples(outline)
		layer = clip if outside else self._matteBounds(poly, soft, clip)
		region = _clip(layer, roi) if layer is not None else None
		if region is None:
			return out

		# coverage of the region to patch, times the Obey Matte
		k = self._coverage(np, rc, [poly], layer, soft, region)
		if outside:
			k = 1.0 - k
		m = self._matte(np, rc, options, region)
		if m is not None:
			k *= m
		if not k.any():
			return out

		# the samples: points on the edge, each averaging a block pushed clear of the
		# edge on the side being kept.  Both modes stay within MAX_SAMPLES (one table
		# row): Along Edge widens its spacing, At Points takes evenly spread points.
		# The push clears a straight edge, but no placement keeps a block on the kept
		# side of an acute corner, so each block also averages only the pixels that
		# really are on the kept side of the shape (below).
		if dense:
			pts, tangents = _resampleClosed(np, outline, spacing, MAX_SAMPLES)
		else:
			ts = [path.getT(int(i)) for i in _spread(np, path.numPoints, MAX_SAMPLES)]
			pts = _project(np, M, path.evalPoints(ts), scale)
			tangents = _project(np, M, path.evalPoints([t + 0.05 for t in ts]), scale) - \
					   _project(np, M, path.evalPoints([t - 0.05 for t in ts]), scale)
		# each block sits on the sampling line (the edge moved Sample Offset along its
		# outward normal), pushed clear of it on the kept side: outward for Inside,
		# inward for Outside
		normals = _outwardNormals(np, outline, tangents)
		push = offset + _clearance(np, normals, block) * (-1.0 if outside else 1.0)
		centres = pts + normals * push[:, None]

		# the colours, from kept-side pixels only; a sample whose block has none (a
		# block wider than a sharp corner leaves room for) is dropped from the loop
		colours, valid = self._sampleBlocks(np, rc, options, type, centres, block, W, H,
											poly=poly, keepInside=outside, offset=offset)
		centres, colours = centres[valid], colours[valid]
		if len(centres) < 3:
			return out							# nothing left to blend from

		# row 0: x, y, then the first and last vertex of the loop (one loop here;
		# the shader sums any number); row 1: the RGBA samples
		table = np.zeros((2, len(centres), 4), np.float32)
		table[0, :, :2] = centres
		table[0, :, 2:] = (0, len(centres) - 1)
		table[1] = colours
		edge = Raster.fromBuffer(table)
		edge.table = True

		patch = Raster.fromBuffer(np.zeros((region.height, region.width, 4), np.float32), dod=region)
		# Signed MVC needs every patched pixel inside the sample loop.  Outside never
		# has that, and nor does Inside once the samples move into the shape (a
		# negative offset leaves a band of the patch between the edge and the loop):
		# both take the angle weights, a convex blend that still meets the samples.
		exterior = outside or offset < 0.0
		shader.evaluate(patch, size, frame, 0.0, {"edge": edge, "exterior": int(exterior)})
		if shader.runtimeError:
			rc.setError("Color Patch: " + shader.runtimeError)
			return out

		p = np.asarray(patch).reshape(region.height, region.width, 4)
		d = np.asarray(out)[region.top - roi.top:region.top - roi.top + region.height,
							region.left - roi.left:region.left - roi.left + region.width]
		_mix(np, d, p, k[..., None])
		return out

	# the picked shape at frame as (ShapePath, Matrix), the matrix taking the path's
	# own space to full-res image pixels (its layers' transforms, collapsed, then
	# world to image); None with nothing picked or no closed outline to fill
	def _pickedShape(self, frame):
		shape = self.property("shape").getValue(frame)
		if shape is None:
			return None
		path = shape.path.getValue(frame)
		if path is None or not path.closed or path.numPoints < 3:
			return None
		return path, self.node.worldToImageTransform * shape.getTransform(frame)

	# the shape's outline in proxy pixels, (n, 2)
	def _outline(self, np, path, M, scale):
		return _project(np, M, path.evalPoints(max(64, FLATTEN * path.numPoints)), scale)

	# The RGBA averages of the block x block squares centred on `centres` (n, 2):
	# ((n, 4) float32, (n,) bool -- whether each block had any pixel to average).
	# As in _sample, a block off the frame is pulled in to lie inside it.  The input
	# is evaluated once over all the blocks' bounds.  With `poly` (the shape, proxy
	# pixels), only pixels wholly on the kept side of the sampling line count: the
	# shape grown by `offset` (shrunk when negative), and then inside it when
	# `keepInside`, else outside it.  Anti-aliased edge pixels are mixed, so they
	# count for neither side.
	def _sampleBlocks(self, np, rc, options, type, centres, block, W, H, poly=None, keepInside=False,
					  offset=0.0):
		bw, bh = min(block, W), min(block, H)
		x0 = np.clip(np.round(centres[:, 0] - block * 0.5).astype(np.int64), 0, W - bw)
		y0 = np.clip(np.round(centres[:, 1] - block * 0.5).astype(np.int64), 0, H - bh)
		left, top = int(x0.min()), int(y0.min())
		box = Recti(left, top, width=int(x0.max()) + bw - left, height=int(y0.max()) + bh - top)
		a = _read(np, self.input.eval(rc, options.at(roi=box), type), box)
		keep = None
		if poly is not None:
			cov = self._offsetCoverage(np, rc, poly, offset, box, box)
			keep = cov >= 1.0 - 1e-3 if keepInside else cov <= 1e-3
		return _blockMeans(np, a, keep, x0 - left, y0 - top, bw, bh)

	# the output raster over the ROI, holding the input.  Raster.copy converts
	# format/channels in place, but an RGB input lands opaque: its alpha is
	# cleared, so the output alpha ends up as just the patch's matte.
	def _copy(self, np, rc, image, roi):
		out = self.allocate(rc, roi=roi)
		d = image.dod
		covered = d is not None and d.left <= roi.left and d.top <= roi.top and \
			d.left + d.width >= roi.left + roi.width and d.top + d.height >= roi.top + roi.height
		if not covered:
			out.clear()
		out.copy(image, roi)
		if not (image.channelMask & Channel_A):
			a = np.asarray(out)
			a.reshape(a.shape[0], a.shape[1], -1)[..., 3] = 0
		return out

	# the RGBA average of the block x block square centred on (px, py); a centre
	# off the frame is pulled in so the block always lies (as far as it can) inside
	def _sample(self, np, rc, options, type, p, block, W, H):
		bw, bh = min(block, W), min(block, H)
		x0 = int(round(p[0] - block * 0.5))
		y0 = int(round(p[1] - block * 0.5))
		x0 = min(max(x0, 0), W - bw)
		y0 = min(max(y0, 0), H - bh)
		cell = Recti(x0, y0, width=bw, height=bh)
		raster = self.input.eval(rc, options.at(roi=cell), type)
		return _read(np, raster, cell).reshape(-1, 4).mean(axis=0).astype(np.float64)

	# the anti-aliased coverage of the union of `polys` (lists of (x, y)), rendered
	# over `layer`, blurred by `soft` (x, y radii in pixels) and returned over
	# `region` (inside `layer`) as float32 (h, w).  Each polygon is its own fill,
	# so overlapping ones that wind opposite ways still union.  Always 32-bit
	# float: the 1-channel half-float blur path is not safe (fxblur.cpp blurs half
	# data as uint16).
	def _coverage(self, np, rc, polys, layer, soft, region):
		r = fx.draw.Renderer(rc.proxy.renderSize, layer, scale=rc.proxy.scale,
							 channels=Channel_A, format=fx.Depth_F32)
		for poly in polys:
			sb = poly if isinstance(poly, ShapeBuilder) else _builder(poly)
			r.fill(sb, fx.draw.Paint(color=Color(1, 1, 1, 1), fillMode=fx.draw.Fill.ZERO))
		mask = r.result()
		if soft[0] > 0.0 or soft[1] > 0.0:
			mask.blur(Point(soft[0], soft[1]))
		m = np.asarray(mask)
		m = m.reshape(m.shape[0], m.shape[1], -1)[..., -1]
		d = mask.dod
		y0, x0 = region.top - d.top, region.left - d.left
		return np.clip(m[y0:y0 + region.height, x0:x0 + region.width], 0.0, 1.0).astype(np.float32)

	# The coverage of `poly` grown by `offset` proxy pixels (shrunk when negative)
	# over `region` (inside `layer`), unblurred, as float32 (h, w).  An exact round
	# offset: the band within |offset| of the edge is a round-jointed stroke of width
	# 2 |offset|, so the grown shape is fill + band and the shrunk one fill - band.
	def _offsetCoverage(self, np, rc, poly, offset, layer, region):
		fill = self._coverage(np, rc, [poly], layer, (0.0, 0.0), region)
		if abs(offset) < 1e-3:
			return fill
		band = _builder(poly).stroked(2.0 * abs(offset), joinStyle=ShapeBuilder.Join.ROUND,
									  capStyle=ShapeBuilder.Cap.ROUND)
		near = self._coverage(np, rc, [band], layer, (0.0, 0.0), region)
		return np.maximum(fill, near) if offset > 0.0 else fill * (1.0 - near)

	# the Obey Matte over `region` as a float32 (h, w) weight, or None when it is
	# off or unconnected.  Mirrors Node::getMask: the chosen channel, optionally
	# inverted, times the opacity.
	def _matte(self, np, rc, options, region):
		node = self.node
		port = node.matte
		if port is None or not port.connected or not node.property("obey_matte.obey").getValue(0):
			return None
		frame = options.frame
		raster = port.eval(rc, options.at(roi=region))
		a = _read(np, raster, region, opaque=False)
		channel = int(node.property("obey_matte.channel").getValue(frame))
		if channel == 4:
			m = a[..., 0] * LUMA[0] + a[..., 1] * LUMA[1] + a[..., 2] * LUMA[2]
		else:
			m = a[..., channel]
		m = np.clip(m, 0.0, 1.0)
		if node.property("obey_matte.invert").getValue(frame):
			m = 1.0 - m
		return m * (float(node.property("obey_matte.opacity").getValue(frame)) / 100.0)


# a closed polygon (a list of (x, y)) as a ShapeBuilder
def _builder(poly):
	sb = ShapeBuilder()
	sb.move(Point(*poly[0]))
	for p in poly[1:]:
		sb.line(Point(*p))
	sb.close()
	return sb


# fx.Points (a path's own space) through M (to full-res image pixels) and on to
# proxy pixels, as float64 (n, 2)
def _project(np, M, points, scale):
	q = [M * p for p in points]
	return np.array([(p.x, p.y) for p in q], np.float64).reshape(-1, 2) * scale


def _tuples(points):
	return [(float(x), float(y)) for x, y in points]


# `count` points evenly spaced along the closed polyline `poly` (n, 2), about
# `spacing` apart (further, to stay within `limit` points), with the polyline's
# direction at each: ((count, 2), (count, 2))
def _resampleClosed(np, poly, spacing, limit):
	ring = np.vstack([poly, poly[:1]])
	cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(ring, axis=0).T))])
	total = cum[-1]
	count = int(min(max(math.ceil(total / spacing), 3), limit))
	s = np.arange(count) * (total / count)
	pts = np.stack([np.interp(s, cum, ring[:, 0]), np.interp(s, cum, ring[:, 1])], axis=1)
	return pts, np.roll(pts, -1, axis=0) - np.roll(pts, 1, axis=0)


# unit normals to `tangents` pointing out of the closed polyline `outline`,
# whichever way it winds: with the shoelace area A > 0, (ty, -tx) points out
def _outwardNormals(np, outline, tangents):
	x, y = outline[:, 0], outline[:, 1]
	area = 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
	n = np.stack([tangents[:, 1], -tangents[:, 0]], axis=1)
	n /= np.maximum(np.hypot(n[:, 0], n[:, 1]), 1e-12)[:, None]
	return n if area > 0.0 else -n


# `p` (a corner as its property evaluates: L * raw) moved by `off` in the corner's
# raw space, so the offset turns and scales with the corner's link `L` (fx.Matrix,
# or None for unlinked: plain p + off).  Falls back to p + off if L can't be undone.
def _offsetThrough(link, p, off):
	if link is not None:
		raw = _untrack(link, p)
		if raw is not None:
			return link * Point(raw.x + off.x, raw.y + off.y)
	return Point(p.x + off.x, p.y + off.y)


# `p` (world units) taken back through `m` -- the node-level Transform or a
# corner's own link (fx.Matrix, world to world; None = identity): the point m
# maps onto p, or None when m is degenerate (or numpy isn't loaded yet).  m's 2D
# part is the homography in rows/columns 0, 1, 3 (column vectors), so a
# perspective track maps back exactly.  fx.Matrix has no inverse; numpy is loaded
# on the main thread by _warmup() before any tool or render runs.
def _untrack(m, p):
	if m is None:
		return p
	np = _NUMPY
	if np is None:
		return None
	idx = (0, 1, 3)
	h = np.array([[m.value(r, c) for c in idx] for r in idx], np.float64)
	try:
		q = np.linalg.solve(h, np.array([p.x, p.y, 1.0]))
	except np.linalg.LinAlgError:
		return None
	if abs(q[2]) < 1e-12:
		return None
	return Point(float(q[0] / q[2]), float(q[1] / q[2]))


# The means of the bw x bh blocks at (lx, ly) (top-left, (n,) ints) in `a`
# (h, w, 4), counting only the pixels where `keep` (h, w bool) is set -- every
# pixel when it is None: ((n, 4) float32, (n,) bool -- whether each block had any
# pixel to count).  Gathered in chunks of at most GATHER_PIXELS pixels.
def _blockMeans(np, a, keep, lx, ly, bw, bh):
	n = len(lx)
	out = np.zeros((n, 4), np.float32)
	valid = np.ones(n, bool)
	dy, dx = np.mgrid[0:bh, 0:bw]
	step = max(1, GATHER_PIXELS // (bw * bh))
	for i in range(0, n, step):
		ys = ly[i:i + step, None, None] + dy
		xs = lx[i:i + step, None, None] + dx
		px = a[ys, xs].reshape(len(ys), -1, 4)
		if keep is None:
			out[i:i + step] = px.mean(axis=1)
			continue
		w = keep[ys, xs].reshape(len(ys), -1).astype(np.float32)
		count = w.sum(axis=1)
		out[i:i + step] = (px * w[..., None]).sum(axis=1) / np.maximum(count, 1.0)[:, None]
		valid[i:i + step] = count > 0
	return out, valid


# indices 0..n-1, or `limit` of them spread evenly round the loop when n is larger
def _spread(np, n, limit):
	if n <= limit:
		return list(range(n))
	return [int(i) for i in np.unique(np.linspace(0, n, limit, endpoint=False).astype(np.int64))]


# How far along each unit normal (n, 2) a block x block sample square must sit
# from the edge so that none of its pixels lie on the far side: the square's
# extent in that direction is block/2 * (|nx| + |ny|) -- a pixel centre is at most
# (block - 1)/2 off the square's centre per axis, plus up to 0.5 from rounding
# the square onto the pixel grid in _sampleBlocks -- and half a pixel more clears
# the anti-aliased edge pixel itself.  Axis-aligned edges need block/2 + 0.5; a
# 45-degree edge needs about 0.71 * block + 0.5.
def _clearance(np, normals, block):
	return 0.5 * block * (np.abs(normals[:, 0]) + np.abs(normals[:, 1])) + 0.5


def _clip(rect, clip):
	x0, y0 = max(rect.left, clip.left), max(rect.top, clip.top)
	x1 = min(rect.left + rect.width, clip.left + clip.width)
	y1 = min(rect.top + rect.height, clip.top + clip.height)
	if x1 <= x0 or y1 <= y0:
		return None
	return Recti(x0, y0, width=x1 - x0, height=y1 - y0)


# a raster's pixels over `rect` as float32 (h, w, 4), zero outside its DOD.  An
# image without an alpha channel reads as opaque (transparent with `opaque` off).
def _read(np, raster, rect, opaque=True):
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
	a = a.reshape(a.shape[0], a.shape[1], -1)
	src = a[y0 - d.top:y1 - d.top, x0 - d.left:x1 - d.left].astype(np.float32)
	if a.dtype == np.uint8:
		src /= 255.0
	n = min(src.shape[2], 4)
	dst = out[y0 - rect.top:y1 - rect.top, x0 - rect.left:x1 - rect.left]
	if n == 1:
		dst[..., :] = src[..., :1]			# a one-channel image: every channel
	else:
		dst[..., :n] = src[..., :n]
		# an RGB image may still sit in a 4-channel buffer (with junk alpha): trust
		# the channel mask, not the buffer shape
		if n < 4 or not (raster.channelMask & Channel_A):
			dst[..., 3] = 1.0 if opaque else 0.0
	return out


# The inverse bilinear map of the quad (a, b, c, d) = (TL, TR, BR, BL): for each
# point p, the (u, v) in [0, 1]^2 with
#   p = a + (b - a) u + (d - a) v + (a - b + c - d) u v.
# Solves the quadratic in v, keeping the root that lands inside the quad, then u
# from whichever axis is better conditioned.  Points just outside the quad (the
# anti-aliased edge) clamp to it.
def _invBilinear(np, quad, px, py):
	(ax, ay), (bx, by), (cx, cy), (dx, dy) = quad
	ex, ey = bx - ax, by - ay
	fx_, fy = dx - ax, dy - ay
	gx, gy = ax - bx + cx - dx, ay - by + cy - dy
	hx, hy = px - ax, py - ay

	def cross(x0, y0, x1, y1):
		return x0 * y1 - y0 * x1

	k2 = cross(gx, gy, fx_, fy)
	k1 = cross(ex, ey, fx_, fy) + cross(hx, hy, gx, gy)
	k0 = cross(hx, hy, ex, ey)

	def solveU(v):
		den_x = ex + gx * v
		den_y = ey + gy * v
		use_x = np.abs(den_x) >= np.abs(den_y)
		den = np.where(use_x, den_x, den_y)
		num = np.where(use_x, hx - fx_ * v, hy - fy * v)
		return num / np.where(np.abs(den) < 1e-12, 1e-12, den)

	with np.errstate(divide="ignore", invalid="ignore"):
		if abs(k2) < 1e-9 * max(1.0, abs(cross(ex, ey, fx_, fy))):
			# a parallelogram (or a trapezoid with TL-BL parallel to TR-BR): linear in v
			v = -k0 / np.where(np.abs(k1) < 1e-12, 1e-12, k1)
			u = solveU(v)
		else:
			w = np.sqrt(np.maximum(k1 * k1 - 4.0 * k0 * k2, 0.0))
			v1 = (-k1 - w) / (2.0 * k2)
			v2 = (-k1 + w) / (2.0 * k2)
			u1, u2 = solveU(v1), solveU(v2)
			# prefer the root inside the unit square, else the one nearest it
			def outside(u, v):
				return np.maximum(np.maximum(-u, u - 1.0), 0.0) + np.maximum(np.maximum(-v, v - 1.0), 0.0)
			pick1 = outside(u1, v1) <= outside(u2, v2)
			u = np.where(pick1, u1, u2)
			v = np.where(pick1, v1, v2)
	u = np.clip(np.nan_to_num(u), 0.0, 1.0)
	v = np.clip(np.nan_to_num(v), 0.0, 1.0)
	return u, v


# d = patch * k + d * (1 - k) on all four channels, in place, for whatever the
# output's dtype is
def _mix(np, d, patch, k):
	scale = 255.0 if d.dtype == np.uint8 else 1.0
	f = d.astype(np.float32)
	if scale != 1.0:
		f /= scale
	f = patch * k + f * (1.0 - k)
	if scale != 1.0:
		d[...] = np.clip(f * scale + 0.5, 0, 255).astype(np.uint8)
	else:
		d[...] = f.astype(d.dtype)


addNode(name=ColorPatchNode.TYPE, label="Color Patch", type=ColorPatchNode, categories=["Image"], obey_matte=True)
