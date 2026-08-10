"""GenAI Emulator extension — the reference video-GenAI pipeline.

Bundles a complete simulation of a cloud video-generation service: the
GenAI node, its task provider, the render-out helper, the tunnel/file-share
helper, the generic fx-free worker, and a local emulator "cloud" service.
See README.md in this directory for the architecture walkthrough and the
checklist for adapting it to a real service.

The prefs below are registered as extension *options* (``option=True``) so
they surface behind this extension's Options button on the Extensions page.
They deliberately mirror what a real service needs, so an adaptation only
re-labels them:

- ``genai.api_key`` — bearer token sent on every API call.  The emulator
  accepts any non-empty value (the default), but the auth plumbing is real.
- ``genai.server`` — the service base URL.  Empty (the default) means
  "emulator": the worker spawns the bundled local service and talks to it
  exactly as it would a remote host.
- ``genai.cloudflared`` — path to the ``cloudflared`` binary for the quick
  tunnel that makes the rendered input fetchable by the service.  Empty
  searches $PATH.  (The emulator falls back to a direct localhost URL when
  no tunnel is available; a real remote service requires the tunnel.)
"""
import fx

from extensions.loader import autoload

fx.prefs.add("genai.api_key", "emulator", option=True, label="API Key")
fx.prefs.add("genai.server", "", option=True, label="Server")
fx.prefs.add("genai.cloudflared", "", option=True, label="cloudflared Path")

autoload(__package__)
