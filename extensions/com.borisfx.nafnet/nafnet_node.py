"""NAFNet - single-image restoration node backed by NAFNet (ECCV 2022).

A reference Python node extension (companion to com.borisfx.xvfi): it pulls
PyTorch on demand (tools.ensure, auto-pinned to the host's libtorch), loads a
vendored model, and runs real inference in process().  Checkpoints are fetched
on demand: until the selected model's weights are installed the node reports a
render error and produces nothing, and the Download Model button fetches them
as a background task (see nafnet_download.py).

Per-frame filter: restores each frame independently (no temporal window).  The
default model is GoPro deblurring.

Platform: CUDA if available, else CPU.  Apple MPS is NOT used: in-process, pip
torch is bound to the ML SDK's libtorch, and that build's MPS backend hard-crashes
in F.pad (constant_pad_nd_mps, MPSAllocator.mm:654) - confirmed from the full
crash stack, and NAFNet pads every forward.  A genuine SDK MPS bug, so Mac runs
on CPU until the SDK libtorch's MPS backend is fixed.
"""
import importlib.util

from fx import *

from tools import extension
from tools.ensure import ensure
from tools.image import numpy_wrap

from . import nafnet_download


def _device(torch):
    # MPS is intentionally NOT used.  In-process, pip torch is bound to the ML
    # SDK's libtorch (version pin), and that build's MPS backend hard-crashes in
    # F.pad: at::native::mps::constant_pad_nd_mps faults in the MPS heap allocator
    # (ai_libtorch_mac MPSAllocator.mm:654) - confirmed from the full stack, and
    # NAFNet pads every forward.  A genuine SDK MPS bug, uncatchable.  Mac = CPU
    # until the SDK libtorch's MPS backend is fixed.
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


class NAFNet(NodeProxy):
    """Restore an image — denoise, deblur, and remove compression artifacts (NAFNet)."""

    def __init__(self, *args, **kwargs):
        self.input = self.addInput("input", "Input")
        self.output = self.addOutput("output", "Output", cache=True)

        self._models = nafnet_download.models()
        self.model = self.addProperty("model", {"label": "Model", "value": 0, "supervise": True,
                                                "items": [m["label"] for m in self._models]})
        self.addProperty("download", {"label": "Download Model", "type": "button",
                                      "callback": self.on_download,
                                      "tooltip": "Fetch the selected model's checkpoint "
                                                 "as a background task"})
        self._net = None            # (model_id, module, device), built lazily on first render

    def setup(self):
        # Called once when the node is added to a Session (main thread).  torch's
        # first import in the process MUST be on the main thread - a first import
        # from the render thread (inside process()) crashes - so warm it up here.
        try:
            ensure("numpy")
            ensure("torch")
        except Exception as e:
            print("[NAFNet] torch warm-up failed:", e)

    def updatePropertyState(self):
        self._net = None            # model changed -> rebuild on the next render
        cfg = self._models[int(self.model.getValue(0))]
        self.property("download").enabled = nafnet_download.button_enabled(cfg)

    def on_download(self):
        """Download Model button (UI thread): submit the background fetch.
        The provider re-enables/refreshes the button when the task settles."""
        cfg = self._models[int(self.model.getValue(0))]
        try:
            nafnet_download.request(cfg)
        except Exception as e:
            displayError(str(e))
        self.updatePropertyState()

    def _weight(self, cfg):
        return nafnet_download.weight_path(cfg)

    def _load(self, cfg):
        if self._net and self._net[0] == cfg["id"]:
            return self._net[1], self._net[2]
        torch = ensure("torch")

        # vendored model, loaded by path (needn't be importable on sys.path)
        src = extension.path(__file__, "vendor", "nafnet", "nafnet.py")
        spec = importlib.util.spec_from_file_location("_nafnet", src)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        device = _device(torch)
        net = mod.NAFNetLocal(img_channel=3, width=cfg["width"],
                              middle_blk_num=cfg["middle_blk_num"],
                              enc_blk_nums=cfg["enc_blk_nums"],
                              dec_blk_nums=cfg["dec_blk_nums"])
        # BasicSR checkpoints store the weights under 'params'
        ckpt = torch.load(self._weight(cfg), map_location="cpu", weights_only=False)
        net.load_state_dict(ckpt.get("params", ckpt))
        net = net.to(device).eval()

        self._net = (cfg["id"], net, device)
        return net, device

    def query(self, rc, options, type, port):
        return self.input.query(rc, options, type)

    def process(self, rc, options, type, port):
        cfg = self._models[int(self.model.getValue(options.frame))]

        # No checkpoint on disk: the node can't do the one thing it exists to
        # do, so report a render error and produce nothing.  Checked before any
        # upstream evaluation - there is no point rendering an input we won't
        # use, and passing it through would be worse than empty: the frame would
        # look like a successful restoration.
        if not nafnet_download.installed(cfg):
            hint = ("use the node's Download Model button"
                    if nafnet_download.downloadable(cfg)
                    else "see the extension's README to install it manually")
            rc.setError("NAFNet: the '%s' checkpoint is not installed - %s"
                        % (cfg.get("label", cfg["id"]), hint))
            return None

        torch = ensure("torch")
        np = ensure("numpy")

        # NAFNet mixes pixels spatially, so evaluate the full DOD as float RGBA
        # (not just options.roi).
        info = self.input.query(rc, options, type)
        if not info or not info["dod"].valid:
            return None
        full = options.copy()
        full.roi = info["dod"]
        full.format = Depth_Float32
        full.channels = Channel_RGBA

        img = self.input.eval(rc, full, type)
        if img is None:
            return None

        net, device = self._load(cfg)

        # RGB 0..1 -> [1, C, H, W]; the model normalises and pads internally
        a = numpy_wrap(img)
        rgb = np.clip(np.ascontiguousarray(a[:, :, :3]), 0.0, 1.0).astype(np.float32)
        x = torch.from_numpy(rgb).permute(2, 0, 1)[None].to(device)

        if rc.canceled:
            return None
        # no_grad (not inference_mode): NAFNet's LayerNorm is a custom
        # autograd.Function whose save_for_backward is rejected under inference_mode.
        with torch.no_grad():
            out = net(x)                                        # [1, C, H, W] in 0..1
        res = out.clamp_(0.0, 1.0)[0].permute(1, 2, 0).cpu().numpy()

        output = self.allocate(img)                            # float32 RGBA, full DOD
        dst = numpy_wrap(output)
        dst[:, :, :3] = res
        if a.shape[2] == 4:
            dst[:, :, 3] = a[:, :, 3]                           # carry the source alpha
        return output


addNode(name="com.borisfx.silhouette.nafnet", label="NAFNet Restore", type=NAFNet,
        categories=["AI/ML", "Image"], dataTypes=["image"],
        intents=["deblur", "denoise", "restore"])

if __name__ == "__main__":
    from tools.dev import test_node
    test_node(Node("com.borisfx.silhouette.nafnet"))
