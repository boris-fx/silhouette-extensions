"""Render a node's input to a movie file — the pipeline's "render out" stage.

fx-side (main thread).  Renders a frame range of any node port through the
graph and streams it into a movie via ``MediaFormat`` — the same writer path
``tools/renderer.py`` drives for Output nodes, without needing one in the
tree.  This is deliberately **blocking with a progress dialog**: the provider
runs it inside ``build()`` before the async task is launched, so the user
watches a normal render progress bar for a few seconds and everything after
(tunnel, submit, poll, download) is background.

    from . import genai_render
    info = genai_render.render_movie(node, node.port("input"),
                                     path, start, end)
    # info = {"path", "frames", "fps", "start"}

Raises ``RenderCanceled`` when the user cancels the progress dialog (callers
should treat that as a silent abort, not an error) and ``RenderError`` for
real failures.  The half-written file is removed on both.
"""

import os

from fx import *


class RenderError(Exception):
    pass


class RenderCanceled(Exception):
    pass


def render_movie(node, port, path, start, end, channels=Channel_RGB,
                 title="Rendering input for generation..."):
    """Render `port` on `node` over [start, end] into the movie file `path`."""
    session = node.session
    if session is None:
        raise RenderError("The node is not part of a session.")
    start, end = int(start), int(end)
    if end < start:
        raise RenderError("Empty frame range.")
    frames = end - start + 1
    if frames < 2:
        # the movie writer requires a real range (GST_Writer.cpp saveField
        # asserts frameIndexMin < frameIndexMax)
        raise RenderError("The frame range must cover at least 2 frames.")

    # the writer comes from the file extension (.mov -> the movie module)
    module = MediaFormat(path=path)
    if not module or not module.writeable:
        raise RenderError("No writer available for '%s'." % os.path.basename(path))
    if not module.streaming:
        raise RenderError("'%s' is not a movie format." % os.path.basename(path))
    # HOST CONTRACT: movie write paths must carry the module's DEFAULT
    # extension — the Output-node path always rebuilds names with
    # module.extension, and the GStreamer writer asserts it
    # (Writer::noDefaultExtension GUARDs .endsWith(".mov"); the container
    # written is ISO/MPEG-4-compatible regardless, see GST_Render.cpp).
    if module.extension and not path.lower().endswith("." + module.extension.lower()):
        raise RenderError("Movie paths must use the writer's default '.%s' "
                          "extension (got '%s')."
                          % (module.extension, os.path.basename(path)))

    # render at the session's configured output resolution, padded/cropped to
    # the display window so every movie frame has identical dimensions
    # (mirrors tools/renderer.py's Output-node behaviour).
    resolution = session.property("output.resolution").value
    proxy = session.proxyInfo(resolution)
    displayWindow = calcROI(session.displayWindow, resolution)
    frameRate = session.frameRate
    pixelAspect = session.pixelAspect

    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    if not module.create(path, []):
        raise RenderError("Could not create '%s'." % path)

    progress = ProgressHandler(cancel=True)
    progress.begin(title)
    progress.total = frames
    ok = False
    try:
        for i in range(frames):
            frame = start + i
            progress.value = i
            if progress.canceled:
                raise RenderCanceled()
            rc = RenderContext(RenderContext.STATE_OUTPUT, node=node,
                               roi=displayWindow, proxy=proxy,
                               format=session.depth)
            image = node.eval(frame, rc, port, "image", channels=channels)
            if rc.canceled:
                raise RenderCanceled()
            if image is None:
                raise RenderError("Nothing to render at frame %d — is the "
                                  "input connected?" % frame)
            image = image.crop(displayWindow)     # constant size, black-padded
            # HOST CONTRACT: the movie writer consumes a contiguous ZERO-BASED
            # frame sequence — its render pipeline counts 0..duration-1 and
            # asserts each putImage matches (GST_Writer.cpp saveField,
            # frameIndex == renderReq.frameIndex).  The session frame above
            # only selects WHAT to render; the writer gets the sequence index.
            if not module.putImage(image, frame=i, frameRate=frameRate,
                                   pixelAspect=pixelAspect, roi=displayWindow,
                                   displayWindow=displayWindow,
                                   channels=channels, premultiply=False,
                                   metadata={}, duration=frames):
                raise RenderError("Could not write frame %d to '%s'." % (frame, path))
        ok = True
    finally:
        progress.end()
        try:
            module.close()                        # finalizes the movie
        except Exception:
            if ok:
                raise
        if not ok:
            try:
                if os.path.exists(path):
                    os.remove(path)               # never leave a broken movie
            except Exception:
                pass

    return {"path": path, "frames": frames, "fps": float(frameRate), "start": float(start)}
