# Runway model table

`v1/runway_config.json` is the curated list of Runway models that Silhouette's
Runway nodes offer. Silhouette fetches it when you click **Check for New Models**
in the Runway extension's Options (Preferences → Extensions → Runway → Options),
so new models reach existing installs without an application update.

- **Generated, not hand-edited.** It is produced from Runway's public
  [OpenAPI spec](https://github.com/runwayml/openapi) plus Boris FX's curated
  labels, descriptions and pricing, by `gen_runway_config.py` in Silhouette's
  Runway extension (`--publish <this repo>` writes it here).
- **`v1` is the file format.** A Silhouette build only reads the version it
  understands; a format change is published under a new `vN/` alongside.
- **Only the model list is used.** Silhouette ignores the file's `service`
  block — the Runway API host always comes from the copy bundled with the app.
- **Models that need newer code stay hidden.** Each model's `requires` tags name
  request features beyond the basics; a build lists a model only if it supports
  all of them.

`watch_spec.py` runs daily (`.github/workflows/runway-watch.yml`) and opens an
issue labelled `runway-models` when Runway's spec lists models that differ from
this table's `spec_models`.
