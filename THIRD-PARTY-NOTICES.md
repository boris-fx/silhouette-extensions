# Third-party notices

Some extensions in this repository vendor third-party source. Those files carry
an attribution header naming their upstream, and their licenses are reproduced
or referenced below. Model weights are **not** distributed in this repository —
see each extension's README for how they are obtained.

---

## NAFNet — `extensions/com.borisfx.nafnet/vendor/nafnet/nafnet.py`

Assembled from three files in [megvii-research/NAFNet](https://github.com/megvii-research/NAFNet)
(*Simple Baselines for Image Restoration*, Chen et al., ECCV 2022). The only
modification is the removal of intra-package imports, so the model is one
self-contained module; the header in the file records this.

NAFNet is released under the **MIT License**, and portions are derived from
[BasicSR](https://github.com/XPixelGroup/BasicSR), which is licensed under the
**Apache License 2.0**. Both permit redistribution and commercial use provided
these notices are retained.

### MIT License

> Copyright (c) 2022 megvii-model
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

### Apache License 2.0 (BasicSR portions)

Copyright 2018-2020 BasicSR Authors. Licensed under the Apache License, Version
2.0, whose full text is at <https://www.apache.org/licenses/LICENSE-2.0>.

Unless required by applicable law or agreed to in writing, software distributed
under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
CONDITIONS OF ANY KIND, either express or implied.

---

## Silhouette's own scripts

The `genai` extension imports the `genai.task_engine` and `genai.expose`
modules that ship with Silhouette. They are not vendored here.
