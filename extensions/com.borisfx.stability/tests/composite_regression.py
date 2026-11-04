"""Run run() in headless Silhouette; no service, account, or project is needed."""

from importlib import import_module
import types
import fx


def run():
    import numpy as np
    module = import_module("extensions.com_borisfx_stability.PyStabilityAI")
    native_raster = module.Raster

    def as_float(r):
        data = np.array(r)
        return data.astype(np.float64) / 255 if data.dtype == np.uint8 else data.astype(np.float64)

    # Model the corrected native over operation while running on an older host.
    # The production process() itself is used unchanged in both paths.
    class OverTarget:
        def __init__(self, **kwargs):
            self.raster = native_raster(**kwargs)
        def copy(self, source, **kwargs):
            self.raster.copy(source, **kwargs)
        def composite(self, foreground, roi):
            bg, fg = as_float(self.raster), as_float(foreground)
            fa, ba = fg[:, :, 3:4], bg[:, :, 3:4]
            alpha = fa + ba * (1-fa)
            rgb = fg[:, :, :3] * fa + bg[:, :, :3] * ba * (1-fa)
            np.divide(rgb,alpha,out=rgb,where=alpha>0)
            values = np.concatenate((rgb,alpha),axis=2)
            dtype = np.array(self.raster).dtype
            if dtype == np.uint8:
                values = np.rint(np.clip(values,0,1)*255)
            return fx.Raster.fromBuffer(values.astype(dtype),dod=roi)

    try:
        for dtype in (np.uint8,np.float16,np.float32):
            original = np.tile((.7,.25,.8,1.),(1,5,1))
            original[0,:,3] = (0,.25,.5,.75,1)
            generated = np.tile((.2,.9,.1),(1,5,1))
            def raster(a):
                if dtype == np.uint8:
                    a = np.rint(np.clip(a,0,1)*255)
                return fx.Raster.fromBuffer(a.astype(dtype))
            input_image, asset = raster(original), raster(generated)
            bg, gen = as_float(input_image), as_float(asset)[:,:,:3]
            matte = bg[:,:,3:4]
            for mode in ("inpaint","outpaint"):
                expected = gen * matte + bg[:,:,:3]*(1-matte) if mode=="inpaint" else bg[:,:,:3]*matte + gen*(1-matte)
                for factory in (native_raster,OverTarget):
                    module.Raster = factory
                    proxy = types.SimpleNamespace(node=types.SimpleNamespace(enabled=True),
                        input=types.SimpleNamespace(eval=lambda *args: input_image),
                        property=lambda name: types.SimpleNamespace(value="fixture"),
                        _load_asset=lambda *args: asset, _will_composite=lambda *args: True,
                        _mode_name=lambda: mode,
                        allocate=lambda source, roi, channels: module.Raster(size=source.size,
                            roi=roi, format=source.format, channelMask=channels))
                    out = module.PyStabilityAI.process(proxy,None,types.SimpleNamespace(roi=input_image.dod),"image",None)
                    got=as_float(out)
                    tolerance=1.1/255 if dtype==np.uint8 else .002 if dtype==np.float16 else 1e-6
                    np.testing.assert_allclose(got[:,:,:3],expected,atol=tolerance,rtol=0)
                    np.testing.assert_array_equal(got[:,:,3:4],matte)
                    np.testing.assert_array_equal(as_float(input_image),bg)
        print("PASS: Stability inpaint/outpaint retain RGB and input matte under current and corrected over APIs")
    finally:
        module.Raster = native_raster
