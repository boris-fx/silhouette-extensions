"""Stability AI extension.

Bundles everything the service needs: the PyStabilityAI node, its task provider
and out-of-process worker, the mode/model config, and its API-key option.

The API key is registered here as an extension *option* pref
(``option=True``), so it doesn't clutter a global preferences page — it surfaces
only behind this extension's Options button on the Extensions page (and via
``fx.showExtensionOptions("com.borisfx.stability")``, which the node's Generate
button calls when the key is missing).

The pref id is kept as ``ml.stability_api_key`` for backward compatibility: any
key a user already entered keeps reading the same value.  The API host is not a
preference — it is fixed in stability_provider.py.
"""
import fx

from extensions.loader import autoload

# Credential option — owned by this extension (the loader auto-tags the owner),
# hidden from the global Preferences dialog, shown only in the Options dialog.
# Registered before autoload so it exists by the time the node/provider run.
fx.prefs.add("ml.stability_api_key", "", option=True, label="API Key")

autoload(__package__)
