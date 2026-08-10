# GenAI Emulator — the reference video-GenAI extension

This extension is a **complete, working simulation of a cloud video-GenAI
service**.  Every stage of the real pipeline runs for real — rendering,
tunneling, task submission, polling, download, asset publishing — except the
"cloud", which is a bundled local emulator that echoes the input video back
after a simulated generation delay.  Development and integration testing burn
**no tokens**.

It is also the **template for integrating a real service** (Runway, etc.):
the mechanics below are common to every provider — only the service adapter
and configuration change.  See *Adding a real service*.

## The pipeline

```
            main thread (fx)                      worker subprocess (no fx)
  ┌──────────────────────────────┐   ┌─────────────────────────────────────────┐
  │ Generate                     │   │                                         │
  │  1. render input → temp .mp4 │──▶│  2. expose it: localhost HTTP server    │
  │     (genai_render.py,        │   │     + Cloudflare quick tunnel           │
  │     blocking progress bar)   │   │     (_expose.py)                        │
  │                              │   │  3. POST the generation task            │
  │                              │   │     {prompt, model, video_url}          │
  │                              │   │  4. poll id until terminal; download    │
  │ 6. on_result: publish into   │◀──│     result → outputs/N.mp4              │
  │    the AssetCatalog; badge   │   │     (_genai_worker.py)                  │
  │    the node                  │   │                                         │
  │ 7. user picks an asset; the  │   │   the "cloud" here is _genai_service.py │
  │    node plays the clip over  │   │   spawned locally — it really fetches   │
  │    the frames it came from   │   │   the tunnel URL, waits, echoes back    │
  └──────────────────────────────┘   └─────────────────────────────────────────┘
```

Stage 1 is deliberately main-thread/blocking (a normal cancellable progress
dialog); everything after `Generate` returns is a background task with live
progress in the Tasks dock.

## Files

| file | role | fx? |
|---|---|---|
| `GenAI.py` | the node: prompt/model/samples in, selected clip out (movie render path: frame mapping, held ends, fit + letterbox, cached `fx.Source`) | yes |
| `genai_provider.py` | task provider: renders the input movie in `build()`, writes `job.json`, publishes results in `on_result()` | yes |
| `genai_render.py` | render a node port over a frame range to a movie file (`MediaFormat`, streaming writer, progress + cancel) | yes |
| `_genai_worker.py` | the **emulator service adapter** (`GenAIAdapter` + `main()`) on the shared engine — submit/poll/cancel/download, plus `setup`/`teardown` (spawns/stops the service) and `expose_input` (FileShare); `durable_media_url=False` | no |
| `_genai_service.py` | the emulator "cloud": create/poll/cancel/download API; echoes the fetched input; durable task registry; parent-death watchdog | no |
| _(shared)_ `genai.task_engine` | the generic resumable engine (checkpoint/resume, PAUSE/CANCEL/orphan, submit/poll/download loops) — ships with Silhouette's scripts; imported, not owned here | no |
| _(shared)_ `genai.expose` | `FileShare`: localhost server + `cloudflared` quick tunnel + DNS-readiness gate — also shipped with Silhouette | no |
| `genai_config.json` | model table + pipeline tuning (gen delay, poll interval, tunnel mode) | — |
| `__init__.py` | option prefs (API key / server / cloudflared path) + autoload | yes |

The fx-free files run from a bare terminal — see *Debugging* below.  The two
shared modules are part of Silhouette itself: this extension imports them, so
it needs no copy of its own, but it does require a Silhouette new enough to
ship them (see `extension.toml`).

## Try it

1. Install the extension via *Preferences → Extensions → Install…* (or, from
   the Python console, `extensions.install("<path to this dir>")`).
2. Create a **GenAI Emulator** node (AI/ML category) and connect any clip.
3. Set the work range to a few seconds, click **Generate**.  A render
   progress bar runs, then the task continues in the Tasks dock:
   *Starting emulator service… → Exposing input video… → Submitting… →
   Generating… → Downloading… → Done.*
4. The result lands in the Asset Browser with the node badged; *Use Latest*
   (or click the thumbnail) and scrub — the clip plays back over exactly the
   work-range frames it was rendered from.

With `cloudflared` installed (`brew install cloudflared`, or set the path in
the extension Options) the input genuinely round-trips the public Cloudflare
edge; without it the emulator degrades to a direct localhost URL
(`tunnel: "auto"` in the config — a real remote service requires the tunnel).

## Adding a real service

Copy this extension, rename the id (`extension.toml`, `__init__.py`,
`fx.showExtensionOptions` call in the node), then:

1. **`_genai_worker.py` — the service adapter**: write an adapter class (like
   `GenAIAdapter`) whose methods talk the vendor's API, and a `main()` that
   builds it and calls `genai.task_engine.run(store, job, adapter)`.  The
   shared engine owns checkpoint/resume, stop semantics, and the submit/poll/
   download loops — you do NOT reimplement them.  Adapter contract (see
   `genai/task_engine.py`'s docstring):
   - `submit(job, media_url, keyframes, sample_index, uploads)` — create-task
     call; return the task id
   - `poll(task_id)` — status call, normalized to
     `{"state": waiting|succeeded|failed, "progress", "result_url", "tokens",
     "message", "meta"?}`
   - `cancel(task_id)` — their cancel (usually queued-only)
   - `download(url, path)` — fetch the finished result
   - `expose_input(path)` / `upload(path)` — make media reachable (upload API,
     tunnel URL, or inline data-URI); set `durable_media_url=False` if the URL
     dies with the process
   - optional `setup(job, store)` / `teardown()` for a spawned local service;
     a real cloud omits them
   - `error_message(http_error)` — human text from an HTTPError
2. **`genai_config.json`** — real model ids + per-model capability flags
   (resolutions, duration bounds, parameter support), and set `tunnel: "on"`
   (a remote service cannot reach localhost).
3. **`genai_provider.py`** — put the service's extra request parameters in
   `job["args"]`, re-label the pref messages.  If a model family has quirks
   (unsupported parameters are often *errors*, not ignored), gate them here
   and in `updatePropertyState`.
4. **`GenAI.py`** — rename the node, add the service's properties
   (resolution/ratio/seed/…).  The render path and submit flow stay.
5. Prefs in `__init__.py`: real key/server defaults.

What you should NOT need to touch: the shared `genai.task_engine` and
`genai.expose` modules, `genai_render.py`, the provider's `on_result`, or the
node's query/process movie path.

Ground rules that apply to every service — see the *Extensions* chapter of the
Silhouette scripting documentation for the full provider contract: secrets ride
environment variables, never `job.json`; the worker never imports `fx`; tasks
never mutate the object model — results land in the sidecar AssetCatalog and
the *user* selects; `on_submit`/`on_result` run on the main thread,
`process()/query()` on the render thread.  Mind the two
frame spaces: `session.workRange` reports the timeline's *user* numbering
(shifted by `session.startFrame`) while `node.eval()`, `options.frame` and
`fx.player.frame` are *internal 0-based* — convert at the boundary (see
`on_submit`) or captures and playback anchors shift by the start frame.

## Debugging from a terminal

```sh
# the emulator service alone
python3 _genai_service.py --port 8787 --gen-seconds 3
curl -X POST localhost:8787/v1/tasks -H "Authorization: Bearer x" \
     -H "Content-Type: application/json" \
     -d '{"model":"emulator-hd-1","video_url":"https://example.com/in.mp4"}'

# the whole worker pipeline against a hand-made store
mkdir -p /tmp/genai-store/inputs && cp some.mov /tmp/genai-store/inputs/input.mov
cat > /tmp/genai-store/job.json <<'EOF'
{ "service": {"url": "", "gen_seconds": 3.0},
  "prompt": "test", "model": "emulator-hd-1", "args": {},
  "input": "inputs/input.mov", "samples": 1,
  "poll_interval": 1.0, "timeout": 300.0, "tunnel": "auto", "cloudflared": "" }
EOF
SFX_GENAI_KEY=x python3 _genai_worker.py /tmp/genai-store
# newline-JSON progress on stdout; result manifest at the end;
# touch /tmp/genai-store/CANCEL to test cancellation
```
