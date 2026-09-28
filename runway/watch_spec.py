#!/usr/bin/env python3
"""Flag changes to Runway's model list since the published model table.

Silhouette's Runway extension fetches ``runway/v<N>/runway_config.json`` from
this repo ("Check for New Models").  That file records, under ``spec_models``,
every model id Runway's OpenAPI spec listed per endpoint when it was generated.
This script fetches the live spec and reports models added or removed since —
the cue to curate them (pricing, labels, testing) in Silhouette and republish.
It also diffs ``spec_endpoints`` (every endpoint that takes a ``model``): a NEW
endpoint is a new verb, which needs a new node in Silhouette — the model table
alone can't deliver it.

Writes a Markdown report and, under GitHub Actions, ``changed=true|false`` to
$GITHUB_OUTPUT.  Stdlib only.

    python3 runway/watch_spec.py --config runway/v1/runway_config.json --out report.md
"""

import argparse
import json
import os
import sys
import urllib.request

SPEC_URL = "https://raw.githubusercontent.com/runwayml/openapi/next/openapi.json"


def spec_models(spec, verb):
    """Model ids in one generation endpoint's request ``oneOf`` (None if the
    endpoint is gone)."""
    try:
        schema = spec["paths"]["/v1/" + verb]["post"]["requestBody"]["content"][
            "application/json"]["schema"]
    except KeyError:
        return None
    ids = set()
    for v in schema.get("oneOf") or schema.get("anyOf") or [schema]:
        if "$ref" in v:
            v = spec["components"]["schemas"][v["$ref"].split("/")[-1]]
        m = v.get("properties", {}).get("model", {})
        mid = m.get("const") or (m.get("enum") or [None])[0]
        if mid:
            ids.add(mid)
    return ids


def model_endpoints(spec):
    """Every POST endpoint whose request picks a ``model`` (mirrors the
    generator's model_endpoints)."""
    out = set()
    for path, item in spec.get("paths", {}).items():
        op = (item or {}).get("post") or {}
        try:
            schema = op["requestBody"]["content"]["application/json"]["schema"]
        except (KeyError, TypeError):
            continue
        for v in schema.get("oneOf") or schema.get("anyOf") or [schema]:
            if "$ref" in v:
                v = spec["components"]["schemas"].get(v["$ref"].split("/")[-1], {})
            m = v.get("properties", {}).get("model", {})
            if m.get("const") or m.get("enum"):
                out.add(path[len("/v1/"):] if path.startswith("/v1/") else path)
                break
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True, help="published runway_config.json")
    ap.add_argument("--spec", default=SPEC_URL, help="OpenAPI spec URL or path")
    ap.add_argument("--out", required=True, help="Markdown report path")
    args = ap.parse_args(argv)

    with open(args.config) as f:
        published = json.load(f)
    known = published.get("spec_models") or {}
    known_endpoints = published.get("spec_endpoints")
    if args.spec.startswith("http"):
        req = urllib.request.Request(args.spec, headers={"User-Agent": "silhouette-runway-watch/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            spec = json.loads(resp.read().decode("utf-8"))
    else:
        with open(args.spec) as f:
            spec = json.load(f)

    lines, changed = [], False
    for verb in sorted(known):
        live = spec_models(spec, verb)
        if live is None:
            lines.append("- `%s`: **endpoint no longer in the spec**" % verb)
            changed = True
            continue
        added, removed = sorted(live - set(known[verb])), sorted(set(known[verb]) - live)
        if added or removed:
            changed = True
            parts = []
            if added:
                parts.append("added " + ", ".join("`%s`" % m for m in added))
            if removed:
                parts.append("removed " + ", ".join("`%s`" % m for m in removed))
            lines.append("- `%s`: %s" % (verb, "; ".join(parts)))

    if known_endpoints is not None:
        live_eps = model_endpoints(spec)
        new_eps = sorted(live_eps - set(known_endpoints))
        gone_eps = sorted(set(known_endpoints) - live_eps)
        if new_eps:
            changed = True
            lines.append("- **new endpoints** (a new verb needs a new Silhouette node): "
                         + ", ".join("`%s`" % e for e in new_eps))
        if gone_eps:
            changed = True
            lines.append("- endpoints removed: " + ", ".join("`%s`" % e for e in gone_eps))

    report = ("Runway's API spec (version %s) lists different models than the "
              "published model table:\n\n%s\n\n"
              "To handle it: in Silhouette's `com.borisfx.runway`, add or expose "
              "models in `runway_overlay.json` (or drop removed ones), then run "
              "`gen_runway_config.py --publish <this repo>` and commit both. "
              "Republishing without exposing anything also acknowledges the "
              "change, which closes this out on the next run.\n"
              % (spec.get("info", {}).get("version", "?"), "\n".join(lines))
              if changed else "No change.\n")
    with open(args.out, "w") as f:
        f.write(report)
    sys.stdout.write(report)

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write("changed=%s\n" % ("true" if changed else "false"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
